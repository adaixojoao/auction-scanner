"""What a hectare of land is worth where a listing is, from official tables.

France: forest prices per forest region (SAFER, Le prix des terres 2025, 2025
figures), the only forest-to-forest comparison available; regions are IGN's
groupings of ecological regions, matched here by département (approximate where
a département spans two). SAFER leaves Corsica out of its forest prices.

Spain and Italy publish agricultural land, not forest. Those prices are used
only when the ad names that kind of land (pasture, meadow, arable, olive,
vineyard in Spain; ordinary farmland in Italy). A rustic plot or a wood that
does not name the crop is not compared with them: forest and scrub sell below
farmland, and the discount would be false. Portugal publishes no such survey.
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


# ─── Spain: agricultural land the ad itself names ─────────────────────
# MAPA, Precios Medios Anuales de las Tierras de Uso Agrario 2024 (base 2020,
# provisional, July 2025). €/ha by autonomous community. A blank cell in the
# survey is left out: the national figure is not that community's price.
# Provincial figures are published only when significant and are not in the
# community tables used here.
MAPA_SOURCE = "MAPA Precios de la Tierra 2024"
_MAPA_CLASS = {
    "PST": "pastizal", "PRD": "prados", "TAS": "tierras arables de secano",
    "TAR": "tierras arables de regadío", "OLS": "olivar de secano",
    "OLR": "olivar de regadío", "VIS": "viñedo de secano", "VIR": "viñedo de regadío",
}
# 2024 €/ha. Keys are the community names below.
_MAPA_2024 = {
    "PST": {
        "Galicia": 10190, "Asturias": 7316, "Cantabria": 2316, "Navarra": 4478,
        "Aragón": 1275, "Cataluña": 1753, "Castilla y León": 1890, "Madrid": 5916,
        "Castilla-La Mancha": 2462, "Extremadura": 3825, "Andalucía": 3593,
    },
    "PRD": {
        "Galicia": 15488, "Asturias": 14815, "Cantabria": 12370, "País Vasco": 9383,
        "Navarra": 14302, "Aragón": 6530, "Cataluña": 7443, "Castilla y León": 3932,
    },
    "TAS": {
        "Galicia": 25167, "País Vasco": 13817, "Navarra": 15026, "La Rioja": 10199,
        "Aragón": 3863, "Cataluña": 11502, "Illes Balears": 20262, "Castilla y León": 5534,
        "Madrid": 9282, "Castilla-La Mancha": 4961, "Comunitat Valenciana": 9065,
        "Murcia": 8346, "Extremadura": 5764, "Andalucía": 9941,
    },
    "TAR": {
        "Galicia": 12721, "País Vasco": 18331, "Navarra": 17499, "La Rioja": 16489,
        "Aragón": 12886, "Cataluña": 21269, "Illes Balears": 27292, "Castilla y León": 11012,
        "Madrid": 20031, "Castilla-La Mancha": 13076, "Comunitat Valenciana": 26120,
        "Murcia": 39180, "Extremadura": 21502, "Andalucía": 25745, "Canarias": 74335,
    },
    "OLS": {
        "Aragón": 3716, "Cataluña": 8058, "Madrid": 7706, "Castilla-La Mancha": 6630,
        "Comunitat Valenciana": 9596, "Extremadura": 7827, "Andalucía": 17029,
    },
    "OLR": {
        "Aragón": 8007, "Cataluña": 18450, "Castilla-La Mancha": 14248,
        "Comunitat Valenciana": 20892, "Extremadura": 21992, "Andalucía": 27052,
    },
    "VIS": {
        "Galicia": 60473, "País Vasco": 57425, "Navarra": 14299, "La Rioja": 40155,
        "Aragón": 6140, "Cataluña": 22739, "Castilla y León": 13538, "Madrid": 12496,
        "Castilla-La Mancha": 7734, "Comunitat Valenciana": 11821, "Murcia": 5137,
        "Extremadura": 12992, "Andalucía": 19112,
    },
    "VIR": {
        "Navarra": 32745, "La Rioja": 44509, "Aragón": 8111, "Castilla y León": 24716,
        "Castilla-La Mancha": 14753, "Comunitat Valenciana": 17476, "Murcia": 9786,
        "Extremadura": 18793,
    },
}
_CCAA_NAME = {
    "galicia": "Galicia", "asturias": "Asturias", "cantabria": "Cantabria",
    "pais vasco": "País Vasco", "navarra": "Navarra", "la rioja": "La Rioja",
    "aragon": "Aragón", "cataluna": "Cataluña", "catalunya": "Cataluña",
    "illes balears": "Illes Balears", "islas baleares": "Illes Balears", "baleares": "Illes Balears",
    "castilla y leon": "Castilla y León", "madrid": "Madrid", "comunidad de madrid": "Madrid",
    "castilla-la mancha": "Castilla-La Mancha", "castilla la mancha": "Castilla-La Mancha",
    "comunitat valenciana": "Comunitat Valenciana", "comunidad valenciana": "Comunitat Valenciana",
    "murcia": "Murcia", "region de murcia": "Murcia",
    "extremadura": "Extremadura", "andalucia": "Andalucía", "canarias": "Canarias",
    "principado de asturias": "Asturias", "euskadi": "País Vasco",
}
_PROVINCE_CCAA = {
    "a coruna": "Galicia", "la coruna": "Galicia", "lugo": "Galicia", "ourense": "Galicia",
    "orense": "Galicia", "pontevedra": "Galicia",
    "asturias": "Asturias", "cantabria": "Cantabria",
    "bizkaia": "País Vasco", "vizcaya": "País Vasco", "gipuzkoa": "País Vasco",
    "guipuzcoa": "País Vasco", "alava": "País Vasco", "araba": "País Vasco",
    "navarra": "Navarra", "la rioja": "La Rioja",
    "huesca": "Aragón", "zaragoza": "Aragón", "teruel": "Aragón",
    "barcelona": "Cataluña", "girona": "Cataluña", "gerona": "Cataluña",
    "lleida": "Cataluña", "lerida": "Cataluña", "tarragona": "Cataluña",
    "illes balears": "Illes Balears", "baleares": "Illes Balears",
    "leon": "Castilla y León", "palencia": "Castilla y León", "burgos": "Castilla y León",
    "soria": "Castilla y León", "segovia": "Castilla y León", "avila": "Castilla y León",
    "salamanca": "Castilla y León", "valladolid": "Castilla y León", "zamora": "Castilla y León",
    "madrid": "Madrid",
    "toledo": "Castilla-La Mancha", "ciudad real": "Castilla-La Mancha", "cuenca": "Castilla-La Mancha",
    "guadalajara": "Castilla-La Mancha", "albacete": "Castilla-La Mancha",
    "alicante": "Comunitat Valenciana", "alacant": "Comunitat Valenciana",
    "castellon": "Comunitat Valenciana", "valencia": "Comunitat Valenciana",
    "murcia": "Murcia",
    "badajoz": "Extremadura", "caceres": "Extremadura",
    "almeria": "Andalucía", "cadiz": "Andalucía", "cordoba": "Andalucía", "granada": "Andalucía",
    "huelva": "Andalucía", "jaen": "Andalucía", "malaga": "Andalucía", "sevilla": "Andalucía",
    "las palmas": "Canarias", "santa cruz de tenerife": "Canarias", "tenerife": "Canarias",
}


def _spain_community(item: dict) -> str | None:
    from common import normalize
    for raw in (item.get("district"), item.get("concelho")):
        key = normalize(raw or "")
        if key in _CCAA_NAME:
            return _CCAA_NAME[key]
        if key in _PROVINCE_CCAA:
            return _PROVINCE_CCAA[key]
    return None


def _spain_class(text: str) -> str | None:
    """The MAPA land class the ad names, or None. A bare 'rústico' names none."""
    from common import has_term
    watered = has_term(text, ["regadio", "rega", "irriguo", "regadiu"])
    if has_term(text, ["olival", "olivar", "oliveira", "ulivar"]):
        return "OLR" if watered else "OLS"
    if has_term(text, ["vinha", "vinhedo", "vinedo", "vigneto", "vigna"]):
        return "VIR" if watered else "VIS"
    if has_term(text, ["prado", "pradera", "lameiro", "prateria"]):
        return "PRD"
    if has_term(text, ["pastizal", "pastagem", "pasto", "pastos", "pastoreo"]):
        return "PST"
    if has_term(text, ["secano", "sequeiro", "cereal", "seminativo", "tierra de labor", "terra de seara",
                       "arable"]):
        return "TAR" if watered else "TAS"
    return None


def spain_agricultural(item: dict, text: str) -> dict | None:
    """{"eur_ha", "label"} when the ad names a crop Spain prices in that community."""
    if (item.get("country") or "").upper() != "ES":
        return None
    kind = _spain_class(text)
    community = _spain_community(item)
    if not kind or not community:
        return None
    price = _MAPA_2024.get(kind, {}).get(community)
    if not price:
        return None
    return {"eur_ha": price,
            "label": (f"{MAPA_SOURCE}, {_MAPA_CLASS[kind]}, {community} "
                      "(agricultural land for sale, not forest)")}


# ─── Italy: the regional average of ordinary farmland ────────────────
# CREA, Indagine sul mercato fondiario 2024, Tabella 4: valori fondiari medi,
# migliaia di euro per ettaro di SAU. The figure is the regional total, the
# column before the yearly change. It mixes every crop, so it is used only
# for ordinary farmland the ad names (arable, pasture, meadow) and not for a
# vineyard, an olive grove or a wood, which this average is not.
CREA_SOURCE = "CREA valori fondiari 2024"
_CREA_2024 = {
    "Piemonte": 23100, "Valle d'Aosta": 14400, "Lombardia": 49100,
    "Trentino-Alto Adige": 95000, "Veneto": 54100, "Friuli-Venezia Giulia": 35500,
    "Liguria": 37100, "Emilia-Romagna": 29200, "Toscana": 18400, "Umbria": 12000,
    "Marche": 13600, "Lazio": 14400, "Abruzzo": 11400, "Molise": 13300,
    "Campania": 17300, "Puglia": 14800, "Basilicata": 8200, "Calabria": 12500,
    "Sicilia": 10300, "Sardegna": 6700,
}
_ITALY_REGION = {
    "piemonte": "Piemonte", "valle d'aosta": "Valle d'Aosta", "aosta": "Valle d'Aosta",
    "lombardia": "Lombardia", "trentino-alto adige": "Trentino-Alto Adige",
    "trentino": "Trentino-Alto Adige", "alto adige": "Trentino-Alto Adige",
    "veneto": "Veneto", "friuli-venezia giulia": "Friuli-Venezia Giulia", "friuli": "Friuli-Venezia Giulia",
    "liguria": "Liguria", "emilia-romagna": "Emilia-Romagna", "emilia romagna": "Emilia-Romagna",
    "toscana": "Toscana", "umbria": "Umbria", "marche": "Marche", "lazio": "Lazio",
    "abruzzo": "Abruzzo", "molise": "Molise", "campania": "Campania", "puglia": "Puglia",
    "basilicata": "Basilicata", "calabria": "Calabria", "sicilia": "Sicilia", "sardegna": "Sardegna",
    "torino": "Piemonte", "cuneo": "Piemonte", "asti": "Piemonte", "alessandria": "Piemonte",
    "novara": "Piemonte", "vercelli": "Piemonte", "biella": "Piemonte", "verbania": "Piemonte",
    "milano": "Lombardia", "bergamo": "Lombardia", "brescia": "Lombardia", "como": "Lombardia",
    "varese": "Lombardia", "pavia": "Lombardia", "cremona": "Lombardia", "mantova": "Lombardia",
    "lecco": "Lombardia", "lodi": "Lombardia", "monza": "Lombardia", "sondrio": "Lombardia",
    "bolzano": "Trentino-Alto Adige", "trento": "Trentino-Alto Adige",
    "venezia": "Veneto", "verona": "Veneto", "vicenza": "Veneto", "padova": "Veneto",
    "treviso": "Veneto", "rovigo": "Veneto", "belluno": "Veneto",
    "udine": "Friuli-Venezia Giulia", "pordenone": "Friuli-Venezia Giulia",
    "gorizia": "Friuli-Venezia Giulia", "trieste": "Friuli-Venezia Giulia",
    "genova": "Liguria", "savona": "Liguria", "imperia": "Liguria", "la spezia": "Liguria",
    "bologna": "Emilia-Romagna", "modena": "Emilia-Romagna", "parma": "Emilia-Romagna",
    "reggio emilia": "Emilia-Romagna", "ferrara": "Emilia-Romagna", "ravenna": "Emilia-Romagna",
    "forli-cesena": "Emilia-Romagna", "forli": "Emilia-Romagna", "rimini": "Emilia-Romagna",
    "piacenza": "Emilia-Romagna",
    "firenze": "Toscana", "pisa": "Toscana", "siena": "Toscana", "arezzo": "Toscana",
    "livorno": "Toscana", "lucca": "Toscana", "grosseto": "Toscana", "prato": "Toscana",
    "pistoia": "Toscana", "massa-carrara": "Toscana", "massa": "Toscana",
    "perugia": "Umbria", "terni": "Umbria",
    "ancona": "Marche", "pesaro": "Marche", "macerata": "Marche", "ascoli piceno": "Marche",
    "fermo": "Marche",
    "roma": "Lazio", "latina": "Lazio", "frosinone": "Lazio", "viterbo": "Lazio", "rieti": "Lazio",
    "laquila": "Abruzzo", "l'aquila": "Abruzzo", "pescara": "Abruzzo", "chieti": "Abruzzo",
    "teramo": "Abruzzo",
    "campobasso": "Molise", "isernia": "Molise",
    "napoli": "Campania", "salerno": "Campania", "caserta": "Campania",
    "avellino": "Campania", "benevento": "Campania",
    "bari": "Puglia", "lecce": "Puglia", "taranto": "Puglia", "foggia": "Puglia",
    "brindisi": "Puglia", "barletta": "Puglia",
    "potenza": "Basilicata", "matera": "Basilicata",
    "reggio calabria": "Calabria", "reggio di calabria": "Calabria",
    "catanzaro": "Calabria", "cosenza": "Calabria", "crotone": "Calabria",
    "vibo valentia": "Calabria",
    "palermo": "Sicilia", "catania": "Sicilia", "messina": "Sicilia", "agrigento": "Sicilia",
    "trapani": "Sicilia", "siracusa": "Sicilia", "ragusa": "Sicilia", "caltanissetta": "Sicilia",
    "enna": "Sicilia",
    "cagliari": "Sardegna", "sassari": "Sardegna", "nuoro": "Sardegna", "oristano": "Sardegna",
    "sud sardegna": "Sardegna",
}


def _italy_region(item: dict) -> str | None:
    from common import normalize
    for raw in (item.get("district"), item.get("concelho")):
        key = normalize(raw or "")
        if key in _ITALY_REGION:
            return _ITALY_REGION[key]
    return None


def italy_agricultural(item: dict, text: str) -> dict | None:
    """{"eur_ha", "label"} for ordinary farmland the ad names, in that region."""
    from common import has_term
    if (item.get("country") or "").upper() != "IT":
        return None
    if not has_term(text, ["seminativo", "pascolo", "prato", "terreno agricolo", "seminativi"]):
        return None
    region = _italy_region(item)
    price = _CREA_2024.get(region or "")
    if not price:
        return None
    return {"eur_ha": price,
            "label": (f"{CREA_SOURCE}, {region} "
                      "(average agricultural land, not a forest or a vineyard price)")}


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
    """{"eur_ha", "label"}: what a hectare goes for where this plot is.

    Woodland uses the official forest price where one is published. Farmland
    uses the official agricultural price only when the ad names that kind of
    land. Anything else uses the scanner's own median, or None: a plot compared
    with the wrong kind of land shows a discount that is not there."""
    from common import has_term
    from scoring import FOREST_WORDS
    full = text or f"{item.get('title') or ''} {item.get('description') or ''}"
    if has_term(full, FOREST_WORDS, negations=False):
        official = forest_value(item)
        if official:
            return official
        # A wood is not farmland. The agricultural surveys are not its price.
        return observed_value(item)
    for official in (spain_agricultural(item, full), italy_agricultural(item, full)):
        if official:
            return official
    return observed_value(item)
