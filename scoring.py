"""
scoring.py — Deep-discount / charity acquisition scorer.
Goal: find properties significantly below market price for charitable use.

Every keyword is matched as a whole word (see common.term_regex), accent- and
case-insensitively. Substring matching is what made "desocupado" count as
occupied, "Casal do ..." count as a house and "11/2023" count as a 1/2 share.
Occupancy/usufruct terms also ignore negated mentions ("não arrendado").
"""
from __future__ import annotations

import contextvars
import functools
import json
import math
import re
from datetime import datetime

from common import (days_left, effective_end, find_area, find_terms, has_term, normalize,
                    price_to_pay as _pay, term_regex, utcnow)
import prices
from prices import place_key as _place_key

FRAC_PATTERNS = [
    "1/2", "1/3", "1/4", "1/5", "1/6", "1/7", "1/8", "1/9",
    "1/10", "1/11", "1/12", "1/14", "1/16", "1/20", "1/24",
    "2/3", "3/4",
    "um meio", "metade", "mitad indivisa",
    "avos", "quota", "quota-parte", "quinhão", "quinhao",
    "fração ideal", "fracao ideal", "parte indivisa", "compropriedade",
    "meação", "quote-part", "cuota indivisa",
]

# Any other "N/M" share ("29/84 de imóvel", "(4986/100000)"). Not dates
# ("11/2023"), references ending in a year ("DMI-1014/2026"), Citius case
# numbers ("12/18.0T8…") or door numbers ("nº 12/14").
_SHARE_RE = re.compile(r"(?<![\d/.])(\d{1,6})\s*/\s*(\d{1,6})(?![\d/.])")
_HOUSE_NUMBER_RE = re.compile(r"(?:\bn\.?\s*[ºo°]|\bn[uú]mero|\bporta)\s*$", re.I)


# A share stated as a percentage, usually only in the description.
_PERCENT_SHARE = re.compile(
    r"(?:\b(?:el|un|o|uma?)\s+)?\b\d{1,2}(?:[.,]\d+)?\s*%?\s+(?:del|de la|do|da|de)\s+"
    r"(?:pleno dominio|plena propiedad|propiedad|pleno dominio|nuda propiedad|propriedade|dominio"
    r"|solar|inmueble|finca|terreno|piso|vivienda|parcela)\b"
    r"|\bproindiviso\b|\bpro indiviso\b|\bindiviso\b|\bparticipaci[oó]n indivisa\b"
    r"|\b\d{1,2}/\d{1,2}\s+(?:de(?:l| la| los| las)?|do|da)\s+"
    r"(?:indiviso|propiedad|inmueble|finca|terreno|piso|vivienda|parcela|solar)\b")


# A share named in words in the text: Slovak/Czech, Croatian, Bulgarian court sales.
SHARE_WORDS = ["spoluvlastnícky podiel", "spoluvlastnický podíl", "spoluvlastníckeho podielu",
               "suvlasnički dio", "suvlasnički udio", "идеална част", "идеални части"]


# Co-ownership sold as a holiday home: "compartes el inmueble con otros 5 socios".
CO_OWNERSHIP = ["copropiedad", "propiedad compartida", "compartes el inmueble", "compartir el inmueble",
                "fractional ownership", "co-ownership", "partihome", "comproprieta", "copropriété"]
_CO_OWNERS = re.compile(r"\bcon (?:otros|otras) \d+ (?:socios|propietarios|copropietarios|familias)\b")

# Not a home all year and not lettable: a seasonal bungalow on a campsite.
NOT_ALL_YEAR = ["no se puede alquilar", "no es posible como vivienda habitual", "no apto como vivienda habitual",
                "no puede ser vivienda habitual", "solo uso temporal", "es de uso temporal",
                "bungalow de temporada", "dentro de un camping", "en un camping"]


def is_co_ownership(text: str) -> bool:
    return bool(has_term(text or "", CO_OWNERSHIP, negations=False) or _CO_OWNERS.search(normalize(text or "")))


def is_percent_share(text: str) -> bool:
    return bool(_PERCENT_SHARE.search(normalize(text or ""))) or has_term(text or "", SHARE_WORDS, negations=False)


def is_fractional_share(title: str) -> bool:
    if has_term(title, FRAC_PATTERNS, negations=False):
        return True
    for m in _SHARE_RE.finditer(title or ""):
        part, whole = int(m.group(1)), int(m.group(2))
        if 1900 <= whole <= 2100:          # "11/2023", "DMI-1014/2026": a date or a reference
            continue
        if _HOUSE_NUMBER_RE.search(title[:m.start()]):   # "nº 12/14": door numbers
            continue
        if 0 < part < whole:
            return True
    return False


OCCUPANCY_PATTERNS = [
    "nije slobodn*", "nisu slobodn*",
    "ocupado", "ocupada", "arrendado", "arrendada", "arrendatário*",
    "inquilino*", "occupied", "tenant*", "locataire*", "affittuari*",
    "ocupantes", "occupato", "occupata", "occupé", "occupée", "loué", "louée",
    "bail en cours", "okupa*",
    "verhuurd", "verhuurde", "huurder", "huurders",
    "sin posesión", "sin posesion", "sin la posesión",
    "contrato de arrendamento", "contratos de arrendamento", "arrendamento em vigor",
    # Servihabitat's card: no keys means "sin posesión" in 14 of 15 pages read.
    "llaves no disponibles",
]

VACANT_PATTERNS = [
    "devoluto", "devoluta", "desocupado", "desocupada",
    "livre de pessoas", "livre de ocupantes", "libre de ocupantes",
    "vacant", "libre d'occupation", "leegstaand", "libre de toute occupation",
    "vide de tout occupant", "inoccupé", "inoccupée", "sin ocupantes",
]

ACCESS_PATTERNS = [
    "sem acesso", "acesso condicionado", "sem servidão",
    "encravado", "encravada", "landlocked",
    # Land with no way in is worth little and cannot be logged or built on.
    # Only Portuguese was matched, so Spanish and French plots never lost a point.
    "sin acceso", "sin acceso rodado", "sin salida a camino", "enclavado", "enclavada",
    "sans accès", "sans acces", "enclavé", "enclavée", "non desservi", "non desservie",
    "senza accesso", "interclus*",
    "zonder ontsluiting", "niet ontsloten",
]

# Spanish social housing: only buyers who qualify, as their own home, at a capped resale price.
SUBSIDISED_HOUSING = ["vivienda protegida", "vivienda de protección oficial", "vpo",
                      "régimen de protección oficial", "vivienda de protección pública"]

UNFINISHED_HOUSE = ["vivienda en construcción", "vivienda en construccion", "casa en construcción",
                    "obra parada", "obra sin terminar", "obra inacabada", "construção inacabada",
                    "moradia inacabada", "em construção", "maison inachevée"]
NO_VIEWING = ["sin visitas previas", "subasta fácil", "subasta facil"]
# A bank flat nobody may visit: in practice someone lives in it.
NOT_VISITABLE = ["no visitable", "no se puede visitar", "sin posibilidad de visita", "não visitável"]

NOT_A_BUILDING = ["casa movel", "casa móvel", "casa prefabricada móvil", "mobile home", "mobil-home",
                  "mobilhome", "caravana residencial"]

USUFRUCT_PATTERNS = [
    "usufruto", "usufructo", "usufruct", "nue-propri*", "nuda proprietà",
    "nuda propiedad", "direito de uso", "direito de habitação",
]

# Timeshares: the use of a flat for some weeks a year, not the flat. Court sales
# describe them in the description only ("el uso y disfrute … durante la semana
# 37 de cada año"), so the whole text is checked.
TIMESHARE_PATTERNS = [
    "habitação periódica", "habitacao periodica", "multipropriedade", "tempo partilhado",
    "timeshare", "time-share", "time share", "timesharing", "time-sharing", "part-time", "parttime",
    "aprovechamiento por turno", "aprovechamiento por turnos", "multipropiedad", "tiempo compartido",
    "multipropriété", "multipropriete", "temps partagé", "jouissance à temps partagé",
    "multiproprietà", "multiproprieta",
]
_TIMESHARE_RE = re.compile(
    r"\b(?:semanas?|semaines?|settiman[ae])\b[^.;]{0,60}?\b(?:de cada|cada|por|chaque|ogni|every|each)\s+"
    r"(?:ano|año|annee|année|anno|year)\b"
    r"|\bdurante (?:a|la|as|las) semanas?\s+(?:n\.?\s*[ºo°]\s*)?\d+"
    r"|\(\s*semanas?\s+\d+\s*\)",
    re.I)


@functools.lru_cache(maxsize=100_000)
def is_timeshare(text: str) -> bool:
    return bool(has_term(text, TIMESHARE_PATTERNS, negations=False) or _TIMESHARE_RE.search(text or ""))


# Sales where you name the price: a sealed-bid letter or a private negotiation.
# Often no price is published at all, and that is the chance, not a gap.
OFFER_SALE_PATTERNS = ["negociação particular", "negociacao particular", "venda por negociação"]

SEALED_BID_PATTERNS = [
    "carta fechada", "proposta em carta", "propostas em carta",
    "venda por propostas", "sealed bid", "offre cachetée",
]

FORCED_SOURCES = {
    "citius", "financas", "zvg", "anaf", "aeat",
    "pvp_giustizia", "poland", "greece", "cyprus",
    "france", "encheres_publiques",   # French judicial auctions (licitor / encheres-publiques)
}


def forced_sale(item: dict) -> bool:
    """A court, tax or social-security source, or a listing the site marked judicial.

    Notaires sells both. The home scorer and the investment scorer both ask this
    before the no-minimum-bid bonus, so a judicial flag is not only a home bonus.
    """
    return (item.get("source") or "") in FORCED_SOURCES or bool(_raw(item).get("judicial"))

TAX_SOURCES = {"financas", "anaf", "aeat"}

# ─── What we are looking for ────────────────────────────────────────
# Homes and plots at ridiculous prices. Urban plots are fine; rural plots only
# when they are big AND cheap; homes in a good location that do not need heavy
# work. Everything else (shops, garages, storage) is not the goal.
#
# The two rural limits can be changed on the Settings page (config filters).
TARGET_DEFAULTS = {"rural_min_m2": 10000, "rural_max_eur_m2": 0.5}
# Land: nothing under 1 ha in Portugal (rural_min_m2) or 2.5 ha abroad is worth
# the owner's time (Sept 2026). Plots near Guarda are the ones wanted most.
PLOT_MIN_ABROAD_M2 = 25000
GUARDA = (40.5373, -7.2676)
GUARDA_POINTS = [(10, 20), (25, 16), (50, 10), (80, 5), (120, 0)]   # no longer scored (2026-09-26)
# Mainland western Europe (Iberia, France, Benelux, DACH, Italy). Islands and
# Central/Eastern Europe (HR, PL, …) sit a flat −20 below an otherwise equal listing.
WESTERN_CONTINENTAL_COUNTRIES = frozenset(
    {"PT", "ES", "FR", "DE", "BE", "NL", "LU", "AT", "CH", "IT"})
OFF_WESTERN_CONTINENTAL = -20
MAINLAND_PORTUGAL = 10   # the owner's home country: language, paperwork, near Guarda (2026-10-04)
# Rough boxes for Atlantic/Mediterranean islands that share those country codes.
_ISLAND_BOXES = (
    (36.5, 40.0, -32.0, -24.5, "Azores"),
    (32.0, 33.2, -17.5, -16.0, "Madeira"),
    (27.0, 29.5, -18.5, -13.0, "Canaries"),
    (38.5, 40.2, 1.0, 4.5, "Balearics"),
    (41.2, 43.1, 8.3, 9.7, "Corsica"),
    (36.4, 38.4, 12.0, 15.7, "Sicily"),
    (38.8, 41.4, 8.0, 9.9, "Sardinia"),
)
_ISLAND_WORDS = (
    "acores", "azores", "ilha de sao miguel", "vila franca do campo",
    "ponta delgada", "ilha da madeira", "porto santo", "canarias", "canary",
    "tenerife", "gran canaria", "lanzarote", "fuerteventura", "baleares",
    "mallorca", "mayorca", "menorca", "ibiza", "eivissa", "corse", "corsica",
    "sicilia", "sicily", "sardegna", "sardinia",
)
# ─── Weights (Settings → "How much each thing counts") ───────────────
# The owner's own dial for each part of the score, 0 (ignore) to 2 (double).
# Only the bonuses and penalties move: the rules (too hot, occupied, too
# small…) stay rules whatever the weights.
WEIGHTS = {
    "heat": "Summer heat by 2090",
    "water": "Water nearby",
    "beach": "Beach",
    "transport": "Airport and train station",
    "risks": "Fire and flood risk",
    "amoc": "Winter cold if the Atlantic current (AMOC) collapses",
    "price": "Low price",
    "sale": "How it is sold (sealed bids, forced sales, deadline)",
    "population": "Population scenario to 2050",
}
# EUROPOP2019, mentioned only as a long-hold caution. A region projected to grow
# scores nothing: the scenario is not a reason to buy. Below -10% by 2050 costs
# a little; below -20% costs a little more. The owner's weight scales both.
POP_OUTLOOK_FROM = -10
POP_OUTLOOK_STEEP = -20
POP_OUTLOOK_MILD = -2
POP_OUTLOOK_STEEP_POINTS = -4
_WEIGHTS: contextvars.ContextVar[dict] = contextvars.ContextVar("weights", default={})


def w(name: str) -> float:
    """The owner's weight for one part of the score (1 unless changed)."""
    v = _WEIGHTS.get().get(name)
    try:
        return max(0.0, min(2.0, float(v))) if v is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


UNCHECKED_CAP = 65   # not located or size unknown: below the minimum until checked
DOUBTFUL_HOME_EUR = 5000      # on a sale portal, a home cheaper than this is a rent, a deposit or a typo
BANK_PORTALS = {"aliseda", "altamira", "servihabitat", "solvia"}
DOUBTFUL_BANK_HOME_EUR = 10000   # banks never sell a whole, free home this cheap
# The Croatian coast: a home there under this is a placeholder price ("1.000 €") or bait.
DOUBTFUL_HR_COAST_HOME_EUR = 25000
HR_COAST_COUNTIES = {"istarska", "primorsko-goranska", "ličko-senjska", "zadarska", "šibensko-kninska",
                     "splitsko-dalmatinska", "dubrovačko-neretvanska"}
SALE_PORTALS = {"fotocasa", "imovirtual", "bienici", "greenacres", "servihabitat", "aliseda", "altamira", "pisos", "thinkspain", "solvia", "imot", "indexoglasi", "nehnutelnosti", "sslv", "safer"}
DOUBTFUL_LAND_EUR_M2 = 0.05   # land cheaper than this per m² has a wrong price or area
NO_PRICE_CAP = 55    # no figure at all, and not a sale where you name the price

# The long run (climate.py): summers no hotter than 35 °C in 50-70 years, water
# all year round, no fires, no floods. Heat is the mean daily maximum of the
# warmest month in 2081-2100 (SSP2-4.5, median of 13 models).
HEAT_POINTS = [(28, 10), (31, 8), (33, 4), (35, 0), (36, -12), (37, -25)]
TOO_HOT_C = 35.0          # the owner's limit: above it the listing is capped under the minimum score
REJECT_HOT_C = 37.0
TOO_HOT_CAP = 60
# Days a year above 35 °C by 2071-2100: the owner's rule is at most 7.
HOT_DAYS_POINTS = [(0, 10), (2, 8), (5, 4), (7, 0), (12, -12), (20, -25)]
TOO_MANY_HOT_DAYS = 7
REJECT_HOT_DAYS = 20
PERMANENT_WATER_POINTS = [(0.2, 15), (0.5, 12), (1.0, 6)]     # km → points (land); homes get half
# Water is a benefit only where it does not flood: under the JRC 100-year flood
# (any depth above this at the position) nearby water earns no bonus.
FLOOD_NO_WATER_BONUS_M = 0.0
# Days a year with FWI > 30 (high fire danger) in 2079-2098, RCP4.5.
FIRE_DANGER_POINTS = [(10, 3), (30, 0), (60, -8), (90, -15)]
HIGH_FIRE_DAYS = 30
VERY_HIGH_FIRE_DAYS = 60
DEEP_FLOOD_M = 1.0

# Whatever else is good about them (a court sale, no minimum bid…), these are
# not the goal, so their score stays under the default minimum score (45) and
# they are hidden unless you shortlist them.
NOT_THE_GOAL_CAP = {"not a home or plot": 35, "unclear what it is": 40, "needs heavy work": 40,
                    "isolated location": 40, "rejected:": 30,
                    # …and what belongs to another goal: each tab ranks one thing.
                    "a plot, not a place to live": 40, "a home, not land": 40}

# Rejected outright, whatever else looks good (the owner's rules, Sept 2026).
_REJECTS = [
    ("unfinished building", re.compile(r"inacabad[oa]|em tosco|por acabar|obra parada|constru[çc][ãa]o suspensa"
                                       r"|\bal rustico\b|\b(?:allo )?stato (?:al )?grezzo\b|\bal grezzo\b", re.I)),
    ("not in the land register", re.compile(
        r"n[ãa]o\s+descri(?:t)?o\s+na\s+(?:C\.?R\.?P|conservat)|omiss[oa]\s+(?:na\s+conservat|no\s+registo)", re.I)),
    ("occupied", None),                               # from the occupancy rules below
    ("land only sold together with another lot", re.compile(
        r"venda\s+conjunta|venda\s+em\s+conjunto|vendid[oa]s?\s+em\s+conjunto", re.I)),
]
# Small homes and plots and expensive homes are held down along curves
# (SMALL_HOME_CAP and the others next to score_detail).
SMALL_HOME_M2 = 40
# Land sold in the same case counts for a home when it costs at most this, or
# this share of the home's price.
CASE_LAND_MAX_EUR = 3000
CASE_LAND_SHARE = 0.4
SMALL_URBAN_PLOT_M2 = 1000   # under this it is a leftover, not a plot to invest in
EXPENSIVE_HOME_EUR = 60000

