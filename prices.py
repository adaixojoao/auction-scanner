"""
prices.py — local prices of homes per m², for "X% below local prices".

Portugal: the median price per m² of homes sold in each municipality, from
Statistics Portugal (INE), in data/pt_home_prices.csv. The file is public data
and is refreshed with `python scripts/update_prices.py` on a PC that can reach
ine.pt (see that script). Spain (MIVAU appraised values), France (DVF sales),
the Netherlands (CBS) and Luxembourg (Observatoire de l'Habitat): one table each
in data/<cc>_home_prices.csv, with province averages for villages. Elsewhere:
the city figures in scoring.MARKET_PRICE_PER_M2.
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
# The median monthly rent per m² of new leases in each municipality (INE), for
# "what would it rent for". Same columns as PT_FILE; eur_m2 is € a month.
PT_RENT_FILE = os.path.join(HERE, "data", "pt_rents.csv")
# Other countries, where an official table per municipality can be had
# (scripts/update_prices.py): France's "carte des loyers" (houses, per commune),
# Spain's SERPAVI (tax returns, per municipio, with the provinces as "prov:<name>"
# for villages too small to have a figure).
RENT_FILES = {"FR": os.path.join(HERE, "data", "fr_rents.csv"),
              "ES": os.path.join(HERE, "data", "es_rents.csv")}
EU_POPULATION_FILE = os.path.join(HERE, "data", "eu_population.csv")
# Eurostat EUROPOP2019 baseline, NUTS 3, 1 January 2019 and 2050 (scripts/update_population_outlook.py).
# A scenario, not a forecast. place_nuts3.csv attaches a municipality name to one region
# when that name belongs to only one region.
EUROPOP_FILE = os.path.join(HERE, "data", "europop2019.csv")
PLACE_NUTS_FILE = os.path.join(HERE, "data", "place_nuts3.csv")
OUTLOOK_LABEL = "EUROPOP2019 baseline scenario, 2019–2050 (not a forecast)"
# Prices of homes per m² outside Portugal (scripts/update_prices.py), same
# columns: a municipality's figure under "Name" and "Name|<province code>", and
# each province's average under "prov:<code>" for villages too small to have one.
PRICE_FILES = {cc: os.path.join(HERE, "data", f"{cc.lower()}_home_prices.csv")
               for cc in ("FR", "ES", "BE", "NL", "LU", "DE", "IT")}


# Court and bank texts often write "S. João da Pesqueira" / "Sta. Maria"; the
# INE table and OpenStreetMap use the full word. Expand only with a period so
# "casas grandes" is not mangled.
_SAINT_ABBREV = (
    (re.compile(r"\bsta\.\s*"), "santa "),
    (re.compile(r"\bsto\.\s*"), "santo "),
    (re.compile(r"\bs\.\s*"), "sao "),
)


def place_key(name: str) -> str:
    """"Lisboa (Santa Maria Maior)" / "S. João da Pesqueira" / "Lagoa (Algarve)"
    → "lisboa" / "sao joao da pesqueira" / "lagoa"."""
    low = normalize(name)
    for pat, repl in _SAINT_ABBREV:
        low = pat.sub(repl, low)
    return re.split(r"[,(/]| - ", low)[0].strip()


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


def _prev_fields(row: dict) -> tuple[float | None, str | None]:
    """The previous figure of the same series, when the file kept one."""
    try:
        prev = float(row.get("prev_eur_m2") or "")
    except (TypeError, ValueError):
        return None, None
    period = (row.get("prev_period") or "").strip()
    if prev <= 0 or not period:
        return None, None
    return prev, period


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict[str, tuple]:
    """{key: (€/m², label, previous €/m² or None, previous period or None)}."""
    table: dict[str, tuple] = {}
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
                    prev, prev_period = _prev_fields(row)
                    found = (value, f"{row.get('source') or 'INE'} {row.get('period') or ''}".strip(),
                             prev, prev_period)
                    # Two municipalities share a name (Lagoa, Calheta): each is kept
                    # under its region too, and the plain name keeps the first one.
                    table[f"{key}|{region_of_name(name)}"] = found
                    table.setdefault(key, found)
    except OSError:
        return {}
    return table


def pt_table(path: str | None = None) -> dict[str, tuple]:
    """{municipality key: (€/m², "INE 2.º trimestre de 2026", previous €/m², previous period)}.

    The last two are None when this file has only the latest figure. Empty if the file is missing."""
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
def _load_parishes(path: str, mtime: float) -> dict[str, tuple]:
    table: dict[str, tuple] = {}
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
                prev, prev_period = _prev_fields(row)
                found = (value, f"{row.get('source') or 'INE'} {row.get('period') or ''}, parish".strip(),
                         prev, prev_period)
                for name in parish_names(row.get("parish") or ""):
                    table.setdefault(f"{town}|{name}", found)
    except OSError:
        return {}
    return table


def parish_price(concelho: str | None, freguesia: str | None, path: str | None = None) -> tuple | None:
    """(€/m², source, previous €/m², previous period) for the listing's parish, when INE has one."""
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


