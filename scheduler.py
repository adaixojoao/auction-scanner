"""
Set up Windows Task Scheduler to run the auction scanner automatically.
Run: python scheduler.py install    — creates scheduled task
     python scheduler.py remove     — removes scheduled task
     python scheduler.py status     — shows task status
"""

import os
import subprocess
import sys


TASK_NAME = "AuctionScanner"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
SCRAPER = os.path.join(SCRIPT_DIR, "scraper.py")
LOG_FILE = os.path.join(SCRIPT_DIR, "scheduler.log")


def install(interval_hours: int = 6):
    # Build the command that Task Scheduler will run
    cmd = f'"{PYTHON}" "{SCRAPER}" --source all 2>&1 >> "{LOG_FILE}"'

    # Use schtasks to create a repeating task
    schtasks_cmd = [
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/TR", cmd,
        "/SC", "HOURLY",
        "/MO", str(interval_hours),
        "/ST", "00:00",
        "/F",  # force overwrite if exists
    ]

    try:
        result = subprocess.run(schtasks_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Scheduled task '{TASK_NAME}' created successfully!")
            print(f"  Runs every {interval_hours} hours")
            print(f"  Script: {SCRAPER}")
            print(f"  Log: {LOG_FILE}")
            print(f"\nTo change interval, edit config.json and re-run: python scheduler.py install")
        else:
            print(f"Failed to create task: {result.stderr}")
            if "Access is denied" in result.stderr:
                print("\nTry running as Administrator.")
    except FileNotFoundError:
        print("schtasks not found — this only works on Windows.")


def remove():
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Scheduled task '{TASK_NAME}' removed.")
    else:
        print(f"Could not remove task: {result.stderr}")


def status():
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"Task '{TASK_NAME}' not found. Run: python scheduler.py install")

    if os.path.exists(LOG_FILE):
        size = os.path.getsize(LOG_FILE)
        print(f"\nLog file: {LOG_FILE} ({size:,} bytes)")
        # Show last 10 lines
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            if lines:
                print("Last 10 lines:")
                for line in lines[-10:]:
                    print(f"  {line.rstrip()}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scheduler.py [install|remove|status]")
        print("\n  install  — Create a Windows scheduled task to run every N hours")
        print("  remove   — Remove the scheduled task")
        print("  status   — Show task status and recent logs")
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "install":
        from config import load_config
        cfg = load_config()
        hours = cfg.get("schedule", {}).get("interval_hours", 6)
        install(interval_hours=hours)
    elif action == "remove":
        remove()
    elif action == "status":
        status()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
