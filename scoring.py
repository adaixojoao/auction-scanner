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

from common import (days_left, find_area, find_terms, has_term, normalize,
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
    "encravado", "landlocked",
]

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
}

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
GUARDA_POINTS = [(10, 20), (25, 16), (50, 10), (80, 5), (120, 0)]

# Whatever else is good about them (a court sale, no minimum bid…), these are
# not the goal, so their score stays under the default minimum score (45) and
# they are hidden unless you shortlist them.
NOT_THE_GOAL_CAP = {"not a home or plot": 35, "needs heavy work": 40, "isolated location": 40,
                    "rejected:": 30}

# Rejected outright, whatever else looks good (the owner's rules, Sept 2026).
_REJECTS = [
    ("unfinished building", re.compile(r"inacabad[oa]|em tosco|por acabar|obra parada|constru[çc][ãa]o suspensa", re.I)),
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
SMALL_URBAN_PLOT_M2 = 150
EXPENSIVE_HOME_EUR = 60000

DWELLING_WORDS = [
    "moradia", "moradias", "apartamento", "vivenda", "habitação", "casa", "casas",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6",
    "vivienda", "chalet", "adosado", "unifamiliar",
    "maison", "appartement", "logement", "pavillon",
    "appartamento", "abitazione", "villetta",
    "wohnung", "haus", "einfamilienhaus", "zweifamilienhaus", "mehrfamilienhaus", "reihenhaus",
    "doppelhaushälfte", "woning", "woonhuis", "tussenwoning", "kuća", "house", "apartment",
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

OTHER_WORDS = [   # not a home and not a plot
    "parking", "garagem", "garage", "garaje", "box", "emplacement", "estacionamento",
    "lugar de garagem", "arrecadação", "arrecadacao", "arrumos", "arrumo", "loja", "armazém", "armazem",
    "escritório", "escritorio", "pavilhão", "pavilhao", "industrial", "estabelecimento",
    "local comercial", "nave", "oficina", "trastero", "aparcamiento", "plaza de garaje",
    "commerce", "local commercial", "bureau", "entrepôt", "hangar", "cave",
    "negozio", "magazzino", "capannone", "ufficio", "posto auto",
    "stellplatz", "tiefgarage", "lager", "büro", "gewerbe*", "bedrijfspand", "kantoor",
    "hotel", "restaurante", "café",
    "lavandaria", "lavanderia", "rouparia", "portaria", "casa das máquinas", "ginásio", "sala de condomínio",
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
    "obras profundas", "reabilitação total", "reabilitação integral", "inabitável", "sem telhado",
    "telhado caído", "muito degradad*", "mau estado", "para demolir", "demolição",
    "a reformar", "para reformar", "reforma integral", "para rehabilitar", "inhabitable",
    "à rénover", "a renover", "à restaurer", "travaux importants", "gros travaux", "en ruine",
    "à réhabiliter", "da ristrutturare", "rudere", "fatiscente", "inagibile",
    "sanierungsbedürftig", "renovierungsbedürftig", "abrissreif", "baufällig", "ruine",
    "opknapper", "bouwvallig", "renovatie nodig",
    # Abandoned: empty for years, falling apart ("devoluta" alone is only empty).
    "abandonad*", "ao abandono", "em abandono", "estado de abandono", "votad* ao abandono",
    "abbandonat*", "in stato di abbandono", "à l'abandon", "verwaarloosd", "verlaten",
]
RUIN_WORDS = HEAVY_WORK   # older name
SOME_WORK = [
    "necessita de obras", "precisa de obras", "necessitar de obras", "carece de obras",
    "obras de conservação", "degradad*", "necesita reforma", "necesita reformas",
    "para remodelar", "a remodelar", "para renovar", "a renovar", "para restaurar",
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
    "vicino al mare", "vista mare", "fronte mare", "vicino alla spiaggia",
    "bord de mer", "vue mer", "proche plage", "proche de la plage", "primera línea de playa",
    "aan zee", "zeezicht", "strandnah", "meerblick", "meeresnähe",
    "blizu mora", "pogled na more", "uz more", "blizu plaže",
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
    r"|\b(?:uz (?:rijeku|more|jezero)|blizu (?:rijeke|mora|jezera)|na obali)\b",
    re.I)


def water_nearby(text: str, item: dict | None = None) -> str | None:
    """The words that put a plot next to water, else what the map found
    within a few hundred metres of its exact position (geo.py), or None."""
    m = WATER_RE.search(text or "")
    if m:
        return m.group(0).strip()
    if item and '"water_check"' in (item.get("raw_json") or ""):
        check = _raw(item).get("water_check") or {}
        found = check.get("found") or []
        if found:
            w = found[0]
            name = f"{w['kind']} {w['name']}" if w.get("name") else w.get("kind", "water")
            return f"{name}, within {check.get('radius_m', 300)} m on the map"
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


def buyer_priorities(targets: dict | None = None) -> str:
    """The goal in words, for the AI check: the same rules as score()."""
    t = {k: (targets or {}).get(k) or v for k, v in TARGET_DEFAULTS.items()}
    return (
        "Homes (houses or flats) and plots at very low prices. In order of preference: a house in "
        "good condition in a great location well under market price; a large farm plot next to "
        "water (river, stream, lake, reservoir) that is very cheap; a house needing some repairs, "
        "dirt cheap, in a great location; a medium farm plot next to water, dirt cheap; a house "
        "in good condition, dirt cheap, in an ordinary location. Rural plots only if at least "
        f"{t['rural_min_m2']:,.0f} m² and cheap (at most €{t['rural_max_eur_m2']:.2f}/m², about "
        f"€{t['rural_max_eur_m2'] * 10000:,.0f} per hectare). Not wanted: small or partial homes "
        "or plots; homes needing heavy work (ruins, full rebuilds) unless they come with a big "
        "farm plot that carries the value; expensive homes; isolated or bad locations; timeshares "
        "(a few weeks a year); shops, "
        "garages, storage and offices."
    )


# "Villa" before a capitalised name after a place marker is a place (Italian
# cadastral "C.C. Villa Banale", "frazione Villa Rosa"), not a detached house.
_VILLA_PLACE = re.compile(r"\b(C\.\s?C\.|loc\.|localit[aà]|frazione|fraz\.|comune di|in|a|di)\s+Villa\s+(?=[A-Z])")


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
        if _PLOT_FOR_A_HOUSE.search(norm) or (
                _STARTS_AS_LAND.match(norm) and not has_term(text, _HOUSE_WORDS_NOT_TYPOLOGY, negations=False)):
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

    if tipo in NOT_PROPERTY_TYPES:
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


def condition(item: dict) -> str:
    """"heavy", "some", "good" or "unknown": how much work the listing admits
    to, else what its photos show (photos.py) when the text says nothing."""
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    if has_term(text, HEAVY_WORK):
        return "heavy"
    if has_term(text, SOME_WORK):
        return "some"
    if has_term(text, GOOD_CONDITION):
        return "good"
    seen = photo_condition(item)
    return seen["condition"] if seen else "unknown"


def photo_condition(item: dict) -> dict | None:
    """What the photos showed, when the check was sure enough to use."""
    if '"photo_check"' not in (item.get("raw_json") or ""):
        return None
    seen = _raw(item).get("photo_check") or {}
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
          targets: dict | None = None) -> tuple[float, list[str]]:
    """0–100: how well a listing fits the goal (see "What we are looking for"),
    with the reasons. `targets` are the config filters (rural_min_m2,
    rural_max_eur_m2); missing values use TARGET_DEFAULTS."""
    raw, reasons = score_detail(item, now, targets)
    return max(0.0, min(100.0, raw)), reasons


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
# Easy to reach: an airport with scheduled flights and a station on the
# long-distance trains, smaller bonuses that fade with the distance.
AIRPORT_POINTS = [(15, 8), (30, 7), (50, 5), (80, 2), (120, 0)]
STATION_POINTS = [(1, 7), (3, 6), (8, 4), (20, 1), (40, 0)]

# What the owner does not want, as the highest score it can reach. They slide
# too: a 38 m² home is held down a little less than a 30 m² one.
SMALL_HOME_CAP = [(25, 35), (40, 45), (75, 130), (100, 200)]   # by m²
EXPENSIVE_HOME_CAP = [(50000, 200), (60000, 100), (75000, 55), (90000, 40)]   # by €
SMALL_URBAN_PLOT_CAP = [(60, 35), (150, 45), (250, 200)]   # by m²
SMALL_RURAL_PLOT_CAP = [(0.3, 30), (1.0, 45), (1.3, 200)]  # by multiple of the minimum
FAR_FROM_TOWN_CAP = [(12, 200), (20, 60), (30, 45), (40, 40)]  # by km from town: a house
                                                               # far from everything is isolated


def score_detail(item: dict, now: datetime | None = None,
                 targets: dict | None = None) -> tuple[float, list[str]]:
    """The score before it is clamped to 0–100: several listings can reach 100,
    and this still says which of them is best (used for sorting)."""
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

    if is_fractional_share(title):
        return 0.0, ["fractional share — skip"]

    if has_term(full, USUFRUCT_PATTERNS):
        return 0.0, ["usufruct — skip"]

    if is_timeshare(full):
        return 0.0, ["timeshare (some weeks a year) — skip"]

    # ── What it is ────────────────────────────────────────────────────
    # A ruin on a big farm: the value is the land, so it is scored as land.
    if (kind == "home" and area >= t["rural_min_m2"] and has_term(full, HEAVY_WORK)
            and has_term(full, RURAL_WORDS, negations=False)):
        kind = "rural_plot"
        reasons.append("ruin on a farm — valued as land")

    # Land: too small is not wanted at all; near Guarda is wanted most.
    if kind in ("urban_plot", "rural_plot"):
        if (item.get("country") or "PT") != "PT":
            t = {**t, "rural_min_m2": max(t["rural_min_m2"], PLOT_MIN_ABROAD_M2)}
        if area and area < t["rural_min_m2"]:
            reasons.append(f"rejected: plot too small ({_ha(area)} < {_ha(t['rural_min_m2'])})")
        guarda = item.get("guarda")
        if guarda:
            bonus = curve(guarda["km"], GUARDA_POINTS) * (0.8 if guarda.get("approx") else 1)
            if bonus >= 1:
                s += bonus
                reasons.append(guarda["text"])

    if kind == "home":
        s += 10
        reasons.append("home")
        s += _home_points(item, full, area, pay, reasons, caps)
    elif kind == "urban_plot":
        s += 8
        reasons.append("urban plot" + (f" ({_ha(area)})" if area else ""))
        if area:
            caps.append(curve(area, SMALL_URBAN_PLOT_CAP))
            if area < SMALL_URBAN_PLOT_M2:
                reasons.append(f"small plot ({area:.0f} m²)")
    elif kind == "rural_plot":
        s += _rural_points(area, pay, t, reasons, full, caps, item)
    elif kind == "other":
        s -= 25
        reasons.append("not a home or plot")
    else:
        # Neither a home nor a plot as far as the text says ("Artigo urbano 4517"):
        # worth a look, but not above the ones that clearly are.
        s -= 10
        reasons.append("unclear what it is — check")

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
        s += curve(ratio, BID_RATIO_POINTS)
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
        points = curve(pay, PRICE_POINTS)
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
        s += 20
        reasons.append("sealed-bid (carta fechada)")

    if source in FORCED_SOURCES:
        s += 6
        reasons.append("forced sale (must sell)")
    if source in TAX_SOURCES:
        s += 4
        reasons.append("tax seizure — no reserve")

    # Minimum bid signal (one bonus per listing: these all describe the same fact).
    min_p = item.get("min_price") or 0
    if not pay and (sealed or source in FORCED_SOURCES
                    or has_term(full, OFFER_SALE_PATTERNS, negations=False)):
        s += 18
        reasons.append("no price — you set your offer")
    elif min_p and price and price > 1000 and min_p < price and curve(min_p, LOW_MIN_BID_POINTS) > 0:
        s += curve(min_p, LOW_MIN_BID_POINTS)
        reasons.append(f"min bid only €{min_p:.0f}")
    elif not min_p and pay and source in FORCED_SOURCES:
        s += 4
        reasons.append("no minimum bid")

    # Urgency. days_left() understands naive dates; the old tz-aware subtraction
    # raised on them, so most sources never got this bonus.
    left = days_left(item.get("date_end"), now or utcnow())
    if left is not None and left > 0:
        s += curve(left, DAYS_LEFT_POINTS)
        if left <= 3:
            reasons.append(f"{left * 24:.0f}h left — urgent" if left < 1 else f"{left:.0f}d left — urgent")
        elif left <= 7:
            reasons.append(f"{left:.0f}d left")

    if price and price < 300:
        s -= 20
        reasons.append("suspiciously cheap — likely tiny/worthless")

    for label, pattern in _REJECTS:
        if pattern is None or not pattern.search(full):
            continue
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
    return s, reasons


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
        reasons.append(f"needs heavy work (ruin / full rebuild){seen}")
    elif state == "some":
        s -= 12
        reasons.append(f"needs some work{seen}")
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
    if water:
        s += 6
        reasons.append(f"next to water ({water})")

    for key, points in (("beach", BEACH_POINTS), ("airport", AIRPORT_POINTS), ("station", STATION_POINTS)):
        near = item.get(key)
        if near:
            bonus = curve(near["km"], points) * (BEACH_APPROX_SHARE if near.get("approx") else 1)
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

    # Below the local price per m² (homes only: land is not priced like buildings)
    mv = market_value_estimate(item)
    if mv and pay and area <= 1000:
        factor, why = local_value_factor(item, state)
        mv *= factor
        market_disc = (mv - pay) / mv
        s += curve(market_disc, MARKET_DISCOUNT_POINTS)
        if market_disc > 0.20:
            adjusted = f"; counted at {factor:.0%}: {', '.join(why)}" if why else ""
            reasons.append(f"{market_disc:.0%} below local prices ({local_price(item)[1]}{adjusted})")
    return s


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
    if water:
        s += 18
        reasons.append(f"next to water ({water})")
    if pay:
        per_m2 = pay / area
        s += curve(per_m2 / max_eur, RURAL_EUR_M2_POINTS)
        if per_m2 <= max_eur / 2:
            reasons.append(f"very cheap land (€{per_m2:.2f}/m²)")
        elif per_m2 <= max_eur:
            reasons.append(f"cheap land (€{per_m2:.2f}/m²)")
        else:
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
    if has_term(title, VEHICLE_KW, negations=False) or tipo in NOT_PROPERTY_TYPES:
        return "outros"
    if tipo in IMOVEL_TYPES or has_term(title, IMOVEL_TITLE_WORDS, negations=False):
        return "imoveis"
    return "outros"