def pt_rent(concelho: str | None, district: str | None = None,
            path: str | None = None) -> tuple[float, str] | None:
    """(€ a month per m² of new leases, source) in that municipality, or None."""
    if not concelho:
        return None
    table = pt_table(path or PT_RENT_FILE)
    key, region = place_key(concelho), region_of_place(district)
    found = (table.get(f"{key}|{region}") if region else None) or table.get(key)
    return (found[0], found[1]) if found else None


def rent_per_m2(country: str, place: str | None, district: str | None = None) -> tuple[float, str] | None:
    """(€ a month per m², source) for a municipality in any country that has a
    rent table; None elsewhere."""
    country = (country or "PT").upper()
    if country == "PT":
        return pt_rent(place, district)
    path = RENT_FILES.get(country)
    if not place or not path:
        return None
    table = pt_table(path)
    found = table.get(place_key(place))
    if not found and district:
        # Servihabitat writes provinces run together: "ciudadreal".
        squeezed = place_key(district).replace(" ", "")
        found = next((v for k, v in table.items() if k.startswith("prov:") and k.replace(" ", "") ==
                      "prov:" + squeezed and "|" not in k), None)
        if found:
            found = (found[0], found[1] + ", province")
    return (found[0], found[1]) if found else None


@functools.lru_cache(maxsize=1)
def _load_population(path: str, mtime: float) -> dict[tuple[str, str], int]:
    """(country, name_key) → population from eu_population.csv."""
    table: dict[tuple[str, str], int] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                cc = (row.get("country") or "").strip().upper()
                pop_str = (row.get("population") or "").strip()
                if not cc or not pop_str:
                    continue
                try:
                    pop = int(pop_str)
                except ValueError:
                    continue
                for name_col in ("name_latin", "name"):
                    raw = (row.get(name_col) or "").strip()
                    if raw:
                        key = (cc, place_key(raw))
                        table.setdefault(key, pop)
    except OSError:
        return {}
    return table


def population_of(country: str, place: str | None) -> int | None:
    """Population of the named municipality, or None if unknown.
    Uses Eurostat LAU 2024 data (eu_population.csv)."""
    if not place:
        return None
    try:
        table = _load_population(EU_POPULATION_FILE, os.path.getmtime(EU_POPULATION_FILE))
    except OSError:
        return None
    cc = (country or "PT").upper()
    return table.get((cc, place_key(place)))


@functools.lru_cache(maxsize=1)
def _load_regions(path: str, mtime: float) -> dict[str, dict]:
    table: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                code = (row.get("nuts3") or "").strip()
                if code:
                    table[code] = row
    except OSError:
        return {}
    return table


