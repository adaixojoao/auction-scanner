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

import re

from common import normalize, price_to_pay
from scoring import categorize, condition, property_kind

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


def renovation(item: dict) -> dict | None:
    """{"low", "high", "eur_m2", "note"} for a home whose area is known, else None."""
    if (item.get("kind") or property_kind(item)) != "home":
        return None
    area = item.get("area_m2") or 0
    if not area or area > MAX_HOME_M2:
        return None
    state = condition(item)
    low, high = RENOVATION_EUR_M2[state]
    return {"low": round(area * low), "high": round(area * high), "eur_m2": (low, high),
            "note": f"{CONDITION_NOTE[state]}; €{low}–{high}/m² over {area:,.0f} m²".replace(",", " ")}


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


def rent(item: dict, cost: float) -> dict | None:
    """What a home would rent for, from the rent per m² in its municipality
    (Portugal: INE's median of new leases; France: the carte des loyers), and
    the gross yield on `cost` (what it takes to own it, with the work). None
    without an area or a figure."""
    if (item.get("kind") or property_kind(item)) != "home":
        return None
    country = (item.get("country") or "PT").upper()
    area = item.get("area_m2") or 0
    if not area or area > MAX_HOME_M2 or cost <= 0:
        return None
    import prices
    place = item.get("concelho") or (item.get("district") if country != "PT" else None)
    found = prices.rent_per_m2(country, place, item.get("district") if place != item.get("district") else None)
    if not found:
        return None
    eur_m2, source = found
    used = min(area, RENT_MAX_M2)
    # A missing village falls back on the province, which is the towns. Count
    # half of that and say so: a remote village does not let at the province rate.
    province = "province" in source
    if province:
        eur_m2 = eur_m2 / 2
    monthly = round(used * eur_m2)
    if monthly <= 0:
        return None
    gross = 1200 * monthly / cost
    where = f"{place} ({source})"
    if province:
        where += "; province average, counted at half — check rents in the village"
    return {"monthly": monthly, "eur_m2": eur_m2, "source": source,
            "province_average": province,
            "yield_pct": round(gross, 1), "net_yield_pct": round(gross * NET_RENT_SHARE, 1),
            "payback_years": round(cost / (12 * monthly), 1),
            "note": f"€{eur_m2:.2f}/m² a month in {where}, "
                    f"over {used:.0f} m²; net of about {RENT_RUNNING_SHARE:.0%} running costs "
                    f"and {RENT_VACANCY_SHARE:.0%} empty months, before income tax"}


def estimate(item: dict, *, bid: float | None = None, own_home: bool = False) -> dict | None:
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
    if bid:
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
    out["rent"] = rent(item, cost)
    return out


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
        rows.append(f"- Rent: about €{r['monthly']:,.0f} a month, {r['yield_pct']}% a year gross and "
                    f"{r['net_yield_pct']}% net, paid back in {r['payback_years']} years ({r['note']})")
    rows.append(f"- {est['note']}")
    return "\n".join(rows)
