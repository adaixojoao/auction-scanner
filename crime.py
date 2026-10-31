"""crime.py — recorded crime where a listing is, against its country.

data/crime.csv (scripts/update_crime.py) holds each country's official rates:
France by commune, Portugal by municipality, Spain for the towns over 20,000
people and every province, Italy by province. One line says the rate and
whether it is high or low for the country. It is shown, not scored: crime
recorded is crime reported, and a town's rate says little about one street.
"""
from __future__ import annotations

import csv
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "crime.csv")
HIGH, LOW = 1.5, 0.67                  # times the country's rate
# What each column counts, by country
LABELS = {
    "FR": {"burglary": "home burglaries {} per 1,000 homes", "violence": "physical violence {} per 1,000"},
    "PT": {"total": "crimes {} per 1,000", "burglary": "property crimes {} per 1,000",
           "violence": "assaults {} per 1,000"},
    "ES": {"total": "offences {} per 1,000", "burglary": "home burglaries {} per 1,000",
           "violence": "violent robberies and assaults {} per 1,000"},
    "IT": {"total": "crimes {} per 1,000", "burglary": "home burglaries {} per 1,000",
           "violence": "robberies {} per 1,000"},
}
SOURCES = {"FR": "SSMSI, Ministère de l'Intérieur", "PT": "INE", "ES": "Ministerio del Interior", "IT": "ISTAT"}


@functools.lru_cache(maxsize=4)
def _table(path: str = PATH) -> dict[tuple[str, str], dict]:
    """{(country, code): row}, and (country, "name:" + key) for Portugal's municipalities."""
    from prices import place_key
    out: dict[tuple[str, str], dict] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                for k in ("total", "burglary", "violence"):
                    row[k] = float(row[k]) if row.get(k) else None
                out[(row["country"], row["code"])] = row
                if row["country"] == "PT" and row["level"] == "municipality":
                    out[("PT", "name:" + place_key(row["name"]))] = row
    except (OSError, ValueError, KeyError):
        return {}
    return out


@functools.lru_cache(maxsize=1)
def _lau_codes() -> dict[tuple[str, str], list[str]]:
    """(country, name key) → the LAU codes of every municipality of that name."""
    import prices
    out: dict[tuple[str, str], list[str]] = {}
    try:
        with open(prices.EU_POPULATION_FILE, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["country"] not in ("ES", "FR"):
                    continue
                for name in {row.get("name"), row.get("name_latin")} - {None, ""}:
                    codes = out.setdefault((row["country"], prices.place_key(name)), [])
                    if row["lau_code"] not in codes:
                        codes.append(row["lau_code"])
    except (OSError, KeyError):
        return {}
    return out


def area(item: dict, path: str = PATH) -> dict | None:
    """The crime row for the listing's place: its municipality, else its province."""
    import geo
    from prices import place_key, population_outlook
    table = _table(path)
    country = (item.get("country") or "PT").upper()
    town = item.get("concelho") or geo.municipality(item)
    if not table or not town:
        return None
    if country == "PT":
        return table.get(("PT", "name:" + place_key(town)))
    if country == "IT":
        region = population_outlook("IT", town)
        row = table.get(("IT", region["nuts3"])) if region else None
        return {**row, "name": row["name"] or region.get("name") or ""} if row else None
    if country == "FR":
        import surroundings
        insee = ((surroundings.current(item) or {}).get("found") or {}).get("fr_risks", {}) or {}
        code = insee.get("insee")
        if not code:
            codes = _lau_codes().get(("FR", place_key(town))) or []
            code = codes[0] if len(codes) == 1 else None
        return table.get(("FR", code)) if code else None
    if country == "ES":
        codes = _lau_codes().get(("ES", place_key(town))) or []
        if len(codes) > 1 and item.get("district"):
            same = [c for c in codes if place_key((table.get(("ES", c[:2])) or {}).get("name", "")).startswith(
                place_key(item["district"])[:5])]
            codes = same or codes
        if len(codes) != 1:
            return None
        return table.get(("ES", codes[0])) or table.get(("ES", codes[0][:2]))
    return None


def note(item: dict, path: str = PATH) -> str | None:
    country = (item.get("country") or "PT").upper()
    row = area(item, path)
    national = _table(path).get((country, country))
    if not row or not national:
        return None
    labels = LABELS[country]
    parts, ratios = [], {}
    for key, label in labels.items():
        mine, theirs = row.get(key), national.get(key)
        if mine is None:
            continue
        parts.append(label.format(f"{mine:.1f}") + (f" (country {theirs:.1f})" if theirs else ""))
        if theirs:
            ratios[key] = mine / theirs
    import geo
    if row["level"] == "province":
        where = f"the province of {row['name'].title()}" if row["name"] else "the province"
    else:
        where = row["name"] or item.get("concelho") or geo.municipality(item) or "the commune"
    if not parts:
        if country == "FR" and row["level"] == "commune":
            return (f"too few crimes recorded to publish in this commune ({row['year']}; "
                    f"{SOURCES[country]}) — usually a quiet place")
        return None
    level = ""
    if ratios:
        # The total decides; France publishes no total, so its worst figure does.
        r = ratios.get("total") or max(ratios.values())
        level = (" — high for the country" if r >= HIGH else " — low for the country" if r <= LOW
                 else " — about average for the country")
        double = [labels[k].split(" {}")[0] for k, v in ratios.items() if k != "total" and v >= 2 and r < HIGH]
        if double:
            level += f", but {' and '.join(double)} twice the country's or more"
    return f"recorded crime in {where}, {row['year']}: {'; '.join(parts)}{level} ({SOURCES[country]})"
