"""The retry job reads again a source that stopped early (Fotocasa refused
halfway, 2026-10-29), not only one that failed, and only in the chosen countries.
Switched-off portals are never retried (2026-11-05)."""
from datetime import datetime, timedelta, timezone

from scheduler import PARTIAL_RETRY_AFTER_H, retry_names
from sources import Source

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
REGISTRY = {
    "fotocasa": Source("fotocasa", "ES", lambda *a, **k: 0, default=False),
    "citius": Source("citius", "PT", lambda *a, **k: 0, default=True),
    "spain": Source("spain", "ES", lambda *a, **k: 0, default=True),
    "fina": Source("fina", "HR", lambda *a, **k: 0, default=False),
    "pisos": Source("pisos", "ES", lambda *a, **k: 0, default=False),
}


def health(name, state, hours_ago=1):
    return {"source": name, "state": state, "last_run": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_a_partial_source_is_read_again_once_enough_time_has_passed():
    late = [health("spain", "partial", PARTIAL_RETRY_AFTER_H + 1)]
    soon = [health("spain", "partial", 1)]
    assert retry_names(late, REGISTRY, [], NOW) == ["spain"]
    assert retry_names(soon, REGISTRY, [], NOW) == []


def test_failed_sources_are_retried_and_ok_ones_are_not():
    got = retry_names([health("citius", "error"), health("pisos", "ok"), health("fotocasa", "dropped")],
                      REGISTRY, [], NOW)
    assert got == ["citius"]


def test_switched_off_portals_are_not_retried():
    got = retry_names([health("fotocasa", "error"), health("pisos", "partial", PARTIAL_RETRY_AFTER_H + 1),
                       health("citius", "error")], REGISTRY, [], NOW)
    assert got == ["citius"]


def test_only_the_chosen_countries_are_retried():
    got = retry_names([health("fina", "error"), health("citius", "error"), health("spain", "error")],
                      REGISTRY, ["PT"], NOW)
    assert got == ["citius"]
