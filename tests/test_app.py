import app


def test_app_url_from_config():
    assert app.app_url({"dashboard": {"host": "127.0.0.1", "port": 9000}}) == "http://127.0.0.1:9000/"
    assert app.app_url({}) == "http://127.0.0.1:8050/"


def test_not_running_when_nothing_answers():
    # The no_network fixture makes every request fail, like an unused port.
    assert app.already_running("http://127.0.0.1:1/") is False


def test_browser_lookup_never_crashes(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda name: None)
    monkeypatch.setattr(app.os.path, "exists", lambda p: False)
    assert app.find_app_browser() is None


def test_window_falls_back_to_the_default_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(app, "find_app_browser", lambda: None)
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    app.open_window("http://127.0.0.1:8050/")
    assert opened == ["http://127.0.0.1:8050/"]


def test_app_mode_window(monkeypatch):
    launched = []
    monkeypatch.setattr(app, "find_app_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(app.subprocess, "Popen", lambda args, **kw: launched.append(args))
    app.open_window("http://127.0.0.1:8050/")
    assert launched[0][:2] == ["/usr/bin/chromium", "--app=http://127.0.0.1:8050/"]


def test_restart_runs_the_new_version_without_updating_again(monkeypatch):
    started = []
    monkeypatch.setattr(app.subprocess, "Popen", lambda args, **kw: started.append((args, kw["env"])))
    app.restart(reopen_window=False)
    args, env = started[0]
    assert args[-1].endswith("app.py") and env[app.NO_UPDATE_ENV] == "1" and env[app.RESTART_ENV] == "1"
    app.restart(reopen_window=True)
    assert app.RESTART_ENV not in started[1][1]


def test_one_copy_starts_at_a_time(tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setattr(app, "STARTING_LOCK", str(tmp_path / "starting.lock"))
    assert app.claim_start() is True
    assert app.claim_start() is False          # a double-click while the first copy updates
    old = time.time() - 3600
    os.utime(app.STARTING_LOCK, (old, old))
    assert app.claim_start() is True           # a lock left behind by a crash is taken over
    app.release_start()
    assert not os.path.exists(app.STARTING_LOCK)
