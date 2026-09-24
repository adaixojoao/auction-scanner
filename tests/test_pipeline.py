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
    db.execute("UPDATE scan_state SET running=1, started_at=?, label='x' WHERE id=1",
               (pipeline.utcnow_iso(),))
    db.commit()
    assert pipeline.scan_status(db)["running"] is False          # no lock at all
    with open(pipeline.LOCK_PATH, "w") as f:
        f.write(f"{os.getpid()} now")
    assert pipeline.scan_status(db)["running"] is True           # live holder
    with open(pipeline.LOCK_PATH, "w") as f:
        f.write("999999999 then")
    assert pipeline.scan_status(db)["running"] is False          # holder died


def test_pid_alive():
    import os
    from common import pid_alive
    assert pid_alive(os.getpid()) and not pid_alive(999999999) and not pid_alive(0)
