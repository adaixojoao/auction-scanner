"""
scheduler.py — Automated scheduling for Auction Scanner.

Usage:
  python scheduler.py install    # Install Windows Task Scheduler task
  python scheduler.py remove     # Remove task
  python scheduler.py status     # Check status
  python scheduler.py run        # Run loop directly (no Task Scheduler)
  python scheduler.py pt         # PT-only scrape
  python scheduler.py eu         # Full EU scrape
  python scheduler.py morning    # Check deadlines + carta status
  python scheduler.py report     # Send weekly report now
"""
import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

LOG = logging.getLogger("scheduler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(SCRIPT_DIR, "auctions.db")
TASK_NAME  = "AuctionScanner"


def run_pt_scrape():
    LOG.info("=== PT scrape starting ===")
    sys.argv = ["scraper.py", "--country", "PT", "--max-price", "100000"]
    try:
        from scraper import main
        main()
    except Exception as e:
        LOG.error(f"PT scrape failed: {e}")


def run_eu_scrape():
    LOG.info("=== EU scrape starting ===")
    sys.argv = ["scraper.py", "--max-price", "100000"]
    try:
        from scraper import main
        main()
    except Exception as e:
        LOG.error(f"EU scrape failed: {e}")


def run_morning_checks():
    LOG.info("=== Morning checks ===")
    try:
        from config import load_config
        from telegram_alert import alert_carta_deadlines
        from scoring import score as score_fn
        cfg = load_config()
        db  = sqlite3.connect(DB_PATH)
        alert_carta_deadlines(db, cfg, score_fn)
        db.close()
        LOG.info("Morning checks complete")
    except Exception as e:
        LOG.error(f"Morning checks failed: {e}")


def run_weekly_report():
    LOG.info("=== Weekly report ===")
    try:
        from config import load_config
        from telegram_alert import alert_weekly_summary
        from datetime import timedelta
        cfg = load_config()
        db  = sqlite3.connect(DB_PATH)
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()
        stats = {
            "new":      db.execute("SELECT COUNT(*) FROM listings WHERE first_seen > ?", (week_ago,)).fetchone()[0],
            "sent":     db.execute("SELECT COUNT(*) FROM carta_log WHERE created_at > ?", (week_ago,)).fetchone()[0],
            "won":      db.execute("SELECT COUNT(*) FROM carta_log WHERE outcome='won'").fetchone()[0],
            "pending":  db.execute("SELECT COUNT(*) FROM carta_log WHERE outcome='pending'").fetchone()[0],
            "exposure": db.execute("SELECT COALESCE(SUM(bid_amount),0) FROM carta_log WHERE outcome='pending'").fetchone()[0],
        }
        tg = cfg.get("telegram", {})
        if tg.get("enabled"):
            alert_weekly_summary(tg["token"], tg["chat_id"], stats)
        db.close()
    except Exception as e:
        LOG.error(f"Weekly report failed: {e}")


def run_loop():
    try:
        import schedule
    except ImportError:
        print("Install schedule: pip install schedule")
        sys.exit(1)

    schedule.every(2).hours.do(run_pt_scrape)
    schedule.every(6).hours.do(run_eu_scrape)
    schedule.every().day.at("08:00").do(run_morning_checks)
    schedule.every().day.at("20:00").do(run_morning_checks)
    schedule.every().monday.at("08:00").do(run_weekly_report)

    LOG.info("Scheduler loop running.")
    LOG.info("  PT scrape:    every 2h")
    LOG.info("  EU scrape:    every 6h")
    LOG.info("  Checks:       08:00 + 20:00 daily")
    LOG.info("  Report:       Monday 08:00")
    LOG.info("Press Ctrl+C to stop.")

    run_pt_scrape()

    import time
    while True:
        schedule.run_pending()
        time.sleep(60)


def install_task():
    python = sys.executable
    script = os.path.join(SCRIPT_DIR, "scheduler.py")
    cmd = (
        f'schtasks /create /tn "{TASK_NAME}" /tr "\"{python}\" \"{script}\" run" '
        f'/sc HOURLY /mo 2 /st 00:00 /f'
    )
    ret = os.system(cmd)
    if ret == 0:
        print(f"Task '{TASK_NAME}' installed. Runs every 2 hours.")
    else:
        print("Failed. Try running as Administrator, or use: python scheduler.py run")


def remove_task():
    ret = os.system(f'schtasks /delete /tn "{TASK_NAME}" /f')
    print("Task removed." if ret == 0 else "Task not found.")


def status_task():
    os.system(f'schtasks /query /tn "{TASK_NAME}" /fo LIST')


def main():
    parser = argparse.ArgumentParser(description="Auction Scanner Scheduler")
    parser.add_argument("action",
        choices=["install", "remove", "status", "run", "pt", "eu", "morning", "report"],
        nargs="?", default="run")
    args = parser.parse_args()
    actions = {
        "install": install_task,
        "remove":  remove_task,
        "status":  status_task,
        "run":     run_loop,
        "pt":      run_pt_scrape,
        "eu":      run_eu_scrape,
        "morning": run_morning_checks,
        "report":  run_weekly_report,
    }
    actions[args.action]()


if __name__ == "__main__":
    main()
