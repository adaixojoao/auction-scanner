"""analytics.py — how your offers did, by source and strategy.

carta_log holds each letter or bid you sent; this module summarises them with
the listing they belong to. outcomes.py is different: that one records what
public auctions closed at on the sites. Nothing here leaves the PC, and nothing
changes scoring.weights automatically — feedback_for_scoring() only lists
patterns for you to read.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from statistics import median

from common import parse_dt

# Outcomes that count as a finished offer (not still waiting).
DECIDED = ("won", "lost", "cancelled", "expired")
# Information requests use "answered"; they are not bids.
OFFER_OUTCOMES = ("pending", "won", "lost", "cancelled", "expired")

LOST_REASONS = (
    "outbid", "minimum_not_met", "withdrawn", "title", "occupancy", "access",
    "condition", "climate", "cost", "diligence", "other",
)
LOST_REASON_LABELS = {
    "outbid": "Outbid",
    "minimum_not_met": "Below the minimum",
    "withdrawn": "I withdrew",
    "title": "Title / charges",
    "occupancy": "Occupancy",
    "access": "Access / visit",
    "condition": "Condition of the building",
    "climate": "Climate / location risk",
    "cost": "All-in cost too high",
    "diligence": "Due diligence found a blocker",
    "other": "Other",
}

SCORE_BANDS = ((85, "85–100"), (70, "70–84"), (50, "50–69"), (0, "0–49"))
# A sale this far from the all-in cost is a typo, not a return to annualise.
_SALE_MULTIPLE = (0.25, 4)
_MONTHS_HELD_MAX = 600


def rent_yield_pct(monthly, all_in) -> float | None:
    """Rent received as a percentage of the all-in cost a year, before income tax
    and the costs you still pay. None when either figure is missing."""
    if not monthly or not all_in or all_in <= 0 or monthly <= 0:
        return None
    return round(1200 * float(monthly) / float(all_in), 1)


def sale_return(sale_price, all_in, months) -> dict:
    """{"sale_multiple", "sale_annual_pct"} from a sale against the all-in cost.

    sale_annual_pct is the yearly rate of that multiple over the months held.
    It stays None when the months are missing, or the multiple is too far from
    1 to be a real sale (a typo in one of the figures)."""
    out = {"sale_multiple": None, "sale_annual_pct": None}
    if not sale_price or not all_in or all_in <= 0 or sale_price <= 0:
        return out
    multiple = float(sale_price) / float(all_in)
    out["sale_multiple"] = round(multiple, 2)
    if not months or months < 1:
        return out
    if not _SALE_MULTIPLE[0] <= multiple <= _SALE_MULTIPLE[1]:
        return out
    out["sale_annual_pct"] = round((multiple ** (12 / float(months)) - 1) * 100, 1)
    return out


def _band(score: float | None) -> str:
    if score is None:
        return "unknown"
    for lo, label in SCORE_BANDS:
        if score >= lo:
            return label
    return "0–49"


def _bid_value_band(bid: float | None, value: float | None) -> str:
    if not bid or not value or value <= 0:
        return "unknown"
    r = bid / value
    if r > 1:
        return ">100% of value"
    if r > 0.85:
        return "85–100%"
    if r > 0.70:
        return "70–85%"
    if r > 0.50:
        return "50–70%"
    return "<50%"


def _rows(db) -> list[dict]:
    """One dict per carta_log offer (is_offer = 1), with listing fields when known."""
    from scoring import market_value_estimate, property_kind
    listings = {r["id"]: dict(r) for r in db.execute("SELECT * FROM listings")}
    out = []
    for r in db.execute("SELECT * FROM carta_log WHERE COALESCE(is_offer, 1) = 1 ORDER BY created_at"):
        row = dict(r)
        item = listings.get(row.get("listing_id") or "")
        kind = (item.get("kind") if item else None) or (property_kind(item) if item else None)
        score = item.get("score") if item else None
        if score is None and item:
            # Older rows may not have a cached score on the listing load path.
            try:
                from scoring import score as score_fn
                score = score_fn(item)[0]
            except Exception:  # noqa: BLE001
                score = None
        bid = row.get("bid_amount")
        value = None
        if item:
            value = market_value_estimate(item) or item.get("price")
        first = parse_dt(item.get("first_seen")) if item else None
        sent = parse_dt(row.get("sent_date") or row.get("created_at"))
        days = None
        if first and sent:
            days = max(0, (sent - first).total_seconds() / 86400)
        climate = None
        if item:
            try:
                from scoring import climate_score
                climate = climate_score(item.get("climate"), kind)
            except Exception:  # noqa: BLE001
                climate = None
        out.append({
            "log_id": row["id"],
            "listing_id": row.get("listing_id"),
            "country": (item or {}).get("country") or row.get("country") or "?",
            "source": (item or {}).get("source") or "?",
            "kind": kind or "unknown",
            "method": row.get("method") or "?",
            "outcome": row.get("outcome") or "pending",
            "bid": bid,
            "winning_bid": row.get("winning_bid"),
            "all_in_cost": row.get("all_in_cost"),
            "monthly_rent": row.get("monthly_rent"),
            "sale_price": row.get("sale_price"),
            "months_held": row.get("months_held"),
            "rent_yield_pct": rent_yield_pct(row.get("monthly_rent"), row.get("all_in_cost")),
            **sale_return(row.get("sale_price"), row.get("all_in_cost"), row.get("months_held")),
            "lost_reason": row.get("lost_reason") or "",
            "diligence_blocker": row.get("diligence_blocker"),
            "occupancy_found": row.get("occupancy_found") or "",
            "title_found": row.get("title_found") or "",
            "access_found": row.get("access_found") or "",
            "condition_after": row.get("condition_after") or "",
            "score": score,
            "score_band": _band(score),
            "bid_value_band": _bid_value_band(bid, value),
            "value": value,
            "days_to_submit": days,
            "climate_grade": (climate or {}).get("grade") or "unknown",
            "sent_date": row.get("sent_date") or (row.get("created_at") or "")[:10],
        })
    return out


def _rate(rows: list[dict], key: str) -> list[dict]:
    """Win rate (and counts) grouped by `key`."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "?")].append(r)
    out = []
    for name, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        decided = [x for x in items if x["outcome"] in DECIDED]
        won = sum(1 for x in decided if x["outcome"] == "won")
        out.append({
            "key": name, "offers": len(items),
            "pending": sum(1 for x in items if x["outcome"] == "pending"),
            "won": won, "lost": sum(1 for x in decided if x["outcome"] == "lost"),
            "cancelled": sum(1 for x in decided if x["outcome"] == "cancelled"),
            "expired": sum(1 for x in decided if x["outcome"] == "expired"),
            "decided": len(decided),
            "win_rate": round(100 * won / len(decided), 1) if decided else None,
        })
    return out


