"""
scoring.py — Deep-discount / charity acquisition scorer.
Goal: find properties significantly below market price for charitable use.

Every keyword is matched as a whole word (see common.term_regex), accent- and
case-insensitively. Substring matching is what made "desocupado" count as
occupied, "Casal do ..." count as a house and "11/2023" count as a 1/2 share.
Occupancy/usufruct terms also ignore negated mentions ("não arrendado").
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from common import days_left, find_terms, has_term, normalize, utcnow

FRAC_PATTERNS = [
    "1/2", "1/3", "1/4", "1/5", "1/6", "1/7", "1/8", "1/9",
    "1/10", "1/11", "1/12", "1/14", "1/16", "1/20", "1/24",
    "2/3", "3/4",
    "um meio", "metade indivisa", "mitad indivisa",
    "avos", "quota", "quota-parte", "quinhão", "quinhao",
    "fração ideal", "fracao ideal", "parte indivisa", "compropriedade",
    "meação", "quote-part", "cuota indivisa",
]

OCCUPANCY_PATTERNS = [
    "ocupado", "ocupada", "arrendado", "arrendada", "arrendatário*",
    "inquilino*", "occupied", "tenant*", "locataire*", "affittuari*",
    "ocupantes", "occupato", "occupata", "occupé", "occupée", "loué", "louée",
    "bail en cours", "okupa*",
]

VACANT_PATTERNS = [
    "devoluto", "devoluta", "desocupado", "desocupada",
    "livre de pessoas", "livre de ocupantes", "libre de ocupantes",
    "vacant", "libre d'occupation", "leegstaand", "libre de toute occupation",
    "vide de tout occupant", "inoccupé", "inoccupée", "sin ocupantes",
]

ACCESS_PATTERNS = [
    "sem acesso", "acesso condicionado", "sem servidão",
    "encravado", "landlocked",
]

USUFRUCT_PATTERNS = [
    "usufruto", "usufructo", "usufruct", "nue-propri*", "nuda proprietà",
    "nuda propiedad", "direito de uso", "direito de habitação",
]

SEALED_BID_PATTERNS = [
    "carta fechada", "proposta em carta", "propostas em carta",
    "venda por propostas", "sealed bid", "offre cachetée",
]

FORCED_SOURCES = {
    "citius", "financas", "zvg", "anaf", "aeat",
    "pvp_giustizia", "poland", "greece", "cyprus",
}

TAX_SOURCES = {"financas", "anaf", "aeat"}

# ─── What we are looking for ────────────────────────────────────────
# Homes and plots at ridiculous prices. Urban plots are fine; rural plots only
# when they are big AND cheap; homes in a good location that do not need heavy
# work. Everything else (shops, garages, storage) is not the goal.
#
# The two rural limits can be changed on the Settings page (config filters).
TARGET_DEFAULTS = {"rural_min_m2": 10000, "rural_max_eur_m2": 0.5}

# Whatever else is good about them (a court sale, no minimum bid…), these are
# not the goal, so their score stays under the default minimum score (45) and
# they are hidden unless you shortlist them.
NOT_THE_GOAL_CAP = {"not a home or plot": 35, "rural plot too small": 35,
                    "needs heavy work": 40}

DWELLING_WORDS = [
    "moradia", "moradias", "apartamento", "vivenda", "habitação", "casa", "casas",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6",
    "vivienda", "chalet", "adosado", "unifamiliar",
    "maison", "appartement", "logement", "pavillon",
    "appartamento", "abitazione", "villetta",
    "wohnung", "haus", "einfamilienhaus", "zweifamilienhaus", "mehrfamilienhaus", "reihenhaus",
    "doppelhaushälfte", "woning", "woonhuis", "tussenwoning", "kuća", "house", "apartment",
]
# Words that mean a home only when nothing says shop or garage: "Loja no rés-do-chão",
# "Garagem no piso -1".
WEAK_DWELLING_WORDS = ["andar", "rés-do-chão", "duplex", "piso", "villa", "stan", "flat"]
HOUSE_TYPES = {normalize(t) for t in (
    "moradia", "apartamento", "apartamento/moradia", "vivienda", "piso", "chalet", "casa",
    "maison", "appartement", "logement", "villa", "pavillon", "woning", "house", "apartment",
    "residencial", "residential",
)}

# Not bare "lote": in Portuguese auctions it means the auction lot. Not "solar"
# outside Spain: in Portugal a solar is a manor house.
URBAN_PLOT_WORDS = [
    "terreno para construção", "terreno urbano", "terrenos urbanos", "lote de terreno",
    "lote para construção", "urbanizável", "urbanizavel", "parcela urbana", "terreno urbanizável",
    "suelo urbano", "parcela edificable", "terreno urbanizable",
    "terrain à bâtir", "terrain constructible", "terreno edificabile", "lotto edificabile",
    "baugrundstück", "bauplatz", "bouwgrond", "bouwkavel", "kavel",
]
URBAN_PLOT_TYPES = {normalize(t) for t in ("terreno_urbano", "suelo", "solar", "terrain à bâtir")}

RURAL_WORDS = [
    "prédio rústico", "rústico", "rústica", "terreno rústico", "terreno agrícola", "agrícola",
    "florestal", "floresta", "mata", "pinhal", "mato", "olival", "vinha", "eucaliptal",
    "montado", "sobreiral", "pastagem", "cultura arvense", "sequeiro", "regadio", "herdade",
    "quinta", "finca rústica", "tierra de labor", "olivar", "viñedo",
    "terrain agricole", "terre agricole", "terres agricoles", "bois", "forêt", "prairie",
    "terreno agricolo", "bosco", "uliveto", "vigneto",
    "ackerland", "landwirtschaftsfläche", "grünland", "wald", "landbouwgrond", "weiland",
]
RURAL_TYPES = {normalize(t) for t in (
    "terreno_rustico", "rustico", "agricola", "florestal", "herdade", "finca", "rustica",
)}

OTHER_WORDS = [   # not a home and not a plot
    "parking", "garagem", "garage", "garaje", "box", "emplacement", "estacionamento",
    "lugar de garagem", "arrecadação", "arrecadacao", "loja", "armazém", "armazem",
    "escritório", "escritorio", "pavilhão", "pavilhao", "industrial", "estabelecimento",
    "local comercial", "nave", "oficina", "trastero", "aparcamiento", "plaza de garaje",
    "commerce", "local commercial", "bureau", "entrepôt", "hangar", "cave",
    "negozio", "magazzino", "capannone", "ufficio", "posto auto",
    "stellplatz", "tiefgarage", "lager", "büro", "gewerbe*", "bedrijfspand", "kantoor",
    "hotel", "restaurante", "café",
]
OTHER_TYPES = {normalize(t) for t in (
    "loja/escritorio", "loja", "escritório", "armazem", "industrial", "garagem", "hotel",
    "local", "local comercial", "nave", "garaje", "trastero", "commerce",
)}
PARKING_WORDS = OTHER_WORDS   # older name

# Homes: how much work they need, and where they are.
HEAVY_WORK = [
    "ruin*", "arruinad*", "em ruínas", "para recuperar", "para reconstruir", "reconstrução",
    "obras profundas", "reabilitação total", "reabilitação integral", "inabitável", "sem telhado",
    "telhado caído", "muito degradad*", "mau estado", "para demolir", "demolição",
    "a reformar", "para reformar", "reforma integral", "para rehabilitar", "inhabitable",
    "à rénover", "a renover", "à restaurer", "travaux importants", "gros travaux", "en ruine",
    "à réhabiliter", "da ristrutturare", "rudere", "fatiscente", "inagibile",
    "sanierungsbedürftig", "renovierungsbedürftig", "abrissreif", "baufällig", "ruine",
    "opknapper", "bouwvallig", "renovatie nodig",
]
RUIN_WORDS = HEAVY_WORK   # older name
SOME_WORK = [
    "necessita de obras", "precisa de obras", "necessitar de obras", "carece de obras",
    "obras de conservação", "degradad*", "necesita reforma", "necesita reformas",
    "para actualizar", "travaux à prévoir", "à rafraîchir", "a rafraichir", "da rimodernare",
    "modernisierungsbedürftig", "renovierungsbedarf",
]
GOOD_CONDITION = [
    "bom estado", "excelente estado", "ótimo estado", "renovad*", "remodelad*", "recuperad*",
    "pronto a habitar", "como nov*", "construção recente",
    "buen estado", "reformad*", "a estrenar", "para entrar a vivir", "listo para vivir",
    "bon état", "très bon état", "rénové", "rénovée", "refait à neuf", "habitable de suite",
    "buono stato", "ottimo stato", "ristrutturat*", "abitabile",
    "renoviert", "saniert", "modernisiert", "gepflegt", "bezugsfertig", "neuwertig",
    "goede staat", "gerenoveerd", "instapklaar",
]
GOOD_LOCATION = [
    "centro da cidade", "centro da vila", "centro da localidade", "centro histórico", "no centro",
    "zona central", "junto ao centro", "perto do centro", "próximo do centro",
    "centro urbano", "zona urbana", "malha urbana", "perto de serviços", "próximo de serviços",
    "junto à praia", "perto da praia", "vista mar", "vista rio", "zona nobre",
    "centro de la ciudad", "céntrico", "céntrica", "casco histórico", "cerca de la playa",
    "centre-ville", "centre ville", "proche commerces", "proche des commerces", "hyper-centre",
    "centro storico", "zona centrale", "vicino al centro",
    "innenstadt", "stadtmitte", "zentrale lage", "zentrumsnah", "centrum",
]
ISOLATED = [
    "isolad*", "lugar isolado", "acesso difícil", "caminho de terra", "sem acessos",
    "aislad*", "isolé", "isolée", "isolato", "isolata", "abgelegen", "alleinlage", "afgelegen",
]

MARKET_PRICE_PER_M2 = {
    "PT": {
        "Lisboa": 4200, "Porto": 3100, "Cascais": 4800, "Sintra": 2800,
        "Braga": 1800, "Coimbra": 1900, "Faro": 2600, "Setúbal": 2200, "Setubal": 2200,
        "Guarda": 900, "Viseu": 1100, "Castelo Branco": 850, "Beja": 750,
        "Évora": 1400, "Evora": 1400, "Portalegre": 700, "Viana do Castelo": 1300,
        "Vila Real": 950, "Bragança": 800, "Braganca": 800, "Santarém": 1100, "Santarem": 1100,
        "Leiria": 1500, "Aveiro": 1700, "Almada": 2800, "Amadora": 2600,
        "Loures": 2200, "Oeiras": 3500, "Matosinhos": 2400,
        "Vila Nova de Gaia": 2200, "Gondomar": 1800,
        "Ilha da Madeira": 2000, "Ilha de São Miguel": 1200,
    },
    "ES": {
        "Madrid": 4500, "Barcelona": 4200, "Valencia": 2100, "Sevilla": 1900,
        "Bilbao": 3200, "Malaga": 2800, "Zaragoza": 1600, "Murcia": 1400,
        "Palma": 3500, "Las Palmas": 2200, "Alicante": 1800, "Cordoba": 1300,
        "Valladolid": 1500, "Vigo": 1700, "Granada": 1600, "Toledo": 1200,
    },
    "FR": {
        "Paris": 9500, "Lyon": 4800, "Marseille": 3200, "Toulouse": 3600,
        "Nice": 4500, "Nantes": 3800, "Strasbourg": 3500, "Montpellier": 3400,
        "Bordeaux": 4200, "Lille": 3000, "Rennes": 3600, "Reims": 2400,
        "Saint-Etienne": 1800, "Grenoble": 3200, "Dijon": 2600, "Angers": 2800,
    },
    "DE": {
        "München": 8500, "Munich": 8500, "Frankfurt": 6500, "Hamburg": 6000,
        "Berlin": 5500, "Stuttgart": 5800, "Düsseldorf": 4500, "Köln": 4800,
        "Leipzig": 3200, "Dresden": 3000, "Hannover": 3200, "Nürnberg": 4200,
        "Bremen": 3000, "Dortmund": 2800, "Essen": 2600, "Bonn": 4000,
    },
    "IT": {
        "Milano": 5500, "Roma": 3800, "Napoli": 2200, "Torino": 2400,
        "Firenze": 3500, "Bologna": 3200, "Venezia": 4500, "Genova": 2000,
        "Palermo": 1400, "Catania": 1200, "Bari": 1600, "Verona": 2800,
        "Padova": 2600, "Trieste": 2200, "Perugia": 1800,
    },
    "NL": {
        "Amsterdam": 6500, "Rotterdam": 4200, "Den Haag": 4800, "Utrecht": 5200,
        "Eindhoven": 4000, "Groningen": 3200, "Tilburg": 3400, "Almere": 3800,
        "Breda": 3600, "Nijmegen": 3800, "Haarlem": 5500, "Arnhem": 3200,
    },
    "HR": {
        "Zagreb": 2800, "Split": 3200, "Rijeka": 2200, "Osijek": 1200,
        "Zadar": 2800, "Pula": 2600, "Dubrovnik": 5500, "Varazdin": 1400,
    },
    "GR": {
        "Athina": 2200, "Athens": 2200, "Thessaloniki": 1600, "Patra": 1200,
        "Heraklion": 1800, "Larissa": 1000, "Rhodes": 2500, "Corfu": 2800,
    },
    "BE": {
        "Bruxelles": 3800, "Brussels": 3800, "Antwerpen": 3200, "Gent": 3000,
        "Liege": 1800, "Bruges": 3200, "Namur": 2000, "Leuven": 3400,
    },
    "RO": {
        "Bucuresti": 1600, "Bucharest": 1600, "Cluj-Napoca": 1800,
        "Timisoara": 1400, "Iasi": 1200, "Constanta": 1300, "Brasov": 1600,
    },
    "PL": {
        "Warszawa": 2800, "Warsaw": 2800, "Krakow": 2400, "Wroclaw": 2200,
        "Poznan": 2000, "Gdansk": 2200, "Lodz": 1400, "Katowice": 1600,
    },
    "CY": {
        "Nicosia": 1800, "Limassol": 3200, "Larnaca": 2000, "Paphos": 2800,
    },
}

# Backward compat alias
PRICE_PER_M2 = MARKET_PRICE_PER_M2["PT"]

_MARKET_INDEX = {
    country: {normalize(name): ppm2 for name, ppm2 in table.items()}
    for country, table in MARKET_PRICE_PER_M2.items()
}


def _place_key(name: str) -> str:
    """"Lisboa (Santa Maria Maior)" / "Porto, Porto" → "lisboa" / "porto"."""
    return re.split(r"[,(/]| - ", normalize(name))[0].strip()


def market_value_estimate(item: dict) -> float | None:
    """area × €/m² for the listing's municipality, if we have a figure for it.

    Matches the place name exactly (accent-insensitive). The old substring match
    priced Porto de Mós as Porto and anything containing "a" as something.
    """
    area = item.get("area_m2") or 0
    if not area or area < 5:
        return None
    town = _known_town(item)
    table = _MARKET_INDEX.get(item.get("country") or "PT", {})
    return area * table[_place_key(town)] if town else None


def buyer_priorities(targets: dict | None = None) -> str:
    """The goal in words, for the AI check: the same rules as score()."""
    t = {k: (targets or {}).get(k) or v for k, v in TARGET_DEFAULTS.items()}
    return (
        "Homes (houses or flats) and plots at very low prices. Homes must be in a good location "
        "(a town or village centre with services, not isolated) and must not need heavy work: no "
        "ruins or full rebuilds, light work is acceptable. Urban building plots are welcome. Rural "
        f"plots only if big (at least {t['rural_min_m2']:,.0f} m²) and cheap (at most "
        f"€{t['rural_max_eur_m2']:.2f}/m², about €{t['rural_max_eur_m2'] * 10000:,.0f} per hectare). "
        "Shops, garages, storage and offices are not wanted."
    )


def property_kind(item: dict) -> str | None:
    """"home", "urban_plot", "rural_plot", "other" (shop, garage, storage…) or
    None when the listing does not say. The title and the portal's own type
    decide first; the description only when they say nothing."""
    title, desc = item.get("title") or "", item.get("description") or ""
    tipo = normalize(item.get("tipo"))
    area = item.get("area_m2") or 0

    urban_words = URBAN_PLOT_WORDS + (["solar"] if item.get("country") == "ES" else [])

    def kind_of(text: str) -> str | None:
        if has_term(text, DWELLING_WORDS, negations=False):
            return "home"
        if has_term(text, OTHER_WORDS, negations=False):
            return "other"
        if has_term(text, WEAK_DWELLING_WORDS, negations=False):
            return "home"
        if has_term(text, RURAL_WORDS, negations=False):
            return "rural_plot"
        if has_term(text, urban_words, negations=False):
            return "urban_plot"
        if has_term(text, ["terreno", "terrenos", "terrain", "terreno/lote", "grundstück", "parcela"],
                     negations=False) and area:
            return "rural_plot" if area >= 5000 else "urban_plot"
        return None

    found = kind_of(title)
    if found:
        return found
    if tipo in HOUSE_TYPES:
        return "home"
    if tipo in RURAL_TYPES:
        return "rural_plot"
    if tipo in URBAN_PLOT_TYPES:
        return "urban_plot"
    if tipo in OTHER_TYPES:
        return "other"
    return kind_of(desc)


KIND_LABELS = {"home": "home", "urban_plot": "urban plot", "rural_plot": "rural plot",
               "other": "not a home or plot"}


def _occupation(item: dict) -> str | None:
    raw = item.get("raw_json") or ""
    if '"occupation"' not in raw:
        return None
    try:
        return json.loads(raw).get("occupation")
    except (TypeError, ValueError, AttributeError):
        return None


def _pay(item: dict) -> float:
    """What you would realistically pay: the current bid, else the minimum, else the price."""
    return item.get("current_bid") or item.get("min_price") or item.get("price") or 0


def _ha(m2: float) -> str:
    return f"{m2 / 10000:.1f} ha" if m2 >= 10000 else f"{m2:,.0f} m²".replace(",", " ")


def score(item: dict, now: datetime | None = None,
          targets: dict | None = None) -> tuple[float, list[str]]:
    """0–100: how well a listing fits the goal (see "What we are looking for"),
    with the reasons. `targets` are the config filters (rural_min_m2,
    rural_max_eur_m2); missing values use TARGET_DEFAULTS."""
    raw, reasons = score_detail(item, now, targets)
    return max(0.0, min(100.0, raw)), reasons


def score_detail(item: dict, now: datetime | None = None,
                 targets: dict | None = None) -> tuple[float, list[str]]:
    """The score before it is clamped to 0–100: several listings can reach 100,
    and this still says which of them is best (used for sorting)."""
    s = 50.0
    reasons: list[str] = []
    t = {k: (targets or {}).get(k) or v for k, v in TARGET_DEFAULTS.items()}

    title   = item.get("title") or ""
    desc    = item.get("description") or ""
    full    = f"{title} {desc}"
    price   = item.get("price")   or 0
    bid     = item.get("current_bid") or 0
    area    = item.get("area_m2") or 0
    source  = item.get("source",  "")
    title_n = normalize(title)
    pay     = _pay(item)
    kind    = property_kind(item)

    if has_term(title, FRAC_PATTERNS, negations=False):
        return 0.0, ["fractional share — skip"]

    if has_term(full, USUFRUCT_PATTERNS):
        return 0.0, ["usufruct — skip"]

    # ── What it is ────────────────────────────────────────────────────
    if kind == "home":
        s += 10
        reasons.append("home")
        s += _home_points(item, full, area, pay, reasons)
    elif kind == "urban_plot":
        s += 8
        reasons.append("urban plot" + (f" ({_ha(area)})" if area else ""))
    elif kind == "rural_plot":
        s += _rural_points(area, pay, t, reasons)
    elif kind == "other":
        s -= 25
        reasons.append("not a home or plot")

    occupation = _occupation(item)   # read from the sale's detail page (ES, FR)
    if occupation == "occupied" or (occupation is None and has_term(full, OCCUPANCY_PATTERNS)):
        s -= 25
        reasons.append("occupied/tenanted")
    elif occupation == "vacant" or has_term(full, VACANT_PATTERNS, negations=False):
        s += 6
        reasons.append("vacant (devoluto)")

    if has_term(full, ACCESS_PATTERNS, negations=False):
        s -= 20
        reasons.append("no road access")

    if "direito" in title_n and "heranca" in title_n:
        s -= 20
        reasons.append("inheritance right only")

    # ── How cheap ─────────────────────────────────────────────────────
    if bid and price and price > 0:
        ratio = bid / price
        if ratio < 0.30:
            s += 35
            reasons.append(f"bid only {ratio:.0%} of VB — extreme discount")
        elif ratio < 0.50:
            s += 25
            reasons.append(f"bid {ratio:.0%} of VB — deep discount")
        elif ratio < 0.70:
            s += 15
            reasons.append(f"bid {ratio:.0%} of VB — good discount")
        elif ratio > 1.50:
            s -= 15
            reasons.append(f"overbid {ratio:.0%} — overheated")
    elif not bid and price:
        s += 8
        reasons.append("no bids yet")

    # Price already cut since we first saw it (e.g. a second, cheaper round)
    drop = item.get("price_drop_pct")
    if drop and drop >= 25:
        s += 10
        reasons.append(f"price cut {drop:.0f}% since first seen")
    elif drop and drop >= 10:
        s += 5
        reasons.append(f"price cut {drop:.0f}% since first seen")

    # Ridiculous in absolute terms
    if pay >= 300:
        if pay <= 5000:
            s += 14
            reasons.append(f"very cheap: €{pay:,.0f}")
        elif pay <= 15000:
            s += 10
            reasons.append(f"cheap: €{pay:,.0f}")
        elif pay <= 30000:
            s += 6
            reasons.append(f"€{pay:,.0f}")
        elif pay <= 60000:
            s += 2

    # ── How the sale works ────────────────────────────────────────────
    if has_term(full, SEALED_BID_PATTERNS, negations=False):
        s += 20
        reasons.append("sealed-bid (carta fechada)")

    if source in FORCED_SOURCES:
        s += 12
        reasons.append("forced sale (must sell)")
    if source in TAX_SOURCES:
        s += 8
        reasons.append("tax seizure — no reserve")

    # Minimum bid signal (one bonus per listing: these all describe the same fact)
    min_p = item.get("min_price") or 0
    if source == "citius" and not price:
        s += 18
        reasons.append("Citius no-minimum court sale")
    elif min_p and min_p <= 500 and price and price > 1000:
        s += 18
        reasons.append(f"min bid only €{min_p:.0f}")
    elif not min_p and source in FORCED_SOURCES:
        s += 10
        reasons.append("no minimum bid")

    # Urgency. days_left() understands naive dates; the old tz-aware subtraction
    # raised on them, so most sources never got this bonus.
    left = days_left(item.get("date_end"), now or utcnow())
    if left is not None and 0 < left <= 3:
        s += 8
        reasons.append(f"{left * 24:.0f}h left — urgent" if left < 1 else f"{left:.0f}d left — urgent")
    elif left is not None and 0 < left <= 7:
        s += 4
        reasons.append(f"{left:.0f}d left")

    if price and price < 300:
        s -= 20
        reasons.append("suspiciously cheap — likely tiny/worthless")

    for label, cap in NOT_THE_GOAL_CAP.items():
        if any(r.startswith(label) for r in reasons):
            s = min(s, cap)
    return s, reasons


def _home_points(item: dict, full: str, area: float, pay: float, reasons: list[str]) -> float:
    """A home should be in a good place and not need heavy work."""
    s = 0.0
    if has_term(full, HEAVY_WORK):
        s -= 25
        reasons.append("needs heavy work (ruin / full rebuild)")
    elif has_term(full, SOME_WORK):
        s -= 10
        reasons.append("needs some work")
    elif has_term(full, GOOD_CONDITION):
        s += 10
        reasons.append("good condition")

    if has_term(full, ISOLATED):
        s -= 20
        reasons.append("isolated location")
    else:
        spot = find_terms(full, GOOD_LOCATION, negations=False)
        town = _known_town(item)
        if spot:
            s += 8
            reasons.append(f"good location ({spot[0]})")
        elif town:
            s += 8
            reasons.append(f"in {town} (town with services)")

    if area and area < 30:
        s -= 5
        reasons.append(f"very small ({area:.0f} m²)")
    elif area >= 60:
        s += 4
        reasons.append(f"{area:.0f} m²")

    # Below the local price per m² (homes only: land is not priced like buildings)
    mv = market_value_estimate(item)
    if mv and pay and area <= 1000:
        market_disc = (mv - pay) / mv
        if market_disc > 0.60:
            s += 15
            reasons.append(f"{market_disc:.0%} below local prices")
        elif market_disc > 0.40:
            s += 10
            reasons.append(f"{market_disc:.0%} below local prices")
        elif market_disc > 0.20:
            s += 5
            reasons.append(f"{market_disc:.0%} below local prices")
    return s


def _rural_points(area: float, pay: float, t: dict, reasons: list[str]) -> float:
    """A rural plot is only interesting when it is big and cheap per m²."""
    min_m2, max_eur = t["rural_min_m2"], t["rural_max_eur_m2"]
    if not area:
        reasons.append("rural plot, size unknown")
        return -12
    if area < min_m2:
        reasons.append(f"rural plot too small ({_ha(area)} < {_ha(min_m2)})")
        return -30
    s = 15.0 if area >= 5 * min_m2 else 10.0
    reasons.append(f"big rural plot ({_ha(area)})")
    if pay:
        per_m2 = pay / area
        if per_m2 <= max_eur / 2:
            s += 15
            reasons.append(f"very cheap land (€{per_m2:.2f}/m²)")
        elif per_m2 <= max_eur:
            s += 8
            reasons.append(f"cheap land (€{per_m2:.2f}/m²)")
        else:
            s -= 20
            reasons.append(f"dear for rural land (€{per_m2:.2f}/m²)")
    return s


def _known_town(item: dict) -> str | None:
    """The listing's town, if it is one of the towns we have local prices for
    (district capitals and cities: places with services). In Portugal
    `district` is the district, not the town: a village in the Guarda district
    is not Guarda, so only the concelho counts there."""
    country = item.get("country") or "PT"
    table = _MARKET_INDEX.get(country, {})
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    if place and _place_key(place) in table:
        return place.strip()
    return None


GOLD_KW = ["ouro", "joalharia", "bijutaria", "relojoaria", "cautela"]
VEHICLE_KW = [
    "veículo", "veiculo", "automóvel", "automovel", "peugeot", "renault",
    "volkswagen", "toyota", "ford", "opel", "smart", "hyundai", "volvo",
    "bmw", "mercedes", "audi", "citroen", "fiat", "seat", "motociclo",
    "moto", "ligeiro", "pesado de mercadorias", "matricula", "matrícula",
]
IMOVEL_TYPES = {normalize(t) for t in (
    "apartamento/moradia", "apartamento", "moradia", "loja/escritorio",
    "terreno_urbano", "terreno_rustico", "armazem", "outro_imovel",
    "hotel", "industrial", "garagem", "terreno", "inmueble", "nekretnina",
    "immobilier", "immobile", "vastgoed", "nieruchomosc", "akinito",
    "imovel", "imóvel", "loja", "escritório", "prédio", "residencial",
    "residential", "house", "apartment", "land",
    # Spanish and French portals type their listings in their own words
    "vivienda", "piso", "chalet", "casa", "local", "local comercial", "suelo", "solar",
    "finca", "nave", "garaje", "trastero", "edificio",
    "maison", "appartement", "terrain", "immeuble", "logement", "local commercial",
    "villa", "pavillon",
)}
IMOVEL_TITLE_WORDS = [
    "prédio", "terreno", "moradia", "apartamento", "fração", "quinta",
    "herdade", "floresta", "casa", "vivienda", "inmueble", "maison",
    "appartement", "immobile", "appartamento", "woning", "woonhuis",
    "piso", "chalet", "adosado", "finca", "immeuble", "terrain", "logement", "pavillon",
]


def categorize(item: dict) -> str:
    title = item.get("title") or ""
    tipo = normalize(item.get("tipo"))

    if has_term(title, GOLD_KW, negations=False):
        return "ouro_joias"
    if has_term(title, VEHICLE_KW, negations=False):
        return "outros"
    if tipo in IMOVEL_TYPES or has_term(title, IMOVEL_TITLE_WORDS, negations=False):
        return "imoveis"
    return "outros"
