"""
updater.py — keep the app on this PC in step with GitHub.

Changes are made on GitHub and merged into `master`. When the app starts (and
from Settings → Updates) it fetches origin/master and moves this folder to it:

- only a fast-forward: never a merge, and never over files you changed;
- auctions.db is copied to backups/ first (a new version may upgrade its schema);
- if requirements.txt changed, the new packages are installed before the app
  loads them;
- not while a scan is running, and never fatal: offline or no git means the
  app simply starts as it is;
- a version that does not start is rolled back: the app returns to the last
  version that started on this PC and skips the broken one until a newer one
  is published (see "Rollback" below).

Your data (auctions.db, config.json, reports/, logs) is not in git, so an update
never touches it.

Only the standard library is used here: this runs before the app imports its
packages, so pip can update them.
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BRANCH = "master"
REMOTE = "origin"
KEEP_BACKUPS = 5
LOG = logging.getLogger("updater")

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW: no console flash


def _git(root: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def _commit(root: str, ref: str) -> dict | None:
    r = _git(root, "log", "-1", "--format=%h%x09%cs%x09%s", ref)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    sha, day, subject = (r.stdout.strip().split("\t", 2) + ["", ""])[:3]
    return {"sha": sha, "date": day, "subject": subject}


def status(root: str = HERE, *, fetch: bool = True) -> dict:
    """Where this folder stands against GitHub. `ok` is False, with a `reason`
    in plain words, when it cannot be updated automatically."""
    out = {"ok": False, "reason": "", "current": None, "latest": None, "behind": 0, "changes": []}
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        out["reason"] = "Git is not installed, so the app cannot update itself. Install Git for Windows."
        return out
    except subprocess.TimeoutExpired:
        out["reason"] = "Git did not answer."
        return out
    if inside.returncode != 0:
        out["reason"] = ("This folder was not installed with git (git clone), so it cannot update "
                         "itself. See README → Updates.")
        return out

    out["current"] = _commit(root, "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != BRANCH:
        out["reason"] = f"This folder is on the branch '{branch}', not '{BRANCH}'; updates only follow {BRANCH}."
        return out

    if fetch:
        try:
            got = _git(root, "fetch", "--quiet", REMOTE, BRANCH, timeout=45)
        except subprocess.TimeoutExpired:
            out["reason"] = "GitHub did not answer in time (offline?)."
            return out
        if got.returncode != 0:
            out["reason"] = "Could not reach GitHub: " + (got.stderr.strip().splitlines() or ["unknown error"])[-1]
            return out

    upstream = f"{REMOTE}/{BRANCH}"
    out["latest"] = _commit(root, upstream)
    if out["latest"] is None:
        out["reason"] = f"{upstream} is unknown; press Check now while online."
        return out
    bad = _read_json(root, _BAD)
    if bad and bad.get("sha") == _full_sha(root, upstream):
        out["reason"] = (f"The newest version ({bad['sha'][:7]}) did not start on this PC, so the app went "
                         "back to the version before it. It will update again when a newer version is published.")
        return out
    counts = _git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}").stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    out["behind"] = behind
    log = _git(root, "log", "--format=%h%x09%cs%x09%s", "-30", f"HEAD..{upstream}").stdout
    out["changes"] = [dict(zip(("sha", "date", "subject"), line.split("\t", 2)))
                      for line in log.splitlines() if line.strip()]

    changed = [line[3:] for line in _git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()]
    if changed:
        out["reason"] = ("Files in the app folder were changed on this PC (" + ", ".join(changed[:5]) +
                         "), so updates will not overwrite them. Undo those changes, or commit them to GitHub.")
        return out
    if ahead and behind:
        out["reason"] = "This folder has its own commits that are not on GitHub; they have to be pushed or dropped first."
        return out
    out["ok"] = True
    return out


def backup_database(root: str = HERE) -> str | None:
    """Copy auctions.db to backups/ (keeping the last few). Returns the copy's path."""
    db_path = os.environ.get("AUCTION_SCANNER_DB") or os.path.join(root, "auctions.db")
    if not os.path.exists(db_path):
        return None
    folder = os.path.join(root, "backups")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"auctions-{datetime.now():%Y%m%d-%H%M%S}.db")
    import sqlite3
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        src.backup(dst)          # consistent even while another process has it open
    finally:
        dst.close()
        src.close()
    for old in sorted(glob.glob(os.path.join(folder, "auctions-*.db")))[:-KEEP_BACKUPS]:
        os.remove(old)
    return target


