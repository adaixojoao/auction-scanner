from datetime import datetime, timezone

import ics_export


def test_calendar_lines_are_escaped_and_folded():
    ev = ics_export.event({"id": "x:1", "country": "PT", "title": "Moradia, T3; com anexos — Guarda " * 3,
                           "date_end": "2026-01-15T10:30:00"},
                          what="Offer deadline", description="linha 1\nlinha 2", now=datetime(2026, 1, 1))
    text = ics_export.calendar([ev])
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))
    unfolded = text.replace("\r\n ", "")
    assert r"Moradia\, T3\; com anexos" in unfolded and r"DESCRIPTION:linha 1\nlinha 2" in unfolded
    assert "DTSTART:20260115T103000Z" in unfolded          # Lisbon is UTC in January


def test_times_with_an_offset_and_dates_without_a_time():
    madrid = ics_export.event({"id": "s:1", "country": "ES", "date_end": "2026-09-30T18:00:00+02:00"},
                              what="Auction ends")
    assert "DTSTART:20260930T160000Z" in madrid
    day = ics_export.event({"id": "z:1", "country": "DE", "date_end": "2026-11-12"}, what="Court hearing")
    assert "DTSTART;VALUE=DATE:20261112" in day and "DTEND;VALUE=DATE:20261113" in day
    assert ics_export.event({"id": "n:1", "date_end": None}, what="x") is None
    assert ics_export._parse("12/11/2026") == datetime(2026, 11, 12)
    assert ics_export._parse("2026-01-01T10:00:00Z").tzinfo == timezone.utc
