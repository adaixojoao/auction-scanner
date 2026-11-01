"""building_age.py — when the homes around a listing were built, against its country.

data/building_age_2021.csv (scripts/update_building_age.py) holds the 2021
census count of each NUTS 3 region's homes by period of construction
(Eurostat cens_21dwop_r3). An old stock means most homes nearby need
insulation, wiring or roof work before they meet today's standards; a young
one means the area has been building, so demand has been there. One line,
shown, not scored: it describes the region, not the house.
"""
from __future__ import annotations

import csv
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "building_age_2021.csv")
PERIODS = {"before_1946": "before 1946", "y1946_1980": "1946–80", "y1981_2000": "1981–2000",
           "since_2001": "since 2001"}
OLD, YOUNG = 1.2, 0.8                  # the region's share built before 1981, times the country's


@functools.lru_cache(maxsize=2)
def _table(path: str = PATH) -> dict[str, dict[str, int]]:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return {r["geo"]: {k: int(r[k]) for k in PERIODS} for r in csv.DictReader(f)}
    except (OSError, ValueError, KeyError):
        return {}


def _shares(row: dict[str, int]) -> dict[str, float]:
    total = sum(row.values())
    return {k: v / total for k, v in row.items()} if total else {}


def note(item: dict, path: str = PATH) -> str | None:
    import prices
    country = (item.get("country") or "PT").upper()
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    region = prices.population_outlook(country, place)
    table = _table(path)
    mine = _shares(table.get((region or {}).get("nuts3", "")) or {})
    national = _shares(table.get("EL" if country == "GR" else country) or {})
    if not region or not mine or not national:
        return None
    old = mine["before_1946"] + mine["y1946_1980"]
    country_old = national["before_1946"] + national["y1946_1980"]
    ratio = old / country_old if country_old else 1
    reading = (" — an older stock than the country's: most homes nearby need insulation, wiring or roof work "
               "to meet today's standards" if ratio >= OLD else
               " — a younger stock than the country's: the area has been building" if ratio <= YOUNG else "")
    split = ", ".join(f"{mine[k] * 100:.0f}% {label}" for k, label in PERIODS.items())
    return (f"homes in {region['name']} built {split} ({old * 100:.0f}% before 1981; country "
            f"{country_old * 100:.0f}%){reading} (Census 2021, Eurostat)")
