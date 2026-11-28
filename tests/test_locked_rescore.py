"""A rescore takes scan.lock like a scan, and a source that met a locked
database reads again once instead of losing its run (2026-11-22: a rescore run
beside a scan made 15 sources fail with "database is locked")."""
import json
import sqlite3

import pytest

import pipeline
import sources
from sources import Source, run_source


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(sources, "LOCKED_RETRY_WAIT_S", 0)


def _source(outcomes):
    """A source that raises or returns each outcome in turn."""
    calls = []

    def func(db, **_):
        outcome = outcomes[len(calls)]
        calls.append(outcome)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return Source("locktest", "PT", func), calls


def _log_rows(db):
    return db.execute("SELECT status, count, message FROM scrape_log WHERE source='locktest'").fetchall()


def test_a_locked_database_is_read_again_once(db, no_wait):
    source, calls = _source([sqlite3.OperationalError("database is locked"), 3])
    result = run_source(db, source, max_price=1)
    assert len(calls) == 2
    assert result["status"] == "ok" and result["count"] == 3
    assert [tuple(r) for r in _log_rows(db)] == [("ok", 3, None)]


def test_locked_twice_is_recorded_once_as_an_error(db, no_wait):
    locked = sqlite3.OperationalError("database is locked")
    source, calls = _source([locked, locked])
    result = run_source(db, source, max_price=1)
    assert len(calls) == 2 and result["status"] == "error"
    rows = _log_rows(db)
    assert len(rows) == 1 and "database is locked" in rows[0]["message"]


def test_other_errors_are_not_retried(db, no_wait):
    source, calls = _source([RuntimeError("site changed"), 3])
    assert run_source(db, source, max_price=1)["status"] == "error"
    assert len(calls) == 1


def test_rescore_refuses_while_a_scan_runs(db, monkeypatch):
    ran = []
    monkeypatch.setattr(pipeline, "rescore", lambda db_, cfg: ran.append(1) or True)
    with pipeline.scan_lock():
        with pytest.raises(pipeline.ScanBusy):
            pipeline.run_rescore(cfg={"filters": {}}, db=db)
    assert ran == []


def test_a_scan_refuses_while_a_rescore_runs(db, monkeypatch):
    seen = []

    def rescore(db_, cfg):
        with pytest.raises(pipeline.ScanBusy):
            pipeline.run_scan(source_names=["bcp"], cfg={}, db=db_)
        seen.append(db_.execute("SELECT running, label FROM scan_state WHERE id=1").fetchone())
        return True
    monkeypatch.setattr(pipeline, "rescore", rescore)
    assert pipeline.run_rescore(cfg={"filters": {}}, db=db) is True
    assert [tuple(r) for r in seen] == [(1, "rescoring")]


def test_rescore_keeps_the_last_scan_time_and_summary(db, monkeypatch):
    summary = json.dumps({"listings": 7, "ok": 1, "empty": 0, "errors": 0, "sources": 1, "stopped": False})
    db.execute("UPDATE scan_state SET finished_at='2026-11-22T21:00:00+00:00', summary=? WHERE id=1",
               (summary,))
    db.commit()
    monkeypatch.setattr(pipeline, "rescore", lambda db_, cfg: False)
    assert pipeline.run_rescore(cfg={"filters": {}}, db=db) is False
    row = db.execute("SELECT running, finished_at, summary FROM scan_state WHERE id=1").fetchone()
    assert row["running"] == 0
    assert row["finished_at"] == "2026-11-22T21:00:00+00:00" and row["summary"] == summary
    assert pipeline.scan_status(db)["running"] is False
