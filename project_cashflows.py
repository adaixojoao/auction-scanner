"""Explicit project cash flows (Gamma G2): NPV, a conventional IRR and carbon
that defaults to zero.

Each case states its horizon and every year's flow in integer cents; a missing
component or an unknown terminal value makes the case incomplete, never zero.
"""
from dataclasses import dataclass
import math


def money(value):
    if type(value) is not int:
        raise ValueError("Cash flows must be integer cents")
    return value


@dataclass(frozen=True)
class ProjectCase:
    name: str  # keep_forest, harvest_and_replant, restoration, rental, resale
    annual_cents: tuple[int, ...]  # year 0 includes purchase and initial costs
    terminal_cents: int | None  # value/cost at end, not duplicated in annual flows
    horizon_years: int
    missing_components: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.horizon_years) is not int or not 1 <= self.horizon_years <= 100:
            raise ValueError("Explicit horizon of 1–100 years required")
        if not isinstance(self.annual_cents, tuple) or len(self.annual_cents) != self.horizon_years + 1:
            raise ValueError("Supply annual cash flows from year 0 through horizon")
        for value in self.annual_cents:
            money(value)
        if self.terminal_cents is not None:
            money(self.terminal_cents)


def cashflows(case):
    if case.missing_components or case.terminal_cents is None:
        return None
    values = list(case.annual_cents)
    values[-1] += case.terminal_cents
    return values


def npv(case, rate):
    if type(rate) not in (int, float) or not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError("Use a finite discount rate between zero and one")
    flows = cashflows(case)
    if flows is None:
        return None
    return sum(value / (1 + rate) ** year for year, value in enumerate(flows))


def conventional_irr(case):
    """IRR only for an initial negative flow followed by nonnegative flows.
    Multiple-sign-change projects require a different explicitly chosen method.
    """
    flows = cashflows(case)
    if flows is None:
        return {"status": "incomplete", "rate": None}
    if flows[0] >= 0 or any(v < 0 for v in flows[1:]) or not any(v > 0 for v in flows[1:]):
        return {"status": "nonconventional", "rate": None}
    lo, hi = -0.99, 10.0

    def at(rate):
        return sum(v / (1 + rate) ** y for y, v in enumerate(flows))
    if at(lo) < 0 or at(hi) > 0:
        return {"status": "outside_search_range", "rate": None}
    for _ in range(120):
        mid = (lo + hi) / 2
        if at(mid) > 0:
            lo = mid
        else:
            hi = mid
    return {"status": "estimated", "rate": (lo + hi) / 2}


def carbon_revenue_cents(eligible, projected_cents, evidence_ids, reviewed):
    """Eligibility is resolved externally; evidence strings alone establish nothing."""
    if eligible is not True or reviewed is not True or not evidence_ids:
        return 0
    if type(projected_cents) is not int or projected_cents < 0:
        raise ValueError("Invalid projected carbon revenue")
    return projected_cents
