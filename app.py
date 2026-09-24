"""
Auction Scanner — the desktop app.

Double-click the "Auction Scanner" icon (made by create_shortcut.bat), or run:

    pythonw app.py      (no console window)
    python app.py       (with a console, for troubleshooting)

It first updates itself from GitHub if a newer version was merged into master
(updater.py), then starts the local server, opens the app in its own window
(Edge or Chrome "app mode", no browser tabs or address bar), scans on the
Settings timetable while it is open, and quits a few minutes after the window
is closed. Opening the icon again while it runs just brings up another window.
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
    # urllib, not requests: this runs before an update may reinstall packages.
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=2) as r:
            return json.load(r).get("app") == "auction-scanner"
    except Exception:
        return False


# Set when the app restarts itself after an update from Settings: the window is
# already open, and the old process may still be letting go of the port.
RESTART_ENV = "AUCTION_SCANNER_RESTART"
NO_UPDATE_ENV = "AUCTION_SCANNER_NO_UPDATE"


STARTING_LOCK = os.path.join(HERE, "starting.lock")


def claim_start() -> bool:
    """Only one copy updates and starts at a time. False if another copy is
    already starting (a double-click while it updates)."""
    for _ in range(2):
        try:
            os.close(os.open(STARTING_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(STARTING_LOCK) < 300:
                    return False
                os.remove(STARTING_LOCK)          # left behind by a crash
            except OSError:
                return False
    return False


def release_start():
    try:
        os.remove(STARTING_LOCK)
    except OSError:
        pass


def restart(*, reopen_window: bool):
    """Start a fresh copy of the app (the new code) with the update already done."""
    env = {**os.environ, NO_UPDATE_ENV: "1"}
    if not reopen_window:
        env[RESTART_ENV] = "1"
    subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=HERE, env=env,
                     creationflags=0x08000000 if sys.platform == "win32" else 0)


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
    from config import load_config    # standard library only, like updater
    import updater

    cfg = load_config()
    url = app_url(cfg)
    restarting = os.environ.pop(RESTART_ENV, "") == "1"
    if restarting:
        for _ in range(40):            # the old process is shutting down
            if not already_running(url):
                break
            time.sleep(0.5)
    elif already_running(url):
        LOG.info("Already running — opening another window")
        open_window(url)
        return 0

    if not claim_start():
        LOG.info("Another copy is starting (updating?) — waiting for it")
        for _ in range(180):
            if already_running(url):
                open_window(url)
                return 0
            time.sleep(1)
        show_error("The app did not start. Look at app.log in the app folder.")
        return 1
    try:
        # Update from GitHub before anything else is imported, then run the new code.
        if os.environ.pop(NO_UPDATE_ENV, "") != "1":
            if updater.update_on_start():
                LOG.info("Updated from GitHub — restarting with the new version")
                release_start()
                restart(reopen_window=True)
                return 0
        elif updater.requirements_outdated():
            LOG.info("requirements.txt: " + updater.install_requirements()[1])
        return run_app(cfg, url, restarting)
    finally:
        release_start()


def run_app(cfg: dict, url: str, restarting: bool) -> int:
    try:
        from werkzeug.serving import make_server
        import dashboard
    except ImportError as e:
        show_error(f"A required package is missing: {e.name}.\n\n"
                   f"Open a terminal in {HERE} and run:\n    pip install -r requirements.txt")
        return 1

    d = cfg.get("dashboard", {})
    try:
        server = make_server(d.get("host", "127.0.0.1"), d.get("port", 8050), dashboard.app, threaded=True)
    except OSError as e:
        show_error(f"Could not start on {url}: {e}\n\nIs another program using port {d.get('port', 8050)}?")
        return 1

    dashboard.app.config["RESTART_APP"] = lambda: restart(reopen_window=False)
    threading.Thread(target=server.serve_forever, name="server", daemon=True).start()
    release_start()                    # a double-click now just opens another window
    LOG.info(f"Auction Scanner running at {url}")
    if not restarting:                 # after a restart the window is already open
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
