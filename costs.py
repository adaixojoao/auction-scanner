"""
costs.py — what a property really costs, on top of the price on the listing.

A €20 000 house is not a €20 000 house: in Portugal the buyer also pays IMT,
imposto do selo and the land-registry fee, and then whatever the building needs
to be habitable. This module estimates all of it so the ⓘ panel, the Telegram
alert and the AI check talk about the real number.

Everything here is an estimate, and it says so:

- Portugal uses the published IMT brackets (see IMT_YEAR). IMT is charged on
  the higher of the price and the taxable value (VPT), which the listings do
  not give, so a property whose VPT is above the price will cost more.
- Spain's ITP is the 2026 general rate of the autonomous community when the
  listing names a province. It is charged on the price or the cadastral
  reference value, whichever is higher; the listing does not give the reference
  value. Reduced rates (a home you will live in, age, family) are not applied.
- France's transfer tax is the DGFiP scale of 1 June 2026: departmental duty,
  the 1.20% municipal tax and the 2.37% collection charge. Most departments
  are at 5%. Notary fees stay a separate 2% estimate.
- Other countries use one typical rate. Transfer tax varies by region in
  Germany and Belgium, so treat those as a ballpark.
- Renovation is €/m² by how much work the description admits to. It is the
  widest guess of the three, and it is shown as a range.
"""
from __future__ import annotations

import json
import re

from common import (counted_flats, find_floor_area, footprint_figure, land_figure, normalize,
                    parse_price, price_to_pay)
from scoring import INVEST_VALUE_MAX_M2, categorize, condition, market_value_estimate, property_kind

IMT_YEAR = 2025

# Continente, IMT_YEAR: (up to, rate, deduction). tax = value × rate − deduction.
# The last two rows are flat rates on the whole value.
IMT_HOME = [
    (104_261, 0.01, 0.0),
    (142_618, 0.02, 1_042.61),
    (194_458, 0.05, 5_321.15),
    (324_058, 0.07, 9_210.41),
    (621_501, 0.08, 12_450.99),
    (1_128_287, 0.06, 0.0),
    (None, 0.075, 0.0),
]
# Lower brackets, for a home the buyer will live in (habitação própria e permanente).
IMT_OWN_HOME = [
    (104_261, 0.00, 0.0),
    (142_618, 0.02, 2_085.22),
    (194_458, 0.05, 6_363.76),
    (324_058, 0.07, 10_253.02),
    (648_022, 0.08, 13_493.60),
    (1_128_287, 0.06, 0.0),
    (None, 0.075, 0.0),
]
IMT_OTHER_URBAN = 0.065     # shops, garages, land for building
IMT_RURAL = 0.05            # prédios rústicos
STAMP_DUTY = 0.008          # imposto do selo on the transfer
PT_REGISTRY_EUR = 250       # registo predial online, with certificates
PT_DEED_EUR = 600           # escritura and papers, when it is not a court sale

# Everywhere else: (transfer tax, other costs as a share of the price, what the
# tax is called, what the other costs are). Spain and France replace the tax
# rate below when the region is known; these rows are the fallback.
COUNTRY_COSTS = {
    "ES": (0.080, 0.015, "ITP, region not recognised (about 6–10%)", "notary, registry and gestoría"),
    "FR": (0.063185, 0.020, "droits de mutation 6.32% (most departments, DGFiP 1 June 2026)",
           "notary fees, about 2% — the regulated scale depends on the price"),
    "IT": (0.090, 0.010, "imposta di registro (9%, at least €1 000)", "notary and registry"),
    "DE": (0.055, 0.020, "Grunderwerbsteuer (3,5–6,5%, by Land)", "notary and Grundbuch"),
    "NL": (0.104, 0.012, "overdrachtsbelasting (10,4% when you will not live in it)", "notary and registry"),
    "BE": (0.120, 0.015, "registration duty (12–12,5%, by region)", "notary and registry"),
    "HR": (0.030, 0.010, "porez na promet nekretnina", "notary and land registry"),
    "GR": (0.031, 0.015, "transfer tax (3,09%)", "notary, lawyer and registry"),
    "RO": (0.000, 0.020, "no transfer tax for the buyer", "notary and land book"),
    "PL": (0.020, 0.015, "PCC transfer tax", "notary and land register"),
    "CY": (0.000, 0.010, "transfer fees (none when VAT is charged)", "stamp duty and registry"),
}
DEFAULT_COSTS = (0.070, 0.015, "transfer tax (typical)", "notary and registry")

# ECB euro foreign-exchange reference rates: units of foreign currency for 1 euro,
# taken on ECB_RATE_DATE. A guide price in that currency is stored as euros at
# this rate. A scan does not look the rate up again, and the listing says so.
ECB_RATE_DATE = "2026-10-08"
ECB_PER_EUR = {"GBP": 0.84698, "CHF": 0.9326}


def euros_from(amount: float, currency: str) -> int | None:
    """The amount in euros at ECB_PER_EUR, rounded to the nearest euro.
    None when that currency has no rate here."""
    per = ECB_PER_EUR.get((currency or "").upper())
    if not per or not amount or amount <= 0:
        return None
    return int(round(float(amount) / per))
IT_REGISTRO_MIN = 1_000

