"""
prices.py — local prices of homes per m², for "X% below local prices".

Portugal: the median price per m² of homes sold in each municipality, from
Statistics Portugal (INE), in data/pt_home_prices.csv. The file is public data
and is refreshed with `python scripts/update_prices.py` on a PC that can reach
ine.pt (see that script). Other countries, and Portuguese places the file does
not have: the city figures in scoring.MARKET_PRICE_PER_M2.
"""
from __future__ import annotations

import csv
import functools
import os
import re

from common import normalize

HERE = os.path.dirname(os.path.abspath(__file__))
PT_FILE = os.path.join(HERE, "data", "pt_home_prices.csv")
COLUMNS = ("municipality", "eur_m2", "period", "source")


def place_key(name: str) -> str:
    """"Lisboa (Santa Maria Maior)" / "PORTO, Porto" / "Lagoa (Algarve)" → "lisboa" / "porto" / "lagoa"."""
    return re.split(r"[,(/]| - ", normalize(name))[0].strip()


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict[str, tuple[float, str]]:
    table: dict[str, tuple[float, str]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    value = float(row["eur_m2"])
                except (KeyError, TypeError, ValueError):
                    continue
                key = place_key(row.get("municipality") or "")
                if key and value > 0:
                    # Two municipalities share a name (Lagoa, Calheta): keep the first,
                    # the file lists the mainland first.
                    table.setdefault(key, (value, f"{row.get('source') or 'INE'} {row.get('period') or ''}".strip()))
    except OSError:
        return {}
    return table


def pt_table(path: str | None = None) -> dict[str, tuple[float, str]]:
    """{municipality key: (€/m², "INE 2.º trimestre de 2026")}; empty if the file is missing."""
    path = path or PT_FILE
    try:
        return _load(path, os.path.getmtime(path))
    except OSError:
        return {}


def local_price(country: str, place: str | None, fallback: dict[str, dict[str, float]]) -> tuple[float, str] | None:
    """(€/m² of homes in that municipality, where the figure comes from), or None.
    `fallback` is the hand-made city table {country: {key: €/m²}}."""
    if not place:
        return None
    key = place_key(place)
    if country == "PT":
        found = pt_table().get(key)
        if found:
            return found
    value = fallback.get(country, {}).get(key)
    return (value, "city estimate") if value else None
