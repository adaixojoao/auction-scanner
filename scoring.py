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

from common import days_left, has_term, normalize, utcnow

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

RURAL_TYPES = {
    "terreno_rustico", "terreno", "rustico", "agricola",
    "florestal", "quinta", "herdade",
}
RURAL_WORDS = ["rústico", "rustico", "floresta", "mata", "quinta"]

DWELLING_WORDS = [
    "moradia", "apartamento", "vivienda", "appartement",
    "maison", "woonhuis", "appartamento", "casa", "casas", "logement",
    "tussenwoning", "vivenda", "habitação",
]

RUIN_WORDS = ["ruin*", "arruinad*", "rudere"]

PARKING_WORDS = ["parking", "garagem", "garage", "garaje", "box", "emplacement",
                 "magazzino", "estacionamento"]

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
    place = (item.get("concelho") or item.get("district") or "").strip()
    if not place:
        return None
    table = _MARKET_INDEX.get(item.get("country") or "PT", {})
    ppm2 = table.get(_place_key(place))
    return area * ppm2 if ppm2 else None


def _occupation(item: dict) -> str | None:
    raw = item.get("raw_json") or ""
    if '"occupation"' not in raw:
        return None
    try:
        return json.loads(raw).get("occupation")
    except (TypeError, ValueError, AttributeError):
        return None


def score(item: dict, now: datetime | None = None) -> tuple[float, list[str]]:
    s = 50.0
    reasons: list[str] = []

    title   = item.get("title") or ""
    desc    = item.get("description") or ""
    full    = f"{title} {desc}"
    price   = item.get("price")   or 0
    bid     = item.get("current_bid") or 0
    area    = item.get("area_m2") or 0
    source  = item.get("source",  "")
    tipo    = normalize(item.get("tipo"))
    title_n = normalize(title)

    if has_term(title, FRAC_PATTERNS, negations=False):
        return 0, ["fractional share — skip"]

    if has_term(full, USUFRUCT_PATTERNS):
        return 0, ["usufruct — skip"]

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

    # Discount depth
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

    # Sealed-bid bonus
    if has_term(full, SEALED_BID_PATTERNS, negations=False):
        s += 20
        reasons.append("sealed-bid (carta fechada)")

    # Source quality
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

    # Property type
    if has_term(title, DWELLING_WORDS, negations=False):
        s += 12
        reasons.append("full dwelling")

    if has_term(title, RUIN_WORDS, negations=False):
        s += 5
        reasons.append("ruins — cheap entry")
    if tipo in RURAL_TYPES or has_term(title, RURAL_WORDS, negations=False):
        s += 8
        reasons.append("rural/land — conservation potential")

    # Size
    if area and area > 50:
        s += 5
        reasons.append(f"{area:.0f}m²")
    if area and area > 120:
        s += 5

    # Price sweet spot
    if price and 1000 <= price <= 30000:
        s += 8
        reasons.append("price sweet spot ≤30k")
    elif price and 30000 < price <= 60000:
        s += 3

    # Market value discount
    mv = market_value_estimate(item)
    if mv and price and mv > 0:
        market_disc = (mv - price) / mv
        if market_disc > 0.60:
            s += 15
            reasons.append(f"{market_disc:.0%} below market estimate")
        elif market_disc > 0.40:
            s += 10
            reasons.append(f"{market_disc:.0%} below market estimate")
        elif market_disc > 0.20:
            s += 5
            reasons.append(f"{market_disc:.0%} below market")

    # Urgency. days_left() understands naive dates; the old tz-aware subtraction
    # raised on them, so most sources never got this bonus.
    left = days_left(item.get("date_end"), now or utcnow())
    if left is not None and 0 < left <= 3:
        s += 8
        reasons.append(f"{left * 24:.0f}h left — urgent" if left < 1 else f"{left:.0f}d left — urgent")
    elif left is not None and 0 < left <= 7:
        s += 4
        reasons.append(f"{left:.0f}d left")

    # Suspiciously cheap
    if price and price < 300:
        s -= 20
        reasons.append("suspiciously cheap — likely tiny/worthless")

    # Parking/storage only
    if has_term(title, PARKING_WORDS, negations=False):
        s -= 12
        reasons.append("parking/storage only")

    return max(0.0, min(100.0, s)), reasons


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
