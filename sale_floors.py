"""sale_floors.py — the lowest price the law lets a forced sale go for.

A court or tax sale is an auction: the best bid is the price and any surplus
goes to the owner, so you do not buy a house by paying what its owner owes.
But each country sets how low a bid may be and still be accepted, and in a few
places that floor sits far below the listed value:

- Spain (court sales on the BOE): a bid of 70% of the value is approved
  outright. Below that, if neither the debtor nor the creditor steps in, the
  court approves a bid over 50% of the valuation or, when lower, one that
  covers the amount the execution was for ("cantidad reclamada", LEC art.
  670.4). A small claim on a large valuation can therefore let a very low bid
  through — and a small claim is often a junior creditor's, with older
  charges (usually the mortgage) passing to the buyer.
- Germany (Zwangsversteigerung): at the first hearing a bid under half the
  Verkehrswert is refused, and under 70% a creditor may ask for refusal
  (ZVG §§ 85a, 74a). Once a hearing has been refused for those reasons the
  limits fall away and the lowest bid (geringstes Gebot) can win.
- Portugal: court sales normally accept from 85% of the base value (CPC
  art. 816); tax sales (CPPT arts. 248–250) open at 70% of the value the tax
  office set, drop to 50% when nobody bids, and can never go below 20%.
- Italy: the minimum offer is 75% of the base price (c.p.c. art. 571), and an
  unsold lot usually comes back up to a quarter cheaper.
- France, Poland, Croatia, Austria, the Netherlands: a rule to know, not a
  lower figure (the listed price is already the floor, or there is none).

Everything here is hedged ("normally", "may"): the scanner cannot confirm a
court's decision, and a floor is never a bid — `bidcap` and the evaluator do
not read it. The scorers use `rank_share()` to rank a sale whose floor sits
below what `common.price_to_pay()` says, and name the rule in the reasons.
"""
from __future__ import annotations

import json
import re

from common import find_price, find_terms, normalize, parse_price

# Spain, LEC art. 670: approved outright from 70% of the value; from 50% (or
# the claim, when lower) unless the debtor or the creditor steps in.
ES_OUTRIGHT = 0.70
ES_APPROVAL = 0.50
# Germany, ZVG § 74a (creditor may ask refusal under 7/10) and § 85a (refused
# under 5/10). The conservative floor while the limits stand is 70%.
DE_CREDITOR_LIMIT = 0.70
DE_COURT_LIMIT = 0.50
# With the limits gone the lowest bid is read out at the hearing and is not
# published, so no figure is named; it ranks as the court limit would.
DE_LIFTED_RANK_SHARE = 0.50
PT_COURT_MIN = 0.85            # CPC art. 816(2): offers from 85% of the base value
IT_MIN_OFFER = 0.75            # c.p.c. art. 571: offers from 75% of the base price
# Only a floor this far under the price ranks a sale: the usual 70–85% rules
# are how every such sale works and are shown, not ranked.
RANK_AT_MOST = 0.60

ES_COURT_SOURCES = {"spain"}
DE_COURT_SOURCES = {"zvg", "zvg_de"}
PT_COURT_SOURCES = {"citius", "eleiloes"}
PT_TAX_SOURCES = {"financas"}
IT_COURT_SOURCES = {"pvp_giustizia", "astalegale", "fallcoaste", "italy"}
FR_COURT_SOURCES = {"france", "encheres_publiques", "vench"}
PL_COURT_SOURCES = {"poland"}
HR_COURT_SOURCES = {"fina", "croatia"}
AT_COURT_SOURCES = {"edikte"}
NL_COURT_SOURCES = {"netherlands", "veilingnotaris", "vastgoedveiling", "veilingbiljet"}

