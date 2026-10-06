"""What a hectare of land is worth where a listing is, from official tables.

France: forest prices per forest region (SAFER, Le prix des terres 2025, 2025
figures), the only forest-to-forest comparison available; regions are IGN's
groupings of ecological regions, matched here by département (approximate where
a département spans two). SAFER leaves Corsica out of its forest prices. Elsewhere no official forest price is published;
agricultural prices (Eurostat apri_lprc) are not used, because forest and scrub
sell well below farmland and the "discount" would be false.
"""
from __future__ import annotations

SAFER_FOREST_2025 = {
    "Nord-Bassin parisien": 6480, "Est": 7880, "Ouest": 5270, "Massif Central": 2850,
    "Sud-Ouest": 3310, "Alpes-Méditerranée-Pyrénées": 3400,
}
SAFER_SOURCE = "SAFER Le prix des terres 2025 (forests, 2025)"
_FR_REGION = {
    "Ouest": "14 22 29 35 44 49 50 53 56 61 72 85 79",
    "Nord-Bassin parisien": "02 08 10 18 27 28 36 37 41 45 51 59 60 62 75 76 77 78 80 89 91 92 93 94 95",
    "Est": "01 21 25 39 52 54 55 57 67 68 70 71 88 90",
    "Sud-Ouest": "16 17 24 31 32 33 40 46 47 64 82 86",
    "Massif Central": "03 12 15 19 23 42 43 48 58 63 69 87",
    "Alpes-Méditerranée-Pyrénées": "04 05 06 07 09 11 13 26 30 34 38 65 66 73 74 81 83 84",
}
FR_DEPT_REGION = {d: region for region, depts in _FR_REGION.items() for d in depts.split()}

def _dept(item: dict) -> str | None:
    d = str(item.get("district") or "").strip().upper()
    if d.isdigit():
        return d.zfill(2)
    return d if d in ("2A", "2B") else None


def forest_value(item: dict) -> dict | None:
    """{"eur_ha", "label"}: the official forest price per hectare where the
    listing is, when there is one (France, by département)."""
    if (item.get("country") or "").upper() != "FR":
        return None
    region = FR_DEPT_REGION.get(_dept(item) or "")
    if not region:
        return None
    return {"eur_ha": SAFER_FOREST_2025[region], "label": f"{SAFER_SOURCE}, {region}"}


# ─── What land is going for where this one is ────────────────────────
# Outside France no official forest price is published, so the comparison is
# the scanner's own asking prices: the median €/ha of the rural plots it has
# seen in the same district, else in the same country. Asking prices, not sales,
# and only from the sites scanned — enough to say "cheap for around here", not
# what the land is worth. A handful of plots is not a market, hence MIN_PLOTS.
MIN_PLOTS = 5
MIN_PLOT_M2 = 5000        # smaller plots are priced as building land, not by the hectare
OBSERVED_LABEL = "asking prices seen by the scanner"


def observed_index(rows) -> dict[str, dict]:
    """{"PT": {...}, "PT:Guarda": {...}} → {"eur_ha", "plots"}: the median €/ha
    of the rural plots in `rows`, by country and by district.

    `rows` are (country, district, title, tipo, area_m2, price) of priced
    listings; the kind is read from the title and the portal's own type."""
    import statistics

    from scoring import property_kind
    groups: dict[str, list[float]] = {}
    for country, district, title, tipo, area, price in rows:
        if not price or not area or area < MIN_PLOT_M2:
            continue
        item = {"title": title or "", "description": "", "tipo": tipo, "area_m2": area,
                "country": (country or "PT").upper()}
        if property_kind(item) != "rural_plot":
            continue
        eur_ha = price / (area / 10000)
        groups.setdefault(item["country"], []).append(eur_ha)
        if district:
            groups.setdefault(f"{item['country']}:{district}", []).append(eur_ha)
    return {key: {"eur_ha": statistics.median(values), "plots": len(values)}
            for key, values in groups.items() if len(values) >= MIN_PLOTS}


def observed_value(item: dict) -> dict | None:
    """{"eur_ha", "label"}: the median asking price per hectare of rural land
    where this listing is (`item["land_market"]`, built by db.load_listings)."""
    from common import COUNTRY_NAMES
    index = item.get("land_market") or {}
    country = (item.get("country") or "PT").upper()
    district = item.get("district")

    def answer(found: dict, where: str) -> dict:
        return {"eur_ha": found["eur_ha"],
                "label": f"{OBSERVED_LABEL}: the median of {found['plots']} plots in {where}"}

    if district and index.get(f"{country}:{district}"):
        return answer(index[f"{country}:{district}"], str(district))
    if index.get(country):
        return answer(index[country], COUNTRY_NAMES.get(country, country))
    return None


def land_value(item: dict, text: str = "") -> dict | None:
    """{"eur_ha", "label"}: what a hectare goes for where this plot is — the
    official forest price for woodland where one is published, else the
    scanner's own median. None when neither can be named, because a plot
    compared with the wrong kind of land shows a discount that is not there."""
    from common import has_term
    from scoring import FOREST_WORDS
    if has_term(text or item.get("title") or "", FOREST_WORDS, negations=False):
        official = forest_value(item)
        if official:
            return official
    return observed_value(item)
