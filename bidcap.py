"""bidcap.py — the most to bid for a listing, and why.

A waterfall from what the property is estimated to be worth down to the bid:

    estimated value − work − taxes and fees − risk and climate reserve − your margin = bid cap

Every input is one the scanner already has: the local price per m² and how
much of it this home is worth (scoring), the fees and the renovation bands
(costs), the climate grade (scoring.climate_score) and how exact the position
is (geo). Settings → Maximum bid is the buyer's policy. Nothing here is a
valuation: the local price is a municipal or parish median, so the result says
how confident it is and what it does not know. It never changes a bid you
type; Offers shows how far yours is from these figures.
"""
from __future__ import annotations

import math

import costs
from common import price_to_pay
from scoring import (TARGET_DEFAULTS, condition, local_price, local_value_factor, market_value_estimate,
                     property_kind)

ROUND = 100          # bids are rounded down to this


def policy(settings: dict | None) -> dict:
    """Settings → Maximum bid over config.DEFAULTS."""
    from config import DEFAULTS
    return {**DEFAULTS["bid_cap"], **((settings or {}).get("bid_cap") or {})}


def _eur(v: float) -> str:
    return f"€{v:,.0f}".replace(",", " ")


def _value(item: dict, kind: str | None, targets: dict, unknowns: list[str]) -> dict | None:
    """{"amount", "basis", "confidence", "note"}: what the property is estimated
    to be worth (a home), or what you are willing to pay for land (a plot)."""
    area = item.get("area_m2") or 0
    if kind == "home":
        if not area:
            unknowns.append("the floor area")
            return None
        if area > costs.MAX_HOME_M2:
            unknowns.append("the floor area (the area given is the plot's)")
            return None
        mv = market_value_estimate(item)
        if not mv:
            unknowns.append("a local price for this place")
            return None
        eur_m2, source = local_price(item)
        factor, why = local_value_factor(item)
        confidence = "low" if source == "city estimate" or condition(item) == "unknown" else "medium"
        counted = f", counted at {factor:.0%} ({', '.join(why)})" if why else ""
        return {"amount": mv * factor, "basis": "market", "confidence": confidence,
                "note": f"€{eur_m2:,.0f}/m² × {area:,.0f} m², the local median ({source}){counted}. "
                        "A median for the area, not a valuation of this property."}
    if kind == "rural_plot":
        if not area:
            unknowns.append("the land area")
            return None
        target = float(targets.get("rural_max_eur_m2") or TARGET_DEFAULTS["rural_max_eur_m2"])
        return {"amount": area * target, "basis": "target", "confidence": "policy",
                "note": f"{area / 10000:,.2f} ha at your land target of €{target:.2f}/m² (Settings → Filters). "
                        "There is no market price for land here: this is what you said land is worth to you."}
    unknowns.append("a value for this kind of property (only homes and rural land are estimated)")
    return None


def _largest_bid(item: dict, room: float, fee_factor: float) -> float:
    """The largest bid b, rounded down, with b + fee_factor × fees(b) ≤ room."""
    if room <= 0:
        return 0.0
    lo, hi = 0.0, room
    for _ in range(50):
        mid = (lo + hi) / 2
        if mid + fee_factor * costs.fees(item, mid) <= room:
            lo = mid
        else:
            hi = mid
    return float(math.floor(lo / ROUND) * ROUND)


