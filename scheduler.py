"""
scheduler.py — runs the scanner on a timetable.

Usage:
  python scheduler.py install    # Windows Task Scheduler: run `tick` every 30 minutes
  python scheduler.py remove     # Remove the task
  python scheduler.py status     # Show the task
  python scheduler.py tick       # Run whatever is due now, then exit
  python scheduler.py run        # Stay open and tick every 5 minutes (no Task Scheduler)
  python scheduler.py due        # Show what is due, run nothing
  python scheduler.py pt         # PT-only scrape now
  python scheduler.py eu         # Other-countries scrape now
  python scheduler.py morning    # Check deadlines + carta status now
  python scheduler.py report     # Send weekly report now

Each job's last run is kept in the database (job_runs), so a PC that slept
through a slot catches up on the next tick instead of waiting for the next
slot, and two ticks never run at once (scheduler.lock).

Timetable (config.json → "schedule"; these are the defaults):
  pt_every_hours  2        PT sources
  eu_every_hours  6        every other country
  check_times     ["08:00", "20:00"]   deadline alerts
  weekly_report   "mon 08:00"
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_NAME = "AuctionScanner"
LOCK_PATH = os.path.join(SCRIPT_DIR, "scheduler.lock")
LOG_PATH = os.path.join(SCRIPT_DIR, "scheduler.log")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "scheduler-output.log")  # print() under pythonw
LOCK_STALE_AFTER = timedelta(hours=3)
# A tick that lands a few minutes early still counts; ticks are 30 min apart.
SLACK = timedelta(minutes=10)

LOG = logging.getLogger("scheduler")

DEFAULT_SCHEDULE = {
    "pt_every_hours": 2,
    "eu_every_hours": 6,
    "check_times": ["08:00", "20:00"],
    "weekly_report": "mon 08:00",
}
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
JOBS = ("pt", "eu", "morning", "report")


def setup_logging():
    if sys.stdout is None:  # pythonw.exe: no console
        sys.stdout = sys.stderr = open(OUTPUT_PATH, "a", encoding="utf-8", buffering=1)
    logging.getLogger("urllib3").setLevel(logging.ERROR)   # one line per retry is noise
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if getattr(sys.stdout, "name", "") != OUTPUT_PATH:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)


# ─── What is due ─────────────────────────────────────────────────────

def _hhmm(text: str) -> tuple[int, int]:
    h, m = text.strip().split(":")
    return int(h), int(m)


def due_jobs(now: datetime, last_runs: dict, schedule: dict | None = None) -> list[str]:
    """Jobs that should run at `now` (an aware local datetime) given their last runs."""
    sched = {**DEFAULT_SCHEDULE, **(schedule or {})}
    due = []

    for job, key in (("pt", "pt_every_hours"), ("eu", "eu_every_hours")):
        every = sched.get(key)
        if not every:
            continue
        last = last_runs.get(job)
        if last is None or now - last >= timedelta(hours=float(every)) - SLACK:
            due.append(job)

    last = last_runs.get("morning")
    for t in sched.get("check_times") or []:
        h, m = _hhmm(t)
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= slot and (last is None or last < slot):
            due.append("morning")
            break

    weekly = (sched.get("weekly_report") or "").strip().lower()
    if weekly:
        day, hhmm = weekly.split()
        h, m = _hhmm(hhmm)
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        slot -= timedelta(days=(now.weekday() - _WEEKDAYS.index(day[:3])) % 7)
        if slot > now:
            slot -= timedelta(days=7)
        last = last_runs.get("report")
        if last is None or last < slot:
            due.append("report")
    return due


# ─── Jobs ────────────────────────────────────────────────────────────

def _scan(countries, label):
    from pipeline import ScanBusy, run_scan
    try:
        run_scan(countries=countries, label=label)
    except ScanBusy:
        LOG.info(f"{label}: another scan is running, skipped")


def run_pt_scrape():
    LOG.info("=== PT scan starting ===")
    _scan(["PT"], "Portugal (scheduled)")


def run_eu_scrape():
    """Every country except Portugal (PT has its own, more frequent job)."""
    LOG.info("=== EU scan starting ===")
    from common import COUNTRY_NAMES
    _scan([c for c in COUNTRY_NAMES if c != "PT"], "other countries (scheduled)")


def run_morning_checks():
    LOG.info("=== Deadline checks ===")
    from config import load_config
    from db import connect
    from telegram_alert import alert_carta_deadlines
    db = connect()
    try:
        alert_carta_deadlines(db, load_config())
    finally:
        db.close()


def weekly_stats(db, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    one = lambda sql, *p: db.execute(sql, p).fetchone()[0]  # noqa: E731
    return {
        "new": one("SELECT COUNT(*) FROM listings WHERE first_seen > ?", week_ago),
        "sent": one("SELECT COUNT(*) FROM carta_log WHERE is_offer=1 AND created_at > ?", week_ago),
        "won": one("SELECT COUNT(*) FROM carta_log WHERE is_offer=1 AND outcome='won'"),
        "pending": one("SELECT COUNT(*) FROM carta_log WHERE is_offer=1 AND outcome='pending'"),
        "exposure": one("SELECT COALESCE(SUM(bid_amount),0) FROM carta_log "
                        "WHERE is_offer=1 AND outcome='pending'"),
    }


def run_weekly_report():
    LOG.info("=== Weekly report ===")
    from config import load_config
    from db import connect, source_health
    from sources import load_all
    from telegram_alert import alert_weekly_summary
    cfg = load_config()
    db = connect()
    try:
        tg = cfg.get("telegram", {})
        if tg.get("enabled"):
            d = cfg.get("dashboard", {})
            alert_weekly_summary(tg["token"], tg["chat_id"], weekly_stats(db),
                                 health=source_health(db, load_all()),
                                 dashboard_url=f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8050)}")
    finally:
        db.close()


JOB_FUNCS = {
    "pt": run_pt_scrape,
    "eu": run_eu_scrape,
    "morning": run_morning_checks,
    "report": run_weekly_report,
}


# ─── Tick & lock ─────────────────────────────────────────────────────

def _acquire_lock() -> bool:
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
            return True
        except FileExistsError:
            from common import lock_holder
            age = time.time() - os.path.getmtime(LOCK_PATH)
            if age < LOCK_STALE_AFTER.total_seconds() and lock_holder(LOCK_PATH):
                return False
            LOG.warning("Removing stale scheduler.lock (its process is gone)")
            try:
                os.remove(LOCK_PATH)
            except FileNotFoundError:
                pass
    return False


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def _last_runs(db) -> dict:
    from db import job_last_run
    return {job: job_last_run(db, job) for job in JOBS}


def tick(dry_run: bool = False) -> list[str]:
    """Run every due job once. Returns the jobs that ran (or would run)."""
    from config import load_config
    from db import connect, set_job_last_run

    schedule = load_config().get("schedule", {})
    db = connect()
    try:
        now = datetime.now().astimezone()
        due = due_jobs(now, _last_runs(db), schedule)
    finally:
        db.close()
    if dry_run or not due:
        return due

    if not _acquire_lock():
        LOG.info("Another tick is still running; skipping.")
        return []
    try:
        for job in due:
            try:
                JOB_FUNCS[job]()
            except Exception:
                LOG.exception(f"Job {job} failed")
            # Mark it run even if it failed: retrying a crashing job every
            # 30 minutes helps nobody. The error is in scheduler.log.
            db = connect()
            try:
                set_job_last_run(db, job)
            finally:
                db.close()
    finally:
        _release_lock()
    return due


def run_loop(every_minutes: int = 5):
    LOG.info("Scheduler loop running (Ctrl+C to stop).")
    while True:
        try:
            ran = tick()
            if ran:
                LOG.info(f"Ran: {', '.join(ran)}")
        except Exception:
            LOG.exception("Tick failed")
        time.sleep(every_minutes * 60)


# ─── Windows Task Scheduler ──────────────────────────────────────────

def _pythonw() -> str:
    """pythonw.exe runs without flashing a console window every 30 minutes."""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def install_task(every_minutes: int = 30):
    script = os.path.join(SCRIPT_DIR, "scheduler.py")
    # A list, not a string: subprocess escapes the inner quotes the way
    # schtasks expects, which the old hand-built command line did not.
    cmd = ["schtasks", "/create", "/tn", TASK_NAME,
           "/tr", f'"{_pythonw()}" "{script}" tick',
           "/sc", "MINUTE", "/mo", str(every_minutes), "/f"]
    ret = subprocess.run(cmd).returncode
    if ret == 0:
        print(f"Task '{TASK_NAME}' installed: checks every {every_minutes} min and runs what is due.")
        print(f"Log: {LOG_PATH}")
    else:
        print("Failed. Try running as Administrator, or use: python scheduler.py run")


def remove_task():
    ret = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"]).returncode
    print("Task removed." if ret == 0 else "Task not found.")


def status_task():
    subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"])
    from db import connect
    db = connect()
    try:
        print("\nLast runs:")
        for job, last in _last_runs(db).items():
            print(f"  {job:<8} {last.astimezone():%Y-%m-%d %H:%M}" if last else f"  {job:<8} never")
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Auction Scanner Scheduler")
    parser.add_argument("action", nargs="?", default="tick",
                        choices=["install", "remove", "status", "tick", "run", "due", *JOBS])
    args = parser.parse_args(argv)
    setup_logging()

    if args.action == "install":
        install_task()
    elif args.action == "remove":
        remove_task()
    elif args.action == "status":
        status_task()
    elif args.action == "tick":
        ran = tick()
        LOG.info(f"Tick: ran {', '.join(ran)}" if ran else "Tick: nothing due")
    elif args.action == "due":
        print("Due now:", ", ".join(tick(dry_run=True)) or "nothing")
    elif args.action == "run":
        run_loop()
    else:
        JOB_FUNCS[args.action]()


if __name__ == "__main__":
    main()
