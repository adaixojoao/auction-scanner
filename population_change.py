"""population_change.py — how many people the place gained or lost since 2011.

data/population_change.csv (scripts/update_population_change.py) holds each
municipality's 2011 census count and its latest count from Eurostat's local
administrative units, with its country's change over the same units. A
town that keeps losing people loses buyers and tenants too; one that grows
fast gets dearer. Shown, not scored. (prices.population_outlook is the
projection to 2050; this is what already happened.)
"""
from __future__ import annotations

import csv
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "population_change.csv")
APART = 10.0              # percentage points from the country's change that are worth saying


@functools.lru_cache(maxsize=2)
def _table(path: str = PATH) -> dict[tuple[str, str], dict]:
    """(country, code) → row, and (country, "name:" + key) for names only one unit has."""
    from prices import place_key
    out: dict[tuple[str, str], dict] = {}
    names: dict[tuple[str, str], list[dict]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                row["change_pct"] = float(row["change_pct"])
                out[(row["country"], row["code"])] = row
                if row["name"]:
                    names.setdefault((row["country"], place_key(row["name"])), []).append(row)
    except (OSError, KeyError, ValueError):
        return {}
    for (country, key), rows in names.items():
        if len(rows) == 1:
            out[(country, "name:" + key)] = rows[0]
    return out


def note(item: dict, path: str = PATH) -> str | None:
    import geo
    from prices import place_key
    country = (item.get("country") or "PT").upper()
    table = _table(path)
    row = None
    if country == "FR":
        import surroundings
        found = (surroundings.current(item) or {}).get("found") or {}
        insee = (found.get("fr_risks") or {}).get("insee")
        row = table.get(("FR", insee)) if insee else None
    name = None if row else geo.municipality(item)
    row = row or (table.get((country, "name:" + place_key(name))) if name else None)
    national = table.get((country, country))
    if not row or not national:
        return None
    change, overall = row["change_pct"], national["change_pct"]
    judged = (" — shrinking much faster than the country" if change <= overall - APART
              else " — growing much faster than the country" if change >= overall + APART else "")
    return (f"people in {row['name']}: {int(row['pop_2011']):,} in 2011, {int(row['pop_latest']):,} in "
            f"{row['year']} ({change:+.1f}%; the country {overall:+.1f}%){judged} (Eurostat LAU)")