# Spanish ITP, general rate for a used property, 2026. A single rate, or a
# marginal scale of (up to, rate) with None as the last open band. Sources:
# each community's published general rate, compared September 2026. The first
# band covers the prices this scanner keeps.
ES_ITP_2026 = {
    "andalucia": (0.07, "Andalucía, general rate 7%"),
    "aragon": (0.08, "Aragón, 8% — the bottom of the regional scale"),
    "asturias": (0.08, "Asturias, 8% up to €300,000"),
    "baleares": (0.08, "Illes Balears, 8% up to €400,000"),
    "canarias": (0.065, "Canarias, general rate 6.5%"),
    "cantabria": (0.09, "Cantabria, general rate 9%"),
    "castilla la mancha": (0.09, "Castilla-La Mancha, general rate 9%"),
    "castilla y leon": ([(250_000, 0.08), (None, 0.10)],
                        "Castilla y León, 8% up to €250,000 and 10% above"),
    "cataluna": ([(600_000, 0.10), (900_000, 0.11), (1_500_000, 0.12), (None, 0.13)],
                 "Cataluña, 10% up to €600,000, then 11%, 12% and 13%"),
    "extremadura": (0.08, "Extremadura, 8% — the bottom of the regional scale"),
    "galicia": (0.08, "Galicia, general rate 8%"),
    "madrid": (0.06, "Madrid, general rate 6%"),
    "murcia": (0.0775, "Murcia, general rate 7.75%"),
    "navarra": (0.06, "Navarra, general rate 6%"),
    "rioja": (0.07, "La Rioja, general rate 7%"),
    "valencia": (0.09, "Comunitat Valenciana, general rate 9%"),
    "bizkaia": (0.04, "Bizkaia, 4% on a home (7% on other property)"),
    "gipuzkoa": (0.04, "Gipuzkoa, 4% on a home (7% on other property)"),
}
# Longer names first so "santa cruz de tenerife" is not read as something shorter.
ES_PROVINCE = (
    ("santa cruz de tenerife", "canarias"), ("las palmas", "canarias"),
    ("a coruna", "galicia"), ("la coruna", "galicia"), ("coruna", "galicia"),
    ("pontevedra", "galicia"), ("ourense", "galicia"), ("orense", "galicia"), ("lugo", "galicia"),
    ("almeria", "andalucia"), ("cadiz", "andalucia"), ("cordoba", "andalucia"),
    ("granada", "andalucia"), ("huelva", "andalucia"), ("jaen", "andalucia"),
    ("malaga", "andalucia"), ("sevilla", "andalucia"),
    ("huesca", "aragon"), ("teruel", "aragon"), ("zaragoza", "aragon"),
    ("asturias", "asturias"), ("oviedo", "asturias"),
    ("illes balears", "baleares"), ("islas baleares", "baleares"), ("baleares", "baleares"),
    ("cantabria", "cantabria"), ("santander", "cantabria"),
    ("albacete", "castilla la mancha"), ("ciudad real", "castilla la mancha"),
    ("cuenca", "castilla la mancha"), ("guadalajara", "castilla la mancha"),
    ("toledo", "castilla la mancha"),
    ("avila", "castilla y leon"), ("burgos", "castilla y leon"), ("leon", "castilla y leon"),
    ("palencia", "castilla y leon"), ("salamanca", "castilla y leon"),
    ("segovia", "castilla y leon"), ("soria", "castilla y leon"),
    ("valladolid", "castilla y leon"), ("zamora", "castilla y leon"),
    ("barcelona", "cataluna"), ("girona", "cataluna"), ("gerona", "cataluna"),
    ("lleida", "cataluna"), ("lerida", "cataluna"), ("tarragona", "cataluna"),
    ("madrid", "madrid"),
    ("murcia", "murcia"), ("navarra", "navarra"), ("la rioja", "rioja"),
    ("alicante", "valencia"), ("alacant", "valencia"), ("castellon", "valencia"),
    ("castello", "valencia"), ("valencia", "valencia"),
    ("badajoz", "extremadura"), ("caceres", "extremadura"),
    ("alava", "alava"), ("araba", "alava"), ("bizkaia", "bizkaia"), ("vizcaya", "bizkaia"),
    ("gipuzkoa", "gipuzkoa"), ("guipuzcoa", "gipuzkoa"),
)


def _where(item: dict) -> str:
    text = normalize(" ".join(str(item.get(k) or "") for k in
                              ("district", "concelho", "freguesia", "title", "description")))
    return re.sub(r"[^a-z0-9]+", " ", text)


def _spanish_community(item: dict) -> str | None:
    text = _where(item)
    for name, community in ES_PROVINCE:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return community
    return None


def _marginal(value: float, bands: list) -> float:
    tax, prev = 0.0, 0.0
    for limit, rate in bands:
        cap = value if limit is None else min(value, limit)
        if cap > prev:
            tax += (cap - prev) * rate
        if limit is None or value <= limit:
            break
        prev = limit
    return tax


def spanish_itp(item: dict, value: float) -> tuple[float, str]:
    """ITP on `value`, and the rule. The general rate; a home you live in is often less."""
    community = _spanish_community(item)
    if community == "alava":
        return (value * 0.08,
                "Álava: sources disagree (4% for a home, 7% generally) — 8% is a ballpark, check the foral rule"
                "; on the price or the cadastral reference value, whichever is higher")
    if community is None:
        rate, note = 0.08, "ITP, region not recognised — 8%, the middle of the 6–10% range"
        amount = value * rate
    else:
        scale, note = ES_ITP_2026[community]
        kind = item.get("kind") or property_kind(item)
        if community in {"bizkaia", "gipuzkoa"} and kind != "home":
            amount = value * 0.07
            note = note.split(",")[0] + ", 7% — not a home"
        elif community == "valencia" and value > 1_000_000:
            amount = value * 0.11
            note = "Comunitat Valenciana, 11% on the whole value above €1,000,000"
        elif community == "asturias" and value > 300_000:
            amount = value * 0.08
            note = "Asturias, 8% — a higher single rate applies above €300,000 and was not used"
        elif isinstance(scale, list):
            amount = _marginal(value, scale)
        else:
            amount = value * scale
    return amount, note + "; on the price or the cadastral reference value, whichever is higher"


# France, DGFiP barème of 1 June 2026. The departmental duty plus the 1.20%
# municipal tax plus 2.37% of the departmental duty for collection.
def _dmto(departmental: float) -> float:
    return departmental + 0.012 + departmental * 0.0237


FR_DMTO_5 = _dmto(0.05)       # 6.3185%, the rate of most departments
FR_DMTO_45 = _dmto(0.045)     # 5.80665%
FR_DMTO_38 = _dmto(0.038)     # 5.09006%, Indre
# Longer phrases first: "indre et loire" is not Indre.
FR_DEPARTMENT = (
    ("indre et loire", FR_DMTO_5, "Indre-et-Loire, departmental rate 5%"),
    ("charente maritime", FR_DMTO_5, "Charente-Maritime, departmental rate 5%"),
    ("hautes alpes", FR_DMTO_45, "Hautes-Alpes, departmental rate 4.50%"),
    ("alpes maritimes", FR_DMTO_45, "Alpes-Maritimes, departmental rate 4.50%"),
    ("ardeche", FR_DMTO_45, "Ardèche, departmental rate 4.50%"),
    ("charente", FR_DMTO_45, "Charente, departmental rate 4.50%"),
    ("drome", FR_DMTO_45, "Drôme, departmental rate 4.50%"),
    ("lozere", FR_DMTO_45, "Lozère, departmental rate 4.50%"),
    ("hautes pyrenees", FR_DMTO_45, "Hautes-Pyrénées, departmental rate 4.50%"),
    ("saone et loire", FR_DMTO_45, "Saône-et-Loire, departmental rate 4.50%"),
    ("oise", FR_DMTO_45, "Oise, departmental rate 4.50%"),
    ("indre", FR_DMTO_38, "Indre, departmental rate 3.80%"),
)


