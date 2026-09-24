"""
Auction Scanner — the desktop app.

Double-click the "Auction Scanner" icon (made by create_shortcut.bat), or run:

    pythonw app.py      (no console window)
    python app.py       (with a console, for troubleshooting)

It starts the local server, opens the app in its own window (Edge or Chrome
"app mode", no browser tabs or address bar), scans on the Settings timetable
while it is open, and quits a few minutes after the window is closed.
Opening the icon again while it runs just brings up another window.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "app.log")
LOG = logging.getLogger("app")

# The window pings every 15 s; minimised windows can be throttled to once a
# minute, so allow a generous gap before deciding it was closed.
IDLE_QUIT_AFTER = 180
# How long to wait for a window's first ping before relying on the idle rule.
FIRST_PING_GRACE = 120
AUTO_SCAN_EVERY = 5 * 60


def setup_logging():
    if sys.stdout is None:  # pythonw: no console
        sys.stdout = sys.stderr = open(os.path.join(HERE, "app-output.log"), "a", encoding="utf-8", buffering=1)
    logging.getLogger("urllib3").setLevel(logging.ERROR)   # one line per retry is noise
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not getattr(sys.stdout, "name", "").endswith("app-output.log"):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)


def show_error(message: str):
    """Tell the user something went wrong, even without a console."""
    LOG.error(message)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Auction Scanner", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def app_url(cfg: dict) -> str:
    d = cfg.get("dashboard", {})
    return f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8050)}/"


def already_running(url: str) -> bool:
    import requests
    try:
        r = requests.get(url + "api/ping", timeout=2)
        return r.ok and r.json().get("app") == "auction-scanner"
    except Exception:
        return False


def find_app_browser() -> str | None:
    """Edge or Chrome, for a window without tabs or an address bar."""
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                               os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((c for c in candidates if c and os.path.exists(c)), None)


def open_window(url: str):
    browser = find_app_browser()
    if browser:
        try:
            subprocess.Popen([browser, f"--app={url}", "--window-size=1440,920"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError as e:
            LOG.warning(f"Could not start {browser}: {e}")
    webbrowser.open(url)


def auto_scan_loop(stop: threading.Event):
    """While the app is open, run whatever the timetable says is due."""
    import scheduler
    from config import load_config
    delay = 20   # first check shortly after opening, so the data is fresh
    while not stop.wait(delay):
        delay = AUTO_SCAN_EVERY
        try:
            if load_config().get("schedule", {}).get("while_app_open", True):
                ran = scheduler.tick()
                if ran:
                    LOG.info(f"Timetable ran: {', '.join(ran)}")
        except Exception:
            LOG.exception("Timetable check failed")


def scan_running() -> bool:
    from db import connect
    from pipeline import scan_status
    db = connect()
    try:
        return scan_status(db)["running"]
    finally:
        db.close()


def main() -> int:
    setup_logging()
    try:
        from config import load_config
        from werkzeug.serving import make_server
        import dashboard
    except ImportError as e:
        show_error(f"A required package is missing: {e.name}.\n\n"
                   f"Open a terminal in {HERE} and run:\n    pip install -r requirements.txt")
        return 1

    cfg = load_config()
    url = app_url(cfg)
    if already_running(url):
        LOG.info("Already running — opening another window")
        open_window(url)
        return 0

    d = cfg.get("dashboard", {})
    try:
        server = make_server(d.get("host", "127.0.0.1"), d.get("port", 8050), dashboard.app, threaded=True)
    except OSError as e:
        show_error(f"Could not start on {url}: {e}\n\nIs another program using port {d.get('port', 8050)}?")
        return 1

    threading.Thread(target=server.serve_forever, name="server", daemon=True).start()
    LOG.info(f"Auction Scanner running at {url}")
    open_window(url)

    stop = threading.Event()
    threading.Thread(target=auto_scan_loop, args=(stop,), name="timetable", daemon=True).start()

    started = time.monotonic()
    try:
        while True:
            time.sleep(5)
            last = dashboard.app.config.get("LAST_HEARTBEAT")
            now = time.monotonic()
            if last is None:
                if now - started < FIRST_PING_GRACE:
                    continue
                last = started   # the window never opened; fall back to the idle rule
            if now - last > IDLE_QUIT_AFTER and not scan_running():
                LOG.info("Window closed — shutting down")
                break
    except KeyboardInterrupt:
        pass
    stop.set()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
