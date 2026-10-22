"""Valuation contracts (Gamma G1): a market benchmark is not a property valuation.

Municipal or parish prices support research benchmarks only. An index label or
an AI estimate never becomes property-specific valuation evidence. Amounts are
integer cents, as in offer_safety. `supported_for` checks structure and binding
only; the records it names must still be verified by whoever supplies them.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json


def digest(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def cents(value, name, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} must be integer cents")
    return value


@dataclass(frozen=True)
class Valuation:
    lot_id: str
    basis: str  # benchmark, as_is, after_work
    low_cents: int
    high_cents: int
    source_type: str  # benchmark_index, comparables, appraisal
    evidence_ids: tuple[str, ...]
    assessed_at: datetime
    expires_at: datetime
    input_binding: str
    reviewed_by: str | None = None

    def __post_init__(self):
        cents(self.low_cents, "low value", positive=True)
        cents(self.high_cents, "high value", positive=True)
        if self.high_cents < self.low_cents:
            raise ValueError("Value range is reversed")
        if self.basis not in ("benchmark", "as_is", "after_work"):
            raise ValueError("Unknown valuation basis")
        if self.source_type not in ("benchmark_index", "comparables", "appraisal"):
            raise ValueError("Unknown valuation source")
        if not isinstance(self.lot_id, str) or not self.lot_id:
            raise ValueError("Lot identity required")
        if not isinstance(self.input_binding, str) or not self.input_binding:
            raise ValueError("Input binding required")
        if any(not isinstance(t, datetime) or t.utcoffset() is None
               for t in (self.assessed_at, self.expires_at)):
            raise ValueError("Timezone-aware valuation times required")
        if self.expires_at <= self.assessed_at:
            raise ValueError("Invalid valuation validity window")

    def supported_for(self, lot_id, current_binding, now):
        if not isinstance(now, datetime) or now.utcoffset() is None:
            return False
        return (self.lot_id == lot_id and self.input_binding == current_binding
                and self.assessed_at <= now < self.expires_at
                and self.basis in ("as_is", "after_work")
                and self.source_type in ("comparables", "appraisal")
                and isinstance(self.evidence_ids, tuple) and bool(self.evidence_ids)
                and all(isinstance(i, str) and i for i in self.evidence_ids)
                and isinstance(self.reviewed_by, str) and bool(self.reviewed_by.strip()))


def benchmark_gap(required_cents, benchmark_cents):
    """Research benchmark gap, not verified discount or profit."""
    cents(required_cents, "required cash", positive=True)
    cents(benchmark_cents, "benchmark", positive=True)
    return (benchmark_cents - required_cents) / benchmark_cents


def resale_margin(valuation, *, lot_id, binding, now, expected_basis,
                  project_cost_cents, disposal_cost_cents):
    """Conservative nominal margin; not IRR or an acquisition approval.

    Cost adapters must establish completeness and avoid mismatched as-is/work cases.
    """
    if expected_basis not in ("as_is", "after_work"):
        raise ValueError("Explicit scenario basis required")
    if not isinstance(valuation, Valuation) or not valuation.supported_for(lot_id, binding, now):
        return {"status": "unknown", "margin_cents": None}
    if valuation.basis != expected_basis:
        return {"status": "basis_mismatch", "margin_cents": None}
    if project_cost_cents is None or disposal_cost_cents is None:
        return {"status": "incomplete", "margin_cents": None}
    cents(project_cost_cents, "project cost", positive=True)
    cents(disposal_cost_cents, "disposal cost")
    return {"status": "supported_estimate",
            "margin_cents": valuation.low_cents - project_cost_cents - disposal_cost_cents}