def _returns(rows: list[dict]) -> dict:
    """What won purchases actually let for or sold for. Nothing here changes the score."""
    won = [r for r in rows if r["outcome"] == "won"]
    rents = [r["rent_yield_pct"] for r in won if r.get("rent_yield_pct") is not None]
    sales = [r["sale_annual_pct"] for r in won if r.get("sale_annual_pct") is not None]
    shown = [r for r in won if r.get("rent_yield_pct") is not None or r.get("sale_multiple") is not None]
    return {
        "won": len(won),
        "with_rent": len(rents),
        "with_sale": len(sales),
        "median_rent_yield_pct": round(median(rents), 1) if rents else None,
        "median_sale_annual_pct": round(median(sales), 1) if sales else None,
        "rows": [{"country": r["country"], "kind": r["kind"], "source": r["source"],
                  "rent_yield_pct": r.get("rent_yield_pct"), "sale_multiple": r.get("sale_multiple"),
                  "sale_annual_pct": r.get("sale_annual_pct"), "months_held": r.get("months_held")}
                 for r in shown[:30]],
    }


def summary(db) -> dict:
    """Everything the Outcomes page and its API show."""
    from db import source_health
    rows = _rows(db)
    decided = [r for r in rows if r["outcome"] in DECIDED]
    pending = [r for r in rows if r["outcome"] == "pending"]
    days = [r["days_to_submit"] for r in rows if r["days_to_submit"] is not None]
    bid_vs = []
    for r in rows:
        if r["bid"] and r.get("all_in_cost"):
            bid_vs.append({"bid": r["bid"], "all_in": r["all_in_cost"],
                           "ratio": round(r["all_in_cost"] / r["bid"], 2) if r["bid"] else None})
        elif r["outcome"] == "won" and r["bid"] and r.get("value"):
            # No recorded all-in: compare to the estimate at send time's value.
            bid_vs.append({"bid": r["bid"], "all_in": None, "value": r["value"]})

    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["outcome"] in ("lost", "cancelled") and r["lost_reason"]:
            reasons[r["lost_reason"]] += 1

    health = {h["source"]: h for h in source_health(db)}
    by_source = _rate(rows, "source")
    for s in by_source:
        h = health.get(s["key"]) or {}
        s["health"] = h.get("state") or "never run"
        s["listings"] = h.get("listings") or 0
        s["useful"] = s["won"] + s["pending"]   # still live or won

    return {
        "counts": {
            "offers": len(rows),
            "pending": len(pending),
            "won": sum(1 for r in decided if r["outcome"] == "won"),
            "lost": sum(1 for r in decided if r["outcome"] == "lost"),
            "cancelled": sum(1 for r in decided if r["outcome"] == "cancelled"),
            "expired": sum(1 for r in decided if r["outcome"] == "expired"),
            "no_response": len(pending),   # still waiting = no response yet
        },
        "win_rate": round(100 * sum(1 for r in decided if r["outcome"] == "won") / len(decided), 1)
                    if decided else None,
        "median_days_to_submit": round(median(days), 1) if days else None,
        "by_country": _rate(rows, "country"),
        "by_source": by_source,
        "by_method": _rate(rows, "method"),
        "by_kind": _rate(rows, "kind"),
        "by_score_band": _rate(rows, "score_band"),
        "by_bid_value_band": _rate(rows, "bid_value_band"),
        "lost_reasons": [{"key": k, "label": LOST_REASON_LABELS.get(k, k), "count": n}
                         for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])],
        "bid_vs_all_in": bid_vs[:50],
        "feedback": feedback_for_scoring(rows),
        "lost_reason_labels": [[k, LOST_REASON_LABELS[k]] for k in LOST_REASONS],
        "returns": _returns(rows),
    }


