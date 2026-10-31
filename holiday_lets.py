"""holiday_lets.py — how many licensed holiday lets a Portuguese parish has.

data/pt_holiday_lets.csv (scripts/update_holiday_lets.py) counts Turismo de
Portugal's register of alojamento local (RNAL) per parish: lets, beds, and the
lets registered in the last two years. For the Invest goal it says whether
short lets are proven where the home is, or untested there. It is shown, not
scored: many lets prove demand and also mean competition, and a town can stop
new licences.
"""
from __future__ import annotations

import csv
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pt_holiday_lets.csv")


@functools.lru_cache(maxsize=4)
def _table(path: str = PATH) -> dict[str, dict]:
    """{municipality key: {"lets", "beds", "recent", "period", "parishes": {parish key: (lets, beds, recent, name)}}}."""
    from prices import parish_names, place_key
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                town = place_key(row.get("municipality") or "")
                if not town:
                    continue
                lets, beds, recent = (int(row.get(k) or 0) for k in ("lets", "beds", "lets_recent"))
                entry = out.setdefault(town, {"lets": 0, "beds": 0, "recent": 0, "period": row.get("period"),
                                              "parishes": {}})
                entry["lets"] += lets
                entry["beds"] += beds
                entry["recent"] += recent
                for part in parish_names(row.get("parish") or ""):
                    entry["parishes"][part] = (lets, beds, recent, row.get("parish"))
    except (OSError, ValueError):
        return {}
    return out


def note(item: dict, path: str = PATH) -> str | None:
    """One line on holiday lets where a Portuguese home is, or None outside
    Portugal or when the file is missing."""
    if (item.get("country") or "PT").upper() != "PT":
        return None
    import geo
    from prices import parish_names, place_key
    table = _table(path)
    town = item.get("concelho") or geo.municipality(item)
    entry = table.get(place_key(town)) if town and table else None
    if not entry:
        return None
    for part in parish_names(item.get("freguesia") or ""):
        hit = entry["parishes"].get(part)
        if hit:
            lets, beds, recent, name = hit
            if not lets:
                break
            return (f"{lets} licensed holiday lets in {name} ({beds} beds; {recent} new in 2 years) "
                    f"— short lets are proven here (Turismo de Portugal RNAL, {entry['period']}; "
                    "new licences depend on the town's rules)")
    if not entry["lets"]:
        return f"no licensed holiday lets in {town} — short lets are untested here (Turismo de Portugal RNAL)"
    return (f"{entry['lets']} licensed holiday lets in {town} ({entry['beds']} beds; {entry['recent']} new in "
            f"2 years) (Turismo de Portugal RNAL, {entry['period']}; new licences depend on the town's rules)")


# ─── Every EU country: nights booked on the platforms, by region ─────────
NIGHTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "platform_nights.csv")


@functools.lru_cache(maxsize=4)
def _nights(path: str = NIGHTS_PATH) -> dict[str, dict]:
    """{nuts3: {"year", "nights", "prev_nights"}} (scripts/update_platform_nights.py)."""
    out = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out[row["nuts3"]] = {"year": row["year"], "nights": int(row["nights"]),
                                     "prev_nights": int(row["prev_nights"]) if row.get("prev_nights") else None}
    except (OSError, ValueError, KeyError):
        return {}
    return out


@functools.lru_cache(maxsize=4)
def _country_median(country: str, path: str = NIGHTS_PATH) -> float | None:
    """The median nights per resident of a country's regions."""
    import statistics
    import prices
    try:
        regions = prices._load_regions(prices.EUROPOP_FILE, os.path.getmtime(prices.EUROPOP_FILE))
    except OSError:
        return None
    rates = [n["nights"] / int(regions[code]["pop_2019"]) for code, n in _nights(path).items()
             if code.startswith(country) and code in regions and int(regions[code].get("pop_2019") or 0) > 0]
    return statistics.median(rates) if len(rates) >= 3 else None


def region_note(item: dict, path: str = NIGHTS_PATH) -> str | None:
    """Short-let nights booked in the listing's NUTS 3 region, per resident and
    against the country's other regions; None when the region is unknown."""
    import prices
    country = (item.get("country") or "PT").upper()
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    region = prices.population_outlook(country, place)
    found = _nights(path).get((region or {}).get("nuts3", ""))
    if not region or not found or not region.get("pop_2019"):
        return None
    per_resident = found["nights"] / region["pop_2019"]
    median = _country_median(country[:2] if country != "GR" else "EL", path)
    level = ""
    if median:
        ratio = per_resident / median
        level = (" — busy for the country" if ratio >= 2 else " — quiet for the country" if ratio <= 0.5
                 else " — about average for the country")
    trend = ""
    if found.get("prev_nights"):
        change = (found["nights"] - found["prev_nights"]) / found["prev_nights"] * 100
        trend = f", {'+' if change >= 0 else ''}{change:.0f}% on the year before"
    return (f"{found['nights']:,} nights booked on Airbnb, Booking and Expedia in {region['name']} in "
            f"{found['year']} ({per_resident:.1f} per resident{trend}){level} (Eurostat, experimental)")