FR_CODE = {
    "36": (FR_DMTO_38, "Indre, departmental rate 3.80%"),
    "05": (FR_DMTO_45, "Hautes-Alpes, departmental rate 4.50%"),
    "06": (FR_DMTO_45, "Alpes-Maritimes, departmental rate 4.50%"),
    "07": (FR_DMTO_45, "Ardèche, departmental rate 4.50%"),
    "16": (FR_DMTO_45, "Charente, departmental rate 4.50%"),
    "26": (FR_DMTO_45, "Drôme, departmental rate 4.50%"),
    "48": (FR_DMTO_45, "Lozère, departmental rate 4.50%"),
    "60": (FR_DMTO_45, "Oise, departmental rate 4.50%"),
    "65": (FR_DMTO_45, "Hautes-Pyrénées, departmental rate 4.50%"),
    "71": (FR_DMTO_45, "Saône-et-Loire, departmental rate 4.50%"),
}


def french_dmto(item: dict, value: float) -> tuple[float, str]:
    """Transfer tax on `value` from the June 2026 departmental scale."""
    code = str(item.get("district") or "").strip()
    if code in FR_CODE:
        rate, label = FR_CODE[code]
        return value * rate, f"{label} → {rate:.2%} with the municipal tax (DGFiP, 1 June 2026)"
    text = _where(item)
    for name, rate, label in FR_DEPARTMENT:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return value * rate, f"{label} → {rate:.2%} with the municipal tax (DGFiP, 1 June 2026)"
    return value * FR_DMTO_5, ("droits de mutation 6.32% — most departments on 1 June 2026; "
                               "Indre is 5.09% and eleven departments are 5.81%")


def purchase_share(item: dict) -> tuple[float, str]:
    """Tax and notary as a share of the price, for the forestry return.

    Portugal stays the rustic rule (5% IMT and 0.8% stamp). Spain and France
    use the same rates as the cost panel."""
    country = (item.get("country") or "PT").upper()
    if country == "PT":
        return 0.058, "IMT 5% on rustic land and 0.8% stamp duty"
    if country == "ES":
        tax, note = spanish_itp(item, 1.0)
        extra = COUNTRY_COSTS["ES"][1]
        return tax + extra, f"{note}; notary about {extra:.1%}"
    if country == "FR":
        tax, note = french_dmto(item, 1.0)
        extra = COUNTRY_COSTS["FR"][1]
        return tax + extra, f"{note}; notary about {extra:.0%}"
    rate, extra, tax_name, extra_name = COUNTRY_COSTS.get(country, DEFAULT_COSTS)
    return rate + extra, f"{tax_name}; {extra_name}"

# What a home needs, in €/m² of floor area: (low, high).
RENOVATION_EUR_M2 = {
    "heavy": (700, 1200),
    "some": (300, 600),
    "good": (0, 150),
    "unknown": (200, 500),
}
CONDITION_LABEL = {"heavy": "a ruin", "some": "it needs work", "good": "it is in good condition",
                   "unknown": "the condition is not stated"}
CONDITION_NOTE = {
    "heavy": "described as a ruin or as needing rebuilding",
    "some": "described as needing work",
    "good": "described as in good condition",
    "unknown": "condition not stated — assume it needs some work",
}
MAX_HOME_M2 = 1000          # above this the area is the plot, not the building

# Court and tax sales hand over a title instead of a notarial deed: no escritura.
JUDICIAL_SOURCES = {"citius", "eleiloes", "financas", "leilosoc", "centroleiloes", "bidleiloeira",
                    "spain", "aeat", "subastasactivas", "france", "encheres_publiques", "zvg",
                    "zvg_de", "justiz_auktion", "italy", "pvp_giustizia", "astalegale", "gobidreal",
                    "netherlands", "veilingnotaris", "veilingbiljet", "croatia", "fina"}


def imt(value: float, kind: str | None, *, own_home: bool = False) -> tuple[float, str]:
    """Portuguese IMT on `value`, and the rule used."""
    if kind == "rural_plot":
        return value * IMT_RURAL, f"{IMT_RURAL:.1%} for a prédio rústico"
    if kind != "home":
        return value * IMT_OTHER_URBAN, f"{IMT_OTHER_URBAN:.1%}, not a dwelling"
    table = IMT_OWN_HOME if own_home else IMT_HOME
    for limit, rate, deduction in table:
        if limit is None or value <= limit:
            return max(0.0, value * rate - deduction), f"a dwelling, {IMT_YEAR} brackets"
    return 0.0, ""


def _item_raw(item: dict) -> dict:
    raw = item.get("raw_json")
    if isinstance(raw, dict):
        return raw
    try:
        raw = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def catastro_home(item: dict) -> tuple[float, str]:
    """(dwelling m², note) from a stored Catastro reading.

    One VIVIENDA is the floor. Common parts, a store and a parking space are
    not. Two or more dwellings are not added together: the notice does not say
    whether the sale is one of them or the whole building. No reading, no note.
    """
    cat = _item_raw(item).get("catastro")
    if not isinstance(cat, dict):
        return 0.0, ""
    homes = []
    for unit in cat.get("units") or []:
        if not isinstance(unit, dict) or not unit.get("m2"):
            continue
        if normalize(unit.get("use")) == "vivienda":
            homes.append(float(unit["m2"]))
    if len(homes) == 1 and homes[0] > 0:
        return homes[0], (
            f"floor {homes[0]:.0f} m² from Catastro (the dwelling; common parts are not counted)")
    if len(homes) > 1:
        listed = ", ".join(f"{m:.0f}" for m in homes)
        return 0.0, (
            f"Catastro lists {len(homes)} dwellings ({listed} m²) — the notice does not say "
            "whether this sale is one of them or all of them")
    built = float(cat.get("built_m2") or 0)
    if built > 0 and "residencial" in normalize(cat.get("use")) and not cat.get("units"):
        return built, f"floor {built:.0f} m² from Catastro (built area; the notice states none)"
    return 0.0, ""


