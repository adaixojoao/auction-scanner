"""Only trusted complete reads can make unseen listings stale."""
from datetime import datetime, timedelta, timezone

import pytest

import db as store

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
BASELINE = NOW - timedelta(days=12)


@pytest.mark.parametrize("count, status, scope, advances", [
    (3, "ok", "full", True),
    (3, "ok", None, True),  # retained legacy compatibility
    (3, "ok", "partial", False),
    (3, "ok", "unknown", False),
    (3, "ok", "", False),
    (3, "ok", "FULL", False),
    (3, "error", "full", False),
    (3, "blocked", "full", False),
    (3, "empty", "full", False),
    (3, "unexpected", "full", False),
    (0, "empty", "full", False),
    (0, "ok", "full", False),
])
def test_only_trusted_reads_advance_freshness(db, count, status, scope, advances):
    store.record_scrape(db, "scopetest", count=1, status="ok", scope="full",
                        timestamp=BASELINE.isoformat())
    store.record_scrape(db, "scopetest", count=count, status=status, scope=scope,
                        timestamp=NOW.isoformat())
    expected = NOW if advances else BASELINE
    assert store.last_ok_by_source(db)["scopetest"] == expected


def test_unknown_coverage_without_a_baseline_has_no_freshness_clock(db):
    store.record_scrape(db, "scopetest", count=3, status="ok", scope="unknown",
                        timestamp=NOW.isoformat())
    assert "scopetest" not in store.last_ok_by_source(db)


def test_invalid_read_does_not_affect_another_source(db):
    store.record_scrape(db, "healthy", count=3, status="ok", scope="full",
                        timestamp=NOW.isoformat())
    store.record_scrape(db, "incomplete", count=3, status="ok", scope="partial",
                        timestamp=NOW.isoformat())
    assert store.last_ok_by_source(db) == {"healthy": NOW}


@pytest.mark.parametrize("scope, becomes_stale", [
    ("partial", False),
    ("unknown", False),
    ("full", True),
])
def test_unseen_listing_uses_only_complete_coverage(db, add, scope, becomes_stale):
    item = add("cardtest", "unseen", title="Moradia", price=20000, concelho="Moura")
    seen = NOW - timedelta(days=10)
    db.execute("UPDATE listings SET last_seen=? WHERE id=?", (seen.isoformat(), item["id"]))
    db.commit()
    store.record_scrape(db, "cardtest", count=3, status="ok", scope=scope,
                        timestamp=NOW.isoformat())
    rows = store.load_listings(db, include_hidden=True, now=NOW)
    listing = next(row for row in rows if row["id"] == item["id"])
    reason = listing.get("hidden_reason") or ""
    assert reason.startswith("stale:") is becomes_stale
    assert db.execute("SELECT COUNT(*) FROM listings WHERE id=?", (item["id"],)).fetchone()[0] == 1
