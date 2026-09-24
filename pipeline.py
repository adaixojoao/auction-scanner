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

from common import LOG, configure_http, lock_holder, parse_dt, utcnow, utcnow_iso

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


def scan_status(db) -> dict:
    row = db.execute("SELECT * FROM scan_state WHERE id = 1").fetchone()
    state = dict(row) if row else {"running": 0}
    started = parse_dt(state.get("started_at"))
    if state.get("running") and (not lock_holder(LOCK_PATH)
                                 or (started and utcnow() - started > STALE_SCAN)):
        state["running"] = 0  # the process died mid-scan (app killed, PC shut down)
    state["running"] = bool(state.get("running"))
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
    from telegram_alert import alert_new_listings
    alert_new_listings(db, cfg)


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
    max_price = max_price or cfg.get("max_price", 50000)
    from common import COUNTRY_NAMES
    label = label or (", ".join(source_names) if source_names
                      else "all countries" if not countries
                      else ", ".join(COUNTRY_NAMES.get(c, c) for c in countries))

    own_db = db is None
    db = db or connect()
    try:
        with scan_lock():
            _set_state(db, running=1, label=label, total=len(chosen), done=0, current=None,
                       started_at=utcnow_iso(), finished_at=None, summary=None)
            results = []
            try:
                for i, source in enumerate(chosen):
                    _set_state(db, current=source.name, done=i)
                    results.append(run_source(db, source, max_price=max_price, config=cfg))
                _set_state(db, current="de-duplicating", done=len(chosen))
                mark_duplicates(db)
                report_path = None
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
                summary = {
                    "listings": sum(r["count"] for r in results),
                    "ok": sum(1 for r in results if r["status"] == "ok"),
                    "empty": sum(1 for r in results if r["status"] == "empty"),
                    "errors": sum(1 for r in results if r["status"] == "error"),
                    "sources": len(results),
                }
                _set_state(db, running=0, current=None, finished_at=utcnow_iso(),
                           summary=json.dumps(summary))
            LOG.info(f"Scan of {label} finished: {summary}")
            return {**summary, "results": results, "report": report_path}
    finally:
        if own_db:
            db.close()
