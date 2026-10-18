"""presentation.py — the short summaries one Listings row shows.

Display only: every figure and state here comes from the module that owns it.

- Price basis: common.price_to_pay and costs.selected_acquisition.
- Estimated all-in: costs.selected_planning (the planning figure, with its
  known-subtotal case when repairs are not priced). No new rate, band or reserve.
- Checks: checklist.build over checklist._effective_row, so a stored
  "verified" whose inputs changed reads "needs rechecking", never verified.
  A fact the notice states (scoring._occupation, scoring.condition) is shown
  as source-reported, never as verified.
- Deadline: the stored date_end with its own precision. A date without a time
  stays a date; a time without an offset is the sale country's local time
  (ics_export.TIME_ZONES, as the calendar export reads it).

Nothing here hides, ranks or gates a listing: db.load_listings and the
existing checks still decide that.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

# The checks a row summarises, in this order, by checklist category or key.
CHECK_AREAS = (("title", "Title and charges"), ("occupancy", "Possession"),
               ("land_access", "Legal access"), ("condition", "Condition"))
_CONDITION_TEXT = {"heavy": "heavy work", "some": "some work", "good": "good condition"}


def _amount(value) -> float | None:
    """A finite amount ≥ 0, else None. A bool is not an amount."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _raw(item: dict) -> dict:
    raw = item.get("raw_json")
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def price(item: dict) -> dict:
    """What the price cell says: one labelled figure, and the others as secondary lines.

    {"amount", "kind", "label", "note", "secondary": [{"label", "amount", "note"}]}.
    amount is None when no purchase figure is established; it is never derived.
    """
    import source_validation
    from common import _charges
    from costs import selected_acquisition
    acq = selected_acquisition(item)
    listed = _amount(item.get("price"))
    floor = _amount(item.get("min_price"))
    bid = _amount(item.get("current_bid"))
    secondary = []
    if acq["kind"] == "unusable":
        ref = _amount(acq.get("reference_amount"))
        if ref:
            secondary.append({"label": "Listed figure", "amount": ref, "note": "Not a purchase price"})
        return {"amount": None, "kind": "unusable", "label": "Not a purchase price",
                "note": acq.get("label") or "", "secondary": secondary}
    pay = _amount(acq.get("current_amount"))
    if not pay:
        return {"amount": None, "kind": "unpublished", "label": "Price not published",
                "note": "No purchase figure is stored for this listing.", "secondary": []}
    charges = _amount(_charges(item)) or 0
    kind = acq.get("current_kind")
    if kind == "minimum" and floor != pay - charges:
        kind = "asking"           # a floor under 20% of the base is a bidding step (price_to_pay)
    if kind == "current_bid":
        label, note = "Current bid", "Not a final purchase price: bidding may go higher."
    elif kind == "minimum":
        label, note = "Minimum offer", "The lowest accepted figure, not a final purchase price."
    elif source_validation.asking_price(item.get("source")):
        kind, label, note = "asking", "Asking price", "Advertised; the agreed price can differ."
    else:
        kind, label, note = "base", "Base value", "A reference value, not an obtainable purchase price."
    if kind == "current_bid" and floor and floor != bid:
        secondary.append({"label": "Minimum offer", "amount": floor, "note": ""})
    if kind in ("current_bid", "minimum") and listed and listed not in (bid, floor):
        secondary.append({"label": "Base value", "amount": listed, "note": "Reference, not a purchase price"})
    if charges:
        secondary.append({"label": "Included debts that stay with the property", "amount": charges, "note": ""})
    if acq["kind"] == "predicted":
        secondary.append({"label": "Predicted closing price", "amount": _amount(acq.get("amount")),
                          "note": "An estimate, not a bid placed"})
    return {"amount": pay, "kind": kind, "label": label, "note": note, "secondary": secondary}


# The estimate's basis in the price cell's words (a predicted close keeps costs' own label).
_BASIS = {"current_bid": "the current bid", "minimum": "the minimum offer", "base": "the base value",
          "asking": "the asking price"}


def all_in(item: dict, price_kind: str | None = None) -> dict:
    """What the estimate cell says: costs.selected_planning, labelled as an estimate.

    {"low", "high", "known_subtotal", "repairs_priced", "basis_label", "missing", "excludes",
     "headline"}. "missing" is what this listing's estimate lacks; "excludes" what none include.
    An unknown figure stays None: it is never shown as €0.
    """
    from costs import floor_resolution, repair_unresolved, selected_acquisition, selected_planning
    from scoring import categorize, condition, property_kind
    acq = selected_acquisition(item)
    empty = {"low": None, "high": None, "known_subtotal": None, "repairs_priced": False,
             "basis_label": "", "missing": [], "excludes": []}
    if (item.get("category") or categorize(item)) != "imoveis":
        return {**empty, "headline": "No cost estimate for this kind of item"}
    if acq["kind"] == "unusable":
        return {**empty, "headline": "Not established: the listed figure is not a purchase price"}
    out = selected_planning(item, bid=acq["amount"] or None, basis=acq["label"])
    if not out:
        return {**empty, "headline": "Not established: there is no price to work from"}
    est = out.get("estimate") or {}
    range_ = est.get("all_in") or {}
    kind = property_kind(item)
    missing = []
    if out.get("incomplete") or (kind == "home" and repair_unresolved(item)):
        missing.append("Repairs not priced: floor area not established")
    elif kind == "home" and floor_resolution(item)["source"] == "guess":
        missing.append("Floor area is an educated guess")
    elif kind != "home":
        missing.append("No works estimate for this kind of property")
    if kind == "home" and condition(item) == "unknown":
        missing.append("Condition not stated")
    # What no listing's estimate includes (costs.estimate's note), said once and apart.
    excludes = ["fire insurance"]
    if (item.get("country") or "PT").upper() == "PT":
        excludes.append("IMT on a taxable value (VPT) above the price")
    low, high = _amount(range_.get("low")), _amount(range_.get("high"))
    priced = low is not None and high is not None and high >= low
    return {"low": round(low) if priced else None, "high": round(high) if priced else None,
            "known_subtotal": round(est["total"]) if _amount(est.get("total")) is not None else None,
            "repairs_priced": priced,
            "basis_label": f"Estimate at {(acq['kind'] != 'predicted' and _BASIS.get(price_kind)) or est.get('basis') or acq['label']}",
            "missing": missing, "excludes": excludes, "headline": ""}