def feedback_for_scoring(rows: list[dict] | None = None, db=None) -> list[dict]:
    """Patterns worth a human look. Does not change scoring.weights."""
    rows = rows if rows is not None else _rows(db)
    tips = []
    lost = [r for r in rows if r["outcome"] in ("lost", "cancelled")]
    if not lost and not rows:
        return [{"text": "No offers logged yet. Mark outcomes on Offers → Sent as they come in."}]

    # Diligence blockers among finished offers
    blocked = [r for r in lost if r.get("diligence_blocker") == 1]
    if len(blocked) >= 2:
        by_climate: dict[str, int] = defaultdict(int)
        for r in blocked:
            by_climate[r.get("climate_grade") or "unknown"] += 1
        hot = {g: n for g, n in by_climate.items() if g in ("caution", "poor") and n >= 2}
        if hot:
            bits = ", ".join(f"{n} with climate {g}" for g, n in sorted(hot.items()))
            tips.append({"text": f"Due diligence blocked {len(blocked)} offers ({bits}). "
                                 "Worth reviewing whether the climate grade should weigh more — "
                                 "weights are not changed automatically."})
        else:
            tips.append({"text": f"Due diligence blocked {len(blocked)} of {len(lost)} finished losses/withdrawals. "
                                 "Check the lost-reason list for title, occupancy or access patterns."})

    # High-score losses
    high = [r for r in lost if (r.get("score") or 0) >= 70]
    if len(high) >= 3:
        reasons = defaultdict(int)
        for r in high:
            reasons[r.get("lost_reason") or "unset"] += 1
        top = ", ".join(f"{LOST_REASON_LABELS.get(k, k)} ×{n}" for k, n in
                        sorted(reasons.items(), key=lambda kv: -kv[1])[:3])
        tips.append({"text": f"{len(high)} offers scoring 70+ were lost or cancelled ({top}). "
                             "The score may be optimistic on those axes — review, do not auto-tune."})

    # Bid-to-value: wins clustered low
    won = [r for r in rows if r["outcome"] == "won" and r["bid_value_band"] != "unknown"]
    if len(won) >= 3:
        low = sum(1 for r in won if r["bid_value_band"] in ("<50%", "50–70%"))
        if low >= max(2, len(won) * 0.6):
            tips.append({"text": f"Most wins ({low}/{len(won)}) were at under 70% of estimated value. "
                                 "That matches a deep-discount strategy; keep the score pointed at cheap homes."})

    # Source: many offers, zero wins, source still "ok"
    by_src = _rate(rows, "source")
    for s in by_src:
        if s["offers"] >= 5 and s["decided"] >= 3 and s["won"] == 0:
            tips.append({"text": f"Source {s['key']}: {s['offers']} offers, no wins yet "
                                 f"({s['lost']} lost, {s['cancelled']} cancelled). "
                                 "Useful as a volume check against Sources health — not a reason to drop it alone."})

    recorded = [r for r in rows if r["outcome"] == "won"
                and (r.get("rent_yield_pct") is not None or r.get("sale_annual_pct") is not None)]
    if recorded and len(recorded) < 3:
        n = len(recorded)
        tips.append({"text": f"{n} won {'purchase has' if n == 1 else 'purchases have'} a rent or a sale "
                             "on record. A few more, and this page can put a figure on them together. "
                             "Weights are not changed from it."})
    if len(recorded) >= 3:
        rents = [r["rent_yield_pct"] for r in recorded if r.get("rent_yield_pct") is not None]
        sales = [r["sale_annual_pct"] for r in recorded if r.get("sale_annual_pct") is not None]
        bits = []
        if rents:
            bits.append(f"rent received averages {median(rents):.1f}% of the all-in cost a year, "
                        "before tax and the costs you still pay")
        if sales:
            bits.append(f"sales that can be annualised average {median(sales):.1f}% a year")
        tips.append({"text": f"{len(recorded)} won purchases have a rent or a sale on record"
                             + (f" — {'; '.join(bits)}" if bits else "")
                             + ". The score's yield is a town average after running costs, so it is "
                             "not this figure. Weights are not changed from it."})

    if not tips:
        tips.append({"text": "Not enough decided offers yet for a scoring review. "
                             "Log lost reasons and diligence blockers as you go. "
                             "When a purchase lets or sells, record the rent or the sale price too."})
    return tips