DWELLING_WORDS = [
    "moradia", "moradias", "apartamento", "vivenda", "habitação", "casa", "casas",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6",
    "vivienda", "chalet", "adosado", "unifamiliar",
    "maison", "appartement", "logement", "pavillon",
    "appartamento", "abitazione", "villetta",
    "wohnung", "haus", "einfamilienhaus", "zweifamilienhaus", "mehrfamilienhaus", "reihenhaus",
    "doppelhaushälfte", "woning", "woonhuis", "tussenwoning", "huis", "bungalow", "kuća", "house", "apartment",
]
# Household goods sold at auction ("Mobiliário de habitação", "Mobília de casa")
# are not homes. A text that names a building first is: "Moradia T3 com mobiliário".
MOVABLE_WORDS = ["mobiliário", "mobília", "móveis", "eletrodomésticos", "electrodomésticos", "recheio"]
BUILDING_WORDS = ["moradia", "apartamento", "prédio", "fração", "fracção", "vivenda", "casa",
                  "habitação", "andar"]
# What a portal types as not property at all (e-leilões tipoId 2–6).
NOT_PROPERTY_TYPES = {"veiculo", "equipamento", "mobiliario", "direitos"}


# "Lote de terreno destinado a construção de moradia" is a plot; "Terreno T0"
# (a portal's typology on land) is land, unless the text also names a house.
_PLOT_FOR_A_HOUSE = re.compile(
    r"(?:lote|terreno)[^.;]{0,60}?(?:destinad[oa]\s+a|para)\s+(?:a\s+)?construcao\s+de\s+(?:uma\s+)?(?:moradia|habitacao|casa|vivenda)"
    r"|\blote\s+(?:p/\s*|para\s+)?(?:moradia|habitacao|vivenda)\b|\bterreno\s+(?:p/\s*|para\s+)(?:moradia|habitacao|construcao)\b")
# A portal's own type that says land ("Terreno P/ Moradia", "Lote", "Terreno rústico").
_LAND_TYPE = re.compile(r"^\s*(?:terreno|lote|land|suelo|solar|terrain)\b")
_STARTS_AS_LAND = re.compile(r"\s*(?:terreno|lote de terreno|lote para construcao|predio rustico)\b")
_HOUSE_WORDS_NOT_TYPOLOGY = [w for w in DWELLING_WORDS if not re.fullmatch(r"t\d", w)]


def _first_at(text: str, terms) -> int | None:
    return _first_at_cached(text or "", tuple(terms))


@functools.lru_cache(maxsize=200_000)
def _first_at_cached(text: str, terms: tuple) -> int | None:
    norm = normalize(text)
    hits = [m.start() for t in terms for m in [term_regex(t).search(norm)] if m]
    return min(hits) if hits else None


def _is_household_goods(text: str) -> bool:
    goods = _first_at(text, MOVABLE_WORDS)
    building = _first_at(text, BUILDING_WORDS)
    return goods is not None and (building is None or goods < building)


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
    "florestal", "floresta", "mata", "pinhal", "mato", "olival", "oliveira*", "vinha", "eucaliptal",
    "terra de cultura", "terra com",
    "montado", "sobreiral", "pastagem", "cultura arvense", "sequeiro", "regadio", "herdade",
    "quinta", "finca rústica", "tierra de labor", "olivar", "viñedo",
    "terrain agricole", "terre agricole", "terres agricoles", "bois", "forêt", "prairie",
    "terreno agricolo", "bosco", "uliveto", "vigneto",
    "ackerland", "landwirtschaftsfläche", "grünland", "wald", "landbouwgrond", "weiland",
]
RURAL_TYPES = {normalize(t) for t in (
    "terreno_rustico", "rustico", "agricola", "florestal", "herdade", "finca", "rustica",
)}

# A stable, hayloft or shed sold under a "Casa en …" title. A house that also
# has one ("casa … con cuadra") stays a house: the dwelling word is in the text.
OUTBUILDING_WORDS = ["cuadra", "pajar", "establo", "cabaña", "cabana", "nave"]

OTHER_WORDS = [   # not a home and not a plot
    "parking", "garagem", "garage", "garaje", "box", "emplacement", "estacionamento",
    "lugar de garagem", "lugar de aparcamento", "lugar de estacionamento", "aparcamento",
    "arrecadação", "arrecadacao", "arrumos", "arrumo", "loja", "armazém", "armazem",
    "escritório", "escritorio", "pavilhão", "pavilhao", "industrial", "estabelecimento",
    "local comercial", "nave", "oficina", "trastero", "aparcamiento", "plaza de garaje",
    "commerce", "local commercial", "bureau", "entrepôt", "hangar", "cave",
    "negozio", "magazzino", "capannone", "ufficio", "posto auto",
    "stellplatz", "tiefgarage", "lager", "büro", "gewerbe*", "bedrijfspand", "kantoor",
    "bedrijfsruimte", "bedrijfshal*", "bedrijfshallen", "bedrijfsunit*", "winkelruimte",
    "hotel", "restaurante", "café",
    "lavandaria", "lavanderia", "rouparia", "portaria", "casa das máquinas", "ginásio", "sala de condomínio",
    "computador*", "ordenador*", "portátil*", "portateis", "portáteis",
]
OTHER_TYPES = {normalize(t) for t in (
    "loja/escritorio", "loja", "escritório", "armazem", "industrial", "garagem", "hotel",
    "local", "local comercial", "nave", "garaje", "trastero", "commerce",
)}
PARKING_WORDS = OTHER_WORDS   # older name

# Homes: how much work they need, and where they are.
HEAVY_WORK = [
    "ruin*", "arruinad*", "em ruínas", "para recuperar", "para reconstruir", "reconstrução",
    "para recuperação", "recuperação total", "necessita de recuperação", "a necessitar de recuperação",
    "carece de recuperação", "para reabilitação", "para reabilitar",
    "obras profundas", "reabilitação total", "reabilitação integral", "inabitável",     "sem telhado", "tejado caído",     "tejado caido", "tejado hundido", "cubierta hundida", "cubierta caída",
    "cubierta caida", "tejado derrumbado", "derruid*", "derrumbe",
    "telhado caído", "muito degradad*", "mau estado", "mal estado", "en mal estado", "para demolir", "demolição",
    "a reformar", "para reformar", "reforma integral", "para rehabilitar", "inhabitable",
    "a rehabilitar", "rehabilitación integral", "rehabilitacion integral", "para reforma", "reforma íntegra",
    "reforma integra", "para rehabilitación", "requiere rehabilitación", "a restaurar",
    "reformarla por completo", "reformar por completo", "reformarla completamente", "reforma completa",
    "necesita restauración", "necesita restauracion", "necesita ser restaurad*", "necesita ser reformad*",
    "restauración completa", "restauracion completa", "para restarurar", "para restarura",
    "à rénover", "a renover", "à restaurer", "travaux importants", "gros travaux", "en ruine",
    "à réhabiliter", "da ristrutturare", "da ristrutturare integralmente",
    "ristrutturazione integrale", "ristrutturazione totale", "necessita di ristrutturazione",
    "rudere", "fatiscente", "inagibile", "non abitabile", "non abitabili",
    "pessimo stato", "in pessimo stato", "pessime condizioni", "in pessime condizioni",
    "pessime condizion*", "da rifare", "al grezzo", "allo stato grezzo",
    "al rustico", "allo stato rustico", "un rustico", "di un rustico", "di rustico",
    "fabbricato rustico", "immobile rustico",
    "priva di tramezzatura", "prive di tramezzature",
    "pericolo di crollo", "parzialmente crollat*", "crollat*",
    "dirut*", "semidirut*", "semi-dirut*", "cadente", "pericolante", "collabente",
    "in rovina", "rovinat*", "di rovina",
    "incompiut*", "non ultimat*", "scheletro", "da recuperare", "da ricostruire",
    "molto degradat*", "fortemente degradat*",
    "sanierungsbedürftig", "renovierungsbedürftig", "abrissreif", "baufällig", "ruine",
    "opknapper", "bouwvallig", "renovatie nodig",
    "ruševina*", "rušev*", "dotrajal*", "zapušten*",
    "основен ремонт", "цялостен ремонт", "руина", "порутен*", "срутен*", "без покрив", "груб строеж",
    "kompletná rekonštrukcia", "celková rekonštrukcia", "schátran*", "ruina", "neobývateľn*",   # Slovak
    # Abandoned: empty for years, falling apart ("devoluta" alone is only empty).
    "abandonad*", "ao abandono", "em abandono", "estado de abandono", "votad* ao abandono",
    "abbandonat*", "in stato di abbandono", "à l'abandon", "verwaerloosd", "verlaten",
]
RUIN_WORDS = HEAVY_WORK   # older name
SOME_WORK = [
    "necessita de obras", "precisa de obras", "necessitar de obras", "carece de obras",
    "obras de conservação", "degradad*", "degradat*", "in stato di degrado",
    "necesita reforma", "necesita reformas",
    # A fibre-cement roof has to come off even when the ad says the house is sound.
    "uralita", "fibrocemento", "amianto", "placas de amianto",
    "para remodelar", "a remodelar", "para renovar", "a renovar", "para restaurar",
    "para actualizar", "requiere reforma", "requiere reformas", "recomendable reforma", "necesita rehabilitación",
    "necesita rehabilitacion", "para finalizar", "por finalizar",
    "travaux à prévoir", "à rafraîchir", "a rafraichir", "en travaux", "partiellement en travaux",
    "travaux à réaliser", "da rimodernare",
    "da sistemare", "necessita di lavori", "necessita di interventi", "mediocre stato", "discreto stato",
    "scarsa manutenzione", "manutenzione straordinaria",
    "manutenzione scadente", "stato di manutenzione scadente", "scadente stato di manutenzione",
    "scadente stato di conservazione", "stato di conservazione scadente", "conservazione molto scadente",
    "modernisierungsbedürftig", "renovierungsbedarf",
    "kluswoning", "kluswoningen",          # Dutch: sold as a renovation project
    "potrebno renoviranje", "za renoviranje", "potrebno uređenje", "za uređenje",
    "за ремонт", "нуждае се от ремонт", "нужда от ремонт", "частичен ремонт",      # Bulgarian
    "na rekonštrukciu", "potrebná rekonštrukcia", "pôvodný stav", "čiastočná rekonštrukcia",   # Slovak
]
GOOD_CONDITION = [
    "bom estado", "excelente estado", "ótimo estado", "estado novo", "estado: novo", "em estado novo",
    "renovad*", "remodelad*", "recuperad*",
    "pronto a habitar", "como nov*", "construção recente",
    "buen estado", "reformad*", "a estrenar", "para entrar a vivir", "listo para vivir",
    "bon état", "très bon état", "rénové", "rénovée", "refait à neuf", "habitable de suite",
    "buono stato", "ottimo stato", "ristrutturat*",
    # bare "abitabile" matches "sottotetto abitabile" (a room type, not the house's state)
    "pienamente abitabile", "già abitabile", "pronto e abitabile", "pronta e abitabile",
    "immobile abitabile", "appartamento abitabile", "casa abitabile",
    "renoviert", "saniert", "modernisiert", "gepflegt", "bezugsfertig", "neuwertig",
    "goede staat", "gerenoveerd", "instapklaar",
    "dobrom stanju", "odličnom stanju",
    "след ремонт", "ремонтиран*", "реновиран*", "добро състояние", "отлично състояние", "готова за живеене",
    "po rekonštrukcii", "zrekonštruovan*", "novostavba", "dobrý stav", "výborný stav", "ihneď obývateľn*",
]
GOOD_LOCATION = [
    "centro da cidade", "centro da vila", "centro da localidade", "centro histórico", "no centro",
    "zona central", "junto ao centro", "perto do centro", "próximo do centro",
    "centro urbano", "zona urbana", "malha urbana", "perto de serviços", "próximo de serviços",
    "junto à praia", "perto da praia", "vista mar", "vista rio", "zona nobre",
    "centro de la ciudad", "céntrico", "céntrica", "casco histórico", "cerca de la playa",
    "centre-ville", "centre ville", "proche commerces", "proche des commerces", "hyper-centre",
    "centro storico", "zona centrale", "vicino al centro",
    "vicino al mare", "vista mare", "fronte mare", "vicino alla spiaggia",
    "bord de mer", "vue mer", "proche plage", "proche de la plage", "primera línea de playa",
    "aan zee", "zeezicht", "strandnah", "meerblick", "meeresnähe",
    "blizu mora", "pogled na more", "uz more", "blizu plaže",
    "до морето", "до плажа", "морска гледка", "първа линия",
    "innenstadt", "stadtmitte", "zentrale lage", "zentrumsnah", "centrum",
]
# Water next to a plot. Place names ("Rio Maior", "Albufeira", "Lagoa", "Ribeira
# de Pena") are not water: a water word counts only after words saying the plot
# touches it ("confronta com o rio", "junto à ribeira"), or as a set phrase.
_WATER = (r"(?:o |a |um |uma |pelo |pela |do |da |el |la |le |l')?"
          r"(?:rio|ribeira|ribeiro|regato|lago|lagoa|albufeira|barragem|charca|represa|a[cç]ude|mar|"
          r"r[ií]o|arroyo|embalse|pantano|rivi[eè]re|[eé]tang|lac|fleuve)")
WATER_RE = re.compile(
    r"\b(?:junto (?:a|ao|à|de|do|da)|perto (?:de|do|da)|pr[oó]ximo (?:de|do|da|ao|à)|"
    r"margens? (?:de|do|da)|frente (?:a|ao|à|para|de|do|da)|confronta\w* (?:com|a|ao|à)|"
    r"atravessad\w* (?:por|pelo|pela)|banhad\w* (?:por|pelo|pela)|limitad\w* (?:por|pelo|pela)|"
    r"(?:com )?acesso (?:a|ao|à)|junto al|orilla (?:de|del)|au bord (?:de|du|de la)|en bordure (?:de|du))\s+"
    + _WATER + r"\b"
    r"|\b(?:curso de [aá]gua|linha de [aá]gua|plano de [aá]gua|espelho de [aá]gua|frente de rio|frente rio)\b"
    # Italian, Dutch, German, Croatian: set phrases.
    r"|\b(?:(?:vicino|accanto|adiacente|affacciat\w|prospiciente|in riva) (?:al|allo|alla|a) "
    r"(?:fiume|lago|torrente|mare|canale)|fronte (?:lago|mare|fiume)|sulle rive del)\b"
    r"|\b(?:aan (?:het|de) (?:water|rivier|vaart|plas|meer|zee)|vaarwater|aan het ijsselmeer)\b"
    r"|\b(?:am (?:see|fluss|bach|ufer|meer)|seeufer|flussufer|wassergrundstück|seegrundstück)\b"
    r"|\b(?:uz (?:rijeku|more|jezero)|blizu (?:rijeke|mora|jezera)|na obali)\b"
    r"|\b(?:(?:до|край|на брега на) (?:река|реката|язовир|язовира|езеро|езерото|морето))\b"   # Bulgarian
    r"|\b(?:pri (?:rieke|jazere|vodnej nádrži|priehrade|Dunaji)|na brehu (?:rieky|jazera))\b",   # Slovak
    re.I)


@functools.lru_cache(maxsize=100_000)
def _water_words(text: str) -> str | None:
    m = WATER_RE.search(text)
    return m.group(0).strip() if m else None


def water_nearby(text: str, item: dict | None = None) -> str | None:
    """The words that put a plot next to water, else what the map found
    within a few hundred metres of its exact position (geo.py), or None."""
    words = _water_words(text or "")
    if words:
        return words
    if item and '"water_check"' in (item.get("raw_json") or ""):
        check = _raw(item).get("water_check") or {}
        found = check.get("found") or []
        if found:
            w = found[0]
            name = f"{w['kind']} {w['name']}" if w.get("name") else w.get("kind", "water")
            where = "about 1 km of the village" if check.get("approx") else f"{check.get('radius_m', 300)} m"
            return f"{name}, within {where} on the map" + (" (approx.)" if check.get("approx") else "")
    return None


