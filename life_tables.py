"""Remaining life expectancy for the Bare ownership wait estimate.

Tables are period life expectancy at age (years remaining), by country and sex.
Sources and years are named on each table. Where a country has no national table
here, Eurostat demo_mlexpec (EU) is used. Figures are estimates for ranking,
never a medical forecast.
"""
from __future__ import annotations

# Remaining years at exact age. Interpolate between anchors.
# Source: Eurostat demo_mlexpec 2023 (EU-27), used as the default and for
# countries without a national extract below.
EUROSTAT_YEAR = 2023
EUROSTAT_SOURCE = "Eurostat demo_mlexpec (EU-27), 2023"
_EUROSTAT = {
    "M": {60: 21.0, 65: 17.2, 70: 13.7, 75: 10.5, 80: 7.7, 85: 5.4, 90: 3.6, 95: 2.3},
    "F": {60: 24.8, 65: 20.5, 70: 16.4, 75: 12.6, 80: 9.2, 85: 6.4, 90: 4.2, 95: 2.6},
    None: {60: 22.9, 65: 18.9, 70: 15.1, 75: 11.6, 80: 8.5, 85: 5.9, 90: 3.9, 95: 2.5},
}

# Portugal — INE Tábuas de mortalidade 2021-2023 (complete expectation at age).
PT_YEAR = 2023
PT_SOURCE = "INE Portugal, tábuas de mortalidade completas 2021-2023"
_PT = {
    "M": {60: 21.5, 65: 17.6, 70: 14.0, 75: 10.7, 80: 7.8, 85: 5.4, 90: 3.5, 95: 2.2},
    "F": {60: 25.6, 65: 21.2, 70: 17.0, 75: 13.0, 80: 9.4, 85: 6.4, 90: 4.1, 95: 2.5},
    None: {60: 23.6, 65: 19.5, 70: 15.6, 75: 11.9, 80: 8.7, 85: 5.9, 90: 3.8, 95: 2.4},
}

# Spain — INE Tablas de mortalidad 2023.
ES_YEAR = 2023
ES_SOURCE = "INE España, tablas de mortalidad 2023"
_ES = {
    "M": {60: 22.4, 65: 18.4, 70: 14.6, 75: 11.2, 80: 8.1, 85: 5.6, 90: 3.7, 95: 2.3},
    "F": {60: 26.5, 65: 22.0, 70: 17.6, 75: 13.5, 80: 9.8, 85: 6.7, 90: 4.3, 95: 2.6},
    None: {60: 24.5, 65: 20.3, 70: 16.2, 75: 12.4, 80: 9.0, 85: 6.2, 90: 4.0, 95: 2.5},
}

# Italy — ISTAT tavole di mortalità 2023.
IT_YEAR = 2023
IT_SOURCE = "ISTAT, tavole di mortalità 2023"
_IT = {
    "M": {60: 22.3, 65: 18.3, 70: 14.6, 75: 11.2, 80: 8.2, 85: 5.7, 90: 3.7, 95: 2.3},
    "F": {60: 26.1, 65: 21.7, 70: 17.4, 75: 13.4, 80: 9.7, 85: 6.6, 90: 4.3, 95: 2.6},
    None: {60: 24.3, 65: 20.1, 70: 16.1, 75: 12.4, 80: 9.0, 85: 6.2, 90: 4.0, 95: 2.5},
}

_TABLES = {
    "PT": (_PT, PT_YEAR, PT_SOURCE),
    "ES": (_ES, ES_YEAR, ES_SOURCE),
    "IT": (_IT, IT_YEAR, IT_SOURCE),
}


def _lerp(table: dict[int, float], age: float) -> float:
    ages = sorted(table)
    if age <= ages[0]:
        return table[ages[0]]
    if age >= ages[-1]:
        return table[ages[-1]]
    for i, a in enumerate(ages[:-1]):
        b = ages[i + 1]
        if a <= age <= b:
            t = (age - a) / (b - a)
            return table[a] * (1 - t) + table[b] * t
    return table[ages[-1]]


def remaining_years(country: str | None, age: float, sex: str | None = None) -> dict:
    """{"years", "source", "year", "sex"} — estimate of years remaining at age."""
    age = max(40.0, min(100.0, float(age)))
    sex_key = sex if sex in ("M", "F") else None
    code = (country or "").upper()
    table, year, source = _TABLES.get(code, (_EUROSTAT, EUROSTAT_YEAR, EUROSTAT_SOURCE))
    series = table.get(sex_key) or table[None]
    return {
        "years": round(_lerp(series, age), 1),
        "source": source,
        "year": year,
        "sex": sex_key,
        "age": age,
    }


def wait_years(holders: list[dict], *, country: str | None,
               joint_survivor: bool = False,
               fallback_age: float | None = None) -> dict:
    """Years until possession for ranking: last survivor when joint, else oldest.

    holders: [{"age", "sex"?}, ...]. fallback_age is used when none are stated
    (caller supplies the guesses.py default).
    """
    ages = [h for h in holders if h.get("age")]
    if not ages and fallback_age is not None:
        ages = [{"age": fallback_age, "sex": None, "guessed": True}]
    if not ages:
        return {"years": None, "source": None, "year": None, "basis": "no age"}
    if joint_survivor and len(ages) >= 2:
        # Last survivor ≈ max remaining (conservative for the buyer: longer wait).
        parts = [remaining_years(country, h["age"], h.get("sex")) for h in ages]
        best = max(parts, key=lambda p: p["years"])
        return {
            **best,
            "basis": (f"last survivor of {len(ages)} holders "
                      f"(ages {', '.join(str(int(h['age'])) for h in ages)})"),
            "guessed": any(h.get("guessed") for h in ages),
        }
    oldest = max(ages, key=lambda h: h["age"])
    part = remaining_years(country, oldest["age"], oldest.get("sex"))
    return {
        **part,
        "basis": f"usufructuary age {int(oldest['age'])}",
        "guessed": bool(oldest.get("guessed")),
    }
