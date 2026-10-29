"""One slow source must not hold up the scan (placsp sat 40 minutes on one
feed, 2026-10-29): past source_time_limit_min every request fails at once,
what was read is kept, and the run is partial."""
import pytest

import common
from common import ScraperSession, SourceTimeLimit, source_deadline
from conftest import FakeResponse
from sources import Source, run_source


def test_a_request_past_the_deadline_fails_at_once(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(common.time, "monotonic", lambda: now[0])
    seen = []
    monkeypatch.setattr("requests.Session.request", lambda self, m, url, **kw: seen.append(kw["timeout"]))
    with source_deadline(60):
        ScraperSession(timeout=180).get("https://example.org/a")
        now[0] += 61
        with pytest.raises(SourceTimeLimit):
            ScraperSession(timeout=180).get("https://example.org/b")
    assert seen == [60]                       # one slow request cannot run past the limit
    ScraperSession(timeout=180).get("https://example.org/c")   # no limit outside a source
    assert seen[-1] == 180


def test_a_source_that_ran_out_of_time_keeps_its_count_as_partial(db, monkeypatch):
    def slow(db, **_):
        common._SOURCE_DEADLINE[0] = 0        # the limit passes while it runs
        return 7
    result = run_source(db, Source("slowtest", "PT", slow), max_price=1, config={"source_time_limit_min": 30})
    assert result["count"] == 7 and result["status"] == "ok" and "time limit" in result["message"]
    scope = db.execute("SELECT scope FROM scrape_log WHERE source = 'slowtest'").fetchone()[0]
    assert scope == "partial"


def test_a_source_stopped_by_the_limit_is_recorded_not_raised(db):
    def stuck(db, **_):
        raise SourceTimeLimit("over")
    result = run_source(db, Source("stucktest", "PT", stuck), max_price=1)
    assert result["status"] == "error" and "time limit" in result["message"]


def test_boe_public_returns_a_count(db, fake_http):
    from sources.es import scrape_boe_public
    fake_http(lambda m, url, kw: FakeResponse("", status=404))     # no BOE those days
    assert scrape_boe_public(db) == 0