# Not bare "isolada": "moradia isolada" is a detached house (and "vivienda aislada"
# in Spain), which is good, not a remote place.
ISOLATED = [
    "lugar isolado", "local isolado", "zona isolada", "sítio isolado", "sitio isolado", "muito isolad*",
    "isolado de tudo", "longe de tudo", "acesso difícil", "caminho de terra", "sem acessos",
    "zona aislada", "lugar aislado", "muy aislad*", "isolé", "isolée", "isolato", "isolata",
    "abgelegen", "alleinlage", "afgelegen",
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


def market_value_estimate(item: dict) -> float | None:
    """area × €/m² for the listing's municipality, if we have a figure for it.

    Matches the place name exactly (accent-insensitive). The old substring match
    priced Porto de Mós as Porto and anything containing "a" as something.
    """
    area = item.get("area_m2") or 0
    if not area or area < 5:
        return None
    found = local_price(item)
    return area * found[0] if found else None


def local_price(item: dict) -> tuple[float, str] | None:
    """(€/m² of homes where the listing is, source): every Portuguese municipality
    from INE (prices.py), else the city table. In Portugal only the concelho
    names the place (`district` is the district, not the town)."""
    country = item.get("country") or "PT"
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    return prices.local_price(country, place, _MARKET_INDEX, district=item.get("district"),
                              parish=item.get("freguesia") if country == "PT" else None)


def _mention_sold_move(item: dict, reasons: list[str]) -> None:
    """The published sale series moved since its previous figure. Shown only:
    the discount already uses the latest figure, so this does not change the score."""
    country = item.get("country") or "PT"
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    move = prices.sale_move(country, place, district=item.get("district"),
                            parish=item.get("freguesia") if country == "PT" else None)
    text = prices.sale_move_text(move)
    if text:
        reasons.append(text)


_OUTLOOK_NOTE = (
    " The EUROPOP2019 population scenario is only a small caution when a whole region is "
    "projected to lose many people by 2050: it is a scenario, not a forecast, and not a reason to buy."
)


def buyer_priorities(targets: dict | None = None, mode: str = "home") -> str:
    """One goal in words, for the AI check: the same rules as score(mode=…).

    The check used to be told a single mixed wish, so it judged a buy-to-let
    and a farm plot as if they had to be somewhere to live."""
    t = {k: (targets or {}).get(k) or v for k, v in TARGET_DEFAULTS.items()}
    if mode == "invest":
        return _INVEST_PRIORITIES + _OUTLOOK_NOTE
    if mode == "land":
        return _LAND_PRIORITIES.format(min_m2=f"{t['rural_min_m2']:,.0f}",
                                       max_eur_m2=f"{t['rural_max_eur_m2']:.2f}",
                                       max_eur_ha=f"{t['rural_max_eur_m2'] * 10000:,.0f}") + _OUTLOOK_NOTE
    if mode == "forest":
        return _FOREST_PRIORITIES.format(min_ha=FOREST_MIN_M2 // 10000, roi=f"{FOREST_ROI_TARGET:.0%}",
                                         years=FOREST_ROI_YEARS) + _OUTLOOK_NOTE
    return (
        "A home (a house or a flat) to live in, at a very low price — not a plot, not a shop or a "
        "garage. A MUST: somewhere to swim (the sea, a lake "
        "or a real river, not a stream) within 1.5 km. Prefer western continental Europe "
        "(mainland Portugal, Spain, France, Benelux, Germany, Austria, Switzerland, Italy); "
        "islands (Azores, Madeira, Canaries, Balearics, Corsica, Sicily, Sardinia) and "
        "Central/Eastern Europe sit a flat step below an otherwise equal listing. In order of "
        "preference: a house in "
        "good condition in a great location well under market price; a house needing some repairs, "
        "dirt cheap, in a great location; a house in good condition, dirt cheap, in an ordinary "
        "location. When the listing does not say "
        "the condition, treat it as needing work (not as a sound home). A plot is judged on the "
        "Investment land goal instead, where land of at least "
        f"{t['rural_min_m2']:,.0f} m² at most €{t['rural_max_eur_m2']:.2f}/m² (about "
        f"€{t['rural_max_eur_m2'] * 10000:,.0f} per hectare) is what counts. Not wanted: small or partial homes; "
        "homes needing heavy work (ruins, full rebuilds); expensive homes; isolated or bad "
        "locations; timeshares "
        "(a few weeks a year); shops, garages, storage, offices and other commercial premises "
        "(bedrijfsruimte, lojas). Without a published price — unless it is a sealed-bid or "
        "private-negotiation sale where you name the offer — the listing cannot be judged for "
        "cheapness and stays out of the top. Water is only a plus where the land does not flood; "
        "a home in a flood zone is a risk."
    ) + _OUTLOOK_NOTE


_INVEST_PRIORITIES = (
    "A home (a house or a flat) bought to make money on, not to live in: the gap between what it "
    "costs all-in — the price plus the transfer tax, the notary and registry, and the work it "
    "needs — and what homes sell for in that municipality, plus what it would let for. Wanted: far "
    "below the local price per m² after those costs; a net rental yield (after running costs and "
    "empty months) worth having; near the sea for a holiday let; a forced sale (court, tax or "
    "social security) or a bank selling what it repossessed, because those must sell. A sitting "
    "tenant is income only when the ad states the rent: the town average is not the lease, and "
    "it makes the home harder to resell. Say which it is here. Okupas, or a bank sale "
    "'sin posesión', are not a tenant: they are out. Homes still listed in that district on the "
    "sites scanned are supply a later buyer can choose among; say how many if you are told, and "
    "do not invent how far that cuts the price. "
    "Not wanted: a gap so wide it cannot be real (more than about 70% under the local price, a "
    "share of the property, a ruin sold as a home, an area that is really the plot) — say what to "
    "check, and do not treat that gap as a bargain or work out a rent on that price; a flat too "
    "small "
    "to let; somewhere the heat by 2071-2100 or a flood zone will take the value away. When the "
    "listing does not say the condition, treat it as needing work. Judge the money, not whether "
    "the buyer would enjoy living there."
)

_LAND_PRIORITIES = (
    "A plot bought as an investment, not to live on and not to farm by hand. What matters is the "
    "price per hectare against what land actually sells for in that district, so say if you think "
    "the comparison is wrong. Wanted: at least {min_m2} m², at most €{max_eur_m2}/m² (about "
    "€{max_eur_ha} per hectare); water on or beside it; a road a lorry can use; a town near "
    "enough that somebody will buy it from you; building land (urban, with a licence) is worth "
    "more than rustic land because anyone can buy it. Forestry — timber, cork, nuts, carbon — is "
    "upside on top, not the reason to buy. Not wanted: a plot too small; one with no access or "
    "landlocked; one sold only together with another lot; a share rather than the whole; a price "
    "per hectare so far below the local one that something is wrong (check the area, the access "
    "and the title) — more than about 70% under is a check, not a bargain; eucalyptus (fire-prone; "
    "new planting is normally restricted in Portugal); "
    "land the notice or a site check places in Natura 2000, a national park, REN or RAN, because planting, felling "
    "and building normally need permits. Land that floods is not land next to water."
)

_FOREST_PRIORITIES = (
    "Land for a forestry project: rustic or forest land of at least {min_ha} ha, as cheap per "
    "hectare as possible, with a climate trees will still stand in 2100 (summer heat, water "
    "stress, fire danger, and the summer water balance if the Atlantic current collapses). What "
    "pays is the standing timber the ad states plus the best crop the climate allows, over "
    "{years} years; the goal is about {roi} a year on the price including the buying costs. "
    "Wanted: already wooded or easily planted, a gentle slope, a forest track a timber lorry can "
    "reach, water on or by the land, cork or holm oak already growing, and a sale that can be "
    "closed sooner rather than later — there is no deadline of this year. The cheaper the "
    "hectare, the better. A plot from 5 ha can be a first buy; 10 ha is the project. Portugal "
    "comes first: you can visit it and the paperwork is in your language. Not wanted: steep ground "
    "only loggable by cable; land machines cannot reach; scrub and firewood-grade coppice; "
    "eucalyptus (fire-prone, and new planting is normally restricted in Portugal — converting to "
    "natives is the habitat case, not a timber crop); building land (suelo urbanizable, a "
    "development sector, futuros desarrollos); a national park or high mountain (about 1 800 m and "
    "above); protected areas (Natura 2000, REN, RAN) "
    "where felling and planting need permits; several scattered parcels; felling "
    "rights sold without the land. Say plainly when the ad does not give the volume or the age of "
    "the stand, because then the timber figure is a guess."
)


def off_western_continental(item: dict) -> str | None:
    """Why a listing is not on western continental Europe, or None when it is.

    Country first; then Azores/Madeira/Canaries/… by district, place name, postcode
    or coordinates. Used for a flat score step, not a hard reject."""
    country = (item.get("country") or "PT").upper()
    if country not in WESTERN_CONTINENTAL_COUNTRIES:
        return country

    region = prices.region_of_place(item.get("district"))
    if region in ("acores", "madeira"):
        return "Azores" if region == "acores" else "Madeira"

    blob = normalize(" ".join(str(item.get(k) or "") for k in
                              ("title", "description", "district", "concelho", "freguesia", "location")))
    if "sao joao da madeira" not in blob:
        for word in _ISLAND_WORDS:
            if word in blob:
                if word in ("acores", "azores", "ilha de sao miguel", "vila franca do campo",
                            "ponta delgada"):
                    return "Azores"
                if word in ("ilha da madeira", "porto santo"):
                    return "Madeira"
                if word in ("canarias", "canary", "tenerife", "gran canaria", "lanzarote",
                            "fuerteventura"):
                    return "Canaries"
                if word in ("baleares", "mallorca", "mayorca", "menorca", "ibiza", "eivissa"):
                    return "Balearics"
                if word in ("corse", "corsica"):
                    return "Corsica"
                if word in ("sicilia", "sicily"):
                    return "Sicily"
                if word in ("sardegna", "sardinia"):
                    return "Sardinia"
                return word

    # PT 9xxx = Azores/Madeira; ES 07 = Balearics, 35/38 = Canaries.
    m = re.search(r"\b(\d{4,5})(?:-\d{3})?\b",
                  f"{item.get('title') or ''} {item.get('description') or ''}")
    if m:
        code = m.group(1)
        if country == "PT" and len(code) == 4 and code.startswith("9"):
            return "Azores/Madeira"
        if country == "ES" and len(code) == 5:
            if code.startswith("07"):
                return "Balearics"
            if code.startswith(("35", "38")):
                return "Canaries"

    lat = lon = None
    try:
        raw = json.loads(item.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        geo = raw.get("geo") if isinstance(raw.get("geo"), dict) else {}
        lat = geo.get("lat") if geo.get("lat") is not None else raw.get("lat")
        lon = geo.get("lon") if geo.get("lon") is not None else raw.get("lon")
        at = (raw.get("climate") or {}).get("at") if isinstance(raw.get("climate"), dict) else None
        if lat is None and isinstance(at, str) and "," in at:
            try:
                lat, lon = (float(x) for x in at.split(",", 1))
            except ValueError:
                lat = lon = None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    for lat0, lat1, lon0, lon1, name in _ISLAND_BOXES:
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            return name
    return None


# "Villa" before a capitalised name after a place marker is a place (Italian
# cadastral "C.C. Villa Banale", "frazione Villa Rosa"), not a detached house.
_VILLA_PLACE = re.compile(r"\b(C\.\s?C\.|loc\.|localit[aà]|frazione|fraz\.|comune di|in|a|di)\s+Villa\s+(?=[A-Z])")


# The description opening on what the thing is ("A. Piena proprietà di ufficio…")
# beats a portal category that says home (Astalegale files it under "Abitazione").
_DESC_OPENS_AS_OTHER = re.compile(
    r"^\W*(?:[a-z]\W+)?(?:(?:diritto di |la )?(?:piena |intera |ed |e )*proprieta (?:di|su|del|della) "
    r"(?:un[oa']? ?)?)?(?:ufficio|uffici|negozio|magazzino|capannone|laboratorio|box auto|garage|posto auto)\b")


# Portals title everything "Casa en X" / "Maison à X"; the first words of the
# description say what it really is. A barn, granary or bare rural plot is not a home.
_DESC_OPENS_AS_OUTBUILDING = re.compile(
    r"^\W*(?:se vende |vendo |a saisir \W*)?(?:une |una |un |ancienne |belle |grande |vieille )*"
    r"(?:grange|granges|panera|horreo|hangar|ecurie|cabanon|palheiro|curral)\b"
    r"|^\W*(?:se vende |vendo |venta de )?(?:una |la )?(?:finca con )?cuadra\b")
_DESC_OPENS_AS_FINCA = re.compile(
    r"^\W*(?:se vende |vendo )?(?:una |un |gran |bonita )*(?:finca (?:rustica|de recreo)|parcelas?"
    r"|terreno(?: grande| rustico| agrario)?)\b"
    r"|^\W*(?:\w+\W+){0,8}?(?:se vende |vendo )?(?:una )?(?:preciosa |bonita )?parcela rustica\b")
_SELLS_A_PLOT = re.compile(
    r"\bse vende (?:una |un )?(?:preciosa |bonita |gran )?(?:parcela|finca rustica|terreno)\b"
    r"|\bactualmente es una parcela\b"
    r"|\bpresentamos (?:esta|una) (?:\w+ )?finca de\b")
_FINCA_WITH_HOUSE = re.compile(r"\b(?:con|y|incluye) (?:una |la |su )?(?:casa|vivienda|edificacion)")


def _sold_as_outbuilding(desc: str) -> bool:
    """The description is a barn, stable or shed, and never names a house."""
    if not desc or has_term(desc, _HOUSE_WORDS_NOT_TYPOLOGY, negations=False):
        return False
    return has_term(desc, OUTBUILDING_WORDS, negations=False)


def property_kind(item: dict) -> str | None:
    """"home", "urban_plot", "rural_plot", "other" (shop, garage, storage…) or
    None when the listing does not say. The title and the portal's own type
    decide first; the description only when they say nothing."""
    title, desc = item.get("title") or "", item.get("description") or ""
    tipo = normalize(item.get("tipo"))
    area = item.get("area_m2") or find_area(title) or find_area(desc) or 0

    urban_words = URBAN_PLOT_WORDS + (["solar"] if item.get("country") == "ES" else [])

    def kind_of(text: str) -> str | None:
        text = _VILLA_PLACE.sub(r"\1 ", text)
        if _is_household_goods(text):
            return "other"
        norm = normalize(text)
        if _PLOT_FOR_A_HOUSE.search(norm) or (_STARTS_AS_LAND.match(norm) and (
                not has_term(text, _HOUSE_WORDS_NOT_TYPOLOGY, negations=False)
                # "Prédio rústico …, Casa Caída": a place name, not a house on it
                or norm.startswith("predio rustico") and not has_term(text, ["com casa", "com moradia"],
                                                                     negations=False))):
            # The title says land; whether rural often only the description says.
            if has_term(f"{text} {desc}", RURAL_WORDS, negations=False) or area >= 5000:
                return "rural_plot"
            return "urban_plot"
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

    ndesc = normalize(desc)
    if tipo in NOT_PROPERTY_TYPES or _DESC_OPENS_AS_OTHER.match(ndesc) or _DESC_OPENS_AS_OUTBUILDING.match(ndesc):
        return "other"
    if ((_DESC_OPENS_AS_FINCA.match(ndesc) or _SELLS_A_PLOT.search(ndesc[:300]))
            and not _FINCA_WITH_HOUSE.search(ndesc[:300])):
        big = max(area, find_area(desc) or 0)       # the ad's area is often the cabin's
        return "rural_plot" if big >= 1000 or has_term(desc, RURAL_WORDS, negations=False) else "urban_plot"
    # "Vivienda en Villaviciosa" whose text is only "antigua cuadra y pajar".
    if _sold_as_outbuilding(desc):
        return "other"
    if _LAND_TYPE.match(tipo) and not has_term(title, _HOUSE_WORDS_NOT_TYPOLOGY + ["com casa", "com moradia"],
                                                negations=False) or _PLOT_FOR_A_HOUSE.search(normalize(title)):
        # The portal says land (or "Lote Moradia"): a plot, whatever house word follows.
        if has_term(f"{title} {desc}", RURAL_WORDS, negations=False) or area >= 5000:
            return "rural_plot"
        return "urban_plot"
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


def _raw(item: dict) -> dict:
    try:
        raw = json.loads(item.get("raw_json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


# Photo notes (English, from photos.py) that the seller's "good condition" hides.
_PHOTO_ROOF_HEAVY = ["collapsed roof", "roof collapsed", "fallen roof", "roof has collapsed",
                     "collapsed barn", "barn roof"]
_PHOTO_ROOF_SOME = ["uralita", "asbestos", "fibre-cement", "fiber-cement", "fibre cement",
                    "fiber cement", "fibrocement"]


def condition(item: dict) -> str:
    """"heavy", "some", "good" or "unknown": how much work the listing admits
    to, else what its photos show (photos.py) when the text says nothing.

    A fallen roof or fibre-cement sheets in the photos raise the estimate even
    when the ad says the house is sound. A barn photo that does not say so
    still says nothing about the house."""
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    if has_term(text, HEAVY_WORK):
        state = "heavy"
    elif has_term(text, SOME_WORK):
        state = "some"
    elif has_term(text, GOOD_CONDITION):
        state = "good"
    else:
        seen = photo_condition(item)
        state = seen["condition"] if seen else "unknown"
    notes = ""
    if '"photo_check"' in (item.get("raw_json") or ""):
        notes = str((_raw(item).get("photo_check") or {}).get("notes") or "")
    if state != "heavy" and has_term(notes, _PHOTO_ROOF_HEAVY, negations=False):
        return "heavy"
    if state in ("good", "unknown") and has_term(notes, _PHOTO_ROOF_SOME, negations=False):
        return "some"
    return state


def photo_condition(item: dict) -> dict | None:
    """What the photos showed, when the check was sure enough to use."""
    if '"photo_check"' not in (item.get("raw_json") or ""):
        return None
    seen = _raw(item).get("photo_check") or {}
    if seen.get("shows_house") is False:           # a barn's state says nothing about the house
        return None
    if seen.get("condition") in ("good", "some", "heavy") and seen.get("confidence") in ("high", "medium"):
        return seen
    return None


_BUILT = re.compile(r"(?:ano\s+de\s+constru[çc][ãa]o|constru[íi]d[oa]\s+em|built\s+in)\D{0,5}((?:18|19|20)\d\d)", re.I)


def built_year(item: dict) -> int | None:
    """The year the building was built: the portal's field, else the text."""
    year = _raw(item).get("ano_construcao")
    if not year:
        m = _BUILT.search(f"{item.get('title') or ''} {item.get('description') or ''}")
        year = m.group(1) if m else None
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None
    return year if 1700 <= year <= 2100 else None


PROVINCE_AVERAGE_VALUE = 0.7   # a village home against its province's average (cities included)


def local_value_factor(item: dict, state: str | None = None) -> tuple[float, list[str]]:
    """How much of the municipality's median price this home is worth before
    any discount, and why (condition, age, distance from town)."""
    state = state or condition(item)
    factor, why = CONDITION_VALUE[state], []
    if state != "good":
        why.append({"unknown": "condition not stated", "some": "needs work", "heavy": "needs heavy work"}[state])
    year = built_year(item)
    if year and state != "good":
        age = curve(year, BUILT_YEAR_VALUE)
        if age < 1:
            factor *= age
            why.append(f"built {year}")
    near = item.get("town_distance")
    if near and near.get("km") is not None:
        town = curve(near["km"], TOWN_KM_VALUE)
        if town < 1:
            factor *= town
            why.append(f"{near['km']:.0f} km from town")
    found = local_price(item)
    if found and "province average" in found[1] and "rest of province" not in found[1]:
        factor *= PROVINCE_AVERAGE_VALUE          # the province's figure includes its cities
        why.append("province average")
    return factor, why


def _inconsistent(text: str) -> str | None:
    """A property text that names the wrong kind of registry: typed in a hurry,
    so the rest (place, size) may be wrong too."""
    if re.search(r"registo\s+criminal|registo\s+civil", text, re.I) and re.search(r"pr[ée]dio|im[óo]vel", text, re.I):
        return "property registered at the criminal or civil registry"
    return None


_CASE_YEAR = re.compile(r"^\s*\d+/(\d{2})\.")


def time_on_sale(item: dict, now: datetime | None = None) -> tuple[float, float, str] | None:
    """(points, years, reason) for a listing that has been on sale for years:
    the portal's publication date, else the year of the court case."""
    now = now or utcnow()
    raw = _raw(item)
    published = raw.get("data_publicacao")
    if published:
        try:
            since = datetime.fromisoformat(str(published)[:10])
        except ValueError:
            since = None
        if since:
            years = (now.replace(tzinfo=None) - since).days / 365.25
            points = curve(years, YEARS_ON_SALE_POINTS)
            return (points, years, f"on sale since {since.year} ({years:.0f} years)") if points < 0 else None
    m = _CASE_YEAR.match(str(raw.get("processo") or ""))
    if m:
        year = 2000 + int(m.group(1)) if int(m.group(1)) <= now.year % 100 else 1900 + int(m.group(1))
        years = now.year - year
        points = curve(years, CASE_AGE_POINTS)
        return (points, years, f"court case from {year} ({years} years)") if points < 0 else None
    return None


def _ha(m2: float) -> str:
    return f"{m2 / 10000:.1f} ha" if m2 >= 10000 else f"{m2:,.0f} m²".replace(",", " ")


def score(item: dict, now: datetime | None = None,
          targets: dict | None = None, mode: str = "home") -> tuple[float, list[str]]:
    """0–100: how well a listing fits the goal of `mode` (MODES), with the
    reasons. `targets` are the config filters (rural_min_m2, rural_max_eur_m2);
    missing values use TARGET_DEFAULTS."""
    raw, reasons = score_detail(item, now, targets, mode)
    return display_score(raw), reasons


SOFT_TOP_FROM = 80.0     # up to here the score is the raw points
SOFT_TOP_SPAN = 40.0     # above it the points count less and less: 100 is never quite reached
# What used to show as 50–100 is the whole scale. A middling 50 shows as 0 and
# a perfect 100 stays 100, so the listings worth a look are spread out instead
# of bunched at the top. Anything below 50 shows as 0.
SPREAD_FROM = 50.0


def _squeeze(raw: float) -> float:
    """Raw points, with the top squeezed so 100 is never quite reached
    (raw 100 → 88, 120 → 93, 160 → 97)."""
    if raw <= SOFT_TOP_FROM:
        return max(0.0, raw)
    room = 100.0 - SOFT_TOP_FROM
    return SOFT_TOP_FROM + room * (1 - math.exp(-(raw - SOFT_TOP_FROM) / SOFT_TOP_SPAN))


def display_score(raw: float) -> float:
    """The 0–100 score shown. The old 50–100 band is stretched across 0–100."""
    shown = _squeeze(raw)
    if shown <= SPREAD_FROM:
        return 0.0
    return round(min(100.0, (shown - SPREAD_FROM) * (100.0 / (100.0 - SPREAD_FROM))), 1)


# ─── Excellent ───────────────────────────────────────────────────────
# The owner looks for THE property, not a list of good ones: "excellent"
# means every wish is met and checked, nothing taken on trust.
EXCELLENT_MAX_PAY = 30000
EXCELLENT_MAX_HOT_DAYS = 7
EXCELLENT_MILD_SUMMER_C = 28.0   # warmest month's mean daily max, where the day count is missing
EXCELLENT_WATER_KM = 1.0
EXCELLENT_AIRPORT_KM = 80        # about an hour by road
EXCELLENT_STATION_KM = 30


def excellent(item: dict, score: float, reasons: list[str]) -> list[str] | None:
    """What makes it excellent, or None when a wish is missing or unchecked."""
    # 40 is the old 70, after 50–100 was stretched to 0–100.
    if score < 40 or any(r.startswith("rejected") for r in reasons):
        return None
    kind = property_kind(item)
    if kind not in ("home", "rural_plot"):
        return None
    c = item.get("climate") or {}
    days = (c.get("hot_days") or {}).get("rcp45_2071-2100")
    summer = (c.get("heat") or {}).get("ssp245_2081-2100")
    if days is None and summer is not None and summer <= EXCELLENT_MILD_SUMMER_C:
        days = 0             # outside the European day-count grid (Azores): a mild summer is enough
    if days is None or days > EXCELLENT_MAX_HOT_DAYS:
        return None
    pay = _pay(item)
    likely = item.get("predicted_final")
    if likely and likely["price"] > pay:
        pay = likely["price"]
    if not pay or pay > EXCELLENT_MAX_PAY:
        return None
    wet = c.get("water_km")
    water = (wet is not None and wet <= EXCELLENT_WATER_KM) or any(r.startswith("next to water") for r in reasons)
    if not water or floods(c):
        return None
    airport, station = item.get("airport") or {}, item.get("station") or {}
    access = (airport.get("km") or 999) <= EXCELLENT_AIRPORT_KM or (station.get("km") or 999) <= EXCELLENT_STATION_KM
    if not access:
        return None
    if kind == "home" and (condition(item) != "good"
                           or any(r.startswith(("needs heavy work", "needs some work", "condition not stated",
                                                "ruin", "abandoned", "degraded", "size unknown"))
                                  for r in reasons)):
        return None             # pristine only: unknown condition is not excellent either

    if kind == "rural_plot" and not (item.get("area_m2") or find_area(item.get("title") or "")):
        return None
    return [f"{days:.0f} days above 35 °C by 2090", "water", "access", f"€{pay:,.0f}"]


def curve(x: float, points: list[tuple[float, float]]) -> float:
    """Straight lines between (x, points) pairs, flat beyond the ends.

    Every amount (price, size, discount, €/m², …) is scored along one of these,
    so €20,000 scores a little more than €20,001 instead of jumping at a step.
    The points are where the old steps were."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


# How cheap, in euros (what you would pay). Rural land counts this at half:
# its €/m² already says how cheap it is.
PRICE_POINTS = [(0, 28), (5000, 25), (15000, 20), (30000, 12), (60000, 4), (80000, -15), (150000, -20)]
# Bid as a share of the base value.
BID_RATIO_POINTS = [(0.1, 38), (0.3, 33), (0.5, 22), (0.7, 12), (1.0, 0), (1.5, -15)]
# Price cut since first seen, in %.
PRICE_DROP_POINTS = [(5, 0), (10, 5), (25, 10)]
EARLIER_ROUND_POINTS = 8        # an earlier round of the same property ended unsold
# Days left before the sale ends.
DAYS_LEFT_POINTS = [(0.25, 9), (3, 7), (7, 3), (10, 0)]
# Home size in m², and how far below local prices (0.4 = 40%).
HOME_AREA_POINTS = [(30, -25), (40, -15), (60, 0), (80, 4), (150, 6)]
MARKET_DISCOUNT_POINTS = [(0.1, 0), (0.2, 4), (0.4, 14), (0.6, 20), (0.9, 24)]
# Rural plot size as a multiple of the minimum (Settings), and €/m² as a share of the maximum.
RURAL_SIZE_POINTS = [(0.5, -30), (1.0, 8), (2.0, 13), (5.0, 25), (10.0, 28)]
RURAL_EUR_M2_POINTS = [(0.2, 18), (0.5, 15), (1.0, 8), (1.5, -10), (3.0, -25)]
# A very low minimum bid (€) on a sale with a real base value.
LOW_MIN_BID_POINTS = [(100, 10), (500, 10), (1500, 0)]
# INE's price per m² is the median of homes sold in the municipality, mostly
# sound ones in town. An old village house is worth less than that: the local
# price is scaled down by condition, age and distance from town before the
# discount is measured (Sept 2026: an 82% "discount" on a 1937 village house
# waiting for a full rebuild was mostly this).
CONDITION_VALUE = {"good": 1.0, "unknown": 0.75, "some": 0.6, "heavy": 0.35}
BUILT_YEAR_VALUE = [(1940, 0.8), (1980, 0.9), (2000, 1.0)]
TOWN_KM_VALUE = [(2, 1.0), (5, 0.85), (10, 0.7)]
# Years on sale (the portal's publication date) and a court case's age: nobody
# bought it in all that time, which usually has a reason.
YEARS_ON_SALE_POINTS = [(1.5, 0), (3, -4), (5, -8), (8, -12)]
CASE_AGE_POINTS = [(6, 0), (10, -3), (15, -6), (20, -8)]

# Kilometres from the middle of the property's own town (geo.py). Measured, so
# it beats guessing "good location" from words the description may not contain.
TOWN_DISTANCE_POINTS = [(0.3, 15), (1, 13), (3, 8), (6, 3), (10, -2), (20, -14), (35, -25)]
# A home near the sea is what the owner wants most after its condition: a big
# bonus by the beach, fading out by 30 km (geo.nearest_beach). Measured from a
# town-level pin it counts BEACH_APPROX_SHARE of that.
BEACH_POINTS = [(0.5, 25), (1, 22), (2, 18), (5, 12), (10, 6), (20, 2), (30, 0)]
BEACH_APPROX_SHARE = 0.6
# The owner's must (2026-10-04): somewhere to swim within 1.5 km — the sea, a lake
# or reservoir, or a river big enough to show from space. A stream does not count.
SWIM_MAX_KM = 1.5
SWIM_POINTS = [(0.3, 25), (0.8, 20), (1.5, 14)]
NO_SWIM = -30
PHOTOS_MISS_THE_HOUSE = -5   # only a barn, land or the view: the house is unseen
# The owner: sea > lakes > rivers. The satellite map cannot tell a lake from a
# river, so its water counts as a river unless the map names a lake or reservoir.
SWIM_SHARE = {"the sea": 1.0, "sea inlet (ría)": 0.9, "lake": 0.75, "reservoir": 0.75, "river": 0.5}
RIA_BEACH_KM = 3.0   # satellite water this close to the open sea is a ría or an estuary, not a river


def swim_spots(item: dict) -> list[tuple[float, str]]:
    """(km, what) of every known place to swim: the sea beach, permanent water on
    the satellite map (wide rivers, lakes), or a river or lake on the map around it."""
    spots = []
    beach = item.get("beach")
    if beach and beach.get("km") is not None:
        spots.append((beach["km"], "the sea"))
    wet = (item.get("climate") or {}).get("water_km")
    if wet is not None:
        by_sea = beach and beach.get("km") is not None and beach["km"] <= RIA_BEACH_KM
        spots.append((wet, "sea inlet (ría)" if by_sea else "river"))
    if '"water_check"' in (item.get("raw_json") or ""):
        check = _raw(item).get("water_check") or {}
        for found in check.get("found") or []:
            if found.get("kind") in SWIM_SHARE:
                spots.append((check.get("radius_m", 300) / 1000, found["kind"]))
    return spots


def swim_points(spot: tuple[float, str]) -> float:
    km, what = spot
    return curve(km, SWIM_POINTS) * SWIM_SHARE[what] if km <= SWIM_MAX_KM else 0.0


def swim_spot(item: dict) -> tuple[float, str] | None:
    """The best place to swim (most points; the nearest when none is in reach), or None."""
    spots = swim_spots(item)
    if not spots:
        return None
    best = max(spots, key=lambda sp: (swim_points(sp), -sp[0]))
    return best if swim_points(best) > 0 else min(spots)


def _town_pin_without_a_water_check(item: dict) -> bool:
    """A municipality or village pin is the town, not the house. Without a water
    check around it, 'nowhere to swim' is not known — a coastal town's centre
    can sit more than a swim away from the water."""
    import geo
    pos = geo.position(item) or {}
    if pos.get("precision") not in ("municipality", "village"):
        return False
    return '"water_check"' not in (item.get("raw_json") or "")
# Easy to reach: an airport with scheduled flights and a station on the
# long-distance trains, smaller bonuses that fade with the distance.
AIRPORT_POINTS = [(15, 8), (30, 7), (50, 5), (80, 2), (120, 0)]
STATION_POINTS = [(1, 7), (3, 6), (8, 4), (20, 1), (40, 0)]

# What the owner does not want, as the highest score it can reach. They slide
# too: a 38 m² home is held down a little less than a 30 m² one.
SMALL_HOME_CAP = [(25, 35), (40, 45), (75, 130), (100, 200)]   # by m²
EXPENSIVE_HOME_CAP = [(50000, 200), (60000, 100), (75000, 55), (90000, 40)]   # by €
SMALL_URBAN_PLOT_CAP = [(200, 35), (1000, 45), (2000, 200)]   # by m²; 2 000 m² can still be a building plot
SMALL_RURAL_PLOT_CAP = [(0.3, 30), (1.0, 45), (1.3, 200)]  # by multiple of the minimum
FAR_FROM_TOWN_CAP = [(12, 200), (20, 60), (30, 45), (40, 40)]  # by km from town: a house
                                                               # far from everything is isolated


@functools.lru_cache(maxsize=100_000)
def _rejects_in(text: str) -> tuple:
    """The _REJECTS labels whose pattern is in the text (cached: the list is
    scored again on every page view, over the same texts)."""
    return tuple(label for label, pattern in _REJECTS if pattern is not None and pattern.search(text))


def _population_outlook_points(item: dict, reasons: list[str]) -> float:
    """A few points off when the region's population scenario falls hard by 2050.

    Growth adds nothing. The text names the scenario and says it is not a forecast."""
    import prices
    country = (item.get("country") or "PT").upper()
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    found = prices.population_outlook(country, place)
    if not found or found["change_pct"] > POP_OUTLOOK_FROM:
        return 0.0
    fewer = -found["change_pct"]
    reasons.append(f"{found['name']}: about {fewer:.0f}% fewer people by 2050 "
                   f"({found['label']}) — a weaker place to count on a local buyer decades from now")
    steep = found["change_pct"] <= POP_OUTLOOK_STEEP
    return (POP_OUTLOOK_STEEP_POINTS if steep else POP_OUTLOOK_MILD) * w("population")


def score_detail(item: dict, now: datetime | None = None,
                 targets: dict | None = None, mode: str = "home") -> tuple[float, list[str]]:
    """The score before it is clamped to 0–100: several listings can reach 100,
    and this still says which of them is best (used for sorting)."""
    token = _WEIGHTS.set((targets or {}).get("weights") or {})
    try:
        if mode == "invest":
            return _score_invest(item, now, targets)
        if mode == "forest":
            return _score_forest(item, now, targets)
        # "home" and "land" buy the same kinds of thing on the same sale terms,
        # so they share a scorer; what each wants decides the rest.
        return _score_detail(item, now, targets, "land" if mode == "land" else "home")
    finally:
        _WEIGHTS.reset(token)


def no_possession(item: dict, full: str) -> bool:
    """Okupas, or a bank that does not have the keys. A sitting tenant is not this:
    the ad says arrendado, and Investment home still scores that as a let."""
    if has_term(full, ["okupad*", "okupa*", "sin posesión", "sin posesion", "sin la posesión"],
                negations=False):
        return True
    posesion = normalize(str(_raw(item).get("posesion") or ""))
    return "okupa" in posesion or "sin poses" in posesion


PLACEHOLDER_PRICE_EUR = 100   # any price below this is a base-bid token or a data error


def _skip_reason(item: dict, title: str, full: str) -> str | None:
    """Never worth buying in any mode: a share, a caravan, a usufruct, subsidised
    housing, a timeshare, a property the seller does not possess."""
    pay = _pay(item)
    if pay and pay < PLACEHOLDER_PRICE_EUR:
        return f"placeholder price (€{pay:g}) — no real asking price, skip"
    if _raw(item).get("proindiviso"):
        return "fractional share (proindiviso) — skip"
    if _raw(item).get("nuda_propiedad"):
        return "usufruct — skip (bare ownership: usufructuary keeps living there)"
    if is_fractional_share(title) or is_percent_share(f"{title} {item.get('description') or ''}"):
        return "fractional share — skip"
    if no_possession(item, full):
        return "occupied without possession (okupa) — skip"
    if is_co_ownership(full):
        return "a share shared with other owners, not the whole property — skip"
    if has_term(full, NOT_ALL_YEAR, negations=False):
        return "seasonal only: cannot be lived in all year or let — skip"
    if has_term(full, NOT_A_BUILDING, negations=False):
        return "mobile home or caravan, not a house — skip"
    if has_term(full, USUFRUCT_PATTERNS):
        return "usufruct — skip"
    if has_term(full, SUBSIDISED_HOUSING):
        return "subsidised housing (buyer must qualify, resale price capped) — skip"
    if is_timeshare(full):
        return "timeshare (some weeks a year) — skip"
    return None


def _doubtful_home_eur(item: dict) -> float:
    if item.get("source") in BANK_PORTALS:
        return DOUBTFUL_BANK_HOME_EUR
    if item.get("country") == "HR" and (item.get("district") or "").lower() in HR_COAST_COUNTIES:
        return DOUBTFUL_HR_COAST_HOME_EUR
    return DOUBTFUL_HOME_EUR


def _doubts(item: dict, kind: str, pay: float, area: float, full: str, reasons: list[str], caps: list[float],
            land_floor: float = DOUBTFUL_LAND_EUR_M2):
    """What must be checked by hand before the score can be trusted, in any mode:
    each caps the score at UNCHECKED_CAP."""
    if has_term(full, UNFINISHED_HOUSE, negations=False):
        caps.append(UNCHECKED_CAP)
        reasons.append("house still under construction — check what is built and licensed")
    if has_term(full, NO_VIEWING, negations=False):
        caps.append(UNCHECKED_CAP)
        reasons.append("auction resold by a middleman: no viewing, cash only")
    if has_term(full, NOT_VISITABLE, negations=False):
        caps.append(UNCHECKED_CAP)
        reasons.append("cannot be visited — usually means someone lives there; ask before bidding")
    if item.get("place_conflict"):
        # The title names a town far from where the listing is placed: the
        # climate and distances belong to the wrong place.
        caps.append(UNCHECKED_CAP)
        reasons.append(f"{item['place_conflict'].get('where', 'title')} names {item['place_conflict']['town']}, "
                       f"{item['place_conflict']['km']:.0f} km from where it is placed — check the location")
    if (kind == "home" and item.get("source") in SALE_PORTALS
            and pay and area >= 40                                  # court sales do start this low
            and pay < _doubtful_home_eur(item)):
        caps.append(UNCHECKED_CAP)
        reasons.append(f"price doubtful (€{pay:,.0f} for a home) — probably a rent or a typo")
    if kind in ("urban_plot", "rural_plot") and pay and area and pay / area < land_floor:
        caps.append(UNCHECKED_CAP)
        reasons.append(f"price doubtful (€{pay:,.0f} for {_ha(area)}) — check the price and area")


def apply_stated_area(item: dict) -> None:
    """Use the plot the ad states when the feed's area is the whole development.

    Houses keep the feed figure: a floor area in the text is not the plot.
    Sets area_note so the list can say why the hectares changed."""
    if item.get("area_note"):
        return
    tipo = normalize(item.get("tipo") or "")
    title = item.get("title") or ""
    if has_term(title, _HOUSE_WORDS_NOT_TYPOLOGY, negations=False) and not _LAND_TYPE.match(tipo):
        return
    from common import prefer_stated_area
    used, feed = prefer_stated_area(item.get("area_m2"), f"{title} {item.get('description') or ''}")
    if feed and used:
        item["area_m2"] = used
        item["area_note"] = (f"feed says {_ha(feed)}; the ad states {_ha(used)} — "
                             "scored on the smaller figure")


def _score_detail(item: dict, now: datetime | None, targets: dict | None,
                  mode: str = "home") -> tuple[float, list[str]]:
    apply_stated_area(item)
    s = 50.0
    reasons: list[str] = []
    caps: list[float] = []
    t = {k: (targets or {}).get(k) or v for k, v in TARGET_DEFAULTS.items()}

    title   = item.get("title") or ""
    desc    = item.get("description") or ""
    full    = f"{title} {desc}"
    price   = item.get("price")   or 0
    # A bid below the minimum accepted will not buy it: judge the discount on the minimum.
    bid     = max(item.get("current_bid") or 0, item.get("min_price") or 0) if item.get("current_bid") else 0
    # No size field (licitor, some Citius): the size written in the text, so a
    # 35 m² "maison" is still a small home.
    area    = item.get("area_m2") or find_area(title) or find_area(desc) or 0
    source  = item.get("source",  "")
    title_n = normalize(title)
    pay     = _pay(item)
    likely = item.get("predicted_final")
    if likely and likely["price"] > pay * 1.02:
        pay = likely["price"]                     # what it will probably take, not the base
        reasons.append(likely["text"])
    kind    = property_kind(item)

    skip = _skip_reason(item, title, full)
    if skip:
        return 0.0, [skip]
    if item.get("area_note"):
        reasons.append(item["area_note"])

    # ── What it is ────────────────────────────────────────────────────
    # A ruin on a big farm: the value is the land, so it is scored as land.
    if (kind == "home" and area >= t["rural_min_m2"] and has_term(full, HEAVY_WORK)
            and has_term(full, RURAL_WORDS, negations=False)):
        kind = "rural_plot"
        reasons.append("ruin on a farm — valued as land")

    if kind in ("home", "urban_plot", "rural_plot") and item.get("climate"):
        s += _climate_points(item["climate"], kind, reasons, caps)
    elif kind in ("home", "urban_plot", "rural_plot") and item.get("unlocated"):
        # Without a position the heat, water and fire checks cannot run: 8 of
        # the top 15 were there only because nothing could be held against them.
        caps.append(UNCHECKED_CAP)
        reasons.append("location unknown — climate not checked")

    _doubts(item, kind, pay, area, full, reasons, caps)

    # Land: too small is not wanted at all.
    if kind in ("urban_plot", "rural_plot"):
        if (item.get("country") or "PT") != "PT":
            t = {**t, "rural_min_m2": max(t["rural_min_m2"], PLOT_MIN_ABROAD_M2)}
        if kind == "rural_plot" and area and area < t["rural_min_m2"]:     # urban plots keep their own rules
            reasons.append(f"rejected: plot too small ({_ha(area)} < {_ha(t['rural_min_m2'])})")
        if kind == "rural_plot" and not area:
            caps.append(UNCHECKED_CAP)
            reasons.append("size unknown — confirm the area before it can rank")

    if kind == "home":
        if mode == "land":
            reasons.append("a home, not land — see My home or Investment home")
        else:
            s += 10
            reasons.append("home")
            s += _home_points(item, full, area, pay, reasons, caps)
    elif kind in ("urban_plot", "rural_plot") and mode == "home":
        # A plot is not somewhere to live. It is ranked on Investment land,
        # where it is judged on what land costs there, not on its condition.
        reasons.append("a plot, not a place to live — see Investment land")
    elif kind == "urban_plot":
        s += 8
        reasons.append("urban plot" + (f" ({_ha(area)})" if area else ""))
        if area:
            caps.append(curve(area, SMALL_URBAN_PLOT_CAP))
            if area < SMALL_URBAN_PLOT_M2:
                reasons.append(f"small plot ({area:.0f} m²) — not an investment")
        s += _land_points(item, kind, area, pay, full, reasons, caps)
    elif kind == "rural_plot":
        s += _rural_points(area, pay, t, reasons, full, caps, item)
        s += _land_points(item, kind, area, pay, full, reasons, caps)
    elif kind == "other":
        s -= 25
        reasons.append("not a home or plot")
    else:
        # Neither a home nor a plot as far as the text says ("Artigo urbano 4517"):
        # worth a look, but not above the ones that clearly are.
        s -= 10
        reasons.append("unclear what it is — check")

    away = off_western_continental(item)
    if away:
        s += OFF_WESTERN_CONTINENTAL
        reasons.append(f"not western continental Europe ({away})")
    elif (item.get("country") or "PT").upper() == "PT":
        s += MAINLAND_PORTUGAL
        reasons.append("mainland Portugal")

    occupation = _occupation(item)   # read from the sale's detail page (ES, FR)
    if occupation == "occupied" or (occupation is None and has_term(full, OCCUPANCY_PATTERNS)):
        s -= 25
        reasons.append("occupied/tenanted")
        reasons.append("rejected: occupied")
    elif occupation == "vacant" or has_term(full, VACANT_PATTERNS, negations=False):
        s += 6
        reasons.append("vacant (devoluto)")

    if has_term(full, ACCESS_PATTERNS, negations=False):
        s -= 20
        reasons.append("no road access")

    if "direito" in title_n and "heranca" in title_n:
        s -= 20
        reasons.append("inheritance right only")

    doubt = _inconsistent(full)
    if doubt:
        s -= 3
        reasons.append(f"text does not add up ({doubt}) — confirm with the court")

    stale = time_on_sale(item, now)
    if stale:
        s += stale[0]
        reasons.append(stale[2])

    # ── How cheap ─────────────────────────────────────────────────────
    # On sale again after an earlier round ended (rounds.py): nobody bought it then,
    # so the seller is likely to take less. First among the reasons: alerts show three.
    er = item.get("earlier_round")
    if er:
        s += EARLIER_ROUND_POINTS
        was = f" at €{er['price']:,.0f}" if er.get("price") else ""
        reasons.insert(0, f"on sale before (ended {er['ended']}{was}) — not sold then")
        if (er.get("cheaper_pct") or 0) >= 5:
            s += curve(er["cheaper_pct"], PRICE_DROP_POINTS)
            reasons.insert(1, f"{er['cheaper_pct']:.0f}% cheaper than the last round")

    if bid and price and price > 0:
        ratio = bid / price
        s += curve(ratio, BID_RATIO_POINTS) * w("price")
        if ratio < 0.30:
            reasons.append(f"bid only {ratio:.0%} of VB — extreme discount")
        elif ratio < 0.50:
            reasons.append(f"bid {ratio:.0%} of VB — deep discount")
        elif ratio < 0.70:
            reasons.append(f"bid {ratio:.0%} of VB — good discount")
        elif ratio > 1.50:
            reasons.append(f"overbid {ratio:.0%} — overheated")
    elif not bid and price:
        s += 8
        reasons.append("no bids yet")

    # Price already cut since we first saw it (e.g. a second, cheaper round)
    drop = item.get("price_drop_pct")
    if drop and drop > 5:
        s += curve(drop, PRICE_DROP_POINTS)
        if drop >= 10:
            reasons.append(f"price cut {drop:.0f}% since first seen")

    # Ridiculous in absolute terms. This is the goal, so a dear sale stays out of
    # the top even with every court bonus: a €97,500 flat used to reach 100.
    # For rural land the price per m² already says how cheap it is (_rural_points),
    # so a bonus for the absolute price counts half: a small cheap plot must not
    # beat a big one.
    if pay >= 300:
        points = curve(pay, PRICE_POINTS) * w("price")
        s += points * (0.5 if kind == "rural_plot" and points > 0 else 1.0)
        if pay <= 5000:
            reasons.append(f"very cheap: €{pay:,.0f}")
        elif pay <= 15000:
            reasons.append(f"cheap: €{pay:,.0f}")
        elif pay <= 30000:
            reasons.append(f"€{pay:,.0f}")
        elif pay > 60000:
            reasons.append(f"€{pay:,.0f} — not a low price")

    # ── How the sale works ────────────────────────────────────────────
    # Sealed bids are a great chance: you set the price and few people bid.
    sealed = has_term(full, SEALED_BID_PATTERNS, negations=False)
    if sealed:
        s += 20 * w("sale")
        reasons.append("sealed-bid (carta fechada)")

    is_forced = forced_sale(item)
    if is_forced:
        s += 6 * w("sale")
        reasons.append("forced sale (must sell)")
    if source in TAX_SOURCES:
        s += 4
        reasons.append("tax seizure — no reserve")

    # Minimum bid signal (one bonus per listing: these all describe the same fact).
    min_p = item.get("min_price") or 0
    offer_sale = sealed or is_forced or has_term(full, OFFER_SALE_PATTERNS, negations=False)
    if not pay and offer_sale:
        s += 18 * w("sale")
        reasons.append("no price — you set your offer")
    elif not pay:
        # Climate and location alone used to push Dutch/German cards with no
        # figure into the top 20; without a price the cheapness goal cannot be
        # checked, so they stay under the board until one appears.
        caps.append(NO_PRICE_CAP)
        reasons.append("price unknown — cannot judge cheapness")
    elif min_p and price and price > 1000 and min_p < price and curve(min_p, LOW_MIN_BID_POINTS) > 0:
        s += curve(min_p, LOW_MIN_BID_POINTS)
        reasons.append(f"min bid only €{min_p:.0f}")
    elif not min_p and pay and is_forced:
        s += 4
        reasons.append("no minimum bid")

    # Urgency. days_left() understands naive dates; the old tz-aware subtraction
    # raised on them, so most sources never got this bonus.
    left = days_left(item.get("date_end"), now or utcnow())
    if left is not None and left > 0:
        s += curve(left, DAYS_LEFT_POINTS) * w("sale")
        if left <= 3:
            reasons.append(f"{left * 24:.0f}h left — urgent" if left < 1 else f"{left:.0f}d left — urgent")
        elif left <= 7:
            reasons.append(f"{left:.0f}d left")

    if price and price < 300:
        s -= 20
        reasons.append("suspiciously cheap — likely tiny/worthless")

    for label in _rejects_in(full):
        if label.startswith("land only") and kind == "home":
            reasons.append("sold together with another lot (its price is not shown)")
            continue
        reasons.append(f"rejected: {label}")
    if has_term(full, ["inacabad*", "em tosco", "por acabar"], negations=False) and kind == "home":
        reasons.append("needs heavy work (unfinished)")

    for label, cap in NOT_THE_GOAL_CAP.items():
        if any(r.startswith(label) for r in reasons):
            caps.append(cap)
    if caps:
        s = min(s, *caps)
    s += _population_outlook_points(item, reasons)
    return s, reasons


# The 1-in-10-year coldest night if the AMOC collapses in a 2 °C warmer world:
# local cold (E-OBS + EURO-CORDEX, 12 km) plus the collapse model's change.
# North Galician and Asturian coast about -8 °C, Lugo -10, Rennes -18, Grenoble
# -24, Alpine villages -31 and colder. A stone house copes with -8 once a decade.
AMOC_COLD_POINTS = [(-30, -15), (-20, -10), (-12, -4), (-8, 0)]
AMOC_COLD_WARN_C = -10
# April-September rain minus evaporation, change if the AMOC collapses (mm).
# North Galician coast: +30 (cooler, less evaporation); Porto -40; Oviedo -75; the Alps -160.
AMOC_DRY_POINTS = [(-250, -10), (-50, 0)]
AMOC_DRY_WARN_MM = -50


def _climate_points(c: dict, kind: str, reasons: list[str], caps: list[float]) -> float:
    """Heat in 2081-2100, permanent water, water stress, fires and floods where
    the listing is (climate.for_item). From a town-level position the local
    risks (water, fire, flood) count half; the heat grid is ~4.5 km anyway."""
    s = 0.0
    local = 0.5 if c.get("approx") else 1.0
    heat = c.get("heat") or {}
    hot = heat.get("ssp245_2081-2100") or heat.get("ssp245_2061-2080")
    days = c.get("hot_days") or {}
    future = days.get("rcp45_2071-2100")
    if future is not None:
        s += curve(future, HOT_DAYS_POINTS) * w("heat")
        worst = days.get("rcp85_2071-2100")
        detail = f"{future:.0f} days a year above 35 °C by 2071-2100" +                  (f", {worst:.0f} worst case" if worst is not None else "") +                  (f"; {days['today']:.0f} today" if days.get("today") is not None else "")
        if future > REJECT_HOT_DAYS:
            reasons.append(f"rejected: too hot in 50-70 years ({detail})")
        elif future > TOO_MANY_HOT_DAYS:
            caps.append(TOO_HOT_CAP)
            reasons.append(f"too hot in 50-70 years ({detail})")
        else:
            reasons.append(detail)
    elif hot is not None:
        s += curve(hot, HEAT_POINTS) * w("heat")
        worst = heat.get("ssp585_2081-2100")
        detail = f"{hot:.1f} °C summer max by 2081-2100" + (f", {worst:.1f} °C worst case" if worst else "") + \
                 (f"; {heat['today']:.1f} °C today" if heat.get("today") else "")
        if hot > REJECT_HOT_C:
            reasons.append(f"rejected: too hot in 50-70 years ({detail})")
        elif hot > TOO_HOT_C:
            caps.append(TOO_HOT_CAP)
            reasons.append(f"too hot in 50-70 years ({detail})")
        else:
            reasons.append(detail)
    wet = c.get("water_km")
    if wet is not None:
        bonus = curve(wet, PERMANENT_WATER_POINTS) * local * (1 if kind == "rural_plot" else 0.5) * w("water")
        if bonus >= 1 and floods(c):
            reasons.append(f"permanent water {wet:.1f} km away, but it floods ({floods(c):.1f} m) — no water bonus")
        elif bonus >= 1:
            s += bonus
            reasons.append(f"permanent water {wet:.1f} km away{' (approx.)' if c.get('approx') else ''}")
    stress = (c.get("stress") or {})
    future = stress.get("stress_2080", stress.get("stress_2050"))
    if future is not None and (future >= 3 or future == -1):
        s -= (15 if future in (4, -1) else 10) * w("water")
        reasons.append("water stress " + ("arid" if future == -1 else "extremely high" if future == 4 else "high")
                       + " by 2080 (WRI Aqueduct)")
    elif future is not None and future <= 1:
        s += 3
        reasons.append("low water stress by 2080 (WRI Aqueduct)")
    fire = c.get("fire") or {}
    if fire.get("burnt_here"):
        s -= 15 * local * w("risks")
        reasons.append(f"burnt since 2016 ({', '.join(map(str, fire['years']))}) — EFFIS")
    elif fire.get("count"):
        s -= min(15, 5 * len(fire.get("years") or [1])) * local * w("risks")
        reasons.append(f"fires within {fire.get('km', 2):.0f} km since 2016 ({', '.join(map(str, fire['years']))})")
    danger = c.get("fire_danger") or {}
    days = danger.get("high_days_2090")
    if days is not None:
        s += curve(days, FIRE_DANGER_POINTS) * w("risks")
        if days >= 30:
            now = danger.get("high_days_now")
            reasons.append(f"{days:.0f} days a year of high fire danger by 2079-2098"
                           + (f" ({now:.0f} today)" if now is not None else "") + " — Copernicus")
    flood = c.get("flood_m")
    if flood and flood > 0 and kind == "home":
        s -= 12 * local * w("risks")
        reasons.append(f"in the 100-year flood zone ({flood:.1f} m) — JRC")
    elif flood and flood > 1 and kind != "home":
        s -= 4 * local
        reasons.append(f"floods in a 100-year flood ({flood:.1f} m)")
    amoc = c.get("amoc_cold10") or {}
    if amoc.get("off") is not None:
        s += curve(amoc["off"], AMOC_COLD_POINTS) * w("amoc")
        if amoc["off"] <= AMOC_COLD_WARN_C:
            reasons.append(f"coldest day in 10 years {amoc['off']:.0f} °C if the Atlantic current collapses"
                           + (f" ({amoc['on']:.0f} °C if not)" if amoc.get("on") is not None else "")
                           + " — one model, ~200 km grid (van Westen 2025)")
    dry = c.get("amoc_dry_mm")
    if dry is not None:
        s += curve(dry, AMOC_DRY_POINTS) * w("amoc")
        if dry <= AMOC_DRY_WARN_MM:
            reasons.append(f"summer water balance {dry:+.0f} mm if the Atlantic current collapses"
                           " — one model, ~200 km grid (van Westen 2025)")
    return s


def floods(c: dict | None) -> float | None:
    """The 100-year flood depth at the position when it is enough to cancel a
    water bonus, else None."""
    flood = (c or {}).get("flood_m")
    return flood if flood and flood > FLOOD_NO_WATER_BONUS_M else None


# ─── Climate resilience: a grade of its own, beside the score ─────────
# 50 is a place the layers say nothing good or bad about; each point of
# _climate_points moves it CLIMATE_SCALE.
CLIMATE_BASE = 50
CLIMATE_SCALE = 1.75
CLIMATE_GRADES = [(75, "excellent"), (55, "good"), (35, "caution")]   # at least this; below is "poor"
GRADE_ORDER = ["poor", "caution", "good", "excellent"]
# What the optional bid guardrail (Settings) multiplies an AI-suggested bid by.
# Only applied to exact positions.
CLIMATE_BID_MULTIPLIER = {"excellent": 1.0, "good": 1.0, "caution": 0.85, "poor": 0.6, "unknown": 1.0}
# Hazards read at the position itself (a few hundred metres): from a town-level
# pin they may belong to the next valley, so they never make the grade "poor".
_LOCAL_SEVERE = {"deep_flood", "burnt_here", "repeated_burns", "home_in_flood_zone"}
# Hazards read from grids of several km: as true for a town pin as for an exact one.
_REGIONAL_SEVERE = {"extreme_heat", "severe_water_stress", "very_high_fire_danger"}
_CAUTION = {"too_hot", "flood_zone", "high_water_stress", "high_fire_danger", "fires_nearby",
            "water_but_floods"}


def _climate_flags(c: dict, kind: str | None) -> list[str]:
    flags = []
    days = (c.get("hot_days") or {}).get("rcp45_2071-2100")
    heat = c.get("heat") or {}
    hot = heat.get("ssp245_2081-2100") or heat.get("ssp245_2061-2080")
    if days is not None:
        flags += ["extreme_heat"] if days > REJECT_HOT_DAYS else ["too_hot"] if days > TOO_MANY_HOT_DAYS else []
    elif hot is not None:
        flags += ["extreme_heat"] if hot > REJECT_HOT_C else ["too_hot"] if hot > TOO_HOT_C else []
    flood = floods(c)
    if flood:
        flags.append("flood_zone")
        if flood > DEEP_FLOOD_M:
            flags.append("deep_flood")
        if kind == "home":
            flags.append("home_in_flood_zone")
    wet = c.get("water_km")
    if wet is not None and wet <= PERMANENT_WATER_POINTS[-1][0]:
        flags.append("water_but_floods" if flood else "permanent_water")
    stress = c.get("stress") or {}
    future = stress.get("stress_2080", stress.get("stress_2050"))
    if future in (4, -1):
        flags.append("severe_water_stress")
    elif future == 3:
        flags.append("high_water_stress")
    fire = c.get("fire") or {}
    if fire.get("burnt_here"):
        flags.append("burnt_here")
        if len(set(fire.get("years") or [])) >= 2:
            flags.append("repeated_burns")
    elif fire.get("count"):
        flags.append("fires_nearby")
    danger = (c.get("fire_danger") or {}).get("high_days_2090")
    if danger is not None and danger >= VERY_HIGH_FIRE_DAYS:
        flags.append("very_high_fire_danger")
    elif danger is not None and danger >= HIGH_FIRE_DAYS:
        flags.append("high_fire_danger")
    return flags


def climate_score(c: dict | None, kind: str | None = None) -> dict:
    """How well the place should hold up to heat, water, fire and floods, from
    the climate layers (climate.for_item). A planning indicator, not a survey:
    the same curves as the listing score, but at the normal weights, so the
    grade does not move with the Settings sliders."""
    if not c:
        return {"score": None, "grade": "unknown", "reasons": [], "flags": [],
                "bid_multiplier": CLIMATE_BID_MULTIPLIER["unknown"], "confidence": "unknown"}
    kind = kind or "home"
    reasons: list[str] = []
    points = _climate_points(c, kind, reasons, [])
    flags = _climate_flags(c, kind)
    exact = not c.get("approx")
    score = max(0.0, min(100.0, CLIMATE_BASE + CLIMATE_SCALE * points))
    grade = next((g for floor, g in CLIMATE_GRADES if score >= floor), "poor")

    def at_most(limit: str) -> None:
        nonlocal grade
        if GRADE_ORDER.index(grade) > GRADE_ORDER.index(limit):
            grade = limit

    if _REGIONAL_SEVERE & set(flags) or (exact and _LOCAL_SEVERE & set(flags)):
        at_most("poor")
    elif _CAUTION & set(flags) or _LOCAL_SEVERE & set(flags):
        at_most("caution")
    if not exact:
        at_most("good")
        reasons.append("position approximate (town or parish): local risks count half and "
                       "cannot make the grade poor")
    return {"score": round(score), "grade": grade, "reasons": reasons, "flags": flags,
            "bid_multiplier": CLIMATE_BID_MULTIPLIER[grade] if exact else 1.0,
            "confidence": "exact" if exact else "approximate"}


def _home_points(item: dict, full: str, area: float, pay: float, reasons: list[str],
                 caps: list[float]) -> float:
    """A home should be in a good place, in good condition, big enough, well
    under local prices and not expensive."""
    s = 0.0
    state = condition(item)
    seen = " (from the photos)" if state != "unknown" and not has_term(
        full, HEAVY_WORK + SOME_WORK + GOOD_CONDITION) else ""
    if state == "heavy":
        s -= 25
        caps.append(40)   # a cheap ruin must not outrank a house you can live in
        reasons.append(f"needs heavy work (ruin / full rebuild){seen}")
    elif state == "some":
        s -= 12
        reasons.append(f"needs some work{seen}")
    elif state == "unknown":
        # Most judicial cards never say: treat like work until the text or photos do.
        # Otherwise Italy's silent listings crowd out homes that admit their state.
        s -= 10
        reasons.append("condition not stated — assume it needs work")
    elif state == "good":
        s += 15
        reasons.append(f"good condition{seen}")

    # Sold together with cheap land in the same case: worth buying both.
    land = [lot for lot in (item.get("case_land") or [])
            if lot.get("price") and lot["price"] <= max(CASE_LAND_MAX_EUR, CASE_LAND_SHARE * (pay or 0))]
    if land:
        lot = land[0]
        s += 6
        size = f", {_ha(lot['area_m2'])}" if lot.get("area_m2") else ""
        reasons.append(f"land in the same case (€{lot['price']:,.0f}{size})")

    water = water_nearby(full, item)
    if water and floods(item.get("climate")):
        reasons.append(f"water nearby ({water}), but it floods — no water bonus")
    elif water:
        s += (3 if water.endswith("(approx.)") else 6) * w("water")
        reasons.append(f"{'near' if water.endswith('(approx.)') else 'next to'} water ({water})")

    if (_raw(item).get("photo_check") or {}).get("shows_house") is False:
        s += PHOTOS_MISS_THE_HOUSE
        reasons.append("the photos don't show the house itself — ask the seller for photos")

    swim = swim_spot(item)
    if swim and swim[0] <= SWIM_MAX_KM:
        s += swim_points(swim) * w("beach")
        reasons.append(f"somewhere to swim {swim[0]:.1f} km away ({swim[1]})")
    elif (swim or item.get("climate")) and not _town_pin_without_a_water_check(item):
        # Placed on the map, and nothing within reach. A town or village pin
        # with no water check is not that: the house may be by the water.
        s += NO_SWIM * w("beach")
        reasons.append(f"nowhere to swim within {SWIM_MAX_KM:g} km"
                       + (f" (nearest: {swim[1]} {swim[0]:.1f} km)" if swim else ""))

    for key, points in (("beach", BEACH_POINTS), ("airport", AIRPORT_POINTS), ("station", STATION_POINTS)):
        near = item.get(key)
        if near:
            weight = w("beach" if key == "beach" else "transport")
            bonus = curve(near["km"], points) * (BEACH_APPROX_SHARE if near.get("approx") else 1) * weight
            if bonus >= 1:
                s += bonus
                reasons.append(near["text"])

    if has_term(full, ISOLATED):
        s -= 35
        reasons.append("isolated location")
    else:
        spot = find_terms(full, GOOD_LOCATION, negations=False)
        near = item.get("town_distance")
        town = _known_town(item)
        if near:
            s += curve(near["km"], TOWN_DISTANCE_POINTS)
            caps.append(curve(near["km"], FAR_FROM_TOWN_CAP))
            reasons.append(near["text"])
            if spot:
                s += 5
                reasons.append(f"good location ({spot[0]})")
        elif spot:
            s += 15
            reasons.append(f"good location ({spot[0]})")
        elif town:
            s += 8
            reasons.append(f"in {town} (town with services)")

    if area:
        s += curve(area, HOME_AREA_POINTS)
        caps.append(curve(area, SMALL_HOME_CAP))
        if area < SMALL_HOME_M2:
            reasons.append(f"small home ({area:.0f} m²)")
        elif area >= 60:
            reasons.append(f"{area:.0f} m²")
    else:
        # It could be the small home you do not want: ask before you offer.
        s -= 4
        reasons.append("size unknown — ask")
    if pay:
        caps.append(curve(pay, EXPENSIVE_HOME_CAP))
        if pay > EXPENSIVE_HOME_EUR:
            reasons.append(f"expensive home (€{pay:,.0f})")

    # Below the local price per m². Over 1,000 m² the stored size is usually the
    # plot: value at most INVEST_VALUE_MAX_M2 of building, and nothing when that
    # size is the plot itself (land_m2 set and equal to the area).
    floor = _discount_floor_m2(item, area)
    mv = market_value_estimate({**item, "area_m2": floor}) if floor else None
    if mv and pay:
        factor, why = local_value_factor(item, state)
        mv *= factor
        market_disc = (mv - pay) / mv
        skipped = _discount_skipped(item, why)
        if not skipped:
            s += curve(market_disc, MARKET_DISCOUNT_POINTS)
        if market_disc > 0.20:
            adjusted = f"; counted at {factor:.0%}: {', '.join(why)}" if why else ""
            reasons.append(f"{market_disc:.0%} below local prices ({local_price(item)[1]}{adjusted}){skipped}")
    _mention_sold_move(item, reasons)
    return s


def _discount_floor_m2(item: dict, area: float) -> float | None:
    """Floor area the home €/m² applies to, or None when the stored size is the plot."""
    if not area:
        return None
    land = _raw(item).get("land_m2")
    try:
        land_m2 = float(land) if land not in (None, "") else None
    except (TypeError, ValueError):
        land_m2 = None
    if land_m2 and abs(land_m2 - area) < 1:
        return None
    if area > 1000:
        return INVEST_VALUE_MAX_M2
    return area


def _rural_points(area: float, pay: float, t: dict, reasons: list[str], full: str = "",
                  caps: list[float] | None = None, item: dict | None = None) -> float:
    """A rural plot is only interesting when it is big and cheap per m²; next
    to water it is worth more."""
    min_m2, max_eur = t["rural_min_m2"], t["rural_max_eur_m2"]
    if not area:
        reasons.append("rural plot, size unknown")
        return -12
    size = area / min_m2
    s = curve(size, RURAL_SIZE_POINTS)
    if caps is not None:
        caps.append(curve(size, SMALL_RURAL_PLOT_CAP))
    if area < min_m2:
        reasons.append(f"rural plot too small ({_ha(area)} < {_ha(min_m2)})")
        return s
    if size >= 5:
        reasons.append(f"large rural plot ({_ha(area)})")
    else:
        reasons.append(f"medium rural plot ({_ha(area)})")
    water = water_nearby(full, item)
    if water and floods(item.get("climate")):
        reasons.append(f"water nearby ({water}), but it floods — no water bonus")
    elif water:
        s += (9 if water.endswith("(approx.)") else 18) * w("water")
        reasons.append(f"{'near' if water.endswith('(approx.)') else 'next to'} water ({water})")
    if pay:
        per_m2 = pay / area
        s += curve(per_m2 / max_eur, RURAL_EUR_M2_POINTS) * w("price")
        if per_m2 <= max_eur / 2:
            reasons.append(f"very cheap land (€{per_m2:.2f}/m²)")
        elif per_m2 <= max_eur:
            reasons.append(f"cheap land (€{per_m2:.2f}/m²)")
        else:
            reasons.append(f"dear for rural land (€{per_m2:.2f}/m²)")
    return s


# ─── A plot as an investment (the "land" goal) ───────────────────────
# What you make on a plot is the gap between its price per hectare and what
# land sells for there, so that gap carries the most points. Size, access and
# a town within reach decide whether anyone will buy it from you.
LAND_DISCOUNT_POINTS = [(0.1, 0), (0.3, 10), (0.5, 20), (0.7, 28)]   # share below the local land price
LAND_DISCOUNT_TRUST = 0.7     # beyond this the gap is not believed: wrong area, a share, no access
LAND_TOO_CHEAP = -12          # …and past it, it costs until someone checks why
# Kilometres from the middle of the plot's own town: a buyer has to want it.
LAND_TOWN_POINTS = [(2, 8), (10, 5), (25, 0), (50, -8)]
LAND_BUILDING_PLOT = 6        # building land resells to anyone; rustic land only to a neighbour
LAND_FORESTRY_UPSIDE = [(0.05, 0), (0.10, 4), (0.20, 8)]   # the forestry return, as an extra on top


def _land_points(item: dict, kind: str, area: float, pay: float, full: str,
                 reasons: list[str], caps: list[float]) -> float:
    """What a plot is worth as an investment: cheap against what land sells for
    there, reachable, and with whatever a forestry project would add on top."""
    s = 0.0
    import land_prices
    fair = land_prices.land_value(item)
    if fair and pay and area:
        worth = fair["eur_ha"] * (area / 10000)
        disc = (worth - pay) / worth
        reasons.append(f"land here sells for €{fair['eur_ha']:,.0f}/ha ({fair['label']}): "
                       + (f"{disc:.0%} below that" if disc >= 0.01 else "not below that"))
        if disc > LAND_DISCOUNT_TRUST:
            # Past ~70% the gap is not a bargain: wrong area, a share, or no access.
            s += LAND_TOO_CHEAP
            reasons.append("so far below what land sells for there usually means a share, no access or a "
                           "wrong area — check why")
        else:
            s += curve(disc, LAND_DISCOUNT_POINTS) * w("price")
    if kind == "urban_plot":
        s += LAND_BUILDING_PLOT
        reasons.append("building land — resells to anyone, not only to a neighbour")
    near = item.get("town_distance")
    if near and near.get("km") is not None:
        s += curve(near["km"], LAND_TOWN_POINTS)
        reasons.append(near["text"])

    # Forestry is one use of the plot, not the only reason to buy it: whatever
    # a project would return is an extra on top of the land's own price.
    upside = _forestry_upside(item, area, pay, full)
    if upside:
        s += curve(upside["roi"], LAND_FORESTRY_UPSIDE)
        reasons.append(upside["text"])
    s += _habitat_points(item, full, reasons, forest=False)
    return s


def _forestry_upside(item: dict, area: float, pay: float, full: str) -> dict | None:
    """{"roi", "text"}: what a forestry project on this plot would return a year
    (the Forestry tab's own figures), when the plot is big enough for one."""
    if not pay or area < FOREST_MIN_M2 or (item.get("country") or "").upper() not in FOREST_COUNTRIES:
        return None
    climate = item.get("climate") or {}
    if not climate:
        return None
    out: dict = {}
    _score_forest(item, None, None, out=out)     # the same figure the Forestry tab shows
    roi = out.get("roi")
    if roi is None or roi <= 0:
        return None
    return {"roi": roi, "text": f"forestry on it would return about {roi:.1%} a year "
                                f"over {FOREST_ROI_YEARS} years — see Forestry"}


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
    """imoveis / ouro_joias / outros.

    A house on Rua do Ouro, or in Ervedosa do Douro, is a house. Gold words
    count when the title is not a property; the portal's own type wins when it
    says the lot is a vehicle, furniture or equipment.
    """
    title = item.get("title") or ""
    tipo = normalize(item.get("tipo"))
    if tipo in NOT_PROPERTY_TYPES:
        return "outros"
    property_sale = tipo in IMOVEL_TYPES or has_term(title, IMOVEL_TITLE_WORDS, negations=False)
    if has_term(title, GOLD_KW, negations=False) and not property_sale:
        return "ouro_joias"
    if has_term(title, VEHICLE_KW, negations=False) and not property_sale:
        return "outros"
    if property_sale:
        return "imoveis"
    return "outros"


# ─── The owner's five wishes, at a glance (2026-10-05) ────────────────
# Shown on each listing as ticks: near an airport, spacious, somewhere to swim
# (the sea first), very good condition, water on the land itself.
WISH_AIRPORT_KM = (30, 60)        # yes within the first, partly within the second
WISH_SPACE_M2 = (150, 100)        # a home: yes from the first, partly from the second
WISH_LAND_M2 = (10000, 5000)      # a plot


def wishes(item: dict) -> list[dict]:
    """[{"key", "label", "state": yes|part|no|unknown, "text"}] for the five wishes."""
    out = []

    def add(key, label, state, text):
        out.append({"key": key, "label": label, "state": state, "text": text})

    air = item.get("airport")
    if air and air.get("km") is not None:
        km = air["km"]
        add("airport", "Airport", "yes" if km <= WISH_AIRPORT_KM[0] else "part" if km <= WISH_AIRPORT_KM[1] else "no",
            f"{km:.0f} km")
    else:
        add("airport", "Airport", "no" if item.get("climate") else "unknown", "far" if item.get("climate") else "?")

    area = item.get("area_m2") or 0
    big, ok = WISH_LAND_M2 if property_kind(item) in ("rural_plot", "urban_plot") else WISH_SPACE_M2
    add("space", "Space", "unknown" if not area else "yes" if area >= big else "part" if area >= ok else "no",
        f"{area:,.0f} m²".replace(",", ".") if area else "?")

    swim = swim_spot(item)
    if swim and swim[0] <= SWIM_MAX_KM:
        add("swim", "Swim", "yes" if swim[1] in ("the sea", "sea inlet (ría)") else "part",
            f"{swim[1].replace('the sea', 'sea')} {swim[0]:.1f} km")
    else:
        add("swim", "Swim", "no" if (swim or item.get("climate")) else "unknown",
            f"{swim[1].replace('the sea', 'sea')} {swim[0]:.0f} km" if swim else ("none" if item.get("climate") else "?"))

    state = condition(item)
    add("condition", "Condition", {"good": "yes", "some": "part", "heavy": "no"}.get(state, "unknown"),
        {"good": "good", "some": "some work", "heavy": "full renovation"}.get(state, "not stated"))

    words = _water_words(f"{item.get('title') or ''} {item.get('description') or ''}")
    check = _raw(item).get("water_check") or {} if '"water_check"' in (item.get("raw_json") or "") else {}
    found = [w for w in check.get("found") or [] if w.get("kind") != "canal"]
    if words:
        add("water", "Water on land", "yes", words)
    elif found and not check.get("approx"):
        add("water", "Water on land", "yes" if check.get("radius_m", 300) <= 300 else "part",
            f"{found[0].get('kind', 'water')} within {check.get('radius_m', 300)} m")
    elif found:
        add("water", "Water on land", "part", f"{found[0].get('kind', 'water')} near the village")
    else:
        add("water", "Water on land", "no" if check else "unknown", "none found" if check else "?")
    return out


# ─── The owner's goals, each ranked on its own (2026-10-05) ──────────
#   home    — a place to live: a home, not a plot (swim, five wishes, AMOC…);
#   invest  — a home to make money on: far below the local price after the
#             taxes and the work, a net rental yield, a forced or bank sale;
#   land    — a plot as an investment: cheap per hectare against what land
#             sells for there, big enough, reachable, with its forestry upside;
#   forest  — a forestry project on that land: 10 ha or more, a climate trees
#             will stand, timber and crops over FOREST_ROI_YEARS.
MODES = {"home": "My home", "invest": "Investment home", "land": "Investment land",
         "forest": "Forestry"}
# What an offer is for. A home to live in is a different tab.
EARNING_MODES = ("invest", "land", "forest")

INVEST_DISCOUNT_POINTS = [(0.1, 0), (0.3, 12), (0.5, 25), (0.7, 35)]     # share below the local price
INVEST_YIELD_POINTS = [(3, 0), (5, 8), (7, 16), (11, 24)]                # net rent a year, % of the cost
INVEST_BEACH_POINTS = [(0.5, 20), (1, 16), (3, 10), (10, 3), (20, 0)]    # km to the sea
INVEST_FORCED_SALE = 8          # a court, tax or social-security sale
INVEST_BANK_SALE = 4            # a bank selling what it repossessed
INVEST_VALUE_MAX_M2 = 250       # m² of building valued at most: a bigger "area" is usually the plot
INVEST_DISCOUNT_TRUST = 0.7     # beyond this share below the local price the gap is not believed
INVEST_TOO_CHEAP = -15          # and 15 points beyond it, it costs: something is wrong until checked
INVEST_RUIN_CAP = 45            # a full rebuild stays under the line that shows as a score


def price_too_far_below(item: dict, all_in: float) -> bool:
    """The all-in cost is so far under this place's own home price that a rent
    on it would not be a return. A province average is not that comparison."""
    area = item.get("area_m2") or 0
    if not area or not all_in or all_in <= 0:
        return False
    found = local_price(item)
    if not found:
        return False
    factor, why = local_value_factor(item)
    if _discount_skipped(item, why):
        return False
    mv = found[0] * min(float(area), INVEST_VALUE_MAX_M2) * factor
    return mv > 0 and (mv - all_in) / mv > INVEST_DISCOUNT_TRUST


def _discount_skipped(item: dict, why: list[str]) -> str:
    """Why a gap to the local price is shown and does not add points, or "".

    A province average includes its cities. Photos that miss the house are
    not a resale. A wide gap still follows the price curve: cutting it off
    would rank a dearer home above a cheaper one."""
    if "province average" in why:
        return " — not counted (a province average, cities included)"
    if (_raw(item).get("photo_check") or {}).get("shows_house") is False:
        return " — not counted (the photos do not show the house)"
    return ""
# A sitting tenant: income from the first day, but the flat cannot be shown, the
# rent is often an old one and a buyer wants it empty. It costs less when the
# rent is known than when nothing about the letting is.
INVEST_TENANT_WITH_RENT = -8
INVEST_TENANT_UNKNOWN = -25
# Other homes still listed in this district, on the sites scanned, before the
# score mentions them. The mention does not change the points: the count is
# not the whole market, and there is no measured cut to the exit price.
INVEST_SUPPLY_NOTE = 8


def _occupied(item: dict, full: str) -> bool:
    occupation = _occupation(item)
    return occupation == "occupied" or (occupation is None and has_term(full, OCCUPANCY_PATTERNS))


def _score_invest(item: dict, now: datetime | None, targets: dict | None) -> tuple[float, list[str]]:
    """A home to make money on: resale under the local price, holiday or long rent."""
    title, desc = item.get("title") or "", item.get("description") or ""
    full = f"{title} {desc}"
    skip = _skip_reason(item, title, full)
    if skip:
        return 0.0, [skip]
    kind = property_kind(item)
    if kind != "home":
        return 0.0, ["not a home — see Investment land"]
    s, reasons, caps = 50.0, [], []
    area = item.get("area_m2") or find_area(title) or find_area(desc) or 0
    pay = _pay(item)
    likely = item.get("predicted_final")
    if likely and likely["price"] > pay * 1.02:
        pay = likely["price"]
        reasons.append(likely["text"])
    _doubts(item, kind, pay, area, full, reasons, caps)
    if item.get("unlocated"):
        caps.append(UNCHECKED_CAP)
        reasons.append("location unknown — check it")

    # What it really takes to own it: the price, the transfer tax, the notary
    # and registry, and the work the condition implies (costs.py). A gap
    # measured on the bare price is not a profit.
    import costs
    # Only INVEST_VALUE_MAX_M2 of building is valued, so only that much is
    # taxed, renovated and let: a bigger "area" is the plot, not floor space.
    building = {**item, "kind": kind, "area_m2": min(area, INVEST_VALUE_MAX_M2) if area else area}
    est = costs.estimate(building, bid=pay) if pay else None
    all_in = pay
    if est:
        all_in = (est["all_in"]["low"] + est["all_in"]["high"]) / 2 if est["all_in"] else est["total"]
        reasons.append(f"€{all_in:,.0f} all-in (price, taxes and fees"
                       + (", and the work it needs)" if est["all_in"] else ")"))

    found = local_price(item) if area else None
    unbelievable = False
    if found and all_in:
        # A listing's area is often the plot: value at most INVEST_VALUE_MAX_M2 of building.
        factor, why = local_value_factor(item)
        mv = found[0] * min(area, INVEST_VALUE_MAX_M2) * factor
        disc = (mv - all_in) / mv
        if disc > 0.1:
            adjusted = f"; counted at {factor:.0%}: {', '.join(why)}" if why else ""
            skipped = _discount_skipped(item, why)
            # The wide-gap case has its own reason just below.
            if skipped and disc <= INVEST_DISCOUNT_TRUST:
                adjusted += skipped
            reasons.append(f"{disc:.0%} below local prices all-in ({found[1]}{adjusted})")
        if disc > INVEST_DISCOUNT_TRUST:
            # Shown, but not rewarded: a gap this wide is usually a share, a tenant or a wrong area.
            unbelievable = True
            s += INVEST_TOO_CHEAP
            reasons.append("so far below the local price usually means a share, a tenant, a ruin or a wrong area"
                           " — check why")
        elif not _discount_skipped(item, why):
            s += curve(disc, INVEST_DISCOUNT_POINTS) * w("price")
    _mention_sold_move(item, reasons)
    # A price this far under local sales is not a rent. Drop whatever figure was
    # worked out, including when the comparison was a province average.
    if unbelievable and est:
        est["rent"] = None
    rent = (est or {}).get("rent")
    state = condition(item)
    occupied = _occupied(item, full)
    if rent and state == "heavy":
        reasons.append("the rent is not counted — it needs a full rebuild before it can be let")
    elif rent and rent.get("trusted") is False:
        reasons.append(f"the ad states €{rent['monthly']:,.0f}/month, far above the price — "
                       "not counted until you check it")
    elif rent and occupied and not rent.get("stated"):
        # The town average is not the lease, and you may not receive it.
        reasons.append(f"town rent about €{rent['monthly']:,.0f}/month ({rent['yield_pct']}% gross) "
                       "is not counted — someone is in it and the ad does not state the lease")
    elif rent:
        s += curve(rent["net_yield_pct"], INVEST_YIELD_POINTS)
        head = (f"lease in the ad €{rent['monthly']:,.0f}/month"
                if rent.get("stated") else f"rent about €{rent['monthly']:,.0f}/month")
        reasons.append(f"{head}: {rent['net_yield_pct']}% a year net of "
                       f"running costs and empty months ({rent['yield_pct']}% gross), "
                       f"paid back in {rent['payback_years']} years"
                       + (" (a province average, halved — check rents in the village)"
                          if rent.get("province_average")
                          else " (a town average — check rents there)" if rent["yield_pct"] > 15 else ""))
    beach = item.get("beach")
    if beach and beach.get("km") is not None:
        s += curve(beach["km"], INVEST_BEACH_POINTS) * (BEACH_APPROX_SHARE if beach.get("approx") else 1) * w("beach")
        if beach["km"] <= 10:
            reasons.append(beach.get("text") or f"{beach['km']:.1f} km from the beach")

    import source_validation
    kind_of_source = (source_validation.CATALOG.get(item.get("source")) or {}).get("kind")
    if kind_of_source == "official":
        s += INVEST_FORCED_SALE * w("sale")
        reasons.append("forced sale (court, tax or social security)")
    elif kind_of_source == "bank":
        s += INVEST_BANK_SALE * w("sale")
        reasons.append("bank sale")

    if state == "heavy":
        caps.append(INVEST_RUIN_CAP)
    s += {"good": 5, "some": -5, "heavy": -15}.get(state, -3)
    reasons.append({"good": "good condition", "some": "needs some work", "heavy": "needs heavy work"}.get(
        state, "condition not stated"))
    if area and area < 30:
        s -= 10
        reasons.append(f"small ({area:.0f} m²)")
    if occupied:
        # A tenant is income when the ad states the rent. The town average is not
        # that lease, so it is not counted above. A price too far below local
        # sales has no rent figure: that is the price, not a missing lease.
        if unbelievable:
            s += INVEST_TENANT_WITH_RENT
            reasons.append("tenanted — check the lease; it cannot be shown and a buyer will want it empty")
        elif not rent:
            s += INVEST_TENANT_UNKNOWN
            reasons.append("occupied/tenanted, and no rent can be worked out — check the lease")
        elif rent.get("stated") and state != "heavy" and rent.get("trusted") is not False:
            s += INVEST_TENANT_WITH_RENT
            reasons.append("tenanted — the ad states the rent, so that is the income; "
                           "it cannot be shown and a buyer will want it empty")
        else:
            s += INVEST_TENANT_WITH_RENT
            reasons.append("tenanted — the town rent is not your income until the lease is known; "
                           "it cannot be shown and a buyer will want it empty")
    c = item.get("climate") or {}
    if floods(c):
        s -= 15 * w("risks")
        reasons.append(f"in the 100-year flood zone ({floods(c):.1f} m)")
    future = (c.get("hot_days") or {}).get("rcp45_2071-2100")
    if future is not None and future > REJECT_HOT_DAYS:
        s -= 15 * w("heat")
        reasons.append(f"{future:.0f} days a year above 35 °C by 2071-2100 — value at risk")
    if pay and not (item.get("min_price") or 0) and forced_sale(item):
        s += 4
        reasons.append("no minimum bid")
    others = item.get("open_homes_here") or 0
    if others >= INVEST_SUPPLY_NOTE:
        reasons.append(f"{others} other homes still listed in this district on the sites scanned — "
                       "a later buyer is choosing among them. This is not the whole market, "
                       "and it is not taken off the price above")
    if pay:
        reasons.append(f"€{pay:,.0f}")
    if caps:
        s = min(s, min(caps))
    s += _population_outlook_points(item, reasons)
    return max(s, 0.0), reasons


FOREST_MIN_M2 = 100_000          # 10 ha: the project. 5 ha can still be a first plot.
FOREST_STARTER_M2 = 50_000       # below this the forestry tab says nothing
FOREST_STARTER_CAP = 64          # a first plot stays under a real 10 ha project
FOREST_CLOSES_SOON = 10          # an auction ending within 60 days
FOREST_CLOSES_NEAR = 6           # within half a year: still early, no calendar-year cutoff
FOREST_CLOSES_LATER = 2          # a live sale further out still counts, just less
FOREST_PRIVATE_SALE = 6          # no end date: an offer can go out now
FOREST_HOME_COUNTRY = 8          # Portugal: a visit and the paperwork are actually possible
FOREST_DOUBTFUL_EUR_M2 = 0.015   # under €150 a hectare: a placeholder price ("999 €") or a wrong area
FOREST_SIZE_POINTS = [(10, 0), (20, 6), (50, 12), (100, 16)]              # hectares
FOREST_HOT_DAYS_POINTS = [(0, 8), (7, 0), (20, -15), (40, -30)]           # days above 35 °C by 2071-2100
FOREST_DRY_POINTS = [(-150, -12), (-50, 0)]                               # mm, summer water balance if AMOC stops
FOREST_WORDS = ["floresta", "florestal", "pinhal", "montado", "souto", "carvalhal", "forestal", "bosque",
                "arbolado", "forêt", "forestier", "boisé", "bosco", "boschivo", "wald",
                "šuma", "гора", "lesný pozemok", "lesná pôda", "lesný"]
BUILDING_LAND = -25
# What makes forest land worth less than its hectares, from the ad's own words.
FOREST_FLAGS = [
    (["forte pente", "très pentu", "tres pentu", "pentes raides", "terrain pentu", "mucho desnivel", "gran desnivel",
      "fuerte pendiente", "muy escarpad*", "muito declive", "declive acentuado", "muito inclinado"],
     -10, "steep — costly or impossible to log"),
    (["taillis", "bois de chauffage", "monte bajo", "matorral", "maquis", "garrigue", "lande", "mato", "matos",
      "leña", "lenha"], -6, "coppice, scrub or firewood-grade wood — little timber value"),
    (["sans accès", "sans acces", "enclavé", "sin acceso", "sem acesso", "piste de", "pista de tierra",
      "acceso por pista", "caminho de terra"], -6, "poor access — check how timber trucks get in"),
    (["non délimitées", "non delimitees", "diferentes parajes", "varias parcelas", "várias parcelas",
      "parcelles dispersées", "plusieurs îlots"], -4, "several separate plots — harder to manage"),
]
# Habitat constraints from the notice and from a stored site_check (Natura 2000 /
# ZNIEFF). Never invented: no map hit means nothing is claimed, not "not protected".
# Eucalyptus is fire-prone and new planting is normally restricted in Portugal
# (DL 96/2013 as amended); it is not scored as a timber crop.
EUCALYPTUS_WORDS = ["eucalipt*", "eucalyp*"]
PROTECTED_LAND_WORDS = [
    "natura 2000", "znieff", "zone protégée", "réserve naturelle",
    "espacio protegido", "espacio natural protegido", "parque natural",
    "parque nacional", "parc national", "parco nazionale", "national park",
    "rede natura", "zona de protecção", "zona de proteção",
    "reserva ecológica nacional", "reserva ecologica nacional",
    "reserva agrícola nacional", "reserva agricola nacional",
    "área da ren", "area da ren", "áreas da ren", "areas da ren",
    "área da ran", "area da ran",
    "rede nacional de áreas protegidas",
    "sítio de importância comunitária", "sitio de importancia comunitaria",
    "zona especial de conservação", "zona de proteção especial",
    "zona de especial protección",
]
_SLOPE = re.compile(r"(?:pente|pendiente|declive|inclinaci[oó]n)\s+(?:de\s+|del\s+)?(?:\d{1,2}\s*(?:à|a|-)\s*)?(\d{2})\s*%",
                    re.I)
FOREST_STEEP_PCT = 40
SITE_CABLE_SHARE = 0.4      # this much of the land over 60% slope: cable yarding only
SITE_TRACK_M = 500          # further than this from a track a timber lorry can use
SITE_MONTADO = 0.3          # this much montado: the cork is already growing
SITE_EUCALYPTUS = 0.3       # this much eucalyptus on the COS map: fire, not a timber crop


def forestry_growing(text: str) -> set[str]:
    import forestry
    return forestry.growing(text)


def _site_check(item: dict) -> dict | None:
    """The site check stored for a listing by the scan (site_check.py), if any."""
    import geo
    return geo._raw(item).get("site_check")


def _habitat_points(item: dict, full: str, reasons: list[str], *, forest: bool) -> float:
    """Natura / REN / RAN / eucalyptus from the notice or a stored site check.

    A map miss is not 'not protected': only a named hit or the ad's own words
    move the score. Eucalyptus is a fire and legal constraint, not a crop.
    """
    s = 0.0
    site = _site_check(item)
    names = [n for n in ((site or {}).get("protected") or []) if n]
    if names:
        where = "" if site.get("exact") else " around it"
        label = names[0] if len(names) == 1 else f"{len(names)} protected sites ({names[0]})"
        s -= 10 if forest else 8
        reasons.append(f"site check: {label}{where} — planting, felling and building normally need permits")
    elif has_term(full, PROTECTED_LAND_WORDS, negations=False):
        s -= 5 if forest else 8
        reasons.append("protected area — planting, felling and building normally need permits")
    if has_term(full, EUCALYPTUS_WORDS, negations=False) or (
            site and (site.get("eucalyptus") or 0) >= SITE_EUCALYPTUS):
        s -= 12 if forest else 8
        if site and (site.get("eucalyptus") or 0) >= SITE_EUCALYPTUS:
            where = "" if site.get("exact") else " around it"
            reasons.append(f"site check: {site['eucalyptus']:.0%} eucalyptus{where} — fire-prone; "
                           "new planting is normally restricted in Portugal (DL 96/2013) — "
                           "converting to natives is the habitat case, not a timber crop")
        else:
            reasons.append("eucalyptus — fire-prone; new planting is normally restricted in Portugal "
                           "(DL 96/2013) — converting to natives is the habitat case, not a timber crop")
    return s


# What the best crop earns a year, as a share of the land's price per hectare.
FOREST_COUNTRIES = {"PT", "ES", "FR", "BE", "NL", "LU"}   # the owner's choice (2026-10-05)
FOREST_DEAD_ZONE = -25       # nothing worth planting will last: not forestry land
FOREST_ROI_TARGET = 0.20    # the owner's goal: at least 20% on the money
FOREST_ROI_YEARS = 10


def forest_return(pay: float, ha: float, best: dict | None, timber: dict | None,
                  land_gain: float = 0.0) -> float | None:
    """The average yearly return on the price over FOREST_ROI_YEARS: a one-off
    gain — the standing timber the ad states, or buying below the official forest
    price, whichever is larger (a forest's price already includes its timber) —
    plus the best crop's equivalent yearly value. None without a price or income."""
    if not pay or (not best and not timber and not land_gain):
        return None
    once = max(timber["eur"] if timber else 0.0, land_gain)
    earned = once + (best["eur_ha_year"] * ha * FOREST_ROI_YEARS if best else 0)
    return earned / pay / FOREST_ROI_YEARS


_BUYER_FEE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*(?:d'honoraires|de honorarios|de honorários)?[^.%]{0,40}?"
                        r"(?:charge de l'acqu[ée]reur|a cargo del comprador|a cargo do comprador)", re.I)


def buying_costs(item: dict, text: str) -> tuple[float, str]:
    """(share of the price, label): taxes and notary, plus a buyer-paid agent fee the ad states."""
    from costs import purchase_share
    share, label = purchase_share(item)
    fee = _BUYER_FEE.search(text or "")
    if fee:
        pct = float(fee.group(1).replace(",", ".")) / 100
        share += pct
        label += f" + {pct:.2%} agent fee paid by the buyer"
    return share, label


# Standing timber's value as a share of the price: above 1 the land comes free.
FOREST_TIMBER_POINTS = [(0.3, 0), (0.8, 6), (1.0, 10), (1.2, 18), (1.6, 25)]
# Yearly return on the price plus buying costs, over FOREST_ROI_YEARS. 20% is the
# goal and is what ranks a plot; a cheap hectare is already inside that return.
FOREST_RETURN_POINTS = [(-0.01, -20), (0, -8), (0.02, 0), (0.05, 12), (0.10, 24), (0.20, 40)]


def _forest_sooner(item: dict, now: datetime | None, reasons: list[str]) -> float:
    """Points for a plot that can be bought sooner. There is no year deadline.

    An auction ending soon ranks above one ending later; a private sale with no
    date can be offered on now. A date next year is still a live sale. Portugal
    ranks above the same plot abroad because a visit and the paperwork are
    possible from here. Cheap land is scored separately, and outweighs timing.
    """
    now = now or utcnow()
    s = 0.0
    end = effective_end(item.get("date_end"))
    if end and end > now:
        days = (end - now).total_seconds() / 86400
        if days <= 60:
            s += FOREST_CLOSES_SOON
            reasons.append(f"closes {end:%Y-%m-%d} — soon")
        elif days <= 180:
            s += FOREST_CLOSES_NEAR
            reasons.append(f"closes {end:%Y-%m-%d} — before long")
        else:
            s += FOREST_CLOSES_LATER
            reasons.append(f"closes {end:%Y-%m-%d} — later")
    elif end is None:
        s += FOREST_PRIVATE_SALE
        reasons.append("no closing date — a private sale; you can make an offer now")
    if (item.get("country") or "").upper() == "PT" and off_western_continental(item) is None:
        s += FOREST_HOME_COUNTRY
        reasons.append("Portugal — you can visit it and the paperwork is in your language")
    likely = item.get("predicted_final") or {}
    ratio = likely.get("ratio")
    if ratio and ratio < 0.9 and not (likely.get("price") and likely["price"] > _pay(item) * 1.02):
        reasons.append(f"similar sales closed around {ratio:.0%} of the base — still budget for the minimum bid")
    return s


# "suelo no urbanizable" is rustic. "suelo urbanizable" and a numbered sector are not.
_DEVELOPMENT_LAND = re.compile(
    r"(?<!\bno )\burbanizable\b|\bfuturos desarrollos\b|\bdesarrollo urbanistico\b"
    r"|\bsector\s+[a-z]{0,8}\d+\b|\bsued[-\s]?\d+\b")
_HIGH_MOUNTAIN = re.compile(
    r"\b(?:altitud|cota|snm|sobre el nivel)[^.]{0,40}?(\d{1,2}[.,]\d{3})")
HIGH_MOUNTAIN_M = 1800


def _development_land(text: str) -> bool:
    return bool(_DEVELOPMENT_LAND.search(normalize(text or "")))


def _high_mountain_text(text: str) -> str | None:
    """'altitud 2.000–2.370 m' when the ad states it. A distance ('a 2 km') is not."""
    found = []
    for m in _HIGH_MOUNTAIN.finditer(normalize(text or "")):
        meters = float(m.group(1).replace(".", "").replace(",", ""))
        if meters >= HIGH_MOUNTAIN_M:
            found.append(meters)
    if not found:
        return None
    if len(found) == 1:
        return f"{found[0]:.0f} m"
    return f"{min(found):.0f}–{max(found):.0f} m"


def _forest_block(item: dict, full: str, kind: str) -> str | None:
    """Building land, a national park or high mountain: not a forestry project."""
    if kind == "urban_plot" or _development_land(full):
        return "building land (urbanizable or a development sector) — not a forestry plot"
    if has_term(full, ["parque nacional", "parc national", "parco nazionale", "national park"],
                negations=False):
        return "inside a national park — not a forestry project"
    site = _site_check(item)
    names = " ".join((site or {}).get("protected") or [])
    if has_term(names, ["parque nacional", "parc national", "parco nazionale", "national park"],
                negations=False):
        return "site check: national park — not a forestry project"
    stated = _high_mountain_text(full)
    if stated:
        return f"high mountain ({stated}) — not a forestry project"
    if site and site.get("min_m") is not None and site["min_m"] >= HIGH_MOUNTAIN_M:
        top = site.get("max_m")
        span = f"{site['min_m']:.0f}–{top:.0f} m" if top else f"{site['min_m']:.0f} m"
        return f"high mountain ({span}) — not a forestry project"
    return None


def _score_forest(item: dict, now: datetime | None, targets: dict | None,
                  out: dict | None = None) -> tuple[float, list[str]]:
    """Land bought as a forestry investment: the rank is the return on the money."""
    apply_stated_area(item)
    title, desc = item.get("title") or "", item.get("description") or ""
    full = f"{title} {desc}"
    skip = _skip_reason(item, title, full)
    if skip:
        return 0.0, [skip]
    if (item.get("country") or "").upper() not in FOREST_COUNTRIES:
        return 0.0, ["outside the forestry countries (PT, ES, FR, Benelux)"]
    kind = property_kind(item)
    area = item.get("area_m2") or find_area(title) or find_area(desc) or 0
    if kind == "home" and area >= FOREST_MIN_M2 and has_term(full, RURAL_WORDS, negations=False):
        kind = "rural_plot"                        # a house on a big estate: the value is the land
    if kind not in ("rural_plot", "urban_plot"):
        return 0.0, ["not land"]
    blocked = _forest_block(item, full, kind)
    if blocked:
        note = [item["area_note"]] if item.get("area_note") else []
        return 0.0, note + [blocked]
    if not area or area < FOREST_STARTER_M2:
        return 0.0, [f"{_ha(area) if area else 'size unknown'} — under 5 ha"]
    starter = area < FOREST_MIN_M2
    s, reasons, caps = 50.0, [], []
    if item.get("area_note"):
        reasons.append(item["area_note"])
    if starter:
        caps.append(FOREST_STARTER_CAP)
        reasons.append(f"{_ha(area)} — a first plot, under 10 ha")
    pay = _pay(item)
    s += _forest_sooner(item, now, reasons)
    _doubts(item, kind, pay, area, full, reasons, caps, land_floor=FOREST_DOUBTFUL_EUR_M2)
    if item.get("unlocated"):
        caps.append(UNCHECKED_CAP)
        reasons.append("location unknown — climate not checked")
    ha = area / 10000
    if pay:
        per_ha = pay / ha
        reasons.append(f"€{per_ha:,.0f} a hectare")
    s += curve(ha, FOREST_SIZE_POINTS)
    if not starter:
        reasons.append(_ha(area))
    if kind == "urban_plot":
        s += BUILDING_LAND
        reasons.append("building land, not rustic — dearer to hold and to plant")
    elif has_term(full, FOREST_WORDS, negations=False):
        s += 5
        reasons.append("forest or woodland")
    for words, points, why in FOREST_FLAGS:
        if has_term(full, words, negations=False):
            s += points
            reasons.append(why)
    slope = _SLOPE.search(full)
    if slope and int(slope.group(1)) >= FOREST_STEEP_PCT and not any(r.startswith("steep") for r in reasons):
        s -= 10
        reasons.append(f"steep ({slope.group(1)}% slope) — costly or impossible to log")
    wooded = has_term(full, FOREST_WORDS, negations=False)
    existing = forestry_growing(full)
    site = _site_check(item)
    if site:
        where = "" if site.get("exact") else " around it"
        if site.get("cable_share", 0) >= SITE_CABLE_SHARE:
            s -= 12
            reasons.append(f"site check: {site['cable_share']:.0%} of the land{where} only by cable yarding (slope)")
        elif site.get("cable_share", 0) + site.get("winch_share", 0) >= 0.5:
            s -= 5
            reasons.append(f"site check: mostly over 30% slope{where} — tracked or winch logging")
        if (site.get("inaccessible") or 0) >= 0.5:
            s -= 10
            reasons.append(f"site check: IGN rates {site['inaccessible']:.0%}{where} inaccessible to timber machines")
        if (site.get("track_m") or 0) > SITE_TRACK_M:
            s -= 4
            reasons.append(f"site check: nearest track {site['track_m']:,.0f} m away")
        if (site.get("montado") or 0) >= SITE_MONTADO:
            existing = existing | {"cork oak"}
            reasons.append(f"site check: {site['montado']:.0%} montado (cork or holm oak){where}")
        if site.get("forest_share") is not None:
            wooded = wooded or site["forest_share"] >= 0.5
            reasons.append(f"site check: {site['forest_share']:.0%} wooded{where}")

    s += _habitat_points(item, full, reasons, forest=True)

    water = water_nearby(full, item)
    c = item.get("climate") or {}
    if water:
        s += (6 if water.endswith("(approx.)") else 12) * w("water")
        reasons.append(f"water on or by the land ({water})")
    elif c.get("water_km") is not None and c["water_km"] <= 1:
        s += (10 if c["water_km"] <= 0.3 else 4) * w("water")
        reasons.append(f"permanent water {c['water_km']:.1f} km away")
    future = (c.get("hot_days") or {}).get("rcp45_2071-2100")
    if future is not None:
        s += curve(future, FOREST_HOT_DAYS_POINTS) * w("heat")
        reasons.append(f"{future:.0f} days a year above 35 °C by 2071-2100")
    stress = (c.get("stress") or {}).get("stress_2080")
    if stress is not None and (stress >= 3 or stress == -1):
        s -= (20 if stress in (4, -1) else 10) * w("water")
        reasons.append("water stress " + ("arid" if stress == -1 else "extremely high" if stress == 4 else "high")
                       + " by 2080")
    fire = c.get("fire") or {}
    if fire.get("burnt_here"):
        s -= 15 * w("risks")
        reasons.append(f"burnt since 2016 ({', '.join(map(str, fire.get('years') or []))})")
    elif fire.get("count"):
        s -= 6 * w("risks")
        reasons.append("fires nearby since 2016")
    danger = (c.get("fire_danger") or {}).get("high_days_2090")
    if danger is not None and danger >= 30:
        s -= 8 * w("risks")
        reasons.append(f"{danger:.0f} days a year of high fire danger by 2090")
    dry = c.get("amoc_dry_mm")
    if dry is not None:
        s += curve(dry, FOREST_DRY_POINTS) * w("amoc")
        if dry <= AMOC_DRY_WARN_MM:
            reasons.append(f"summer water balance {dry:.0f} mm if the Atlantic current collapses")
    import forestry
    on_land = bool(water) and not water.endswith("(approx.)")    # near the village is not by the land
    crops = forestry.options(c, item.get("country"), ha, water_on_land=on_land, existing=existing,
                             trees=forestry.trees_for_item(item), wooded=wooded,
                             slope_pct=(site or {}).get("slope_mean"))
    timber = forestry.standing_timber(full, item.get("country"), ha)
    if timber and pay:
        share = timber["eur"] / pay
        s += curve(share, FOREST_TIMBER_POINTS) * w("price")
        reasons.append(f"standing timber {'~' if timber['estimated'] else ''}{timber['m3']:,.0f} m³"
                       f"{' (estimated: average Latvian stand, Silava NFI 2019–2023; the ad gave no volume)' if timber['estimated'] else ''}"
                       f"{' (young stand: valued at its sale in ~20 years)' if timber['young'] else ''}"
                       f" ≈ €{timber['eur']:,.0f} ({share:.0%} of the price, at {timber['label']})")
        if timber["rights_only"]:
            caps.append(UNCHECKED_CAP)
            reasons.append("felling rights may be sold without the land — check")
        if share >= 1 + FOREST_ROI_TARGET:
            reasons.append(f"timber alone is worth {share - 1:.0%} more than the price — the land comes free")
    if crops and c:
        best = crops[0]
        reasons.append(forestry.describe(best))
    elif c:
        s += FOREST_DEAD_ZONE
        reasons.append("no timber, cork, nut or carbon crop would still thrive here in 2100")
    import land_prices
    fair = land_prices.forest_value(item)
    if fair and pay:
        worth = fair["eur_ha"] * ha
        reasons.append(f"forest land here sells for €{fair['eur_ha']:,}/ha ({fair['label']}): "
                       f"{'%d%% below' % round((1 - pay / worth) * 100) if pay < worth else 'not below'} that")
    costs, costs_label = buying_costs(item, full)
    if pay:
        reasons.append(f"buying costs ≈ €{pay * costs:,.0f} ({costs_label})")
    unreachable = bool(site) and ((site.get("inaccessible") or 0) >= 0.5
                                  or site.get("cable_share", 0) >= SITE_CABLE_SHARE)
    if unreachable and crops and c:
        # timber that cannot be brought out cannot be sold: only the carbon counts
        crops = [{**crops[0], "eur_ha_year": crops[0]["carbon_eur_ha_year"]}] + crops[1:]
        reasons.append("no timber income counted: machines cannot reach most of the land")
    roi = None if caps else forest_return(pay * (1 + costs) if pay else pay, ha, crops[0] if crops and c else None, timber,
                        # land nobody can work is not worth the regional forest price
                        land_gain=max(0.0, fair["eur_ha"] * ha - pay) if fair and pay and not unreachable else 0.0)
    if out is not None:
        out["roi"] = roi
    if roi is not None:
        s += curve(roi, FOREST_RETURN_POINTS) * w("price")
        reasons.append(f"return ≈ {roi:.1%} a year over {FOREST_ROI_YEARS} years (timber now + best crop, "
                       f"on the price and the buying costs)"
                       + (" — meets the 20% goal" if roi >= FOREST_ROI_TARGET else ""))
    elif pay and not caps:
        reasons.append("no return can be worked out — not ranked as an investment")
    if pay:
        reasons.append(f"€{pay:,.0f}")
    if caps:
        s = min(s, min(caps))
    s += _population_outlook_points(item, reasons)
    return max(s, 0.0), reasons
