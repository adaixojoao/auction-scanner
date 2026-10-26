import os

import pytest

import pipeline
from conftest import FakeResponse


def test_run_scan_records_progress_and_summary(db, fake_http):
    fake_http(lambda m, u, kw: FakeResponse("<html></html>", status=404))
    result = pipeline.run_scan(source_names=["bcp", "leilosoc"], cfg={"filters": {}}, db=db,
                               report=False, alerts=False)
    assert result["sources"] == 2 and result["errors"] == 2 and result["listings"] == 0
    state = pipeline.scan_status(db)
    assert state["running"] is False and state["label"] == "bcp, leilosoc"
    assert state["summary"]["errors"] == 2 and state["finished_at"]
    assert not os.path.exists(pipeline.LOCK_PATH)


def test_a_scan_runs_below_other_programs_on_windows(monkeypatch):
    import ctypes
    calls = []

    class Kernel:
        def GetCurrentThread(self):
            return 1

        def SetThreadPriority(self, handle, level):
            calls.append(level)
            return 1

    # Linux has no ctypes.windll, so the stand-in is installed on the module.
    windll = type("windll", (), {"kernel32": Kernel()})()
    monkeypatch.setattr(pipeline.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    with pipeline._below_other_programs():
        assert calls == [pipeline._BACKGROUND_BEGIN]
    assert calls == [pipeline._BACKGROUND_BEGIN, pipeline._BACKGROUND_END]


def test_only_one_scan_at_a_time(db):
    with pipeline.scan_lock():
        with pytest.raises(pipeline.ScanBusy):
            pipeline.run_scan(source_names=["bcp"], cfg={}, db=db)
    assert not os.path.exists(pipeline.LOCK_PATH)


def test_stale_lock_is_taken_over(db, fake_http):
    fake_http(lambda m, u, kw: FakeResponse("", status=500))
    with open(pipeline.LOCK_PATH, "w") as f:
        f.write("123")
    os.utime(pipeline.LOCK_PATH, (0, 0))   # 1970: long dead
    pipeline.run_scan(source_names=["bcp"], cfg={}, db=db, report=False, alerts=False)


def test_scan_writes_report_and_sends_alerts(db, add, fake_http, monkeypatch, tmp_path):
    fake_http(lambda m, u, kw: FakeResponse("", status=500))
    sent = []
    monkeypatch.setattr("telegram_alert.send_telegram", lambda t, c, m, **k: sent.append(m) or True)
    add(external_id="a", title="Moradia", price=20000)
    cfg = {"filters": {}, "report": {"out_dir": str(tmp_path / "r"), "desktop_copy": False},
           "telegram": {"enabled": True, "token": "t", "chat_id": "c", "min_score": 50}}
    result = pipeline.run_scan(source_names=["bcp"], cfg=cfg, db=db)
    assert os.path.exists(result["report"]) and len(sent) == 1


def test_scan_state_of_a_killed_process_is_not_running(db):
    import os
    db.execute(
        "UPDATE scan_state SET running=1, done=4, current='bpi', started_at=?, label='x' WHERE id=1",
        (pipeline.utcnow_iso(),))
    db.commit()
    state = pipeline.scan_status(db)
    assert state["running"] is False and state["summary"]["stopped"] is True
    assert state["summary"]["sources"] == 4 and state["finished_at"]
    assert pipeline.scan_status(db)["finished_at"] == state["finished_at"]  # one close, not every poll

    db.execute(
        "UPDATE scan_state SET running=1, stop=0, finished_at=NULL, summary=NULL, started_at=? WHERE id=1",
        (pipeline.utcnow_iso(),))
    db.commit()
    with open(pipeline.LOCK_PATH, "w") as f:
        f.write(f"{os.getpid()} now")
    assert pipeline.scan_status(db)["running"] is True           # live holder
    assert db.execute("SELECT running FROM scan_state WHERE id=1").fetchone()[0] == 1
    with open(pipeline.LOCK_PATH, "w") as f:
        f.write("999999999 then")
    closed = pipeline.scan_status(db)
    assert closed["running"] is False and closed["summary"]["stopped"] is True


def _ok(source):
    return {"source": source.name, "count": 0, "status": "ok", "message": None, "duration_s": 0}


def test_a_stop_finishes_the_current_source_and_skips_the_rest(db, monkeypatch):
    seen = []

    def fake(conn, source, **kw):
        seen.append(source.name)
        if len(seen) == 1:
            pipeline.request_stop(conn)
        return _ok(source)

    monkeypatch.setattr("sources.run_source", fake)
    monkeypatch.setattr(pipeline, "rescore", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rescored")))
    result = pipeline.run_scan(countries=["PT"], cfg={"filters": {}}, db=db, report=False, alerts=False)
    assert result["stopped"] is True and result["sources"] == 1 and len(seen) == 1
    state = pipeline.scan_status(db)
    assert state["running"] is False and state["stop"] is False and state["summary"]["stopped"] is True
    from db import job_last_run
    assert job_last_run(db, "pt") is not None          # this week is done
    assert job_last_run(db, "eu") is None
    assert not os.path.exists(pipeline.LOCK_PATH)


def test_stopping_one_source_does_not_skip_the_country_scan(db, monkeypatch):
    def fake(conn, source, **kw):
        pipeline.request_stop(conn)
        return _ok(source)

    monkeypatch.setattr("sources.run_source", fake)
    result = pipeline.run_scan(source_names=["bcp", "leilosoc"], cfg={"filters": {}}, db=db,
                               report=False, alerts=False)
    assert result["stopped"] is True and result["sources"] == 1
    from db import job_last_run
    assert job_last_run(db, "pt") is None and job_last_run(db, "eu") is None


def test_pid_alive():
    import os
    from locks import pid_alive
    assert pid_alive(os.getpid()) and not pid_alive(999999999) and not pid_alive(0)


def test_seaside_homes_held_back_only_by_a_town_pin_are_looked_up(monkeypatch):
    """2026-10-26: 68 of 222 seaside homes missed Live and invest only because
    the pin was the town; they are looked up beyond the per-scan budget."""
    import db as dbmod
    import pipeline
    rows = [
        {"id": "a", "kind": "home", "hidden_reason": None,
         "reasons": ["position is the town, not the house — the beach and the climate are not this building's"]},
        {"id": "b", "kind": "home", "hidden_reason": None, "reasons": ["climate unknown"]},
        {"id": "c", "kind": "home", "hidden_reason": "stale", "reasons": ["position is the town, not the house"]},
        {"id": "d", "kind": "other", "hidden_reason": None, "reasons": ["position is the town, not the house"]},
    ]
    monkeypatch.setattr(dbmod, "load_listings", lambda *a, **k: rows)
    assert [it["id"] for it in pipeline.seaside_unplaced(None, {})] == ["a"]


def test_a_scan_from_the_app_runs_in_its_own_process(monkeypatch):
    """2026-10-26: a scan on the app's thread left the app at 7 GB. It runs in a
    child process; a busy lock comes back as ScanBusy."""
    import subprocess
    import types

    import pytest

    import pipeline
    ran = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd) or types.SimpleNamespace(returncode=0))
    assert pipeline.run_scan_apart(countries=["PT"], label="Portugal") == 0
    assert ran[0][1].endswith("pipeline.py") and ran[0][2] == "--scan" and '"PT"' in ran[0][3]
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=75))
    with pytest.raises(pipeline.ScanBusy):
        pipeline.run_scan_apart(source_names=["bcp"])
