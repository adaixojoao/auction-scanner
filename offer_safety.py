"""Operational offer safety (Alpha): exact amounts, a complete cost scenario,
and what a binding offer still needs before the app would call it approved.

Money is integer cents. Offer amounts typed by a person are European text
("4000", "4000,50", "4.000,50") and go through `parse_offer_text` only: the
lenient `common.parse_price` is for scraped pages, never for an amount you
send. Canonical euro values (decimal dot) go through `euro_number_to_cents`.
An unknown cost stays None and makes the scenario "incomplete"; a documented
zero is allowed. Nothing here verifies a property: `approve` only reports
which recorded findings are missing, and software approval is never legal
verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_CENTS = 10_000_000_000   # technical input bound (€100 M), not the buyer's budget
BID_TEXT = re.compile(r"(?:0|[1-9]\d*|[1-9]\d{0,2}(?:\.\d{3})+)(?:,\d{1,2})?")


class SafetyError(ValueError):
    pass


def euro_number_to_cents(value) -> int:
    """Canonical euro input: int/Decimal or decimal-dot text; no float/bool."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
        raise SafetyError("Use integer, Decimal or canonical decimal text")
    if isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        raise SafetyError("Invalid canonical euro amount")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0:
            raise SafetyError("Amount must be finite and nonnegative")
        cents = amount * 100
        if cents != cents.to_integral_value() or cents > MAX_CENTS:
            raise SafetyError("Amount exceeds precision or technical limit")
        return int(cents)
    except (InvalidOperation, OverflowError) as exc:
        raise SafetyError("Invalid amount") from exc


def parse_offer_text(text) -> int:
    """A typed offer in European format → cents. Anything else is refused."""
    if not isinstance(text, str):
        raise SafetyError("Offer must be European-format text")
    text = text.strip()
    if not BID_TEXT.fullmatch(text):
        raise SafetyError("Write the amount as 4000, 4000,50 or 4.000,50 — no other text")
    cents = euro_number_to_cents(text.replace(".", "").replace(",", "."))
    if cents <= 0:
        raise SafetyError("The offer must be more than zero")
    return cents


def require_cents(value, name, *, positive=False) -> int:
    if (type(value) is not int or value < (1 if positive else 0)
            or value > MAX_CENTS):
        raise SafetyError(f"Invalid {name}: integer cents required")
    return value


def percentage(value, name) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise SafetyError(f"Invalid {name}")
    try:
        v = Decimal(str(value))
        if not v.is_finite() or not 0 <= v <= 100:
            raise SafetyError(f"{name} must be finite and between 0 and 100")
        return v
    except InvalidOperation as exc:
        raise SafetyError(f"Invalid {name}") from exc


def fingerprint(payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def cash_euros(cfg: dict) -> float:
    """Settings → Budget's cash you can lose, or 0 (unset) when it is not a
    finite non-negative number — a hand-edited "NaN" or "1e999" must refuse
    offers, not slip past every comparison."""
    value = (cfg or {}).get("cash_on_hand")
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and 0 <= number <= MAX_CENTS / 100 else 0.0


@dataclass(frozen=True)
class Scenario:
    status: str
    required_cents: int | None
    missing: tuple[str, ...]
    scenario_hash: str


COMPONENTS = ("obligations", "taxes", "fees", "works", "reserve")


def build_scenario(bid_cents, cash_cents, components, context) -> Scenario:
    """Components: obligations, taxes, fees, works, reserve, each cents or None.

    The adapter must evidence zeros and supply the selected conservative works
    allowance, not silently choose a lower range endpoint.
    """
    require_cents(bid_cents, "bid", positive=True)
    require_cents(cash_cents, "cash")
    if set(components) != set(COMPONENTS):
        raise SafetyError("Supply every scenario component")
    missing = tuple(n for n in COMPONENTS if components[n] is None)
    for n in COMPONENTS:
        if components[n] is not None:
            require_cents(components[n], n)
    total = None if missing else bid_cents + sum(components.values())
    status = ("budget_not_set" if cash_cents == 0 else "incomplete" if missing
              else "over_budget" if total > cash_cents else "within_estimate")
    payload = {"version": 1, "bid": bid_cents, "cash": cash_cents,
               "components": components, "context": context}
    return Scenario(status, total, missing, fingerprint(payload))


REQUIRED = ("identity", "ownership", "possession", "title", "access", "use", "procedure", "availability")


def approve(kind, scenario, findings) -> dict:
    """findings must be resolved documentary/manual records, not model claims.
    Historical recording never claims today's approval.
    """
    if kind in ("information_request", "historical"):
        return {"authorised_acquisition": False, "blockers": []}
    if kind != "binding_offer":
        raise SafetyError("Unknown operation kind")
    blockers = [n for n in REQUIRED
                if findings.get(n, {}).get("status") not in ("verified_by_user", "professionally_reviewed")
                or not findings.get(n, {}).get("reference")]
    if scenario is None or scenario.status != "within_estimate":
        blockers.append("financial_scenario")
    return {"authorised_acquisition": not blockers, "blockers": blockers}


# ─── The adapter: this repository's records → a scenario ────────────────

def _cents(euros) -> int | None:
    if euros is None:
        return None
    try:
        value = float(euros)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(round(value * 100), MAX_CENTS)


def scenario_for(item: dict, bid_cents: int | None, cfg: dict) -> dict:
    """The offer's cost scenario from costs.py, shown and stored with the send.

    Taxes and fees are costs.estimate's combined line (so `fees` is a
    documented zero: it is inside `taxes`). Works take the HIGH end of the
    repair range; a home with no floor area has unknown works. Retained
    charges (debts that pass with the property) and the reserve are unknown
    unless recorded, so most scenarios are honestly "incomplete".
    """
    import costs
    cash = round(cash_euros(cfg) * 100)
    if not bid_cents:
        price = costs.price_to_pay(item)
        bid_cents = _cents(price) if price else None
    if not bid_cents:
        return {"status": "no_amount", "required_cents": None, "missing": ["bid"], "scenario_hash": None,
                "notes": ["No amount to plan with."]}
    est = costs.estimate(item, bid=bid_cents / 100)
    notes = []
    taxes = _cents(est["fees"]) if est else None
    works = None
    if est and est.get("renovation"):
        works = _cents(est["renovation"]["high"])
        notes.append("works: the high end of the repair estimate")
    else:
        notes.append("works: no repair estimate (no floor area, or not a home) — unknown")
    reserve = _cents(costs.risk_reserve(item, (cfg or {}).get("bid_cap"))) if (cfg or {}).get("bid_cap") else None
    components = {"obligations": None, "taxes": taxes, "fees": 0 if taxes is not None else None,
                  "works": works, "reserve": reserve}
    notes.append("retained charges (debts that pass with the property): not recorded — unknown")
    s = build_scenario(bid_cents, cash, components, {"listing": item.get("id"), "basis": "costs.estimate"})
    return {"status": s.status, "required_cents": s.required_cents, "missing": list(s.missing),
            "scenario_hash": s.scenario_hash, "components": components, "notes": notes}
