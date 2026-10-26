"""
pipeline.py — the one scan routine.

"Scan now" in the app, the background scheduler and `python scraper.py` all run
run_scan(): scrape the chosen sources, flag duplicates, rebuild the report and
send alerts. Progress goes to the scan_state table, so the app shows a scan's
progress even when the scheduler started it in another process, and a lock
file stops two scans running at once.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import timedelta

import locks
from common import LOG, configure_http, make_session, utcnow_iso
from locks import lock_holder

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(HERE, "scan.lock")
REPORTS_DIR = os.path.join(HERE, "reports")
# A scan lock in the old format (no OS lock) older than this is stale.
STALE_SCAN = timedelta(hours=3)


class ScanBusy(Exception):
    """Another scan (app, scheduler or command line) is already running."""


# Below the programs in front (Cursor, a browser). Windows only; the call
# is paired so the thread is back to normal when the scan ends.
_BACKGROUND_BEGIN = 0x00010000
_BACKGROUND_END = 0x00020000


@contextlib.contextmanager
def _below_other_programs():
    """While a scan runs, Windows puts the programs the owner is using first."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    began = bool(kernel.SetThreadPriority(kernel.GetCurrentThread(), _BACKGROUND_BEGIN))
    try:
        yield
    finally:
        if began:
            kernel.SetThreadPriority(kernel.GetCurrentThread(), _BACKGROUND_END)


@contextlib.contextmanager
def scan_lock():
    """Hold scan.lock for the whole scan (locks.py: an OS lock, so a reused pid
    never looks like a live scan, and a scan never removes another's lock)."""
    try:
        lock = locks.acquire(LOCK_PATH, legacy_max_age=STALE_SCAN.total_seconds())
    except locks.LockBusy as e:
        raise ScanBusy("another scan is running") from e
    if lock.took_over:
        LOG.warning(f"Took over a stale scan.lock from an older version ({lock.took_over})")
    try:
        yield
    finally:
        locks.release(lock)


