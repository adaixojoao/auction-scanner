"""
Sale deadlines as calendar events (.ics), for Outlook, Google Calendar or
Apple Calendar: the end of an online auction, a court hearing, the deadline
for offers. Each event reminds you a day before.

Sale times are the local time of the sale's country (a French hearing at 14h
is 14h in Paris), so they are converted to UTC with that country's time zone.
A date without a time becomes an all-day event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common import parse_date_dmy, utcnow

TIME_ZONES = {
    "PT": "Europe/Lisbon", "ES": "Europe/Madrid", "FR": "Europe/Paris", "IT": "Europe/Rome",
    "DE": "Europe/Berlin", "NL": "Europe/Amsterdam", "BE": "Europe/Brussels", "HR": "Europe/Zagreb",
    "GR": "Europe/Athens", "RO": "Europe/Bucharest", "PL": "Europe/Warsaw", "CY": "Asia/Nicosia",
}

WHAT = {"lawyer": "Court hearing", "hearing": "Court hearing", "online": "Auction ends",
        "formal": "Sale date", "letter": "Offer deadline"}


def _escape(text) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545)."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode("utf-8")
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode("utf-8"))
            current = b""
        current += encoded
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def _parse(value) -> datetime | None:
    """Like common.parse_dt, but a time without an offset stays naive: it is
    the sale country's local time, not UTC."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        iso = parse_date_dmy(text)
        return datetime.fromisoformat(iso) if iso else None


def _to_utc(dt: datetime, country: str) -> datetime:
    if dt.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.replace(tzinfo=ZoneInfo(TIME_ZONES.get(country, "Europe/Lisbon")))
        except Exception:   # no time-zone data (Windows without tzdata): treat as UTC
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def event(item: dict, *, what: str, description: str = "", location: str = "",
          now: datetime | None = None) -> list[str] | None:
    """VEVENT lines for one listing's sale date, or None if it has no date."""
    raw_end = item.get("date_end")
    dt = _parse(raw_end)
    if not dt:
        return None
    country = item.get("country") or "PT"
    stamp = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    has_time = "T" in str(raw_end) and (dt.hour or dt.minute)
    if has_time:
        start = _to_utc(dt, country)
        when = [f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%SZ}"]
    else:
        when = [f"DTSTART;VALUE=DATE:{dt:%Y%m%d}", f"DTEND;VALUE=DATE:{dt + timedelta(days=1):%Y%m%d}"]
    title = f"{what} ({country}): {item.get('title') or item['id']}"   # Windows shows no flag emoji
    lines = ["BEGIN:VEVENT", f"UID:{item['id']}-date@auction-scanner", f"DTSTAMP:{stamp}", *when,
             f"SUMMARY:{_escape(title[:150])}"]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if item.get("url"):
        lines.append(f"URL:{item['url']}")
    lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P1D", f"DESCRIPTION:{_escape(title[:150])}",
              "END:VALARM", "END:VEVENT"]
    return lines


def calendar(events: list[list[str]]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Auction Scanner//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:Auction deadlines"]
    for ev in events:
        lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