def floor_resolution(item: dict) -> dict:
    """The one floor figure the decision path may use, and where it came from.

    The stored ``area_m2`` is left as scraped. A land or footprint figure is
    not rewritten into a floor, and a large ambiguous area is not turned into
    a 250 m² house here: ``building_m2`` already returns that cap only when
    the stored number was accepted as floor and then limited.
    """
    metres, source = building_m2(item)
    labels = {
        "stored": "stored floor area",
        "notice": "floor area written in the notice",
        "catastro": "Catastro dwelling",
        "capped": "building-size cap (a larger stored figure is not all floor)",
        "": "floor area not established",
    }
    return {"m2": metres, "source": source, "label": labels.get(source, source),
            "established": source in ("stored", "notice", "catastro") and metres > 0}


def selected_acquisition(item: dict) -> dict:
    """The price Live and invest plans with. Current bid, minimum and a typed bid stay distinct.

    A predicted close is an estimate. It is selected only when ranking would
    use it: higher than the price you would pay now, and not a rejected figure.
    A portal figure the advert marks as an auction deposit or opening bid is
    not selected. It stays a listed reference.
    """
    from common import price_to_pay
    pay = float(price_to_pay(item) or 0)
    if item.get("price_unusable"):
        listed = float(item.get("price") or item.get("min_price") or 0) or None
        return {"amount": None, "kind": "unusable", "estimate": False,
                "label": item["price_unusable"], "text": "",
                "current_amount": None, "current_kind": "unusable",
                "reference_amount": listed}
    if item.get("current_bid") and item["current_bid"] >= (item.get("min_price") or 0):
        current_kind = "current_bid"
    elif item.get("min_price"):
        current_kind = "minimum"
    else:
        current_kind = "asking"
    likely = item.get("predicted_final") or {}
    predicted = float(likely.get("price") or 0) if isinstance(likely, dict) else 0.0
    if predicted and not item.get("price_unusable") and pay and predicted > pay * 1.02:
        return {"amount": predicted, "kind": "predicted", "estimate": True,
                "label": "predicted closing price (an estimate, not a bid placed)",
                "text": likely.get("text") or "", "current_amount": pay, "current_kind": current_kind,
                "reference_amount": None}
    return {"amount": pay, "kind": current_kind, "estimate": False, "label": {
        "current_bid": "the current bid",
        "minimum": "the minimum accepted",
        "asking": "the asking or base price",
    }[current_kind], "text": "", "current_amount": pay, "current_kind": current_kind,
            "reference_amount": None}


def building_m2(item: dict) -> tuple[float, str]:
    """(m² of building to renovate and let, where the figure came from).

    The stored area, otherwise the first size in the title or the description
    that is not the land or the footprint, otherwise one dwelling in Catastro.
    A stored area the notice calls land or implantação is not the floor: the
    floor is then the notice's own floor figure, or unknown when that is the
    only size. Nothing is invented. At most INVEST_VALUE_MAX_M2: a bigger
    number is the plot, and pricing all of it would treat the land as floor
    space. The source is "stored", "notice", "catastro" or "capped"
    ("" when there is no floor).
    """
    stored = float(item.get("area_m2") or 0)
    title, desc = item.get("title"), item.get("description")
    notice = find_floor_area(title) or find_floor_area(desc) or 0
    text = f"{title or ''}\n{desc or ''}"
    if stored and (land_figure(text, stored) or footprint_figure(text, stored)):
        raw, source = (float(notice), "notice") if notice else (0.0, "")
    elif stored:
        raw, source = stored, "stored"
    else:
        raw, source = float(notice), "notice" if notice else ""
    if not raw:
        raw, _note = catastro_home(item)
        source = "catastro" if raw else ""
    if not raw:
        return 0.0, ""
    if raw > INVEST_VALUE_MAX_M2:
        return float(INVEST_VALUE_MAX_M2), "capped"
    return float(raw), source


def renovation(item: dict) -> dict | None:
    """{"low", "high", "eur_m2", "note"} for a home whose floor area is known, else None."""
    if (item.get("kind") or property_kind(item)) != "home":
        return None
    area, source = building_m2(item)
    if not area:
        return None
    state = condition(item)
    low, high = RENOVATION_EUR_M2[state]
    where = f"{area:,.0f} m²".replace(",", " ")
    if source == "notice":
        where += " (the size in the notice)"
    elif source == "catastro":
        where += " (Catastro's dwelling, not a size written in the notice)"
    elif source == "capped":
        where += " of building — a larger area is the plot, not floor space"
    return {"low": round(area * low), "high": round(area * high), "eur_m2": (low, high),
            "note": f"{CONDITION_NOTE[state]}; €{low}–{high}/m² over {where}"}


def bands_text() -> str:
    """The renovation bands in one line, so the AI check is told the same numbers."""
    return ", ".join(f"\u20ac{low}\u2013{high}/m\u00b2 when {CONDITION_LABEL[state]}"
                     for state, (low, high) in RENOVATION_EUR_M2.items())


def _pt_lines(value: float, kind: str | None, judicial: bool, own_home: bool) -> list[dict]:
    tax, rule = imt(value, kind, own_home=own_home)
    lines = [{"label": "IMT", "amount": tax, "note": rule},
             {"label": "Imposto do selo", "amount": value * STAMP_DUTY, "note": f"{STAMP_DUTY:.1%} of the price"},
             {"label": "Land registry", "amount": float(PT_REGISTRY_EUR), "note": "registo predial"}]
    if not judicial:
        lines.append({"label": "Deed", "amount": float(PT_DEED_EUR),
                      "note": "escritura and papers; a court sale has none"})
    return [line for line in lines if line["amount"] > 0]


def _other_lines(item: dict, value: float, country: str) -> list[dict]:
    if country == "ES":
        tax, tax_name = spanish_itp(item, value)
        _, extra, _, extra_name = COUNTRY_COSTS["ES"]
    elif country == "FR":
        tax, tax_name = french_dmto(item, value)
        _, extra, _, extra_name = COUNTRY_COSTS["FR"]
    else:
        rate, extra, tax_name, extra_name = COUNTRY_COSTS.get(country, DEFAULT_COSTS)
        tax = value * rate
        if country == "IT":
            tax = max(tax, IT_REGISTRO_MIN)
    lines = []
    if tax:
        lines.append({"label": "Transfer tax", "amount": tax, "note": tax_name})
    if extra:
        lines.append({"label": "Notary and registry", "amount": value * extra,
                      "note": f"{extra_name}, about {extra:.1%}"})
    return lines


