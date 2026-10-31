"""incomes.py — what people earn where a listing is, against its country.

data/incomes.csv (scripts/update_incomes.py) holds each country's official
figure by municipality: France and Spain the median income per consumption
unit (INSEE Filosofi, INE ADRH), Portugal the median gross declared income
per tax household (INE), Italy the mean declared income per taxpayer (MEF).
The measures differ, so a place is only compared with its own country. One
line, shown, not scored: it says who the buyers and tenants nearby are.
"""
from __future__ import annotations

import csv
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "incomes.csv")
HIGH, LOW = 1.2, 0.8                  # times the country's figure
MEASURES = {
    "FR": ("median living standard", "INSEE Filosofi"),
    "ES": ("median income per consumption unit", "INE household income atlas"),
    "PT": ("median gross income per tax household", "INE"),
    "IT": ("mean declared income per taxpayer", "MEF, IRPEF"),
}


@functools.lru_cache(maxsize=2)
def _table(path: str = PATH) -> dict[tuple[str, str], dict]:
    """(country, code) → row, and (country, "name:" + key) for names only one place has."""
    from prices import place_key
    out: dict[tuple[str, str], dict] = {}
    names: dict[tuple[str, str], list[dict]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                row["income"] = float(row["income"])
                out[(row["country"], row["code"])] = row
                if row["level"] == "municipality" and row["name"]:
                    names.setdefault((row["country"], place_key(row["name"])), []).append(row)
    except (OSError, KeyError, ValueError):
        return {}
    for (country, key), rows in names.items():
        if len(rows) == 1:
            out[(country, "name:" + key)] = rows[0]
    return out


def _place(item: dict, country: str, table: dict) -> dict | None:
    if country == "FR":
        import surroundings
        found = (surroundings.current(item) or {}).get("found") or {}
        insee = (found.get("fr_risks") or {}).get("insee")
        if insee:
            # A commune too small to publish: its département's
            dep = insee[:3] if insee.startswith("97") else insee[:2]
            return table.get(("FR", insee)) or table.get(("FR", dep))
    import geo
    from prices import place_key
    name = geo.municipality(item)
    return table.get((country, "name:" + place_key(name))) if name else None


def note(item: dict, path: str = PATH) -> str | None:
    country = (item.get("country") or "PT").upper()
    if country not in MEASURES:
        return None
    table = _table(path)
    row, national = _place(item, country, table), table.get((country, country))
    if not row or not national or not national["income"]:
        return None
    what, source = MEASURES[country]
    ratio = row["income"] / national["income"]
    where = row["name"] or "the area"
    if row["level"] == "department":
        where = f"département {row['code']} (the commune is too small to publish)"
    judged = " — well above" if ratio >= HIGH else " — well below" if ratio <= LOW else ""
    return (f"{what} €{row['income']:,.0f} a year in {where}, {ratio:.2f}× the country's "
            f"€{national['income']:,.0f}{judged} ({source} {row['year']})")