def apply(root: str = HERE, *, fetch: bool = True) -> dict:
    """Fast-forward to origin/master. Returns the status plus `updated`,
    `backup` and `requirements_changed`."""
    st = status(root, fetch=fetch)
    st.update(updated=False, backup=None, requirements_changed=False)
    if not st["ok"] or not st["behind"]:
        return st
    before = _requirements_hash(root)
    before_sha = _full_sha(root, "HEAD")
    st["backup"] = backup_database(root)
    merged = _git(root, "merge", "--ff-only", f"{REMOTE}/{BRANCH}")
    if merged.returncode != 0:
        st["ok"] = False
        st["reason"] = "The update could not be applied: " + (merged.stderr.strip().splitlines() or ["?"])[-1]
        return st
    st["updated"] = True
    st["requirements_changed"] = _requirements_hash(root) != before
    st["previous"], st["current"] = st["current"], _commit(root, "HEAD")
    _remember(root, st)
    # Not yet known to work: confirm_start() clears this once it has started.
    _write_json(root, _CHECK, {"from": before_sha, "to": _full_sha(root, "HEAD"), "starts": 0})
    _remove(root, _BAD)
    LOG.info(f"Updated {st['previous']['sha']} → {st['current']['sha']} ({st['behind']} change(s))")
    return st


# ─── Packages ─────────────────────────────────────────────────────────

_MARKER = ".installed-requirements"


def _requirements_hash(root: str) -> str:
    try:
        with open(os.path.join(root, "requirements.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def requirements_outdated(root: str = HERE) -> bool:
    """Has requirements.txt changed since pip last installed it from here?"""
    try:
        with open(os.path.join(root, _MARKER), encoding="utf-8") as f:
            return f.read().strip() != _requirements_hash(root)
    except OSError:
        return True


def install_requirements(root: str = HERE) -> tuple[bool, str]:
    """pip install -r requirements.txt with this Python. Call it before the app
    imports its packages."""
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            "-r", os.path.join(root, "requirements.txt")],
                           cwd=root, capture_output=True, text=True, timeout=900, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines() or ["pip failed"])[-1]
    with open(os.path.join(root, _MARKER), "w", encoding="utf-8") as f:
        f.write(_requirements_hash(root))
    return True, "packages installed"


# ─── At start-up ──────────────────────────────────────────────────────

_LAST = ".last-update.json"