RENT_MAX_M2 = 200            # a bigger house does not rent for proportionally more
# A block the ad counts ("trois appartements T3") but does not measure. Each
# figure is a modest floor for that type, not a survey and not this building.
# The rent says so. The resale comparison and the repair bill stay on a floor
# the ad or the feed actually states.
FLAT_PLAN_M2 = {"T1": 25, "T2": 40, "T3": 55, "T4": 75, "T5": 90, "T6": 110}
FLAT_BLOCK_MAX = 8           # past this, guessing a floor would invent a building
# Italian cadastral "vani" are rooms, not square metres, and the cadastre
# publishes no conversion. Agents often count about 20 m². 15 understates the
# floor, so a rent worked out from it does not pretend the home is larger than
# it is. It is never stored as the floor and never used for the resale discount
# or the repair bill.
VANI_M2 = 15.0
VANI_MAX = 12.0              # past this it is a block or a misread, not one home
# A lease the ad states, above this share of the cost a year, is not believed:
# the number is usually the price, a year, or a deposit.
STATED_YIELD_MAX = 30
# What a landlord keeps of the rent: the rest goes on the property tax (IMI and
# its equivalents), insurance, repairs, the agent and the months it stands
# empty. One planning figure, not a calculation of this landlord's tax.
RENT_RUNNING_SHARE = 0.20    # property tax, insurance, repairs, management
RENT_VACANCY_SHARE = 0.08    # about a month a year between tenants
NET_RENT_SHARE = 1 - RENT_RUNNING_SHARE - RENT_VACANCY_SHARE


# The fee lines are one figure; the bid calculator plans on this much more at
# the high end (a VPT above the price in Portugal, a dearer region elsewhere).
FEES_HIGH_FACTOR = 1.25


def fee_lines(item: dict, value: float, *, own_home: bool = False) -> list[dict]:
    """The taxes and fees of buying `item` for `value`."""
    country = (item.get("country") or "PT").upper()
    if country != "PT":
        return _other_lines(item, value, country)
    kind = item.get("kind") or property_kind(item)
    return _pt_lines(value, kind, (item.get("source") or "") in JUDICIAL_SOURCES, own_home)


def fees(item: dict, value: float, *, own_home: bool = False) -> float:
    return sum(line["amount"] for line in fee_lines(item, value, own_home=own_home))


def _liquidity_vacancy(population: int | None) -> float:
    """Effective vacancy rate, rising continuously as population falls.
    A city of 100k+ gets the base rate; a hamlet of 500 gets ~4x more vacancy.
    Uses tanh so the curve is smooth with no hard steps:
      pop=100000 → factor≈1.00, vacancy≈RENT_VACANCY_SHARE
      pop=10000  → factor≈0.76, vacancy×1.3
      pop=2000   → factor≈0.54, vacancy×1.9
      pop=500    → factor≈0.39, vacancy×2.6
      pop=100    → factor≈0.22, vacancy×4.5
    """
    import math
    if not population or population <= 0:
        return RENT_VACANCY_SHARE * 2.0   # unknown → assume small
    factor = math.tanh(population / 25000)
    factor = max(factor, 0.15)
    return RENT_VACANCY_SHARE / factor


# A month's rent written next to a rent word. "Arrendamento" alone is the
# contract ("em vigor desde 2019"), so it only counts with a currency or a month.
_STATED_RENT = re.compile(
    r"\b(renda(?:s| mensal)?|aluguer(?:es)?|aluguel|alquiler(?:es)?|loyer(?:s)?|"
    r"huurprijs|huur|kaltmiete|miete|renta(?: mensual)?)\b([^.]{0,48})")
_RENT_AMOUNT_FIRST = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|eur(?:os)?)?"
    r"(?:\s*(?:/|por|al|par|per)\s*)?(?:mes|mois|maand|monat)?"
    r"\s*(?:de\s+)?\b(renda|aluguer|aluguel|alquiler|loyer|huur|miete)\b")
_RENT_MONTH = re.compile(r"€|eur|mes|mois|maand|monat")


def stated_monthly_rent(text: str) -> float | None:
    """€ a month the ad itself states for a lease, or None.

    The sale price is not this. The number has to sit with a rent word, and
    inside what one month's rent can be. A year next to the contract is left out."""
    low = normalize(text or "")
    windows = [(m.group(1), m.group(2)) for m in _STATED_RENT.finditer(low)]
    for m in _RENT_AMOUNT_FIRST.finditer(low):
        windows.append((m.group(2), m.group(1)))
    for _word, window in windows:
        if re.search(r"\bm2\b", window):
            continue
        amount = parse_price(window)
        if amount is None:
            continue
        if 1900 <= amount <= 2100 and not _RENT_MONTH.search(window):
            continue
        if 80 <= amount <= 4000:
            return float(amount)
    return None


def _finish_rent(item: dict, cost: float, monthly: float, *, source: str, province: bool,
                 stated: bool = False, eur_m2: float | None = None, area_used: float | None = None,
                 town_monthly: float | None = None) -> dict | None:
    """The yield of `monthly` on `cost`, after the planning haircut for running
    costs and empty months. `stated` is a lease the ad names, not a town average."""
    if monthly <= 0 or cost <= 0:
        return None
    import prices
    country = (item.get("country") or "PT").upper()
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    population = prices.population_of(country, place)
    vacancy = _liquidity_vacancy(population)
    net_share = 1 - RENT_RUNNING_SHARE - vacancy
    gross = 1200 * monthly / cost
    kept = monthly * net_share
    if kept <= 0:
        return None
    pop_note = f"pop. {population:,}" if population else "population unknown"
    trusted = not (stated and gross > STATED_YIELD_MAX)
    if stated:
        note = f"€{monthly:,.0f} a month stated in the ad"
        if town_monthly:
            note += f" (the town average is about €{town_monthly:,.0f})"
        if not trusted:
            note += " — far above the price, so check it before treating it as the rent"
    else:
        where = f"{place} ({source})"
        if province:
            where += "; province average, counted at half — check rents in the village"
        note = f"€{eur_m2:.2f}/m² a month in {where}, over {area_used:.0f} m²"
    note += (f"; {RENT_RUNNING_SHARE:.0%} running costs, {vacancy:.0%} vacancy ({pop_note}), "
             f"before income tax. Gross, before those, is {round(gross, 1)}%")
    return {"monthly": monthly, "eur_m2": eur_m2, "source": source,
            "province_average": province, "stated": stated, "trusted": trusted,
            "population": population,
            "yield_pct": round(gross, 1), "net_yield_pct": round(gross * net_share, 1),
            "payback_years": round(cost / (12 * kept), 1),
            "note": note}


