"""Nothing holds the database while waiting on a website, the app does not
score at start when a scan is about to, and a busy database never loses a
job's last run. (2026-11-18: the closing watch held the database through 128
requests while the app scored, and the scan starting then lost 8 sources.)"""
import sqlite3
from datetime import datetime, timedelta, timezone

import app
import outcomes
import scheduler
from conftest import FakeResponse

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class _Watching:
    """A session that notes, at each request, whether a write was open."""

    def __init__(self, db, reply):
        self.db, self.reply, self.open_at_request = db, reply, []

    def get(self, url, **kw):
        self.open_at_request.append(self.db.in_transaction)
        return FakeResponse(json_data=self.reply(url))


def test_the_closing_watch_saves_before_each_request(db, add):
    for n in (1, 2, 3):
        add("eleiloes", f"c{n}", title="Moradia", price=10000, date_end="2026-10-10T11:30:00",
            raw_json={"referencia": f"NP{n}"})
    session = _Watching(db, lambda url: {"item": {"lanceAtual": 12500.0}})
    assert outcomes.watch_closing(db, session, NOW) == 3
    assert session.open_at_request == [False, False, False]


def test_the_detail_fetch_saves_before_each_request(db, add, monkeypatch):
    import sources.pt as pt
    monkeypatch.setattr(pt.time, "sleep", lambda s: None)
    end = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
    for n in (1, 2, 3):
        add("eleiloes", f"d{n}", title="Moradia", tipo="moradia", price=20000, date_end=end,
            raw_json={"referencia": f"LO{n}"})
    session = _Watching(db, lambda url: {"item": None})      # withdrawn: marked, not asked again
    monkeypatch.setattr(pt, "_eleiloes_session", lambda: session)
    pt.fetch_eleiloes_details(db, limit=10, max_price=50000)
    assert session.open_at_request == [False, False, False]
    assert db.execute("SELECT COUNT(*) FROM listings WHERE raw_json LIKE '%detail_checked%'").fetchone()[0] == 3


def test_the_warm_up_waits_for_a_scan_that_is_due(monkeypatch, caplog):
    import logging
    import dashboard
    built = []
    monkeypatch.setattr(dashboard, "_build_list", lambda mode, tag=None: built.append(mode))
    monkeypatch.setattr(app, "_scan_about_to_start", lambda: True)
    with caplog.at_level(logging.INFO, logger="app"):
        app.warm_up()
    assert built == []
    assert any("Warm-up skipped: a scan is running or due" in r.message for r in caplog.records)


def test_a_scan_is_due_until_every_scan_job_ran(db, monkeypatch):
    from db import set_job_last_run
    monkeypatch.setattr("config.load_config", lambda: {"schedule": {}})
    now = datetime.now().astimezone()
    assert scheduler.scan_due(now) is True                    # nothing ever ran
    for job in ("pt", "eu", "retry"):
        set_job_last_run(db, job, now)
    assert scheduler.scan_due(now) is False
    assert scheduler.scan_due(now + timedelta(hours=3)) is True   # the 2-hour retry is due again


def test_a_busy_database_does_not_lose_or_crash_a_job_record(db, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(scheduler, "RECORD_WAIT_S", 0)
    real, calls = dbmod.set_job_last_run, []

    def busy_twice(conn, job, when=None):
        calls.append(job)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        real(conn, job, when)
    monkeypatch.setattr(dbmod, "set_job_last_run", busy_twice)
    scheduler._record("morning")
    assert len(calls) == 3
    assert db.execute("SELECT COUNT(*) FROM job_runs WHERE job='morning'").fetchone()[0] == 1


def test_a_job_record_that_never_gets_through_is_logged_not_raised(db, monkeypatch, caplog):
    import db as dbmod
    monkeypatch.setattr(scheduler, "RECORD_WAIT_S", 0)

    def always_busy(conn, job, when=None):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(dbmod, "set_job_last_run", always_busy)
    scheduler._record("morning")                              # no exception
    assert any("Could not record that morning ran" in r.message for r in caplog.records)