def calculate_bid_cap(listing: dict, settings: dict | None, evidence: dict | None) -> dict:
    """The bid cap of one listing. `settings` is the config (bid_cap, filters);
    `evidence` is {"climate": scoring.climate_score(...), "location":
    geo.location_confidence(...)}. See the module's docstring."""
    p = policy(settings)
    targets = (settings or {}).get("filters") or {}
    ev = evidence or {}
    clim, loc = ev.get("climate") or {}, ev.get("location") or {}
    kind = listing.get("kind") or property_kind(listing)
    country = (listing.get("country") or "PT").upper()
    reasons: list[str] = []
    unknowns: list[str] = []

    value = _value(listing, kind, targets, unknowns)
    budget = float(p.get("max_all_in") or 0)
    work = costs.renovation(listing) if kind == "home" else None
    if kind == "home" and not work and "the floor area" not in unknowns:
        unknowns.append("the cost of the work (no floor area to work it out from)")
    reno_low, reno_high = (work["low"], work["high"]) if work else (0.0, 0.0)
    if kind == "home" and condition(listing) == "unknown":
        unknowns.append("the condition (the description does not say; work is assumed)")
    if country == "PT":
        unknowns.append("the taxable value (VPT): IMT is charged on it when it is above the price")

    exact_location = loc.get("level") == "exact"
    exact_climate = clim.get("confidence") == "exact"
    if not exact_location:
        unknowns.append(f"the exact location ({(loc.get('label') or 'not known').lower()})")
    if not exact_climate:
        unknowns.append("the climate at the exact position")

    out = {"estimated_market_value": round(value["amount"]) if value and value["basis"] == "market" else None,
           "market_value_confidence": value["confidence"] if value else "unknown",
           "basis": value, "budget": budget or None,
           "taxes_and_fees_low": None, "taxes_and_fees_high": None,
           "renovation_low": reno_low if work else None, "renovation_high": reno_high if work else None,
           "risk_reserve": None, "climate_adjustment": 0.0, "all_in_low": None, "all_in_high": None,
           "recommended_bid": None, "absolute_max_bid": None, "price_to_pay": price_to_pay(listing) or None,
           "waterfall": [], "reasons": reasons, "unknowns": unknowns,
           "note": "Estimate from local medians, typical rates and your settings, not a valuation."}

    if p.get("require_exact") and not (exact_location and exact_climate):
        reasons.append("No bid cap: Settings → Maximum bid asks for an exact location and climate data first.")
        return out
    base = value["amount"] if value else budget
    if not base:
        reasons.append("No bid cap: nothing to work from" + (f" ({unknowns[0]} is missing)." if unknowns else "."))
        return out

    adviser = float((p.get("adviser_reserve_by_country") or {}).get(country, p.get("adviser_reserve_eur") or 0))
    land = 0.0
    if kind == "rural_plot":
        ha = (listing.get("area_m2") or 0) / 10000
        land = ha * float(p.get("rural_reserve_per_ha") or 0) + float(p.get("rural_reserve_fixed") or 0)
    reserve = base * float(p.get("contingency_pct") or 0) / 100 + adviser + land
    climate_adj = 0.0
    m = clim.get("bid_multiplier", 1.0)
    if value and exact_climate and m < 1:
        climate_adj = value["amount"] * (1 - m)
        reasons.append(f"Climate {clim.get('grade')}: {1 - m:.0%} of the value held back.")
    elif value and not exact_climate and clim.get("grade") in ("caution", "poor"):
        reasons.append(f"Climate {clim.get('grade')} from an approximate position: not held back until "
                       "the location is exact.")
    margin = base * float(p.get("margin_pct") or 0) / 100 if value else 0.0

    room_max = (value["amount"] - reno_low - reserve - climate_adj) if value else budget - reno_low - reserve
    room_rec = (value["amount"] - reno_high - reserve - climate_adj - margin) if value else budget - reno_high - reserve
    budget_binds = False
    if value and budget:
        budget_binds = budget - reno_high < room_rec
        room_max, room_rec = min(room_max, budget - reno_low), min(room_rec, budget - reno_high)
    absolute = _largest_bid(listing, room_max, 1.0)
    recommended = _largest_bid(listing, room_rec, costs.FEES_HIGH_FACTOR)
    if recommended >= absolute:            # no margin and no range in the work: stay below the maximum
        recommended = max(0.0, absolute - ROUND)
        budget_binds = False

    fees_low = costs.fees(listing, recommended) if recommended else 0.0
    fees_high = fees_low * costs.FEES_HIGH_FACTOR
    out.update({"taxes_and_fees_low": round(fees_low), "taxes_and_fees_high": round(fees_high),
                "risk_reserve": round(reserve), "climate_adjustment": round(climate_adj),
                "all_in_low": round(recommended + fees_low + reno_low),
                "all_in_high": round(recommended + fees_high + reno_high),
                "recommended_bid": recommended, "absolute_max_bid": absolute})

    rows = [{"label": "Estimated value" if value and value["basis"] == "market"
             else "Your land target" if value else "Your all-in budget", "amount": base,
             "note": value["note"] if value else "Settings → Maximum bid: no value to work from, so your budget"}]
    if work:
        rows.append({"label": "Renovation allowance", "amount": -reno_high, "note": work["note"] + " (high end)"})
    rows.append({"label": "Taxes and fees", "amount": -fees_high,
                 "note": f"at the recommended bid, with {costs.FEES_HIGH_FACTOR - 1:.0%} on top of the typical figure"})
    rows.append({"label": "Risk reserve", "amount": -reserve,
                 "note": f"{p.get('contingency_pct') or 0}% contingency + {_eur(adviser)} for a lawyer or adviser"
                         + (f" + {_eur(land)} for the land (conservation, clearing)" if land else "")})
    if climate_adj:
        rows.append({"label": "Climate reserve", "amount": -climate_adj, "note": f"grade {clim.get('grade')}"})
    if margin:
        rows.append({"label": "Your margin", "amount": -margin, "note": f"{p.get('margin_pct')}% below the value"})
    left = sum(r["amount"] for r in rows) - recommended
    if left > 0.5:
        rows.append({"label": "Held back by your budget" if budget_binds else "Rounded down", "amount": -left,
                     "note": f"all-in at most {_eur(budget)}" if budget_binds
                     else f"to €{ROUND}, and below the absolute maximum"})
    rows.append({"label": "Bid cap (recommended)", "amount": recommended, "note": "", "total": True})
    out["waterfall"] = [{**r, "amount": round(r["amount"])} for r in rows]

    if not absolute:
        reasons.append("On these figures no bid leaves room: the work, fees and reserves take the whole value.")
    pay = out["price_to_pay"]
    if pay and absolute and pay > absolute:
        reasons.append(f"The price to pay ({_eur(pay)}) is above the absolute maximum ({_eur(absolute)}).")
    elif pay and recommended and pay > recommended:
        reasons.append(f"The price to pay ({_eur(pay)}) is above the recommended bid ({_eur(recommended)}).")
    if value and value["basis"] == "market" and pay and pay > value["amount"]:
        reasons.append(f"Priced above its estimated value ({_eur(value['amount'])}).")
    reasons.append(f"Absolute maximum {_eur(absolute)}: break-even with the low end of the work, and no margin.")
    return out


