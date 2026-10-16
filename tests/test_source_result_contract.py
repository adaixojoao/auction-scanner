"""Invalid scraper returns must not masquerade as healthy empty scans."""
import pytest
import requests

from sources import ScanCount, Source, SourceUnavailable, run_source


@pytest.mark.parametrize("value, count, status", [
    (0, 0, "empty"),
    (3, 3, "ok"),
    (ScanCount(2, "partial", 1), 2, "ok"),
    ({"count": 0, "scope": "full"}, 0, "empty"),
    ({"count": 3, "scope": "partial"}, 3, "ok"),
])
def test_valid_counts_keep_their_status(db, value, count, status):
    source = Source("contracttest", "PT", lambda *a, **k: value)
    result = run_source(db, source, max_price=100000)
    assert (result["count"], result["status"]) == (count, status)
    assert result["message"] is None
    row = db.execute(
        "SELECT count, status FROM scrape_log WHERE source='contracttest'"
    ).fetchone()
    assert tuple(row) == (count, status)


@pytest.mark.parametrize("value", [
    None, True, -1, 1.5, "2", {},
    {"count": None}, {"count": True}, {"count": -1},
    {"count": 1.5}, {"count": "2"},
])
def test_invalid_counts_are_recorded_as_errors(db, value):
    source = Source("contracttest", "PT", lambda *a, **k: value)
    result = run_source(db, source, max_price=100000)
    assert (result["count"], result["status"]) == (0, "error")
    assert "non-negative integer count" in result["message"]
    row = db.execute(
        "SELECT count, status, scope FROM scrape_log WHERE source='contracttest'"
    ).fetchone()
    assert tuple(row) == (0, "error", "partial")


@pytest.mark.parametrize("exception, status", [
    (requests.Timeout("fixture timeout"), "error"),
    (SourceUnavailable("fixture login required"), "blocked"),
])
def test_raised_failures_remain_distinct_from_empty(db, exception, status):
    def scrape(*args, **kwargs):
        raise exception

    result = run_source(db, Source("contracttest", "PT", scrape), max_price=100000)
    assert (result["count"], result["status"]) == (0, status)
    assert result["message"]
    row = db.execute(
        "SELECT status, scope FROM scrape_log WHERE source='contracttest'"
    ).fetchone()
    assert tuple(row) == (status, "partial")


def test_invalid_return_rolls_back_uncommitted_scraper_writes(db):
    db.execute("CREATE TABLE batch2_contract_probe (value TEXT)")
    db.commit()

    def scrape(connection, **kwargs):
        connection.execute("INSERT INTO batch2_contract_probe VALUES ('pending')")
        return None

    result = run_source(db, Source("contracttest", "PT", scrape), max_price=100000)
    assert result["status"] == "error"
    assert db.execute("SELECT COUNT(*) FROM batch2_contract_probe").fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM scrape_log WHERE source='contracttest'"
    ).fetchone()[0] == "error"
