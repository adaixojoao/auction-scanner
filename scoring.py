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

# INE 2024 average price/m² by distrito
PRICE_PER_M2 = {
    "Lisboa": 4200, "Porto": 3100, "Cascais": 4800, "Sintra": 2800,
    "Braga": 1800, "Coimbra": 1900, "Faro": 2600, "Setúbal": 2200,
    "Guarda": 900, "Viseu": 1100, "Castelo Branco": 850, "Beja": 750,
    "Évora": 1400, "Portalegre": 700, "Viana do Castelo": 1300,
    "Vila Real": 950, "Bragança": 800, "Santarém": 1100,
    "Leiria": 1500, "Aveiro": 1700,
    "Ilha da Madeira": 2000, "Ilha de São Miguel": 1200,
}


def market_value_estimate(item: dict) -> float | None:
    district = (item.get("district") or "").strip()
    area = item.get("area_m2") or 0
    if not district or not area or area < 5:
        return None
    for name, ppm2 in PRICE_PER_M2.items():
        if name.lower() in district.lower() or district.lower() in name.lower():
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
