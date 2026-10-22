"""Collection contracts (Beta): when a search proves absence, when a lot is
available, and which hazard readings are usable.

A positive listing count never proves a complete search. Scope (regions,
categories, price filters, date window, sale-state filters) is set by the
source adapter, not guessed here.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math


def content_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PartitionResult:
    partition: str
    scope_hash: str
    complete: bool
    natural_end: bool
    seen_ids: frozenset[str]
    stop_reason: str | None = None

    def supports_absence(self, listing_id, established_partition, established_scope_hash):
        """Eligibility for inferring absence — never an instruction to hide."""
        return (self.complete is True and self.natural_end is True
                and self.stop_reason is None
                and established_partition == self.partition
                and established_scope_hash == self.scope_hash
                and listing_id not in self.seen_ids)


@dataclass(frozen=True)
class Availability:
    state: str
    lot_id: str
    source: str
    basis: str
    observed_at: datetime
    expires_at: datetime | None

    def current_available(self, lot_id, now):
        times = (self.observed_at, self.expires_at, now)
        if any(not isinstance(t, datetime) or t.utcoffset() is None for t in times):
            return False
        return (self.lot_id == lot_id and self.state == "available"
                and self.basis == "matched_affirmative_sale_state"
                and self.observed_at <= now < self.expires_at)


def classify_lot_record(expected_id, observed_id, supported_state, *, blocked=False, error=False):
    if blocked:
        return "blocked"
    if error:
        return "error"
    if not expected_id or observed_id != expected_id:
        return "unknown"
    allowed = {"available", "reserved", "withdrawn", "ended"}
    return supported_state if supported_state in allowed else "unknown"


def usable_hazards(climate):
    """Flood and fire readings both present, numeric, finite and >= 0.
    A measured zero is valid; an empty fire dictionary is incomplete."""
    def valid(value):
        if type(value) not in (int, float):
            return False
        try:
            return math.isfinite(value) and value >= 0
        except OverflowError:
            return False
    if not isinstance(climate, dict):
        return False
    danger = climate.get("fire_danger")
    return (valid(climate.get("flood_m")) and isinstance(danger, dict)
            and valid(danger.get("high_days_2090")))


FIRE_KEYS = ("high_days_2090", "rcp45_2079-2098")   # canonical first, then the older layer's key


def hazard_metrics(climate):
    """(flood depth m, high fire-danger days) as usable numbers, or None each.

    The one reading shared by the climate grade and the evaluator. A present
    but invalid canonical fire metric is not rescued by an older one; the
    top-level fire_danger_days is read only when no fire key is present."""
    def number(value):
        if type(value) not in (int, float):
            return None
        try:
            return float(value) if math.isfinite(value) and value >= 0 else None
        except OverflowError:
            return None
    if not isinstance(climate, dict):
        return None, None
    danger = climate.get("fire_danger")
    days = None
    keyed = isinstance(danger, dict) and any(k in danger for k in FIRE_KEYS)
    if keyed:
        days = number(danger[next(k for k in FIRE_KEYS if k in danger)])
    else:
        days = number(climate.get("fire_danger_days"))
    return number(climate.get("flood_m")), days
