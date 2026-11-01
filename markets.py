"""markets.py — the country's mortgage rates and house price trend.

data/mortgage_rates.csv and data/house_prices.csv (scripts/update_markets.py)
hold the ECB's average rate on new house-purchase loans in each euro-area
country and the 12-month Euribor, and Eurostat's house price index. Two
lines, shown, not scored: they describe the country's market, not the house.
"""
from __future__ import annotations

import csv
import functools
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MORTGAGES = os.path.join(HERE, "data", "mortgage_rates.csv")
HOUSE_PRICES = os.path.join(HERE, "data", "house_prices.csv")
LOAN, YEARS = 100_000, 30
NAMES = {"AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany",
         "DK": "Denmark", "EE": "Estonia", "ES": "Spain", "FI": "Finland", "FR": "France", "GR": "Greece",
         "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LT": "Lithuania", "LU": "Luxembourg",
         "LV": "Latvia", "MT": "Malta", "NL": "the Netherlands", "PL": "Poland", "PT": "Portugal",
         "RO": "Romania", "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "CH": "Switzerland",
         "NO": "Norway", "IS": "Iceland"}


@functools.lru_cache(maxsize=2)
def _rates(path: str = MORTGAGES) -> dict[str, list[tuple[str, float]]]:
    """{country: [(month, rate)] oldest first}; "EA" the euro area, "EURIBOR" the 12-month Euribor."""
    out: dict[str, list[tuple[str, float]]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                out.setdefault(r["country"], []).append((r["month"], float(r["rate"])))
    except (OSError, KeyError, ValueError):
        return {}
    return {k: sorted(v) for k, v in out.items()}


@functools.lru_cache(maxsize=2)
def _prices(path: str = HOUSE_PRICES) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return {r["country"]: r for r in csv.DictReader(f)}
    except OSError:
        return {}


def monthly_payment(rate_pct: float, loan: float = LOAN, years: int = YEARS) -> float:
    r, n = rate_pct / 100 / 12, years * 12
    return loan / n if r == 0 else loan * r / (1 - (1 + r) ** -n)


def _change(series: list[tuple[str, float]]) -> str:
    if len(series) < 13:
        return ""
    diff = series[-1][1] - series[-13][1]
    return f", {'up' if diff > 0 else 'down'} {abs(diff):.2f} points in a year" if abs(diff) >= 0.05 else \
        ", about the same as a year ago"


def mortgage_note(item: dict, path: str = MORTGAGES) -> str | None:
    country = (item.get("country") or "PT").upper()
    rates = _rates(path)
    mine = rates.get(country)
    if not mine:
        return None
    month, rate = mine[-1]
    area = rates.get("EA")
    euro = f"; euro area {area[-1][1]:.2f}%" if area else ""
    euribor = rates.get("EURIBOR")
    variable = (f"; 12-month Euribor {euribor[-1][1]:.2f}% ({euribor[-1][0]}{_change(euribor)}), the index most "
                "variable-rate loans follow" if euribor else "")
    return (f"new mortgages in {NAMES.get(country, country)} cost {rate:.2f}% a year on average ({month}"
            f"{_change(mine)}{euro}): €{monthly_payment(rate):,.0f} a month per €{LOAN:,} over {YEARS} years"
            f"{variable} (ECB)")


def _pct(v: str) -> str:
    return f"{float(v):+.1f}%" if v not in ("", None) else "n/a"


def house_price_note(item: dict, path: str = HOUSE_PRICES) -> str | None:
    country = (item.get("country") or "PT").upper()
    table = _prices(path)
    row = table.get(country)
    if not row or row.get("year_pct") in ("", None):
        return None
    five = f", {_pct(row['five_year_pct'])} in five years" if row.get("five_year_pct") else ""
    area = table.get("EA")
    euro = (f" (euro area {_pct(area['year_pct'])}, {_pct(area['five_year_pct'])})"
            if area and country != "EA" and area.get("five_year_pct") else "")
    return (f"house prices in {NAMES.get(country, country)}: {_pct(row['year_pct'])} over the year to "
            f"{row['quarter']}{five}{euro} (Eurostat house price index)")
