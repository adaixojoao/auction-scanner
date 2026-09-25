"""
rounds.py — the same property on sale again after an earlier sale of it ended.

A court sale that finds no buyer, or whose buyer does not pay, comes back: the
same case and property in a new round (a new e-leilões auction, the case listed
again on Citius), often cheaper or open to offers. Having seen the earlier
round end is a strong hint that the seller will take less.

Rounds are matched on the court case number (Citius, e-leilões; Spain's
expediente) and then on the property, because one case can sell several:
areas within 15 % when both are known, else titles sharing most words.
Citius and e-leilões can show the same round with different dates, so a sale
on the other site only counts as an earlier round if this listing appeared
after it ended; on the same site, another listing is always another round.
The scanner only knows rounds that ended since it was installed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from common import effective_end, normalize, parse_dt, price_to_pay as pay

AREA_TOLERANCE = 0.15
TITLE_OVERLAP = 0.6
_COLUMNS = "id, source, title, concelho, area_m2, price, current_bid, min_price, date_end, first_seen, raw_json"


def case_key(raw: dict) -> str | None:
    """"165/10.3TBMRA, Juízo de Moura" → "165/10.3tbmra"."""
    proc = raw.get("processo") or raw.get("expediente") or raw.get("caseNumber")
    if not proc:
        return None
    key = re.sub(r"\s+", "", str(proc).split(",")[0]).lower()
    return key if len(key) >= 5 and re.search(r"\d", key) else None


def _raw(value) -> dict:
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def index(db) -> dict[str, list[dict]]:
    """Every listing with a case number, by case: {case: [listing, …]}."""
    out: dict[str, list[dict]] = {}
    rows = db.execute(f"SELECT {_COLUMNS} FROM listings "
                      "WHERE raw_json LIKE '%\"processo\"%' OR raw_json LIKE '%\"expediente\"%' "
                      "OR raw_json LIKE '%\"caseNumber\"%'")
    for r in rows:
        item = dict(r)
        key = case_key(_raw(item.pop("raw_json")))
        if key:
            out.setdefault(key, []).append(item)
    return out


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", normalize(title)) if len(w) > 2}


def same_property(a: dict, b: dict) -> bool:
    area_a, area_b = a.get("area_m2") or 0, b.get("area_m2") or 0
    if area_a and area_b:
        return abs(area_a - area_b) <= AREA_TOLERANCE * max(area_a, area_b)
    wa, wb = _words(a.get("title") or ""), _words(b.get("title") or "")
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= TITLE_OVERLAP


def land_in_case(item: dict, idx: dict[str, list[dict]], now: datetime, kind_of) -> list[dict]:
    """Land sold in the same case and round as this listing, still on sale: a
    house and the plot next to it (Lage: a €7,500 house, its field for €500).
    `kind_of` is scoring.property_kind (not imported here: scoring imports prices)."""
    key = case_key(_raw(item.get("raw_json")))
    if not key:
        return []
    out = []
    for other in idx.get(key, []):
        if other["id"] == item["id"] or other["source"] != item.get("source"):
            continue
        ended = effective_end(other.get("date_end"))
        if ended is not None and ended <= now:
            continue
        if kind_of({**other, "country": item.get("country")}) in ("rural_plot", "urban_plot"):
            out.append({"id": other["id"], "price": pay(other) or None, "area_m2": other.get("area_m2")})
    return sorted(out, key=lambda lot: lot["price"] or 0)


def earlier_round(item: dict, idx: dict[str, list[dict]], now: datetime) -> dict | None:
    """The latest earlier round of this listing's property that has ended, or None:
    {"id", "source", "ended", "price", "cheaper_pct"}."""
    key = case_key(_raw(item.get("raw_json")))
    if not key:
        return None
    mine = effective_end(item.get("date_end"))
    seen = parse_dt(item.get("first_seen"))
    found = []
    for other in idx.get(key, []):
        if other["id"] == item["id"]:
            continue
        ended = effective_end(other.get("date_end"))
        if ended is None or ended > now or (mine is not None and ended >= mine):
            continue
        relisted = seen is not None and seen >= ended - timedelta(days=1)
        if other["source"] != item.get("source") and not relisted:
            continue                      # possibly the same round shown on two sites
        if same_property(item, other):
            found.append((ended, other))
    if not found:
        return None
    ended, other = max(found, key=lambda pair: pair[0])
    before, now_price = pay(other), pay(item)
    cheaper = round((before - now_price) / before * 100, 1) if before and now_price and now_price < before else None
    return {"id": other["id"], "source": other["source"], "ended": ended.date().isoformat(),
            "price": before or None, "cheaper_pct": cheaper}
