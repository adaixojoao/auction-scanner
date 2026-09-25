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
# Parishes, where INE has them (the Porto and Lisbon areas, Setúbal, the
# Algarve, cities over 100,000 people): a village's price, not its municipality's.
PT_PARISH_FILE = os.path.join(HERE, "data", "pt_parish_prices.csv")
PARISH_COLUMNS = ("municipality", "parish", "eur_m2", "period", "source")


def place_key(name: str) -> str:
    """"Lisboa (Santa Maria Maior)" / "PORTO, Porto" / "Lagoa (Algarve)" → "lisboa" / "porto" / "lagoa"."""
    return re.split(r"[,(/]| - ", normalize(name))[0].strip()


# INE marks the island municipalities: "Calheta (R.A.M.)" is Madeira's,
# "Calheta (R.A.A.)" the Azores', and "Lagoa" is the Algarve's.
_REGION_MARKS = {"r.a.m.": "madeira", "r.a.a.": "acores"}
_AZORES_PLACES = ("acores", "azores", "sao miguel", "terceira", "faial", "pico", "flores", "graciosa",
                  "santa maria", "sao jorge", "corvo", "ponta delgada", "angra do heroismo", "horta")


def region_of_name(name: str) -> str:
    low = normalize(name)
    return next((region for mark, region in _REGION_MARKS.items() if mark in low), "continente")


def region_of_place(district: str | None) -> str | None:
    """"Ilha da Madeira" → madeira, "Ilha de São Miguel" → acores, a mainland
    district → continente; None when there is no district to go by."""
    if not district:
        return None
    low = normalize(district)
    if "madeira" in low and "sao joao da madeira" not in low or "porto santo" in low:
        return "madeira"
    if any(place in low for place in _AZORES_PLACES):
        return "acores"
    return "continente"


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
                name = row.get("municipality") or ""
                key = place_key(name)
                if key and value > 0:
                    found = (value, f"{row.get('source') or 'INE'} {row.get('period') or ''}".strip())
                    # Two municipalities share a name (Lagoa, Calheta): each is kept
                    # under its region too, and the plain name keeps the first one.
                    table[f"{key}|{region_of_name(name)}"] = found
                    table.setdefault(key, found)
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


def parish_names(name: str) -> list[str]:
    """"União das freguesias de Vila do Bispo e Raposeira" → ["vila do bispo", "raposeira"]."""
    low = normalize(name)
    low = re.sub(r"^uniao das freguesias de\s+|^uniao de freguesias de\s+", "", low)
    return [p.strip() for p in re.split(r",| e ", low) if p.strip()]


@functools.lru_cache(maxsize=4)
def _load_parishes(path: str, mtime: float) -> dict[str, tuple[float, str]]:
    table: dict[str, tuple[float, str]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    value = float(row["eur_m2"])
                except (KeyError, TypeError, ValueError):
                    continue
                town = place_key(row.get("municipality") or "")
                if not town or value <= 0:
                    continue
                found = (value, f"{row.get('source') or 'INE'} {row.get('period') or ''}, parish".strip())
                for name in parish_names(row.get("parish") or ""):
                    table.setdefault(f"{town}|{name}", found)
    except OSError:
        return {}
    return table


def parish_price(concelho: str | None, freguesia: str | None, path: str | None = None) -> tuple[float, str] | None:
    """(€/m², source) for the listing's parish, when INE has one."""
    if not concelho or not freguesia:
        return None
    path = path or PT_PARISH_FILE
    try:
        table = _load_parishes(path, os.path.getmtime(path))
    except OSError:
        return None
    town = place_key(concelho)
    for name in parish_names(freguesia) or [normalize(freguesia)]:
        found = table.get(f"{town}|{name}")
        if found:
            return found
    return None


def local_price(country: str, place: str | None, fallback: dict[str, dict[str, float]],
                district: str | None = None, parish: str | None = None) -> tuple[float, str] | None:
    """(€/m² of homes in that municipality, where the figure comes from), or None.
    `fallback` is the hand-made city table {country: {key: €/m²}}. `district`
    tells same-named municipalities apart (Calheta in Madeira or the Azores)."""
    if not place:
        return None
    key = place_key(place)
    if country == "PT":
        found = parish_price(place, parish)
        if found:
            return found
        table = pt_table()
        region = region_of_place(district)
        found = (table.get(f"{key}|{region}") if region else None) or table.get(key)
        if found:
            return found
    value = fallback.get(country, {}).get(key)
    return (value, "city estimate") if value else None
