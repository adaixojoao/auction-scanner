"""guesses.py — educated guesses that may enter a score, and what they cost.

Court and tax notices (Citius, e-leilões, the Finanças sales) seldom state the
floor area and seldom give coordinates. Scored only on stated facts, such a
sale never reaches the top, however good it is. So two gaps may be filled with
a guess:

- the floor area: the house's outline on the map with its levels, else the
  lower quartile of the floors the scanner itself has seen for homes of the
  same type (T3, V2…) in the same district, else in the country, else the
  ground the house covers on the map (one level: at least that);
- the beach from a parish, village or town pin: the distance from the pin
  plus how far the house can be from it (SPREAD_KM), the worst case.

The rules, so a guess cannot pass for a fact:
- conservative: a lower quartile, a worst case, never a middle or a best;
- named in the reasons, with what it was worked out from;
- each costs GUESS_FACTOR of the rank, so a sale known for certain beats one
  guessed at the same numbers;
- guesses rank, facts bid: the bid cap and the evaluator still need a stated
  floor (bidcap._value, costs.floor_resolution).

Nothing is invented: the floors are ones the scanner has seen, and a guess is
made only once MIN_FLOORS of them agree on the type and place.
"""
from __future__ import annotations

import re
import statistics

from common import footprint_figure, land_figure, normalize

GUESS_FACTOR = 0.95            # each guess an item is ranked on costs 5% of its rank
MIN_FLOORS = 8                 # floors seen before their lower quartile is used
FLOOR_RANGE = (20.0, 400.0)    # a stored figure outside this is not one home's floor
# How far the house can be from a pin of this precision (geo.py), in km: a
# village's houses lie within about 1.5 km of its pin, a parish's within about
# 3 km (the average Portuguese parish is about 30 km²), a municipality's
# anywhere in it.
SPREAD_KM = {"village": 1.5, "parish": 3.0, "municipality": 10.0}

_TYPOLOGY = re.compile(r"\b[TVF]\s?([0-9])(?:\s?\+\s?\d)?\b")
_ROOMS = re.compile(r"\b([1-9])\s+(?:quartos|dormit[oó]rios|habitaciones|chambres)\b", re.I)


def typology(item: dict) -> int | None:
    """The bedrooms a notice names (T3, V2, F4, "3 quartos"), from the title
    first; None when it names none or several different ones."""
    for text in (item.get("title"), item.get("description")):
        text = str(text or "")
        found = {int(n) for n in _TYPOLOGY.findall(text)} | {int(n) for n in _ROOMS.findall(text)}
        if len(found) == 1:
            return found.pop()
        if found:
            return None
    return None


def _home_type(item: dict) -> str:
    import outline
    return "flat" if outline.is_flat(item) else "house"


def observed_floors(rows) -> dict:
    """{(country, district key, "house"/"flat", bedrooms): (lower quartile m², count)}
    from listings that state a floor area, with the whole country under the
    district key "". Only groups of MIN_FLOORS or more."""
    groups: dict[tuple, list[float]] = {}
    for r in rows:
        r = dict(r)
        area = float(r.get("area_m2") or 0)
        if not FLOOR_RANGE[0] <= area <= FLOOR_RANGE[1]:
            continue
        n = typology(r)
        if n is None:
            continue
        text = f"{r.get('title') or ''}\n{r.get('description') or ''}"
        if land_figure(text, area) or footprint_figure(text, area):
            continue
        country = (r.get("country") or "PT").upper()
        kind = _home_type(r)
        for district in (normalize(r.get("district")), ""):
            groups.setdefault((country, district, kind, n), []).append(area)
    out = {}
    for key, values in groups.items():
        if len(values) >= MIN_FLOORS:
            out[key] = (round(statistics.quantiles(values, n=4)[0]), len(values))
    return out


def floor_guess(item: dict) -> dict | None:
    """{"m2", "basis"}: a conservative floor area for a home whose notice gives
    none, or None when there is nothing honest to go on."""
    import outline
    drawn = outline.current(item) or {}
    house = drawn.get("house") or {}
    if house.get("floor_m2"):
        return {"m2": float(house["floor_m2"]),
                "basis": f"the house's outline on the map, {house['levels']} levels"}
    n = typology(item)
    index = item.get("floor_market") or {}
    if n is not None and index:
        country = (item.get("country") or "PT").upper()
        kind = _home_type(item)
        district = normalize(item.get("district"))
        places = ([((country, district, kind, n), item.get("district"))] if district else []) + \
            [((country, "", kind, n), country)]
        for key, where in places:
            found = index.get(key)
            if found:
                label = f"T{n} {'flats' if kind == 'flat' else 'houses'}"
                return {"m2": float(found[0]),
                        "basis": f"{label} in {where}: the lower quartile of {found[1]} floors the scanner has seen"}
    if house.get("m2"):
        return {"m2": float(house["m2"]), "basis": "the ground the house covers on the map (one level at least)"}
    return None


def beach_worst_case(item: dict) -> dict | None:
    """{"km", "spread", "precision"} for a home whose pin is a village, parish
    or town: the farthest the sea can be from the house. None for an exact pin
    or without a beach distance."""
    import geo
    pos = geo.position(item) or {}
    spread = SPREAD_KM.get(pos.get("precision"))
    km = (item.get("beach") or {}).get("km")
    if spread is None or km is None:
        return None
    return {"km": round(float(km) + spread, 1), "spread": spread, "precision": pos["precision"]}


def charge(rank: float, guesses: list[str]) -> float:
    """The rank after the guesses it rests on."""
    return rank * GUESS_FACTOR ** len(guesses) if rank > 0 else rank


def reason(guesses: list[str]) -> str:
    each = f"{round((1 - GUESS_FACTOR) * 100):.0f}%"
    return (f"ranked on {len(guesses)} educated guess{'es' if len(guesses) > 1 else ''} "
            f"(−{each} each; confirm before bidding): " + "; ".join(guesses))
