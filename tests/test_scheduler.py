from datetime import datetime, timedelta, timezone

import scheduler
from scheduler import due_jobs

TZ = timezone(timedelta(hours=1))


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def test_everything_due_on_first_run():
    assert due_jobs(at(2026, 9, 28, 9), {}) == ["pt", "eu", "backup", "closing", "morning", "report"]  # a Monday


def test_intervals_with_slack():
    now = at(2026, 9, 24, 12, 0)
    last = {"pt": now - timedelta(hours=1, minutes=55), "eu": now - timedelta(hours=3),
            "backup": now - timedelta(hours=3), "morning": now - timedelta(hours=1),
            "report": now - timedelta(days=1), "closing": now - timedelta(minutes=5)}
    assert due_jobs(now, last) == ["pt"]          # 1h55 ≥ 2h − 10 min slack


def test_check_times_catch_up_once():
    now = at(2026, 9, 24, 21, 0)                  # slept through 08:00 and 20:00
    last = {"pt": now, "eu": now, "backup": now, "report": now, "closing": now, "morning": at(2026, 9, 23, 20, 5)}
    assert due_jobs(now, last) == ["morning"]
    last["morning"] = now
    assert due_jobs(now + timedelta(minutes=30), last) == ["closing"]     # the closing watch, every tick


def test_weekly_report_after_monday_slot():
    last = {"pt": at(2026, 9, 28, 9), "eu": at(2026, 9, 28, 9), "backup": at(2026, 9, 28, 9),
            "morning": at(2026, 9, 28, 9), "report": at(2026, 9, 21, 8, 1)}
    assert "report" not in due_jobs(at(2026, 9, 28, 7, 59), last)
    assert "report" in due_jobs(at(2026, 9, 28, 8, 0), last)
    assert "report" in due_jobs(at(2026, 9, 30, 8, 0), last)       # missed Monday, caught Wednesday
    last["report"] = at(2026, 9, 28, 8, 30)
    assert "report" not in due_jobs(at(2026, 10, 2, 8, 0), last)


def test_schedule_can_disable_jobs():
    off = {"eu_every_hours": 0, "backup_every_hours": 0, "check_times": [], "weekly_report": "",
           "closing_every_minutes": 0}
    assert due_jobs(at(2026, 9, 24, 9), {}, off) == ["pt"]


def test_tick_runs_due_jobs_once_and_records_them(db, monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(scheduler, "LOCK_PATH", str(tmp_path / "lock"))
    monkeypatch.setattr(scheduler, "JOB_FUNCS", {j: (lambda j=j: ran.append(j)) for j in scheduler.JOBS})
    import config
    monkeypatch.setattr(config, "load_config", lambda: {"schedule": {}})
    first = scheduler.tick()
    assert ran == first and "pt" in first
    ran.clear()
    assert scheduler.tick() == [] and ran == []
    assert not (tmp_path / "lock").exists()


def test_tick_skips_when_locked(db, monkeypatch, tmp_path):
    import os
    lock = tmp_path / "lock"
    lock.write_text(f"{os.getpid()} now")    # held by a live process
    monkeypatch.setattr(scheduler, "LOCK_PATH", str(lock))
    monkeypatch.setattr(scheduler, "JOB_FUNCS", {j: (lambda: 1 / 0) for j in scheduler.JOBS})
    import config
    monkeypatch.setattr(config, "load_config", lambda: {"schedule": {}})
    assert scheduler.tick() == []


def test_lock_left_by_a_dead_process_is_taken_over(db, monkeypatch, tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("999999999 yesterday")   # no such process
    ran = []
    monkeypatch.setattr(scheduler, "LOCK_PATH", str(lock))
    monkeypatch.setattr(scheduler, "JOB_FUNCS", {j: (lambda j=j: ran.append(j)) for j in scheduler.JOBS})
    import config
    monkeypatch.setattr(config, "load_config", lambda: {"schedule": {}})
    assert scheduler.tick() and ran