def _block_text(item: dict) -> str:
    """The title or the description, whichever names the flats. Not both:
    a title that repeats the description would count six T3s."""
    title = item.get("title") or ""
    desc = item.get("description") or ""
    nt, nd = normalize(title), normalize(desc)
    if not nd or nd in nt:
        return title
    if not nt or nt in nd:
        return desc

    def named(text: str) -> int:
        typed, bare = counted_flats(text)
        return sum(typed.values()) + bare

    if named(title) and not named(desc):
        return title
    return desc or title


def flat_block(item: dict) -> dict | None:
    """A building the ad counts as several flats, or None for a single home.

    {"area", "label", "count"}. `area` is a planning floor when every flat has
    a type and there are at most FLAT_BLOCK_MAX of them, else None and the
    label says why no rent was worked out. A measured floor is not this."""
    typed, bare = counted_flats(_block_text(item))
    count = sum(typed.values()) + bare
    if count < 2:
        return None
    if bare or count > FLAT_BLOCK_MAX or any(t not in FLAT_PLAN_M2 for t in typed):
        why = (f"{count} apartments — too many to guess a floor from the types"
               if count > FLAT_BLOCK_MAX else
               f"{count} apartments, sizes not stated — rent not worked out")
        return {"area": None, "label": why, "count": count}
    area = float(sum(n * FLAT_PLAN_M2[t] for t, n in typed.items()))
    if len(typed) == 1:
        t, n = next(iter(typed.items()))
        label = (f"{n} {t} apartments, each counted at {FLAT_PLAN_M2[t]:.0f} m²"
                 " — the ad states no floor area")
    else:
        parts = ", ".join(f"{n} {t}" for t, n in sorted(typed.items()))
        label = f"{parts}, each counted at a planning size — the ad states no floor area"
    if area > INVEST_VALUE_MAX_M2:
        label += f"; only {INVEST_VALUE_MAX_M2:.0f} m² is counted"
        area = float(INVEST_VALUE_MAX_M2)
    return {"area": area, "label": label, "count": count}


_VANI_RE = re.compile(
    r"consist(?:enza|\.)(?:\s+di)?\s+(\d{1,2}(?:[.,]\d+)?)\s+vani\b"
    r"|(?:cat(?:egoria|\.)?\s*)?A\s*/?\s*\d\w?(?:[^.\n]{0,60}?)vani\s+(\d{1,2}(?:[.,]\d+)?)"
    r"|(?:cat(?:egoria|\.)?\s*)?A\s*/?\s*\d\w?(?:[^.\n]{0,60}?)(\d{1,2}(?:[.,]\d+)?)\s+vani\b",
    re.I)


def vani_floor(item: dict) -> dict | None:
    """A cautious floor from Italian cadastral vani, or None.

    {"area", "label"}. One consistenza is counted at VANI_M2. Several different
    figures are not added. A floor the notice or Catastro states is not this:
    the caller only asks when that floor is missing."""
    nums: list[float] = []
    for m in _VANI_RE.finditer(_block_text(item)):
        raw = next(g for g in m.groups() if g)
        n = float(raw.replace(",", "."))
        if 1 <= n <= VANI_MAX and n not in nums:
            nums.append(n)
    if not nums:
        return None
    if len(nums) > 1:
        return {"area": None,
                "label": (f"the court gives {len(nums)} sizes in vani — not added together, "
                          "and not a floor area")}
    n = nums[0]
    shown = f"{n:g}"
    return {"area": n * VANI_M2,
            "label": (f"{shown} vani, counted at {VANI_M2:.0f} m² each — the court states "
                      "no floor area, so this is a cautious estimate, not a measured floor")}


def rent(item: dict, cost: float) -> dict | None:
    """What a home would rent for, from the rent per m² in its municipality
    (Portugal: INE's median of new leases; France: the carte des loyers;
    Spain: SERPAVI; Italy: OMI quotations by comune), and
    the yield on `cost` (what it takes to own it, with the work), net of the
    default running costs and empty months. None without an area or a figure.

    Several flats named by type but with no floor area ("trois appartements
    T3") are let as that many homes, at FLAT_PLAN_M2 each. Italian cadastral
    vani, when the notice states no floor, are counted at VANI_M2 each. Neither
    size is used for the resale comparison or the repair bill."""
    if (item.get("kind") or property_kind(item)) != "home":
        return None
    country = (item.get("country") or "PT").upper()
    area, _source = building_m2(item)
    block = None
    vani = None
    if not area:
        block = flat_block(item)
        if block and block.get("area"):
            area = block["area"]
        elif not block:
            vani = vani_floor(item)
            if vani and vani.get("area"):
                area = vani["area"]
    if not area or cost <= 0:
        return None
    import geo
    import prices
    place = item.get("concelho") or geo.municipality(item)
    found = prices.rent_per_m2(country, place, item.get("district") if place != item.get("district") else None)
    if not found:
        return None
    eur_m2, source = found
    # One house stops growing at RENT_MAX_M2. A counted block is several leases,
    # already capped at the building size, so three T3s are not cut back to one.
    used = area if block else min(area, RENT_MAX_M2)
    # A missing village falls back on the province, which is the towns. Count
    # half of that and say so: a remote village does not let at the province rate.
    province = "province" in source
    if province:
        eur_m2 = eur_m2 / 2
    monthly = round(used * eur_m2)
    out = _finish_rent(item, cost, monthly, source=source, province=province,
                       eur_m2=eur_m2, area_used=used)
    planned = block if block and block.get("area") else (vani if vani and vani.get("area") else None)
    if out and planned and planned.get("label"):
        out["planning"] = planned["label"]
        out["note"] += f"; {planned['label']}. Repairs are not in this yield"
    return out


