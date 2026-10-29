"""A source that reads far less than usual, or stops early, must not show as
plain "ok": Fotocasa read 1-2k of its usual 13k for weeks and looked fine."""
from db import record_scrape, source_health


def _run(db, count, ts, scope="full", status="ok"):
    record_scrape(db, "fotocasa", count=count, status=status, message=None, duration_s=1,
                  timestamp=ts, scope=scope, pages_seen=None)


def _state(db):
    return {h["source"]: h for h in source_health(db)}["fotocasa"]


def test_a_run_far_below_the_usual_count_is_dropped(db):
    for day, n in (("01", 13300), ("05", 13335), ("06", 13413)):
        _run(db, n, f"2026-10-{day}T10:00:00+00:00")
    _run(db, 1153, "2026-10-24T10:00:00+00:00")
    h = _state(db)
    assert h["state"] == "dropped" and h["usual_count"] == 13335


def test_a_partial_run_says_so_and_a_normal_one_is_ok(db):
    for day, n in (("01", 13300), ("05", 13335)):
        _run(db, n, f"2026-10-{day}T10:00:00+00:00")
    _run(db, 1626, "2026-10-29T10:00:00+00:00", scope="partial")
    assert _state(db)["state"] == "partial"
    _run(db, 13100, "2026-10-30T10:00:00+00:00")
    assert _state(db)["state"] == "ok"


def test_a_small_source_with_no_history_is_ok(db):
    _run(db, 10, "2026-10-29T10:00:00+00:00")
    assert _state(db)["state"] == "ok" and _state(db)["usual_count"] is None