def evidence_for(item: dict) -> dict:
    """Climate and location for calculate_bid_cap, from what the listing already has."""
    import geo
    from scoring import climate_score
    return {"climate": climate_score(item.get("climate"), item.get("kind")),
            "location": geo.location_confidence(item)}


def for_item(item: dict, settings: dict | None) -> dict:
    return calculate_bid_cap(item, settings, evidence_for(item))


def compare(cap: dict, bid: float | None) -> dict | None:
    """How your bid sits against the recommended and absolute figures, or None
    when there is no bid or no cap to compare."""
    if not bid or not cap.get("absolute_max_bid"):
        return None
    rec, abs_ = cap.get("recommended_bid") or 0, cap["absolute_max_bid"]
    if bid > abs_:
        return {"level": "above_max", "text": f"Your bid is {_eur(bid - abs_)} above the absolute maximum."}
    if rec and bid > rec:
        return {"level": "above_rec", "text": f"Your bid is {_eur(bid - rec)} above the recommended bid."}
    if rec and bid < rec:
        return {"level": "below_rec", "text": f"Your bid is {_eur(rec - bid)} below the recommended bid."}
    return {"level": "at_rec", "text": "Your bid matches the recommended bid."}


def record_offer(db, carta_log_id: int, cap: dict, bid: float | None) -> None:
    """Keep the calculator's figures with the offer, and a note when yours differs."""
    note = (compare(cap, bid) or {}).get("text") if bid and cap.get("absolute_max_bid") else None
    if note and note.startswith("Your bid matches"):
        note = None
    db.execute("UPDATE carta_log SET bid_cap_recommended = ?, bid_cap_absolute = ?, bid_cap_note = ? WHERE id = ?",
               (cap.get("recommended_bid"), cap.get("absolute_max_bid"), note, carta_log_id))
    db.commit()