def estimate(item: dict, *, bid: float | None = None, own_home: bool = False,
             basis: str | None = None) -> dict | None:
    """What buying this listing really costs.

    {"base", "basis", "lines": [{"label", "amount", "note"}], "fees", "total",
     "renovation": {…} | None, "all_in": {"low", "high"} | None, "note"}
    None when the listing has no price to work from, or is not a property: a
    car or a lot of jewellery pays no IMT.
    """
    value = float(bid or price_to_pay(item) or 0)
    if value <= 0 or (item.get("category") or categorize(item)) != "imoveis":
        return None
    country = (item.get("country") or "PT").upper()
    if basis:
        pass
    elif bid:
        basis = "your bid"
    elif item.get("current_bid") and item["current_bid"] >= (item.get("min_price") or 0):
        basis = "the current bid"
    elif item.get("current_bid"):
        basis = "the minimum accepted (the current bid is below it)"
    elif item.get("min_price") and item["min_price"] != item.get("price"):
        basis = "the minimum accepted"
    else:
        basis = "the base value"

    lines = fee_lines(item, value, own_home=own_home)
    fee_total = sum(line["amount"] for line in lines)
    work = renovation(item)
    out = {"base": value, "basis": basis, "lines": lines, "fees": fee_total, "total": value + fee_total,
           "renovation": work, "all_in": None,
           "note": ("Estimate. Portuguese IMT is charged on the higher of the price and the taxable "
                    "value (VPT), which the listing does not give. Fire insurance is not included."
                    if country == "PT"
                    else "Estimate. Fire insurance is not included; there is no single published "
                         "premium for this building.")}
    if work:
        out["all_in"] = {"low": value + fee_total + work["low"], "high": value + fee_total + work["high"]}
    cost = (out["all_in"]["low"] + out["all_in"]["high"]) / 2 if work else out["total"]
    # A price this far under local sales is a share, a wrong area or not the
    # price. A rent worked out on it is not a return, so it is not calculated.
    import scoring
    if scoring.price_too_far_below(item, cost):
        out["rent"] = None
        return out
    town = rent(item, cost)
    stated = stated_monthly_rent(f"{item.get('title') or ''} {item.get('description') or ''}")
    if stated:
        out["rent"] = _finish_rent(item, cost, stated, source="stated in the ad", province=False,
                                   stated=True, town_monthly=(town or {}).get("monthly"))
    else:
        out["rent"] = town
    return out


def repair_unresolved(item: dict) -> bool:
    """A home whose floor is missing, land, or only a building-size cap.

    Repairs priced on that cap would invent a floor. The stored area is not changed.
    """
    import scoring
    if scoring.property_kind(item) != "home":
        return False
    return not floor_resolution(item)["established"]


def money_out(item: dict, *, bid: float | None = None, typical: bool = False,
              basis: str | None = None) -> dict | None:
    """What one purchase takes from cash you can lose.

    {"amount", "detail", "estimate"}. The amount is the low end of the all-in
    figure when the floor area is known — stored, written in the notice, or one
    Catastro dwelling —
    otherwise the price plus taxes and fees. `typical` uses the middle of the
    repair range, for a home whose condition was not stated. A stored area
    larger than the building cap counts only that much floor. Repairs are left
    out when there is no floor area, and the detail says so.
    """
    est = estimate(item, bid=bid, basis=basis)
    if not est:
        return None
    if est["all_in"]:
        low, high = est["all_in"]["low"], est["all_in"]["high"]
        if typical:
            amount = (low + high) / 2
            detail = (f"price, taxes, fees and the middle of the repair range"
                      f" (€{low:,.0f}–€{high:,.0f})")
        else:
            amount = low
            detail = (f"price, taxes, fees and the low end of repairs"
                      f" (high end about €{high:,.0f})")
    else:
        amount = est["total"]
        detail = "price, taxes and fees — repairs are not included (no floor area, or not a home)"
    return {"amount": round(amount), "detail": detail, "estimate": est}


def selected_planning(item: dict, *, bid: float | None = None,
                      basis: str | None = None) -> dict | None:
    """The one Live and invest planning figure.

    The midpoint of the existing repair range, including when the notice says
    the building is in good condition. That is an estimate, not a survey.
    A home with no established floor keeps the price-plus-fees subtotal and
    does not grow a repair range. Other tabs keep ``money_out`` at the low end
    unless they pass ``typical``.
    """
    out = money_out(item, bid=bid, typical=True, basis=basis)
    if not out:
        return None
    if repair_unresolved(item):
        est = dict(out["estimate"])
        est["all_in"] = None
        est["renovation"] = None
        amount = round(est["total"])
        return {"amount": amount, "incomplete": True,
                "detail": ("price, taxes and fees — a known subtotal; repairs are not "
                           "priced because the floor area is not established"),
                "estimate": est}
    out["incomplete"] = False
    return out


def planning_view(item: dict, cash: float, *, typical: bool = False) -> dict:
    """Affordability on the Live and invest planning figure.

    ``typical`` is ignored: this view always uses ``selected_planning``, the
    midpoint, including a home advertised as being in good condition. A missing
    budget is not affordable. An unresolved floor is not a complete within-budget
    result. A shortlist does not change this label.
    """
    del typical
    acq = selected_acquisition(item)
    out = selected_planning(item, bid=acq["amount"] or None, basis=acq["label"])
    est = (out or {}).get("estimate") or {}
    work = est.get("renovation")
    point = "middle" if work else None
    subtotal = est.get("total")
    if not cash or cash <= 0:
        status = "budget_not_set"
        note = "Cash you can lose is not set."
    elif not out:
        status = "unknown"
        if acq.get("kind") == "unusable":
            ref = acq.get("reference_amount")
            shown = f" The listed figure is €{ref:,.0f}." if ref else ""
            note = (f"{acq['label']}.{shown} It is not used as the acquisition price, "
                    "so it is not compared with the cash.")
        else:
            note = "No planning cost: there is no price to work from."
    elif out.get("incomplete"):
        if subtotal and subtotal > cash:
            status = "over"
            note = (f"Known subtotal €{out['amount']:,.0f} (price, taxes and fees) is already "
                    f"over the €{cash:,.0f} you can lose. Repairs are not priced because the "
                    "floor area is not established.")
        else:
            status = "incomplete"
            note = (f"Known subtotal €{out['amount']:,.0f} (price, taxes and fees). Repairs are "
                    "not priced because the floor area is not established. This is not a "
                    "complete planning cost.")
    elif out["amount"] > cash:
        status = "over"
        note = (f"Planning cost €{out['amount']:,.0f} is over the €{cash:,.0f} you can lose"
                f" ({out['detail']}).")
    else:
        status = "within"
        note = f"Planning cost €{out['amount']:,.0f} fits the €{cash:,.0f} you can lose ({out['detail']})."
    return {"acquisition": {k: acq.get(k) for k in (
                "amount", "kind", "estimate", "label", "current_amount", "current_kind",
                "reference_amount")},
            "all_in": est.get("all_in"), "renovation": work, "renovation_point": point,
            "known_subtotal": subtotal, "planning_amount": (out or {}).get("amount"),
            "status": status, "note": note}