def _set_state(db, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE scan_state SET {sets} WHERE id = 1", tuple(fields.values()))
    db.commit()


def request_stop(db) -> None:
    """Ask the running scan to finish the source it is on and then stop."""
    _set_state(db, stop=1)


def _stop_requested(db) -> bool:
    db.commit()  # end this connection's read, so a stop from the app is visible
    row = db.execute("SELECT stop FROM scan_state WHERE id = 1").fetchone()
    return bool(row and row["stop"])


def _postpone_country_jobs(db, countries) -> None:
    """A stopped country scan counts as this interval's run, so the timetable
    does not start the same country again until the next week or fortnight."""
    from db import set_job_last_run
    if countries is None or "PT" in countries:
        set_job_last_run(db, "pt")
    if countries is None or any(c != "PT" for c in countries):
        set_job_last_run(db, "eu")


def _close_dead_scan(db, state) -> None:
    """The process that held the scan was killed. Write that down, so the bar
    and the list refresh see a finished scan instead of one still on a source."""
    finished = utcnow_iso()
    summary = {
        "listings": 0,
        "ok": 0,
        "empty": 0,
        "errors": 0,
        "sources": state.get("done") or 0,
        "stopped": True,
    }
    _set_state(db, running=0, stop=0, current=None, finished_at=finished,
               summary=json.dumps(summary))
    state.update(running=0, stop=0, current=None, finished_at=finished, summary=summary)


def scan_status(db) -> dict:
    row = db.execute("SELECT * FROM scan_state WHERE id = 1").fetchone()
    state = dict(row) if row else {"running": 0}
    # The OS lock says whether the scan's process is alive; a long scan that
    # is still running is shown as running, however long it takes.
    holder = lock_holder(LOCK_PATH, legacy_max_age=STALE_SCAN.total_seconds())
    if state.get("running") and not holder:
        _close_dead_scan(db, state)
    state["running"] = bool(state.get("running"))
    state["stop"] = bool(state.get("stop"))
    if isinstance(state.get("summary"), str):
        state["summary"] = json.loads(state["summary"]) if state["summary"] else None
    state["last_scrape"] = db.execute("SELECT MAX(timestamp) FROM scrape_log").fetchone()[0]
    return state


def reports_dir(cfg: dict) -> str:
    path = (cfg.get("report") or {}).get("out_dir") or REPORTS_DIR
    os.makedirs(path, exist_ok=True)
    return path


def build_report(db, cfg: dict) -> str:
    from report import generate_report
    from sources import load_all
    max_price = cfg.get("max_price", 50000)
    return generate_report(
        db, max_price=max_price, max_bid=cfg.get("max_bid", max_price),
        filters=cfg.get("filters", {}), out_dir=reports_dir(cfg),
        desktop_copy=(cfg.get("report") or {}).get("desktop_copy", True),
        known_sources=load_all())


def send_alerts(db, cfg: dict):
    """E-mail (if enabled) and Telegram (if enabled) for listings not yet alerted."""
    notify_cfg = cfg.get("notifications", {})
    if notify_cfg.get("enabled"):
        from notifications import send_alerts as send_email_alerts
        send_email_alerts(db, notify_cfg, max_price=cfg.get("max_price", 50000),
                          filters=cfg.get("filters"))
    from telegram_alert import alert_last_calls, alert_new_listings, alert_price_cuts, alert_shortlist_reminders
    alert_new_listings(db, cfg)
    alert_price_cuts(db, cfg)
    alert_shortlist_reminders(db, cfg)
    alert_last_calls(db, cfg)


def enrich_order(db, cfg: dict) -> list[dict]:
    """The listings to locate and assess first: the best of each tab in turn
    (scoring.MODES), so land is not left until last behind the homes."""
    from db import load_listings
    from scoring import MODES
    ranked = [sorted(load_listings(db, filters=cfg.get("filters"), mode=m),
                     key=lambda it: -it.get("rank", it["score"])) for m in MODES]
    order, seen, best = [], set(), {}
    for row in zip(*[r + [None] * (max(map(len, ranked)) - len(r)) for r in ranked]):
        for it in row:
            if not it:
                continue
            best[it["id"]] = max(best.get(it["id"], 0), it.get("rank") or it.get("score") or 0)
            if it["id"] not in seen:
                seen.add(it["id"])
                order.append(it)
    # 85 or more in any goal is looked up first: its photos, climate and place
    # are what the detail panel is asked to show in full.
    from photos import KEEP_SCORE
    high = [it for it in order if best.get(it["id"], 0) >= KEEP_SCORE]
    rest = [it for it in order if best.get(it["id"], 0) < KEEP_SCORE]
    # Research candidates (an extreme-discount lead hidden only by its score)
    # are what investigation needs located and assessed: the same selection
    # the Listings research panel shows (db.research_selection).
    research = [it for it in _research_rows(db, cfg) if it["id"] not in {h["id"] for h in high}]
    research_ids = {it["id"] for it in research}
    rest = [it for it in rest if it["id"] not in research_ids]
    for it in high + research + rest:
        it["best_rank"] = best.get(it["id"], it.get("rank") or it.get("score") or 0)
    return high + research + rest


def _research_rows(db, cfg: dict) -> list[dict]:
    """The research-selected Live and invest rows, read in full."""
    from db import load_listings, research_selection
    slim = load_listings(db, filters=cfg.get("filters"), mode="both", include_hidden=True,
                         slim_hidden=True)
    valued, unvalued = research_selection(slim)
    ids = [it["id"] for it in valued + unvalued]
    if not ids:
        return []
    full = {it["id"]: it for it in load_listings(
        db, filters=cfg.get("filters"), mode="both", include_hidden=True,
        where=f"id IN ({','.join('?' * len(ids))})", params=tuple(ids))}
    return [full[i] for i in ids if i in full]


# Live and invest's homes by the sea that its gates leave out only because the
# pin is the town, not the house (2026-10-26: 68 of 222 visible seaside homes).
# They are looked up every scan, beyond geo.GEOCODE_PER_SCAN, up to this many.
SEASIDE_LOCATE_MAX = 400
_TOWN_PIN = "position is the town, not the house"


def seaside_unplaced(db, cfg: dict, limit: int = SEASIDE_LOCATE_MAX) -> list[dict]:
    """Shown homes that Live and invest would judge once their pin is the house."""
    from db import load_listings
    rows = load_listings(db, filters=cfg.get("filters"), include_hidden=True, apply_min_score=False,
                         mode="both", slim_hidden=True)
    out = [it for it in rows if not it.get("hidden_reason") and it.get("kind") == "home"
           and (it.get("reasons") or [""])[0].startswith(_TOWN_PIN)]
    return out[:limit]


def locate_seaside(db, cfg: dict, session) -> int:
    """Look up the house of each seaside home held back by a town pin, then read
    the climate of those that now have one. Returns how many were tried."""
    import cadastre
    import climate
    import geo
    seaside = seaside_unplaced(db, cfg)
    if not seaside:
        return 0
    towns = geo.town_index(db)
    geo.geocode_pending(db, session, seaside, limit=len(seaside), towns=towns)
    cadastre.locate_pending(db, session, seaside, towns, limit=len(seaside))
    climate.assess_pending(db, seaside, geo.town_index(db), seaside_first=True)
    LOG.info(f"Seaside homes held back by a town pin: {len(seaside)} looked up")
    return len(seaside)


def _plots_for_site_check(db, cfg: dict) -> list[dict]:
    """The top forestry plots, then the top other plots. A habitat check of
    every plot is what made a scan slow; the ones you might buy are enough."""
    from db import load_listings
    forest = sorted(load_listings(db, filters=cfg.get("filters"), mode="forest"),
                    key=lambda it: -it.get("rank", it["score"]))[:20]
    land = sorted(load_listings(db, filters=cfg.get("filters"), mode="land"),
                  key=lambda it: -it.get("rank", it["score"]))
    seen = {it["id"] for it in forest}
    rest = [it for it in land if it["id"] not in seen][:20]
    return forest + rest


def rescore(db, cfg: dict) -> bool:
    """Refresh every goal; False means normal publication must be suppressed."""
    try:
        from db import load_listings
        from scoring import MODES
        for mode in MODES:
            load_listings(db, filters=cfg.get("filters"), include_hidden=True,
                          mode=mode, refresh=True)
    except Exception:
        LOG.exception("Rescoring after the scan failed; normal publication suppressed")
        return False
    return True


def run_scan(countries=None, source_names=None, *, cfg: dict | None = None,
             max_price: float | None = None, label: str | None = None,
             report: bool = True, alerts: bool = True, db=None) -> dict:
    """Scrape, de-duplicate, report, alert. Raises ScanBusy if a scan is running.

    countries:    list of codes (None = every country) — ignored if source_names is given
    source_names: explicit sources, e.g. ["citius"]
    """
    from config import load_config
    from db import connect, mark_duplicates
    from sources import REGISTRY, load_all, run_source, sources_for

    cfg = cfg or load_config()
    configure_http(cfg.get("proxies"))
    load_all()
    chosen = [REGISTRY[n] for n in source_names] if source_names else sources_for(countries)
    # Keep anything that could serve any goal; each tab caps its own budget.
    from common import scrape_max_price
    max_price = scrape_max_price(cfg, max_price or cfg.get("max_price", 50000))
    from common import COUNTRY_NAMES
    label = label or (", ".join(source_names) if source_names
                      else "all countries" if not countries
                      else ", ".join(COUNTRY_NAMES.get(c, c) for c in countries))

    own_db = db is None
    db = db or connect()
    try:
        with scan_lock(), _below_other_programs():
            _set_state(db, running=1, stop=0, label=label, total=len(chosen), done=0, current=None,
                       started_at=utcnow_iso(), finished_at=None, summary=None)
            results = []
            stopped = False
            report_path = None
            publication_rescored = False
            try:
                for i, source in enumerate(chosen):
                    if _stop_requested(db):
                        stopped = True
                        break
                    _set_state(db, current=source.name, done=i)
                    results.append(run_source(db, source, max_price=max_price, config=cfg))
                if not stopped and _stop_requested(db):
                    stopped = True
                if not stopped:
                    _set_state(db, current="de-duplicating", done=len(chosen))
                    try:
                        from links import link_court_sales
                        link_court_sales(db)
                    except Exception:  # noqa: BLE001 — a join must never fail the scan
                        LOG.exception("Joining Citius to e-leilões failed")
                    mark_duplicates(db)
                    try:
                        import outcomes
                        outcomes.record_results(db)      # ended sales and their last bid
                    except Exception:  # noqa: BLE001
                        LOG.exception("Recording auction results failed")
                    _set_state(db, current="map positions")
                    try:
                        import geo
                        best = enrich_order(db, cfg)
                        session = make_session()
                        geo.locate_towns(db, session, best)
                        geo.geocode_pending(db, session, best, towns=geo.town_index(db))
                        import cadastre
                        cadastre.locate_pending(db, session, best, geo.town_index(db))
                        geo.check_water_pending(db, session, best)
                        import outline                    # the house and its plot on the map
                        outline.check_pending(db, session, best)
                        import climate                   # heat by 2090, water, fire, flood: local files
                        climate.assess_pending(db, best, geo.town_index(db), seaside_first=True)
                    except Exception:  # noqa: BLE001 — a map position must never fail the scan
                        LOG.exception("Locating listings failed")
                    try:
                        locate_seaside(db, cfg, make_session())
                    except Exception:  # noqa: BLE001
                        LOG.exception("Locating seaside homes failed")
                    try:
                        import site_check                 # slope, forest type, Natura: land and forestry
                        plots = _plots_for_site_check(db, cfg)
                        site_check.check_pending(db, plots)
                    except Exception:  # noqa: BLE001
                        LOG.exception("Site check failed")
                    _set_state(db, current="photo check")
                    try:
                        import photos                    # the photos of the best homes (needs a looker)
                        photos.check_pending(db, cfg, photos.queue(db, cfg))
                    except Exception:  # noqa: BLE001 — a photo check must never fail the scan
                        LOG.exception("Photo check failed")
                    _set_state(db, current="checking links")
                    try:
                        import confirm                   # sold and reserved pages leave the top
                        confirm.check_top(db, cfg, make_session())
                    except Exception:  # noqa: BLE001 — a dead link must never fail the scan
                        LOG.exception("Link check failed")
                    try:
                        import confirm                   # off a source's list: is the page still up?
                        confirm.check_stale(db, cfg, make_session())
                    except Exception:  # noqa: BLE001
                        LOG.exception("Stale check failed")
                    if not rescore(db, cfg):
                        raise RuntimeError("Post-enrichment rescoring failed; reports and alerts not published")
                    publication_rescored = True
                    if report:
                        _set_state(db, current="report")
                        report_path = build_report(db, cfg)
                    if alerts:
                        _set_state(db, current="alerts")
                        send_alerts(db, cfg)
                        try:
                            from telegram_alert import alert_source_failures
                            alert_source_failures(db, cfg, [r["source"] for r in results])
                        except Exception:  # noqa: BLE001 — an alarm must never fail the scan
                            LOG.exception("Source alarm failed")
                        try:
                            from outbox import queue_requests
                            queue_requests(db, cfg)          # offered on Telegram; sent only on your tap
                        except Exception:  # noqa: BLE001
                            LOG.exception("Preparing information requests failed")
            finally:
                finished_at = utcnow_iso()
                if not stopped:
                    _set_state(db, current="refreshing the lists")
                    if not publication_rescored:
                        rescore(db, cfg)
                    try:
                        import photos
                        photos.keep_high(db, cfg)
                    except Exception:  # noqa: BLE001 — saving photos must never fail the scan
                        LOG.exception("Keeping photos failed")
                try:
                    import dashboard
                    dashboard.refresh_cached_lists(finished_at)
                except Exception:  # noqa: BLE001 — the open page keeps the list it has
                    LOG.exception("Refreshing the lists failed")
                if stopped and not source_names:
                    _postpone_country_jobs(db, countries)
                summary = {
                    "listings": sum(r["count"] for r in results),
                    "ok": sum(1 for r in results if r["status"] == "ok"),
                    "empty": sum(1 for r in results if r["status"] == "empty"),
                    "errors": sum(1 for r in results if r["status"] == "error"),
                    "sources": len(results),
                    "stopped": stopped,
                }
                _set_state(db, running=0, stop=0, current=None, finished_at=finished_at,
                           summary=json.dumps(summary))
            LOG.info(f"Scan of {label} {'stopped' if stopped else 'finished'}: {summary}")
            return {**summary, "results": results, "report": report_path}
    finally:
        if own_db:
            db.close()


# ─── A scan in its own process ───────────────────────────────────────
# The app used to scan on one of its threads. A scan reads every goal's
# ~135,000 listings, and that memory stayed with the app: on 2026-10-26 the
# open app held 7 GB on a 5.9 GB PC and stalled everything else. A scan from
# the app now runs in a child process, whose memory is returned when it ends;
# the app then reloads its open lists.
_BUSY_EXIT = 75


def run_scan_apart(countries=None, source_names=None, label: str | None = None) -> int:
    """Run run_scan() in a child process and wait for it. Raises ScanBusy when
    another scan holds the lock; returns the child's exit code otherwise."""
    import subprocess
    args = json.dumps({"countries": countries, "source_names": source_names, "label": label})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    code = subprocess.run([sys.executable, os.path.join(HERE, "pipeline.py"), "--scan", args],
                          cwd=HERE, creationflags=flags).returncode
    if code == _BUSY_EXIT:
        raise ScanBusy("another scan is running")
    try:
        import dashboard
        dashboard.refresh_cached_lists(utcnow_iso())   # the app's open lists, from the new scores
    except Exception:  # noqa: BLE001 — the open page keeps the list it has
        LOG.exception("Refreshing the lists failed")
    return code


def _scan_main(argv: list[str]) -> int:
    import logging
    import logging.handlers
    handler = logging.handlers.RotatingFileHandler(os.path.join(HERE, "scan.log"), maxBytes=2_000_000,
                                                   backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    args = json.loads(argv[argv.index("--scan") + 1])
    try:
        run_scan(**args)
    except ScanBusy:
        return _BUSY_EXIT
    return 0


if __name__ == "__main__" and "--scan" in sys.argv:
    sys.exit(_scan_main(sys.argv))
