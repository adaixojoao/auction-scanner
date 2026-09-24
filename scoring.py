"""
scoring.py — Deep-discount / charity acquisition scorer.
Goal: find properties significantly below market price for charitable use.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone


FRAC_PATTERNS = [
    "1/2", "1/3", "1/4", "1/5", "1/6", "1/7", "1/8", "1/9",
    "1/10", "1/11", "1/12", "1/14", "1/16", "1/20", "1/24",
    "avos", "quota", "quinhão", "quinhao", "quota-parte",
    "fração ideal", "fracao ideal", "parte indivisa", "compropriedade",
]

OCCUPANCY_PATTERNS = [
    "ocupado", "arrendado", "arrendatário", "inquilino",
    "occupied", "tenant", "locataire", "affittuario",
]

ACCESS_PATTERNS = [
    "sem acesso", "acesso condicionado", "sem servidão",
    "encravado", "landlocked",
]

USUFRUCT_PATTERNS = [
    "usufruto", "usufruct", "nue-propri", "direito de uso",
    "direito de habitação",
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

DWELLING_WORDS = [
    "moradia", "apartamento", "vivienda", "appartement",
    "maison", "woonhuis", "appartamento", "casa", "logement",
    "tussenwoning", "vivenda", "habitação",
]

RUIN_WORDS = ["ruína", "ruina", "ruine", "rudere", "ruin", "arruinado"]

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


def market_value_estimate(item: dict) -> float | None:
    area = item.get("area_m2") or 0
    if not area or area < 5:
        return None
    country = item.get("country", "PT")
    concelho = (item.get("concelho") or item.get("district") or "").strip()
    if not concelho:
        return None
    country_data = MARKET_PRICE_PER_M2.get(country, {})
    for name, ppm2 in country_data.items():
        if name.lower() in concelho.lower() or concelho.lower() in name.lower():
            return area * ppm2
    return None


def score(item: dict) -> tuple[float, list[str]]:
    s = 50.0
    reasons: list[str] = []

    title   = (item.get("title")       or "").lower()
    desc    = (item.get("description") or "").lower()
    full    = f"{title} {desc}"
    price   = item.get("price")   or 0
    bid     = item.get("current_bid") or 0
    area    = item.get("area_m2") or 0
    source  = item.get("source",  "")
    tipo    = (item.get("tipo")   or "").lower()

    if any(p in title for p in FRAC_PATTERNS):
        return 0, ["fractional share — skip"]

    if any(p in full for p in USUFRUCT_PATTERNS):
        return 0, ["usufruct — skip"]

    if any(p in full for p in OCCUPANCY_PATTERNS):
        s -= 25
        reasons.append("occupied/tenanted")

    if any(p in full for p in ACCESS_PATTERNS):
        s -= 20
        reasons.append("no road access")

    if "direito" in title and ("herança" in title or "heranca" in title):
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

    # Sealed-bid bonus
    if any(p in full for p in SEALED_BID_PATTERNS):
        s += 20
        reasons.append("sealed-bid — set your own price")

    # Source quality
    if source in FORCED_SOURCES:
        s += 12
        reasons.append("forced sale (must sell)")
    if source in TAX_SOURCES:
        s += 8
        reasons.append("tax seizure — no reserve")
    if source == "citius" and (not price or price == 0):
        s += 18
        reasons.append("Citius no-minimum court sale")

    # Minimum bid signal
    min_p = item.get("min_price") or 0
    if min_p and min_p <= 500 and price and price > 1000:
        s += 18
        reasons.append(f"min bid only €{min_p:.0f}")
    elif not min_p and source in FORCED_SOURCES:
        s += 10
        reasons.append("no minimum bid")

    # Property type
    if any(w in title for w in DWELLING_WORDS):
        s += 12
        reasons.append("full dwelling")

    if any(w in title for w in RUIN_WORDS):
        s += 5
        reasons.append("ruins — cheap entry")
    if tipo in RURAL_TYPES or any(w in title for w in ["rústico", "rustico", "floresta", "mata", "quinta"]):
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
    elif price and 30001 <= price <= 60000:
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

    # Urgency
    if item.get("date_end"):
        try:
            end = datetime.fromisoformat(item["date_end"].replace("Z", "+00:00"))
            days_left = (end - datetime.now(timezone.utc)).days
            if 0 < days_left <= 3:
                s += 8
                reasons.append(f"{days_left}d left — urgent")
            elif 0 < days_left <= 7:
                s += 4
                reasons.append(f"{days_left}d left")
        except (ValueError, TypeError):
            pass

    # Suspiciously cheap
    if price and price < 300:
        s -= 20
        reasons.append("suspiciously cheap — likely tiny/worthless")

    # Parking/storage only
    if any(w in title for w in ["parking", "garagem", "garage", "box", "emplacement", "magazzino"]):
        s -= 12
        reasons.append("parking/storage only")

    return max(0.0, min(100.0, s)), reasons


def categorize(item: dict) -> str:
    title = (item.get("title") or "").lower()
    tipo  = (item.get("tipo")  or "").lower()

    GOLD_KW    = {"ouro", "joalharia", "bijutaria", "relojoaria", "cautela"}
    VEHICLE_KW = {
        "veículo", "veiculo", "automóvel", "automovel", "peugeot", "renault",
        "volkswagen", "toyota", "ford", "opel", "smart", "hyundai", "volvo",
        "bmw", "mercedes", "audi", "citroen", "fiat", "seat", "motociclo",
        "moto ", "ligeiro", "pesado de mercadorias", "matricula",
    }
    IMOVEL_TYPES = {
        "apartamento/moradia", "apartamento", "moradia", "loja/escritorio",
        "terreno_urbano", "terreno_rustico", "armazem", "outro_imovel",
        "hotel", "industrial", "garagem", "terreno", "inmueble", "nekretnina",
        "immobilier", "immobile", "vastgoed", "nieruchomosc", "akinito",
        "imovel", "imóvel",
    }

    if any(kw in title for kw in GOLD_KW):
        return "ouro_joias"
    if any(kw in title for kw in VEHICLE_KW):
        return "outros"
    if (tipo in IMOVEL_TYPES
            or "prédio" in title or "terreno" in title
            or "moradia" in title or "apartamento" in title
            or "fração" in title or "quinta" in title
            or "herdade" in title or "floresta" in title):
        return "imoveis"
    return "outros"