def risk_reserve(item: dict, settings: dict | None = None) -> float:
    """The risk reserve for one listing, using the same base as the bid cap (F08).

    Base is estimated market value (area × local_price_per_m²), matching bidcap.
    Falls back to acquisition price when market value is not established (same
    fallback as bidcap's `value if value else budget`). Returns 0 when settings
    is None or empty — caller passes config["bid_cap"]; no config fallback here.
    """
    if not settings:
        return 0.0
    base = market_value_estimate(item)
    if base is None:
        from common import price_to_pay
        base = float(price_to_pay(item) or item.get("price") or 0)
    if not base:
        return 0.0
    country = (item.get("country") or "PT").upper()
    adviser = float((settings.get("adviser_reserve_by_country") or {}).get(country,
                     settings.get("adviser_reserve_eur") or 0))
    return base * float(settings.get("contingency_pct") or 0) / 100 + adviser


def over_cash(item: dict, cash: float, *, bid: float | None = None, typical: bool = False,
              basis: str | None = None, settings: dict | None = None) -> str | None:
    """Why this purchase is over the cash you can lose, or None.

    A cash figure of 0 is not set, and hides nothing: the Offers page refuses
    the bid instead. `settings` is the full config dict; when given, the risk
    reserve (contingency + adviser) is added to the all-in figure so the cash
    check uses the same scenario as the bid cap display (F08).
    """
    if not cash or cash <= 0:
        return None
    out = (selected_planning(item, bid=bid, basis=basis) if typical
           else money_out(item, bid=bid, typical=False, basis=basis))
    if not out:
        return None
    reserve = risk_reserve(item, (settings or {}).get("bid_cap"))
    total = out["amount"] + reserve
    if total <= cash:
        return None
    res_str = f" + €{reserve:,.0f} reserve" if reserve else ""
    return (f"cash €{total:,.0f} all-in{res_str} is over the €{cash:,.0f} you can lose "
            f"({out['detail']})")


def over_before_repairs(item: dict, cash: float) -> bool:
    """The price with its taxes and fees is already over the cash. Repairs only
    add to it, so over_cash hides the listing whatever the photos or the climate
    say: a light check that spares the heavy ones (common.light_checks_from)."""
    if not cash or cash <= 0:
        return False
    est = estimate(item)
    return bool(est) and est["total"] > cash


def sealed_cheque(item: dict, amount: float) -> str | None:
    """The usual 5% cheque on a Portuguese sealed offer or e-leilão.

    An estimate of the deposit, which counts toward the price. Confirm the
    figure in the notice.
    """
    if amount <= 0 or item.get("source") not in ("citius", "eleiloes"):
        return None
    cheque = round(amount * 0.05)
    return (f"About €{cheque:,.0f} as a cheque (5% of €{amount:,.0f}). "
            "A Portuguese sealed offer or an e-leilão normally asks for that. "
            "Confirm the amount in the notice. The cheque counts toward the price.")


def cash_view(item: dict, cash: float, *, bid: float | None = None, cheque: bool = False) -> dict:
    """What the Offers page shows next to an amount, and whether it is over the cash."""
    limit = float(cash or 0)
    out = money_out(item, bid=bid)
    spent = out["amount"] if out else None
    base = out["estimate"]["base"] if out else 0
    deposit = sealed_cheque(item, base) if cheque and out else None
    if limit <= 0:
        return {"set": False, "limit": None, "spent": spent, "over": False, "deposit": deposit,
                "text": "Cash you can lose is not set. Set it under Settings → Budget before an offer."}
    if out is None:
        if item.get("price_unusable"):
            text = (f"{item['price_unusable']}. It is not used as the acquisition price, "
                    "so it is not compared with the cash.")
        else:
            text = f"Within the €{limit:,.0f} you can lose. This listing has no price to add taxes to."
        over = False
    elif spent > limit:
        text = over_cash(item, limit, bid=bid) or ""
        over = True
    else:
        text = f"About €{spent:,.0f} all-in, within the €{limit:,.0f} you can lose ({out['detail']})."
        over = False
    return {"set": True, "limit": limit, "spent": spent, "over": over, "deposit": deposit, "text": text}


def as_text(est: dict | None) -> str:
    """The estimate as plain lines, for the AI check and the letter drafts."""
    if not est:
        return ""
    rows = [f"- Price used: €{est['base']:,.0f} ({est['basis']})"]
    rows += [f"- {line['label']}: €{line['amount']:,.0f} ({line['note']})" for line in est["lines"]]
    rows.append(f"- Taxes and fees: €{est['fees']:,.0f}; to own it: €{est['total']:,.0f}")
    if est["renovation"]:
        work = est["renovation"]
        rows.append(f"- Work: €{work['low']:,.0f}–{work['high']:,.0f} ({work['note']})")
        rows.append(f"- All-in: €{est['all_in']['low']:,.0f}–{est['all_in']['high']:,.0f}")
    if est.get("rent"):
        r = est["rent"]
        rows.append(f"- Rent: about €{r['monthly']:,.0f} a month, {r['net_yield_pct']}% a year "
                    f"after {RENT_RUNNING_SHARE:.0%} running costs and {RENT_VACANCY_SHARE:.0%} empty months "
                    f"({r['yield_pct']}% gross), paid back in {r['payback_years']} years ({r['note']})")
    rows.append(f"- {est['note']}")
    return "\n".join(rows)
