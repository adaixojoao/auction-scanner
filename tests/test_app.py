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
    monkeypatch.setattr(app, "app_windows", lambda: [])
    monkeypatch.setattr(app, "find_app_browser", lambda: None)
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    app.open_window("http://127.0.0.1:8050/")
    assert opened == ["http://127.0.0.1:8050/"]


def test_app_mode_window(monkeypatch):
    launched = []
    monkeypatch.setattr(app, "app_windows", lambda: [])
    monkeypatch.setattr(app, "find_app_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(app.subprocess, "Popen", lambda args, **kw: launched.append(args))
    app.open_window("http://127.0.0.1:8050/")
    assert launched[0][:2] == ["/usr/bin/chromium", "--app=http://127.0.0.1:8050/"]


def test_an_open_window_is_shown_instead_of_a_second_one(monkeypatch):
    launched, closed, focused = [], [], []
    monkeypatch.setattr(app, "app_windows", lambda: [10, 20])
    monkeypatch.setattr(app, "_foreground_window", lambda: 20)
    monkeypatch.setattr(app, "_close_window", closed.append)
    monkeypatch.setattr(app, "focus_window", focused.append)
    monkeypatch.setattr(app.subprocess, "Popen", lambda *args, **kw: launched.append(args))
    app.open_window("http://127.0.0.1:8050/")
    assert closed == [10] and focused == [20] and launched == []


def test_only_page_windows_count_as_the_app():
    assert app.is_app_window("Listings — Auction Scanner")
    assert not app.is_app_window("Auction Scanner")          # the error dialog
    assert not app.is_app_window("Listings — Auction Scanner - Google Chrome")


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


def test_the_warm_up_scores_the_open_goals_and_leaves_the_rest(monkeypatch):
    """Live and invest goes first: it reads the climate of seaside homes. The
    other goals wait until their tab is opened."""
    import dashboard
    from scoring import MODES
    done = []
    monkeypatch.setattr(dashboard, "_build_list", lambda mode, tag=None: done.append(mode))
    monkeypatch.setattr(dashboard, "score_only", lambda mode: done.append(("scored", mode)))
    app.warm_up()
    assert done == list(MODES)[:dashboard.MAX_LISTS] == ["both", "home"]


def test_the_warm_up_logs_the_peak_and_the_memory_still_free(monkeypatch, caplog):
    import logging
    import dashboard
    monkeypatch.setattr(dashboard, "_build_list", lambda mode, tag=None: None)
    monkeypatch.setattr(dashboard, "score_only", lambda mode: None)
    monkeypatch.setattr(app, "memory_use", lambda: (900 * 1024 ** 2, 2 * 1024 ** 3))
    with caplog.at_level(logging.INFO, logger="app"):
        app.warm_up()
    assert any(r.message == "Memory peaked at 900 MB, 2.0 GB free" for r in caplog.records)
    assert not any("Warm-up skipped" in r.message for r in caplog.records)


def test_the_warm_up_still_finishes_when_memory_cannot_be_read(monkeypatch, caplog):
    import logging
    import dashboard
    monkeypatch.setattr(dashboard, "_build_list", lambda mode, tag=None: None)
    monkeypatch.setattr(dashboard, "score_only", lambda mode: None)
    monkeypatch.setattr(app, "memory_use", lambda: None)
    with caplog.at_level(logging.INFO, logger="app"):
        app.warm_up()
    assert any(r.message == "List warmed up" for r in caplog.records)
    assert any(r.message == "Memory could not be read" for r in caplog.records)


def test_memory_use_reads_the_peak_and_what_is_free():
    import sys
    got = app.memory_use()
    if sys.platform not in ("win32", "linux"):
        assert got is None
        return
    peak, free = got
    assert peak >= 1024 ** 2 and free >= 1024 ** 2