def export_csv(db) -> str:
    """Anonymised CSV of your offers: no titles, contacts, letters or notes."""
    rows = _rows(db)
    buf = io.StringIO()
    fields = ["country", "source", "kind", "method", "outcome", "score_band", "bid_value_band",
              "climate_grade", "bid", "winning_bid", "all_in_cost", "lost_reason",
              "diligence_blocker", "occupancy_found", "title_found", "access_found",
              "condition_after", "days_to_submit", "sent_date",
              "monthly_rent", "sale_price", "months_held", "rent_yield_pct", "sale_annual_pct"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in fields})
    return buf.getvalue()


def update_detail(db, log_id: int, data: dict) -> dict:
    """Save the optional outcome fields on one carta_log row. ValueError on bad input."""
    row = db.execute("SELECT * FROM carta_log WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        raise ValueError("not found")
    fields = {}
    for key in ("winning_bid", "all_in_cost", "monthly_rent", "sale_price", "months_held"):
        if key in data and data[key] is not None and data[key] != "":
            try:
                fields[key] = float(data[key])
            except (TypeError, ValueError) as e:
                raise ValueError(f"{key} must be a number") from e
            if fields[key] < 0:
                raise ValueError(f"{key} must be ≥ 0")
            if key == "months_held" and fields[key] > _MONTHS_HELD_MAX:
                raise ValueError(f"months held looks too long (above {_MONTHS_HELD_MAX})")
        elif key in data:
            fields[key] = None
    if "lost_reason" in data:
        reason = str(data.get("lost_reason") or "").strip()
        if reason and reason not in LOST_REASONS:
            raise ValueError(f"lost_reason must be one of {', '.join(LOST_REASONS)}")
        fields["lost_reason"] = reason or None
    if "diligence_blocker" in data:
        v = data["diligence_blocker"]
        if v in (None, ""):
            fields["diligence_blocker"] = None
        elif v in (True, False, 0, 1, "0", "1"):
            fields["diligence_blocker"] = 1 if v in (True, 1, "1") else 0
        else:
            raise ValueError("diligence_blocker must be true, false or empty")
    for key in ("occupancy_found", "title_found", "access_found", "condition_after"):
        if key in data:
            fields[key] = (str(data.get(key) or "").strip()[:80] or None)
    if not fields:
        return dict(row)
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE carta_log SET {sets} WHERE id = ?", (*fields.values(), log_id))
    db.commit()
    return dict(db.execute("SELECT * FROM carta_log WHERE id = ?", (log_id,)).fetchone())
