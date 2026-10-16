"""research_evidence.py — what the scanner already stores about a listing, as
evidence for research_indicators.

Read-only, and only from data the app has collected: price history, the link
check, an earlier round of the same property, Spain's cadastre record, the map
outline and the site check. Nothing is fetched and nothing is guessed: a
category with no stored evidence stays "unknown" in the panel. The panel is
research only: it feeds no score, bid cap, alert or report.
"""
from __future__ import annotations

from datetime import date

from common import parse_dt, utcnow
from geo import _raw, position

# How exactly the pin is known, as research_indicators' location_confidence.
_PIN_CONFIDENCE = {"verified": 1.0, "cadastre": 1.0, "sale": 1.0, "street": 0.8,
                   "village": 0.5, "parish": 0.3, "municipality": 0.2}


def _day(value) -> date | None:
    when = parse_dt(value) if value else None
    return when.date() if when else None


def _record(kind: str, n: int, item: dict, observed: date, value: dict, source: str,
            location_confidence: float = 1.0) -> dict:
    return {"evidence_id": f"{kind}:{n}", "kind": kind, "source": source,
            "observed_on": observed.isoformat(), "lot_id": item["id"], "value": value,
            "confidence": 1.0, "location_confidence": location_confidence}


def _sale_events(db, item: dict, today: date) -> list[dict]:
    out = []
    last = None
    for row in db.execute("SELECT observed_at, price FROM price_history WHERE listing_id = ? "
                          "ORDER BY observed_at", (item["id"],)).fetchall():
        day = _day(row["observed_at"])
        if day is None or row["price"] is None or row["price"] == last:
            continue
        last = row["price"]
        out.append(_record("sale_event", len(out) + 1, item, day,
                           {"event_type": "price_observed", "amount": row["price"]},
                           "the scanner's price history"))
    check = _raw(item).get("link_check") or {}
    day = _day(check.get("at"))
    if day and check.get("availability") in ("withdrawn", "reserved", "ended", "available"):
        out.append(_record("sale_event", len(out) + 1, item, day,
                           {"event_type": check["availability"], "notes": check.get("why")},
                           "the scanner's link check"))
    er = item.get("earlier_round") or {}
    day = _day(er.get("ended"))
    if day and day <= today:
        # rounds.py infers this from a later listing of the same property; an
        # ended round is not proof that nobody bid, so it is not auction_failed.
        out.append(_record("sale_event", len(out) + 1, item, day,
                           {"event_type": "earlier_round_ended", "amount": er.get("price"),
                            "notes": f"earlier listing {er.get('id')}"},
                           "rounds.py (same property listed again)"))
    return out


def _document_claims(item: dict, today: date) -> list[dict]:
    """The notice's area against Spain's cadastre record, when both are stored."""
    raw = _raw(item)
    cat = raw.get("catastro") or {}
    notice_area = item.get("area_m2")
    if not cat or not notice_area:
        return []
    observed = _day(cat.get("at")) or _day(item.get("last_seen")) or today
    out = [_record("document_claim", 1, item, observed,
                   {"field": "area_m2", "claimed_value": round(float(notice_area)),
                    "document_type": "listing"}, item.get("source") or "listing")]
    official = cat.get("built_m2") if item.get("tipo") != "terreno" else cat.get("plot_m2")
    if official:
        out.append(_record("document_claim", 2, item, observed,
                           {"field": "area_m2", "claimed_value": round(float(official)),
                            "document_type": "cadastre"}, "Catastro"))
    return out


def _parcel(item: dict, today: date, pin: float) -> list[dict]:
    import outline
    found = outline.current(item)
    if not found:
        return []
    plot = found.get("plot") or {}
    value = {"plot_m2": plot.get("official_m2") or plot.get("m2"), "plot_source": plot.get("source"),
             "parcel_ref": plot.get("ref"), "summary": [f"{r['label']}: {r['value']}"
                                                       for r in outline.summary(found)]}
    return [_record("parcel_observation", 1, item, _day(found.get("at")) or today, value,
                    "map outline (cadastre / OpenStreetMap)", pin)]


def _habitat(item: dict, today: date, pin: float) -> list[dict]:
    check = _raw(item).get("site_check") or {}
    day = _day(check.get("at"))
    if not check or not day:
        return []
    value = {"protected_areas": check.get("protected") or [], "forest_share": check.get("forest_share"),
             "exact": check.get("exact", False)}
    return [_record("habitat_observation", 1, item, day, value,
                    "site check (Natura 2000 / ZNIEFF)", pin if check.get("exact") else min(pin, 0.5))]


def listing_evidence(db, item: dict, today: date | None = None) -> list[dict]:
    """research_indicators evidence records for one listing, from stored data."""
    today = today or utcnow().date()
    pos = position(item) or {}
    pin = _PIN_CONFIDENCE.get(pos.get("precision"), 0.0)
    return (_sale_events(db, item, today) + _document_claims(item, today)
            + _parcel(item, today, pin) + _habitat(item, today, pin))


def research_panel(db, item: dict, today: date | None = None) -> dict:
    """research_indicators.build_indicators for the listing's detail panel."""
    from research_indicators import build_indicators
    today = today or utcnow().date()
    subject = {"lot_id": item["id"], "price": item.get("price"), "area_m2": item.get("area_m2")}
    return build_indicators(subject, listing_evidence(db, item, today), today)