@functools.lru_cache(maxsize=1)
def _load_place_nuts(path: str, mtime: float) -> dict[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                country = (row.get("country") or "").strip().upper()
                key = (row.get("name_key") or "").strip()
                nuts = (row.get("nuts3") or "").strip()
                if country and key and nuts:
                    table[(country, key)] = nuts
    except OSError:
        return {}
    return table


def population_outlook(country: str, place: str | None) -> dict | None:
    """The EUROPOP2019 scenario for the municipality's NUTS 3 region, or None.

    {"nuts3", "name", "change_pct", "pop_2019", "pop_2050", "label"}. change_pct
    is the projected change in people from 2019 to 2050. Eurostat presents the
    figure as one scenario, not a forecast, and the label says so. None when
    the place is unknown or its name sits in more than one region."""
    if not place:
        return None
    try:
        places = _load_place_nuts(PLACE_NUTS_FILE, os.path.getmtime(PLACE_NUTS_FILE))
        regions = _load_regions(EUROPOP_FILE, os.path.getmtime(EUROPOP_FILE))
    except OSError:
        return None
    nuts = places.get(((country or "PT").upper(), place_key(place)))
    row = regions.get(nuts or "")
    if not row:
        return None
    try:
        change = float(row["change_pct"])
        pop_2019 = int(row["pop_2019"])
        pop_2050 = int(row["pop_2050"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"nuts3": nuts, "name": row.get("name") or nuts, "change_pct": change,
            "pop_2019": pop_2019, "pop_2050": pop_2050, "label": OUTLOOK_LABEL}


# A published series that moved by less than this is not mentioned: a 12-month
# median twitches by less from one quarter to the next.
SALE_MOVE_MIN_PCT = 2


def _resolve_price(country: str, place: str | None, fallback: dict[str, dict[str, float]],
                   district: str | None = None, parish: str | None = None) -> tuple | None:
    """(€/m², label, previous €/m² or None, previous period or None) for the series
    `local_price` would use, or None."""
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
    path = PRICE_FILES.get(country)
    if path and os.path.exists(path):
        table = pt_table(path)
        found = (table.get(f"{key}|{place_key(district)}") if district else None) or table.get(key)
        if not found and district:
            found = table.get("prov:" + place_key(district))
            found = (found[0], found[1] + ", province average", found[2], found[3]) if found else None
        if found:
            return found
    value = fallback.get(country, {}).get(key)
    return (value, "city estimate", None, None) if value else None


def local_price(country: str, place: str | None, fallback: dict[str, dict[str, float]],
                district: str | None = None, parish: str | None = None) -> tuple[float, str] | None:
    """(€/m² of homes in that municipality, where the figure comes from), or None.
    `fallback` is the hand-made city table {country: {key: €/m²}}. `district`
    tells same-named municipalities apart (Calheta in Madeira or the Azores)."""
    found = _resolve_price(country, place, fallback, district, parish)
    return (found[0], found[1]) if found else None


def sale_move(country: str, place: str | None, district: str | None = None,
              parish: str | None = None) -> dict | None:
    """How the sale series used for this place moved since its previous figure.

    {"eur_m2", "prev_eur_m2", "prev_period", "change_pct", "label"}. None when
    that series has no earlier figure. A parish price is not compared with the
    municipality's previous figure: they are different series."""
    found = _resolve_price(country, place, {}, district, parish)
    if not found or not found[2] or not found[3]:
        return None
    eur, label, prev, prev_period = found
    return {"eur_m2": eur, "prev_eur_m2": prev, "prev_period": prev_period,
            "change_pct": (eur - prev) / prev * 100, "label": label}


def sale_move_text(move: dict | None) -> str | None:
    """One line for a move large enough to mention, or None. It does not change a score:
    the discount already uses the latest figure."""
    if not move or abs(move["change_pct"]) < SALE_MOVE_MIN_PCT:
        return None
    way = "up" if move["change_pct"] > 0 else "down"
    return (f"sold prices here {way} {abs(move['change_pct']):.0f}% since {move['prev_period']} "
            f"({move['label']})")
