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
import time
from datetime import timedelta

from common import LOG, configure_http, make_session, parse_dt, utcnow, utcnow_iso
from locks import lock_holder

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(HERE, "scan.lock")
REPORTS_DIR = os.path.join(HERE, "reports")
# A scan that has not finished after this long crashed; its lock and state are stale.
STALE_SCAN = timedelta(hours=3)


class ScanBusy(Exception):
    """Another scan (app, scheduler or command line) is already running."""


@contextlib.contextmanager
def scan_lock():
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {utcnow_iso()}\n")
            break
        except FileExistsError:
            fresh = time.time() - os.path.getmtime(LOCK_PATH) < STALE_SCAN.total_seconds()
            if fresh and lock_holder(LOCK_PATH):
                raise ScanBusy("another scan is running")
            LOG.warning("Removing stale scan.lock (its process is gone)")
            with contextlib.suppress(FileNotFoundError):
                os.remove(LOCK_PATH)
    else:
        raise ScanBusy("could not take scan.lock")
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(LOCK_PATH)


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


def scan_status(db) -> dict:
    row = db.execute("SELECT * FROM scan_state WHERE id = 1").fetchone()
    state = dict(row) if row else {"running": 0}
    started = parse_dt(state.get("started_at"))
    if state.get("running") and (not lock_holder(LOCK_PATH)
                                 or (started and utcnow() - started > STALE_SCAN)):
        state["running"] = 0  # the process died mid-scan (app killed, PC shut down)
    state["running"] = bool(state.get("running"))
    state["stop"] = bool(state.get("stop"))
    state["summary"] = json.loads(state["summary"]) if state.get("summary") else None
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
    order, seen = [], set()
    for row in zip(*[r + [None] * (max(map(len, ranked)) - len(r)) for r in ranked]):
        for it in row:
            if it and it["id"] not in seen:
                seen.add(it["id"])
                order.append(it)
    return order


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


def rescore(db, cfg: dict) -> None:
    """Score again what the scan changed (new towns, auction results, land prices),
    for every goal, so the next page view finds the scores ready."""
    try:
        from db import load_listings
        from scoring import MODES
        for mode in MODES:
            load_listings(db, filters=cfg.get("filters"), include_hidden=True, mode=mode, refresh=True)
    except Exception:  # noqa: BLE001 — only a speed-up
        LOG.exception("Rescoring after the scan failed")


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
        with scan_lock():
            _set_state(db, running=1, stop=0, label=label, total=len(chosen), done=0, current=None,
                       started_at=utcnow_iso(), finished_at=None, summary=None)
            results = []
            stopped = False
            report_path = None
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
                        import climate                    # heat by 2090, water, fire, flood: local files
                        climate.assess_pending(db, best, geo.town_index(db))
                    except Exception:  # noqa: BLE001 — a map position must never fail the scan
                        LOG.exception("Locating listings failed")
                    try:
                        import site_check                 # slope, forest type, Natura: land and forestry
                        plots = _plots_for_site_check(db, cfg)
                        site_check.check_pending(db, plots)
                    except Exception:  # noqa: BLE001
                        LOG.exception("Site check failed")
                    _set_state(db, current="photo check")
                    try:
                        import photos                    # the photos of the best homes (needs an API key)
                        from db import load_listings
                        best = sorted(load_listings(db, filters=cfg.get("filters")),
                                      key=lambda it: -it.get("rank", it["score"]))
                        photos.check_pending(db, cfg, best)
                    except Exception:  # noqa: BLE001 — a photo check must never fail the scan
                        LOG.exception("Photo check failed")
            finally:
                finished_at = utcnow_iso()
                if not stopped:
                    _set_state(db, current="refreshing the lists")
                    rescore(db, cfg)
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
