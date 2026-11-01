"""short_lets.py — the rules on holiday lets where a listing is.

data/short_let_rules.csv holds each country's rule ("*") and the cities with
their own, every row with the law it comes from and the day it was checked.
There is no EU register of these rules, so the file is kept by hand: check the
laws again when one of them changes. Shown, not scored.
"""
from __future__ import annotations

import csv
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "short_let_rules.csv")
ALIASES = {"athens": "athina", "lisbon": "lisboa", "oporto": "porto", "salonica": "thessaloniki"}


@functools.lru_cache(maxsize=2)
def _rules(path: str = PATH) -> dict[tuple[str, str], dict]:
    from prices import place_key
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return {(r["country"], "*" if r["place"] == "*" else place_key(r["place"])): r for r in csv.DictReader(f)}
    except (OSError, KeyError):
        return {}


def notes(item: dict, path: str = PATH) -> list[str]:
    import geo
    from prices import place_key
    country = (item.get("country") or "PT").upper()
    rules = _rules(path)
    out = []
    name = geo.municipality(item) or item.get("concelho")
    if name:
        key = place_key(name)
        city = rules.get((country, ALIASES.get(key, key)))
        if city:
            out.append(f"holiday lets in {city['place']}: {city['rule']} ({city['law']})")
    national = rules.get((country, "*"))
    if national:
        out.append(f"holiday lets: {national['rule']} ({national['law']})")
    return out
