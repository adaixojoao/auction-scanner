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
- Other countries use one typical rate per country; transfer tax varies by
  region in Spain, Germany and Belgium, so treat those as a ballpark.
- Renovation is €/m² by how much work the description admits to. It is the
  widest guess of the three, and it is shown as a range.
"""
from __future__ import annotations

from common import price_to_pay
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
# tax is called, what the other costs are). Regional rates are the middle of the range.
COUNTRY_COSTS = {
    "ES": (0.080, 0.015, "ITP transfer tax (6–10%, by región)", "notary, registry and gestoría"),
    "FR": (0.058, 0.020, "droits de mutation", "notary fees and disbursements"),
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


def _other_lines(value: float, country: str) -> list[dict]:
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
    kind = item.get("kind") or property_kind(item)
    judicial = (item.get("source") or "") in JUDICIAL_SOURCES
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

    lines = _pt_lines(value, kind, judicial, own_home) if country == "PT" else _other_lines(value, country)
    fees = sum(line["amount"] for line in lines)
    work = renovation(item)
    out = {"base": value, "basis": basis, "lines": lines, "fees": fees, "total": value + fees,
           "renovation": work, "all_in": None,
           "note": ("Estimate. Portuguese IMT is charged on the higher of the price and the taxable "
                    "value (VPT), which the listing does not give." if country == "PT"
                    else "Estimate: one typical rate per country; regional rates differ.")}
    if work:
        out["all_in"] = {"low": value + fees + work["low"], "high": value + fees + work["high"]}
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
    rows.append(f"- {est['note']}")
    return "\n".join(rows)