# Words a German notice uses when the 5/10 and 7/10 limits no longer apply.
DE_LIMITS_LIFTED = [
    "wertgrenzen weggefallen", "wertgrenzen sind weggefallen", "wertgrenzen entfallen",
    "wertgrenzen gelten nicht", "keine wertgrenzen", "ohne wertgrenzen",
    "5/10 und 7/10 grenze entfallen", "5/10- und 7/10-grenze entfallen",
    "zuschlag wurde versagt", "zuschlag versagt worden",
]
# BOE writes €1 or a few euros where no real claim applies (seen live, 2026-11-18).
MIN_REAL_CLAIM = 100
# A charges field that says nothing ranks ahead of the buyer. Read on the BOE
# (2026-11-18): "No constan", "SIN CARGAS.", "libre de cargas, salvo afecciones
# fiscales", "anteriores no tiene, posteriores sí". The boilerplate "se
# mantendrán las cargas anteriores" says nothing either way: unknown.
_NO_CHARGES = re.compile(
    r"^\s*(?:no|ninguna)\b|\bsin cargas\b|\blibre de cargas\b|\bno constan\b"
    r"|\bno (?:existen|hay|tiene|tienen) cargas\b|\banteriores,? no (?:tiene|tienen|constan|existen|hay)\b"
    r"|\bno (?:existen|constan|hay) cargas anteriores\b|\bsin cargas anteriores\b")


