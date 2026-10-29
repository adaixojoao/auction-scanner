"""The retry job reads again a source that stopped early (Fotocasa refused
halfway, 2026-10-29), not only one that failed, and only in the chosen countries."""
from datetime import datetime, timedelta, timezone

from scheduler import PARTIAL_RETRY_AFTER_H, retry_names
from sources import Source

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
REGISTRY = {n: Source(n, c, lambda *a, **k: 0) for n, c in
            (("fotocasa", "ES"), ("citius", "PT"), ("fina", "HR"), ("pisos", "ES"))}


def health(name, state, hours_ago=1):
    return {"source": name, "state": state, "last_run": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_a_partial_source_is_read_again_once_enough_time_has_passed():
    late = [health("fotocasa", "partial", PARTIAL_RETRY_AFTER_H + 1)]
    soon = [health("fotocasa", "partial", 1)]
    assert retry_names(late, REGISTRY, [], NOW) == ["fotocasa"]
    assert retry_names(soon, REGISTRY, [], NOW) == []


def test_failed_sources_are_retried_and_ok_ones_are_not():
    got = retry_names([health("citius", "error"), health("pisos", "ok"), health("fotocasa", "dropped")],
                      REGISTRY, [], NOW)
    assert got == ["citius"]


def test_only_the_chosen_countries_are_retried():
    got = retry_names([health("fina", "error"), health("citius", "error")], REGISTRY, ["PT", "ES"], NOW)
    assert got == ["citius"]
