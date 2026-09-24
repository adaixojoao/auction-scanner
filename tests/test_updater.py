import os
import sqlite3
import subprocess

import pytest

import updater


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def write(folder, name, text):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def repos(tmp_path):
    """GitHub (a bare repo), a checkout that publishes changes, and the PC's copy."""
    origin, work, pc = (str(tmp_path / n) for n in ("origin.git", "work", "pc"))
    git(str(tmp_path), "init", "--bare", origin)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/master")
    git(str(tmp_path), "clone", origin, work)
    git(work, "checkout", "-b", "master")
    write(work, "app.py", "v1\n")
    write(work, "requirements.txt", "flask\n")
    write(work, ".gitignore", "auctions.db\nconfig.json\nbackups/\n.last-update.json\n.installed-requirements\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "First version")
    git(work, "push", "-u", "origin", "master")
    git(str(tmp_path), "clone", origin, pc)
    return work, pc


def publish(work, name, text, message):
    write(work, name, text)
    git(work, "commit", "-am", message)
    git(work, "push", "origin", "master")


def test_the_pc_follows_github(repos):
    work, pc = repos
    with sqlite3.connect(os.path.join(pc, "auctions.db")) as db:        # the user's data
        db.execute("CREATE TABLE listings (id TEXT)")
    publish(work, "app.py", "v2\n", "Better scoring")
    publish(work, "app.py", "v3\n", "Letters for Italy")

    st = updater.status(pc)
    assert st["ok"] and st["behind"] == 2
    assert [c["subject"] for c in st["changes"]] == ["Letters for Italy", "Better scoring"]

    done = updater.apply(pc)
    assert done["updated"] and not done["requirements_changed"]
    assert open(os.path.join(pc, "app.py"), encoding="utf-8").read() == "v3\n"
    assert done["backup"] and os.path.exists(done["backup"])             # database copied first
    assert updater.last_update(pc)["changes"][0]["subject"] == "Letters for Italy"
    assert updater.status(pc)["behind"] == 0 and not updater.apply(pc)["updated"]


def test_new_packages_are_installed(repos, monkeypatch):
    work, pc = repos
    publish(work, "requirements.txt", "flask\ntzdata\n", "Add tzdata")
    assert updater.apply(pc)["requirements_changed"]
    assert updater.requirements_outdated(pc)
    ran = []
    real_run = subprocess.run
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda args, **kw: ran.append(args) or real_run(["git", "--version"], capture_output=True))
    assert updater.install_requirements(pc)[0]
    assert ran[0][1:4] == ["-m", "pip", "install"] and not updater.requirements_outdated(pc)


def test_changes_made_on_the_pc_are_never_overwritten(repos):
    work, pc = repos
    publish(work, "app.py", "v2\n", "Update")
    write(pc, "app.py", "my own edit\n")
    write(pc, "config.json", "{}")                  # untracked data does not block
    st = updater.apply(pc)
    assert not st["ok"] and not st["updated"] and "app.py" in st["reason"]
    assert open(os.path.join(pc, "app.py"), encoding="utf-8").read() == "my own edit\n"


def test_only_master_is_followed(repos):
    _, pc = repos
    git(pc, "checkout", "-b", "experiment")
    assert "not 'master'" in updater.status(pc)["reason"]


def test_local_commits_block_a_fast_forward(repos):
    work, pc = repos
    publish(work, "app.py", "v2\n", "Update")
    write(pc, "notes.txt", "x")
    git(pc, "add", "notes.txt")
    git(pc, "commit", "-m", "Local")
    st = updater.status(pc)
    assert not st["ok"] and "own commits" in st["reason"]


def test_offline_or_not_installed_with_git(repos, tmp_path):
    _, pc = repos
    git(pc, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    assert "Could not reach GitHub" in updater.status(pc)["reason"]
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not installed with git" in updater.status(str(plain))["reason"]


def test_update_on_start_respects_the_setting_and_running_scans(repos, monkeypatch):
    work, pc = repos
    monkeypatch.setattr(updater, "install_requirements", lambda root=None: (True, "ok"))
    publish(work, "app.py", "v2\n", "Update")
    write(pc, "config.json", '{"updates": {"auto": false}}')
    assert updater.update_on_start(pc) is False
    write(pc, "config.json", "{}")
    write(pc, "scan.lock", f"{os.getpid()} 2026-09-24T14:14:06")    # a scan that is running
    assert updater.update_on_start(pc) is False
    write(pc, "scan.lock", "999999999 2026-09-24T14:14:06")          # left by a stopped scan
    assert updater.update_on_start(pc) is True
    assert open(os.path.join(pc, "app.py"), encoding="utf-8").read() == "v2\n"


# ─── Rollback: a version that does not start on this PC ─────────────────

def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def test_a_version_that_starts_becomes_the_last_good_one(repos):
    work, pc = repos
    publish(work, "app.py", "v2\n", "Update")
    assert updater.apply(pc)["updated"] and updater.update_pending(pc)
    assert updater.start_attempt(pc) is None           # first start of the new version: go
    updater.confirm_start(pc)
    assert not updater.update_pending(pc)
    assert open(os.path.join(pc, ".last-good"), encoding="utf-8").read() == head(pc)


def test_a_version_that_does_not_start_is_rolled_back_and_skipped(repos):
    work, pc = repos
    good = head(pc)
    publish(work, "app.py", "broken\n", "Broken version")
    updater.apply(pc)
    assert updater.start_attempt(pc) is None           # it starts... and dies without confirming
    chk = updater.start_attempt(pc)                    # the next start sees that
    assert chk and updater.roll_back(pc, chk)
    assert head(pc) == good and open(os.path.join(pc, "app.py"), encoding="utf-8").read() == "v1\n"
    assert updater.last_update(pc)["rolled_back"] is True

    st = updater.status(pc)                            # the broken version is not installed again
    assert not st["ok"] and "did not start on this PC" in st["reason"]
    assert not updater.apply(pc)["updated"]

    publish(work, "app.py", "v3\n", "Fixed")           # a newer version is tried
    assert updater.apply(pc)["updated"] and not os.path.exists(os.path.join(pc, ".bad-update.json"))


def test_a_crash_while_starting_rolls_back_at_once(repos):
    work, pc = repos
    publish(work, "app.py", "v2\n", "Good")
    updater.apply(pc)
    updater.start_attempt(pc)
    updater.confirm_start(pc)
    v2 = head(pc)
    publish(work, "app.py", "v3\n", "Crashes")
    updater.apply(pc)
    updater.start_attempt(pc)
    updater.start_failed(pc)                           # the app raised while starting
    assert updater.roll_back(pc) and head(pc) == v2   # back to the last good version, not further


def test_a_taken_port_does_not_count_against_a_version(repos):
    work, pc = repos
    publish(work, "app.py", "v2\n", "Update")
    updater.apply(pc)
    updater.start_attempt(pc)
    updater.start_inconclusive(pc)                     # port 8050 was taken by another program
    assert updater.start_attempt(pc) is None


def test_no_rollback_over_changes_made_on_the_pc(repos):
    work, pc = repos
    publish(work, "app.py", "v2\n", "Update")
    updater.apply(pc)
    updater.start_attempt(pc)
    write(pc, "app.py", "edited by hand\n")
    assert updater.roll_back(pc, updater.start_attempt(pc)) is False
    assert open(os.path.join(pc, "app.py"), encoding="utf-8").read() == "edited by hand\n"