def _raw(item: dict) -> dict:
    try:
        raw = json.loads(item.get("raw_json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


def _eur(v: float) -> str:
    return f"€{v:,.0f}".replace(",", " ")


def _num(v) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(parse_price(v) or 0) if v else 0.0


def spain_charges(raw: dict) -> tuple[str, float]:
    """("none" | "stated" | "unknown", amount): the registry certificate when it
    was read (registry_charges), else the BOE "Cargas" field."""
    from registry_charges import summary
    read = summary(raw.get("cert"))
    if read and (read[0] != "unknown" or raw.get("cert", {}).get("status") == "read"):
        return read[0], read[1]
    text = str(raw.get("cargas") or "").strip()
    # Only a figure with a currency marker: "Ejecución 922/2014" is not €922.
    amount = _num(raw.get("charges_eur")) or float(find_price(text) or 0)
    if amount > 0:
        return "stated", amount
    if text and _NO_CHARGES.search(normalize(text)):
        return "none", 0.0
    return "unknown", 0.0


def _spain(item: dict, raw: dict, base: float) -> dict | None:
    tipo = normalize(raw.get("tipo_subasta") or "")
    if tipo and "judicial" not in tipo:
        return None                      # AEAT, TGSS and notarial sales follow other rules
    # The claim rule is for an execution ("en vía de apremio"). A voluntary sale
    # (co-owners dividing a house) or an insolvency follows its own terms.
    execution = not tipo or "apremio" in tipo
    value = _num(raw.get("tasacion_eur")) or base
    if not value:
        return None
    out = {
        "country": "ES",
        "law": "LEC art. 670",
        "amount": round(value * ES_OUTRIGHT),
        "basis": "outright",
        "rule": (f"A bid of 70% of the value ({_eur(value * ES_OUTRIGHT)}) is approved outright. Below that, "
                 f"if neither the debtor nor the creditor steps in, the court normally approves a bid over "
                 f"50% of the valuation ({_eur(value * ES_APPROVAL)}) or one that covers the amount claimed."),
        "caveats": [],
    }
    claim = _num(raw.get("claim_eur")) if execution else 0
    if claim < MIN_REAL_CLAIM:
        claim = 0
    lots = _num(raw.get("lotes") or raw.get("lots"))
    if claim and lots > 1:
        out["caveats"].append("the amount claimed is for the whole sale, which has several lots — "
                              "it is not this lot's floor")
        claim = 0
    if claim:
        out["claim"] = claim
        out["claim_share"] = claim / value
    if claim and claim < value * ES_APPROVAL:
        state, charges = spain_charges(raw)
        out.update(amount=round(claim), basis="claim")
        out["rule"] = (f"The amount claimed is {_eur(claim)}, {claim / value:.0%} of the {_eur(value)} valuation. "
                       "If no one bids 70% and neither the debtor nor the creditor steps in, the court normally "
                       "approves a bid that covers the claim (LEC art. 670.4), even under half the value.")
        out["caveats"].append("the owner can pay the claim and stop the sale until the last moment — "
                              "small claims are often paid off")
        from registry_charges import summary
        read = summary(raw.get("cert"))
        if read:
            out["caveats"].append(read[2])
        if state == "stated":
            out["charges"] = charges
            if not read:
                out["caveats"].append(f"charges of {_eur(charges)} stay with the property and are paid on top")
        elif state == "unknown" and not (read and raw["cert"].get("status") == "read"):
            out["caveats"].append("a small claim is often a junior creditor's (community fees, a second loan): "
                                  "older charges such as the mortgage stay with the property and are not "
                                  "stated here — read the edict and the registry certificate")
        out["charges_state"] = state
    elif claim:
        out["caveats"].append(f"the amount claimed ({_eur(claim)}) is over half the value, so it does not "
                              "lower the floor")
    return out


def _germany(item: dict, text: str, base: float) -> dict:
    lifted = bool(find_terms(text, DE_LIMITS_LIFTED, negations=False))
    if lifted:
        return {
            "country": "DE", "law": "ZVG §§ 74a, 85a", "amount": None, "basis": "limits_lifted",
            "rule": ("The notice says the 5/10 and 7/10 value limits no longer apply: the lowest bid "
                     "(geringstes Gebot, read out at the hearing) can win, however far under the Verkehrswert."),
            "caveats": ["the lowest bid can include rights that stay with the property (Grundschulden "
                        "ranking ahead of the creditor) — ask the court before the hearing"],
        }
    return {
        "country": "DE", "law": "ZVG §§ 74a, 85a", "amount": round(base * DE_CREDITOR_LIMIT) if base else None,
        "basis": "first_hearing",
        "rule": (f"At a first hearing a bid under half the Verkehrswert ({_eur(base * DE_COURT_LIMIT)}) is refused "
                 f"and under 70% ({_eur(base * DE_CREDITOR_LIMIT)}) a creditor may ask for refusal. If the sale is "
                 "refused for those reasons, the limits normally fall away at the next hearing.") if base else
                ("At a first hearing a bid under half the Verkehrswert is refused, and under 70% a creditor may "
                 "ask for refusal; after such a refusal the limits normally fall away."),
        "caveats": [],
    }


def legal_floor(item: dict) -> dict | None:
    """What the law says about how low this sale may go, or None.

    {"country", "law", "rule": text, "amount": the lowest accepted price this
    round when a figure can be named (else None), "basis", "caveats": [...],
    and "next_round" when an unsold sale is normally offered again lower}."""
    source = item.get("source") or ""
    raw = _raw(item)
    base = float(item.get("price") or 0)
    stated_min = float(item.get("min_price") or 0)
    text = " ".join(str(item.get(k) or "") for k in ("title", "description"))

    if source in ES_COURT_SOURCES:
        return _spain(item, raw, base)
    if source in DE_COURT_SOURCES:
        return _germany(item, text, base)
    if source in PT_COURT_SOURCES:
        return {"country": "PT", "law": "CPC art. 816", "basis": "minimum",
                "amount": round(stated_min or base * PT_COURT_MIN) if base or stated_min else None,
                "rule": "Offers are normally accepted from 85% of the base value.",
                "next_round": ("If no acceptable offer comes in, the sale normally moves to private negotiation, "
                               "where the agent of execution can accept less with the parties' agreement."),
                "caveats": ["the debtor's spouse, children or parents can take the sale at the winning price "
                            "(direito de remição)"]}
    if source in PT_TAX_SOURCES:
        return {"country": "PT", "law": "CPPT arts. 248–250", "basis": "minimum",
                "amount": round(stated_min or base) if base or stated_min else None,
                "rule": ("A tax sale opens at 70% of the value the tax office set (e-auction); with no bids it "
                         "moves to sealed bids at 50%."),
                "next_round": ("With no bids again it goes back to auction; whatever the rounds, it cannot be "
                               "sold for less than 20% of that value."),
                "caveats": ["the debtor can pay the tax debt and stop the sale"]}
    if source in IT_COURT_SOURCES:
        return {"country": "IT", "law": "c.p.c. arts. 571, 591", "basis": "minimum",
                "amount": round(stated_min or base * IT_MIN_OFFER) if base or stated_min else None,
                "rule": "Offers are accepted from 75% of the base price (offerta minima).",
                "next_round": "An unsold lot usually comes back with the base price cut by up to a quarter.",
                "caveats": []}
    if source in FR_COURT_SOURCES or (source == "notaires" and raw.get("judicial")):
        return {"country": "FR", "law": "CPCE art. L322-6", "basis": "starting_price", "amount": None,
                "rule": ("The mise à prix is only the opening figure; bids go through a lawyer at the "
                         "hearing. If nobody bids, the creditor normally takes the property at the mise à prix."),
                "caveats": ["anyone can outbid the winner by 10% within 10 days (surenchère)"]}
    if source in PL_COURT_SOURCES:
        return {"country": "PL", "law": "KPC arts. 965, 983", "basis": "starting_price", "amount": None,
                "rule": "The first auction starts at 3/4 of the valuation; a second one at 2/3.",
                "caveats": []}
    if source in HR_COURT_SOURCES:
        return {"country": "HR", "law": "Ovršni zakon", "basis": "starting_price", "amount": None,
                "rule": ("At a first electronic auction a property is not sold under 80% of its set value; "
                         "at a second one not under 60%."),
                "caveats": []}
    if source in AT_COURT_SOURCES:
        return {"country": "AT", "law": "EO § 151", "basis": "starting_price", "amount": None,
                "rule": "The lowest bid the court accepts is normally half the valuation (Schätzwert).",
                "caveats": []}
    if source in NL_COURT_SOURCES:
        return {"country": "NL", "law": "BW art. 3:268", "basis": "none", "amount": None,
                "rule": ("There is no legal minimum; the seller (usually the bank) can refuse the best bid "
                         "when it is too low."),
                "caveats": []}
    return None


def rank_share(item: dict, pay: float) -> tuple[float, str] | None:
    """(the floor as a share of what you would pay, a reason) when the law lets
    this round go meaningfully under `pay`, else None.

    A live bid above the floor is what has to be beaten, so then there is
    nothing to add. A Spanish claim with charges not stated ranks only as the
    50% approval rule would: the charges ahead of it may wipe out the gap."""
    if not pay or (item.get("current_bid") or 0) > 0:
        return None
    floor = legal_floor(item)
    if not floor:
        return None
    if floor["basis"] == "limits_lifted":
        return DE_LIFTED_RANK_SHARE, "no value limits at this hearing — the lowest bid can win (ZVG)"
    amount = floor.get("amount")
    if not amount or amount >= pay * 0.98:
        return None
    if floor["basis"] == "claim":
        share = (amount + floor.get("charges", 0)) / pay
        why = f"the court may approve a bid covering the {_eur(amount)} claimed (LEC 670.4)"
        if floor.get("charges_state") == "unknown":
            value = amount / floor["claim_share"] if floor.get("claim_share") else pay
            share = max(share, value * ES_APPROVAL / pay)
            why += " — charges ahead of it not stated"
        return (share, why) if share <= RANK_AT_MOST else None
    share = amount / pay
    if share > RANK_AT_MOST:
        return None
    return share, f"the law lets a bid of {_eur(amount)} ({share:.0%}) be accepted ({floor['law']})"


def floor_price(item: dict) -> float | None:
    """What a Spanish court sale with a small claim could go for if the court
    approves a bid covering it: the claim plus the charges the notice states.
    None for every other sale. Used to decide what is shown against a budget,
    never as a price to pay or a bid."""
    if item.get("source") not in ES_COURT_SOURCES or (item.get("current_bid") or 0) > 0:
        return None
    floor = legal_floor(item)
    if not floor or floor["basis"] != "claim":
        return None
    return float(floor["amount"] + floor.get("charges", 0))


def fits_budget(item: dict, budget: float) -> bool:
    """A small-claim sale is kept in view when the claim (plus stated charges)
    is within the budget, even if its value is not."""
    fp = item.get("floor_price")
    if fp is None and "floor_price" not in item:
        fp = floor_price(item)
    return bool(budget and fp and fp <= budget)


def budget_note(item: dict, budget: float) -> str | None:
    """The reason a sale over the budget is still shown, or None."""
    pay = item.get("price") or 0
    if not (budget and pay > budget and fits_budget(item, budget)):
        return None
    return (f"over your budget at its value ({_eur(pay)}); within it only if the court approves "
            f"a bid covering the claim ({_eur(item.get('floor_price') or floor_price(item) or 0)})")