def _remember(root: str, st: dict):
    info = {"at": datetime.now().isoformat(timespec="seconds"), "from": st.get("previous"),
            "to": st.get("current"), "changes": st.get("changes", [])}
    try:
        with open(os.path.join(root, _LAST), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def last_update(root: str = HERE) -> dict | None:
    try:
        with open(os.path.join(root, _LAST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _auto_enabled(root: str) -> bool:
    try:
        with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
            return (json.load(f).get("updates") or {}).get("auto", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def _scan_running(root: str) -> bool:
    """A scan holds scan.lock. A lock left behind by a scan that was stopped
    (app closed or killed, PC shut down) does not count: its process is gone.
    It used to count for 3 hours, and the app started without updating."""
    from locks import lock_holder
    lock = os.path.join(root, "scan.lock")
    return (os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 3 * 3600
            and lock_holder(lock) is not None)


def update_on_start(root: str = HERE) -> bool:
    """Update if an update is due; install packages if requirements changed.
    Returns True when the code changed, so the caller restarts to run it.
    Never raises."""
    changed = False
    try:
        if _auto_enabled(root) and _scan_running(root):
            LOG.info("No automatic update: a scan is running (it updates next time)")
        elif _auto_enabled(root):
            st = apply(root)
            changed = st["updated"]
            if not st["ok"] and st["reason"]:
                LOG.info(f"No automatic update: {st['reason']}")
        if requirements_outdated(root):
            ok, message = install_requirements(root)
            (LOG.info if ok else LOG.warning)(f"requirements.txt: {message}")
    except Exception:
        LOG.exception("Update check failed; starting the current version")
    return changed


# ─── Rollback ─────────────────────────────────────────────────────────
# After an update, _CHECK says "from A to B, not confirmed yet". The app
# calls start_attempt() before it starts and confirm_start() once it serves
# pages; B then becomes the last good version (_GOOD). A start that raises
# (start_failed) or a second start without a confirmation means B does not
# work here: roll_back() returns to the last good version and _BAD keeps the
# app from installing B again.

_CHECK = ".update-check.json"
_GOOD = ".last-good"
_BAD = ".bad-update.json"


def _read_json(root: str, name: str) -> dict | None:
    try:
        with open(os.path.join(root, name), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(root: str, name: str, data: dict):
    try:
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        LOG.warning(f"Could not write {name}")


def _remove(root: str, name: str):
    try:
        os.remove(os.path.join(root, name))
    except OSError:
        pass


def _full_sha(root: str, ref: str) -> str:
    try:
        r = _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def update_pending(root: str = HERE) -> bool:
    """Was this version installed by an update that has not started yet?"""
    return _read_json(root, _CHECK) is not None


def start_attempt(root: str = HERE) -> dict | None:
    """Call before starting. Returns the pending update when the new version
    already failed to start (so the caller rolls back), else None."""
    chk = _read_json(root, _CHECK)
    if not chk:
        return None
    if chk.get("failed") or chk.get("starts", 0) >= 1:
        return chk
    chk["starts"] = chk.get("starts", 0) + 1
    _write_json(root, _CHECK, chk)
    return None


def start_failed(root: str = HERE):
    """The new version raised while starting."""
    chk = _read_json(root, _CHECK)
    if chk:
        chk["failed"] = True
        _write_json(root, _CHECK, chk)


def start_inconclusive(root: str = HERE):
    """The start stopped for a reason that is not the code (the port is taken):
    do not count it against the new version."""
    chk = _read_json(root, _CHECK)
    if chk:
        chk["starts"] = 0
        _write_json(root, _CHECK, chk)


def confirm_start(root: str = HERE):
    """The app is up and serving: this version works here."""
    sha = _full_sha(root, "HEAD")
    if sha:
        try:
            with open(os.path.join(root, _GOOD), "w", encoding="utf-8") as f:
                f.write(sha)
        except OSError:
            pass
    _remove(root, _CHECK)


def roll_back(root: str = HERE, chk: dict | None = None) -> bool:
    """Return to the last version that started here. Never over files changed
    on this PC. True when the code changed (the caller restarts)."""
    chk = chk or _read_json(root, _CHECK)
    if not chk:
        return False
    head = _full_sha(root, "HEAD")
    try:
        with open(os.path.join(root, _GOOD), encoding="utf-8") as f:
            target = f.read().strip() or chk.get("from", "")
    except OSError:
        target = chk.get("from", "")
    if not target or head != chk.get("to") or target == head or not _full_sha(root, target):
        LOG.warning("Not rolling back: this folder is not where the update left it")
        _remove(root, _CHECK)
        return False
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        LOG.warning("Not rolling back: files in the app folder were changed on this PC")
        return False
    reset = _git(root, "reset", "--hard", target)
    if reset.returncode != 0:
        LOG.error("Rollback failed: " + (reset.stderr.strip().splitlines() or ["?"])[-1])
        return False
    _write_json(root, _BAD, {"sha": head, "back_to": target, "at": datetime.now().isoformat(timespec="seconds")})
    _remove(root, _CHECK)
    info = {"at": datetime.now().isoformat(timespec="seconds"), "rolled_back": True,
            "from": _commit(root, head), "to": _commit(root, target), "changes": []}
    _write_json(root, _LAST, info)
    LOG.warning(f"Version {head[:7]} did not start; went back to {target[:7]}")
    return True


def main(argv=None) -> int:
    """python updater.py [check|apply]"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    action = (argv or sys.argv[1:] or ["check"])[0]
    st = apply() if action == "apply" else status()
    cur, new = st.get("current") or {}, st.get("latest") or {}
    print(f"This PC: {cur.get('sha', '?')} {cur.get('date', '')} {cur.get('subject', '')}")
    print(f"GitHub:  {new.get('sha', '?')} {new.get('date', '')} {new.get('subject', '')}")
    if st.get("updated"):
        print(f"Updated ({st['behind']} change(s)). Database backup: {st.get('backup') or 'none'}")
        if st.get("requirements_changed"):
            print("requirements.txt changed:", install_requirements()[1])
    elif st["reason"]:
        print(st["reason"])
    else:
        print(f"{st['behind']} update(s) available." if st["behind"] else "Up to date.")
    for c in st.get("changes", [])[:15]:
        print(f"  {c['sha']} {c['date']} {c['subject']}")
    return 0 if st["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