def checks(item: dict, rows: dict | None, cfg: dict | None) -> dict:
    """Recorded checks for title, possession, legal access and condition.

    rows are checklist._effective_row results (checklist.stored_for). States:
    verified, not_applicable, concern, needs_recheck, source_reported, unverified.
    """
    import checklist
    from scoring import _occupation, condition
    built = checklist.build(item, rows or {}, cfg)
    out = []
    for area, label in CHECK_AREAS:
        mine = [x for x in built["items"] if x["category"] == area or x["key"] == area]
        if not mine:
            continue
        statuses = [x["status"] for x in mine]
        if "concern" in statuses:
            state, text = "concern", "Concern recorded"
        elif all(s == "not_applicable" for s in statuses):
            state, text = "not_applicable", "Not applicable (recorded by you)"
        elif all(s in checklist.DONE for s in statuses):
            state, text = "verified", "Verified by you"
        elif any(x["needs_recheck"] for x in mine):
            state, text = "needs_recheck", "Needs rechecking: listing inputs changed"
        else:
            state, text = "unverified", "No verification recorded"
            done = sum(1 for s in statuses if s in checklist.DONE)
            if done:
                text = f"{done} of {len(mine)} checked by you"
            reported = None
            if area == "occupancy":
                reported = _occupation(item)
                reported = f"Source reports {reported}" if reported else None
            elif area == "condition":
                seen = _CONDITION_TEXT.get(condition(item))
                reported = f"Notice or photos suggest {seen}" if seen else None
            if reported and not done:
                state, text = "source_reported", f"{reported}; not verified"
        out.append({"area": area, "label": label, "state": state, "text": text})
    return {"items": out, "concerns": sum(1 for x in out if x["state"] == "concern")}


def deadline(item: dict, now: datetime | None = None) -> dict:
    """The stored deadline with its precision.

    {"precision": "instant" | "local" | "date" | "unknown" | "invalid",
     "date", "time", "timezone", "instant", "days"}. A date-only value gets no
    time; "days" counts calendar days from today in the sale's time zone.
    This is a label: expiry and eligibility stay with common.effective_end.
    """
    from ics_export import TIME_ZONES, _parse
    raw = item.get("date_end")
    none = {"precision": "unknown", "date": None, "time": None, "timezone": None, "instant": None, "days": None}
    if raw in (None, ""):
        return none
    dt = _parse(raw)
    if dt is None:
        return {**none, "precision": "invalid"}
    if dt.year == 9999:
        return none
    zone_name = TIME_ZONES.get((item.get("country") or "PT").upper(), "Europe/Lisbon")
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(zone_name)
    except Exception:  # noqa: BLE001 — no time-zone data (Windows without tzdata)
        zone = None
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(zone).date() if zone else now.date()
    has_time = "T" in str(raw) or " " in str(raw).strip()
    if dt.tzinfo is not None:
        local = dt.astimezone(zone) if zone else dt
        return {"precision": "instant", "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
                "timezone": zone_name if zone else local.strftime("UTC%z"),
                "instant": dt.astimezone(timezone.utc).isoformat(), "days": (local.date() - today).days}
    if has_time and (dt.hour, dt.minute, dt.second) != (0, 0, 0):
        return {"precision": "local", "date": dt.date().isoformat(), "time": dt.strftime("%H:%M"),
                "timezone": zone_name if zone else None,
                "instant": dt.replace(tzinfo=zone).astimezone(timezone.utc).isoformat() if zone else None,
                "days": (dt.date() - today).days}
    return {**none, "precision": "date", "date": dt.date().isoformat(), "timezone": zone_name if zone else None,
            "days": (dt.date() - today).days}


def thumbnail(item: dict) -> str | None:
    """One small picture: the first copy kept on this PC, else the cover (https only)."""
    from urllib.parse import quote

    from photos import _url
    names = _raw(item).get("kept_photos")
    if isinstance(names, list) and any(isinstance(n, str) for n in names):
        return f"/api/kept-photo?id={quote(str(item.get('id') or ''), safe='')}&n=0"
    return _url(item.get("image_url"))


def summary(item: dict, check_rows: dict | None, cfg: dict | None, now: datetime | None = None) -> dict:
    basis = price(item)
    return {"price": basis, "all_in": all_in(item, basis["kind"]), "checks": checks(item, check_rows, cfg),
            "deadline": deadline(item, now), "thumbnail": thumbnail(item)}
