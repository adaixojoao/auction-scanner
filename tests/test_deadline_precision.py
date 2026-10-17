"""Exact timezone-aware deadlines are not extended to end-of-day."""
from datetime import datetime, timezone

import pytest

from common import days_left, effective_end, parse_dt


@pytest.mark.parametrize("value", [
    "2026-10-16",
    "16/10/2026",
    "2026-10-16T00:00:00",
    datetime(2026, 10, 16),
])
def test_date_only_and_legacy_midnight_keep_whole_day(value):
    assert effective_end(value) == datetime(2026, 10, 16, 23, 59, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [
    "2026-10-16T00:00:00Z",
    "2026-10-16T00:00:00+00:00",
    "2026-07-16T00:00:00+01:00",
    "2026-10-16T00:00:00-01:00",
    "2026-10-16T15:30:00+01:00",
    datetime(2026, 10, 16, tzinfo=timezone.utc),
])
def test_explicit_timezone_is_an_exact_instant(value):
    actual = effective_end(value)
    expected = parse_dt(value)
    assert actual == expected
    assert actual.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize("value", [
    None, "", "not a date", "2026-02-30",
    "9999-01-01", "9999-01-01T00:00:00Z",
])
def test_unknown_or_invalid_deadline_has_no_countdown(value):
    assert effective_end(value) is None
    assert days_left(value) is None


def test_explicit_midnight_is_already_past_an_hour_later():
    now = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)
    assert days_left("2026-10-16T00:00:00Z", now=now) == pytest.approx(-1 / 24)


def test_equivalent_offsets_have_identical_countdowns():
    now = datetime(2026, 7, 15, 22, tzinfo=timezone.utc)
    local = days_left("2026-07-16T00:00:00+01:00", now=now)
    utc = days_left("2026-07-15T23:00:00Z", now=now)
    assert local == utc == pytest.approx(1 / 24)


def test_nonmidnight_naive_timestamp_keeps_legacy_utc():
    assert effective_end("2026-10-16T15:30:00") == datetime(
        2026, 10, 16, 15, 30, tzinfo=timezone.utc)


def test_one_microsecond_after_midnight_is_not_a_whole_day():
    assert effective_end("2026-10-16T00:00:00.000001") == datetime(
        2026, 10, 16, microsecond=1, tzinfo=timezone.utc)
