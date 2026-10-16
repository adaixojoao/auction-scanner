"""Research indicators from supplied property evidence.

This module does not fetch external data, establish legal facts, assign an
investment score, or approve a purchase or bid. It reports derived indicators
with evidence provenance and caveats. Missing or unusable evidence stays
unknown rather than being treated as a negative finding.

Python 3.11+. Standard library only.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1

# Defaults are research-data freshness limits, not claims that evidence becomes
# false after these periods. A record may set expires_after_days to tighten its
# applicable limit.
DEFAULT_MAX_AGE_DAYS: dict[str, int] = {
    "comparable_sale": 3650,
    "document_claim": 365,
    "sale_event": 3650,
    "acquisition_requirement": 30,
    "liquidity_observation": 365,
    "route_observation": 365,
    "parcel_observation": 1825,
    "aerial_observation": 1825,
    "habitat_observation": 1825,
    "restoration_observation": 1825,
}

SUPPORTED_KINDS = frozenset(DEFAULT_MAX_AGE_DAYS)


@dataclass(frozen=True)
class Evidence:
    """A dated, attributed observation associated with one exact lot.

    `source_chain_id` identifies copies or derivatives of the same underlying
    evidence. Records with the same non-empty chain ID are counted once per
    indicator, to avoid treating an aggregator copy and its source as
    independent corroboration.
    """

    evidence_id: str
    kind: str
    source: str
    observed_on: date
    lot_id: str
    value: Mapping[str, Any]
    confidence: float
    location_confidence: float
    source_chain_id: str | None = None
    resolution_m: float | None = None
    expires_after_days: int | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Evidence":
        """Parse one JSON-like record; raises ValueError for malformed input."""
        try:
            observed = row["observed_on"]
            if isinstance(observed, datetime):
                observed = observed.date()
            elif isinstance(observed, str):
                observed = date.fromisoformat(observed)
            elif not isinstance(observed, date):
                raise ValueError("observed_on must be an ISO date")

            value = row["value"]
            if not isinstance(value, Mapping):
                raise ValueError("value must be an object")

            evidence = cls(
                evidence_id=str(row["evidence_id"]).strip(),
                kind=str(row["kind"]).strip(),
                source=str(row["source"]).strip(),
                observed_on=observed,
                lot_id=str(row["lot_id"]).strip(),
                value=dict(value),
                confidence=float(row.get("confidence", 0.0)),
                location_confidence=float(row.get("location_confidence", 0.0)),
                source_chain_id=(
                    str(row["source_chain_id"]).strip()
                    if row.get("source_chain_id") is not None
                    else None
                ),
                resolution_m=(
                    float(row["resolution_m"])
                    if row.get("resolution_m") is not None
                    else None
                ),
                expires_after_days=(
                    int(row["expires_after_days"])
                    if row.get("expires_after_days") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid evidence record: {exc}") from exc

        if not evidence.evidence_id:
            raise ValueError("evidence_id must not be empty")
        if not evidence.kind:
            raise ValueError("kind must not be empty")
        if not evidence.source:
            raise ValueError("source must not be empty")
        if not evidence.lot_id:
            raise ValueError("lot_id must not be empty")
        if not 0.0 <= evidence.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= evidence.location_confidence <= 1.0:
            raise ValueError("location_confidence must be between 0 and 1")
        if evidence.resolution_m is not None and evidence.resolution_m < 0:
            raise ValueError("resolution_m must not be negative")
        if evidence.expires_after_days is not None and evidence.expires_after_days < 0:
            raise ValueError("expires_after_days must not be negative")
        return evidence


@dataclass
class Indicator:
    """One category result. Status describes evidence, not purchase eligibility."""

    category: str
    status: str
    value: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


class EvidenceIndex:
    """Validates, associates, freshness-checks, and deduplicates evidence."""

    def __init__(
        self,
        subject_lot_id: str,
        rows: Iterable[Evidence | Mapping[str, Any]],
        today: date,
        min_confidence: float = 0.0,
        min_location_confidence: float = 0.0,
    ) -> None:
        self.subject_lot_id = subject_lot_id
        self.today = today
        self.min_confidence = min_confidence
        self.min_location_confidence = min_location_confidence
        self.records: list[Evidence] = []
        self.issues: list[str] = []
        self.excluded: dict[str, list[str]] = {}

        for position, row in enumerate(rows):
            try:
                record = row if isinstance(row, Evidence) else Evidence.from_mapping(row)
                self._validate(record)
                self.records.append(record)
            except (ValueError, TypeError) as exc:
                self.issues.append(f"record[{position}]: {exc}")

    @staticmethod
    def _validate(record: Evidence) -> None:
        if record.kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported kind {record.kind!r}")
        if not record.evidence_id or not record.source or not record.lot_id:
            raise ValueError("evidence_id, source and lot_id are required")
        if not 0.0 <= record.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= record.location_confidence <= 1.0:
            raise ValueError("location_confidence must be between 0 and 1")

    def for_kind(self, kind: str) -> tuple[list[Evidence], list[str]]:
        """Return eligible, deduplicated evidence and explanatory exclusions."""
        accepted: list[Evidence] = []
        exclusions: list[str] = []
        seen_chains: set[str] = set()

        for record in self.records:
            if record.kind != kind:
                continue
            if record.lot_id != self.subject_lot_id:
                exclusions.append(
                    f"{record.evidence_id}: excluded because lot_id does not "
                    "exactly match the subject"
                )
                continue
            if record.observed_on > self.today:
                exclusions.append(f"{record.evidence_id}: observation date is in the future")
                continue
            if record.confidence < self.min_confidence:
                exclusions.append(f"{record.evidence_id}: confidence below configured minimum")
                continue
            if record.location_confidence < self.min_location_confidence:
                exclusions.append(
                    f"{record.evidence_id}: location confidence below configured minimum"
                )
                continue

            max_age = DEFAULT_MAX_AGE_DAYS[kind]
            if record.expires_after_days is not None:
                max_age = min(max_age, record.expires_after_days)
            age_days = (self.today - record.observed_on).days
            if age_days > max_age:
                exclusions.append(
                    f"{record.evidence_id}: stale ({age_days} days old; "
                    f"limit is {max_age} days)"
                )
                continue

            chain = record.source_chain_id
            if chain:
                if chain in seen_chains:
                    exclusions.append(
                        f"{record.evidence_id}: duplicate source chain {chain!r}"
                    )
                    continue
                seen_chains.add(chain)
            accepted.append(record)

        self.excluded[kind] = exclusions
        return accepted, exclusions


def _indicator(
    category: str,
    records: Sequence[Evidence],
    value: dict[str, Any],
    caveats: Sequence[str] = (),
    status: str = "informational",
) -> Indicator:
    return Indicator(
        category=category,
        status=status,
        value=value,
        evidence_ids=[record.evidence_id for record in records],
        caveats=list(caveats),
    )


def _unknown(category: str, exclusions: Sequence[str] = ()) -> Indicator:
    caveats = ["No current, usable evidence was supplied for this category."]
    caveats.extend(exclusions)
    return Indicator(category=category, status="unknown", caveats=caveats)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _canonical(value: Any) -> str:
    """Stable comparison representation for document-claim values."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _comparable_indicator(
    index: EvidenceIndex,
    subject: Mapping[str, Any],
) -> Indicator:
    records, exclusions = index.for_kind("comparable_sale")
    subject_price = _finite_number(subject.get("price"))
    subject_area = _finite_number(subject.get("area_m2"))

    if subject_price is None or subject_area is None or subject_area <= 0:
        return _unknown(
            "property_specific_comparables",
            ["A positive subject price and area_m2 are needed to calculate a price comparison."]
            + exclusions,
        )

    subject_type = subject.get("property_type")
    max_distance = _finite_number(subject.get("max_comparable_distance_m"))
    if max_distance is None:
        max_distance = 50_000.0

    usable: list[tuple[Evidence, float]] = []
    for record in records:
        values = record.value
        status = str(values.get("transaction_status", "")).casefold()
        if status not in {"completed", "completed_sale"}:
            continue

        price = _finite_number(values.get("sale_price"))
        area = _finite_number(values.get("area_m2"))
        distance = _finite_number(values.get("distance_m"))
        if price is None or area is None or price <= 0 or area <= 0:
            continue
        if distance is None or distance < 0 or distance > max_distance:
            continue
        if subject_type is not None and values.get("property_type") != subject_type:
            continue

        usable.append((record, price / area))

    if not usable:
        return _unknown(
            "property_specific_comparables",
            [
                "No completed, in-range transactions matching the supplied filters "
                "could be calculated."
            ] + exclusions,
        )

    rates = [rate for _, rate in usable]
    median_rate = statistics.median(rates)
    subject_rate = subject_price / subject_area
    discount_pct = (
        (median_rate - subject_rate) / median_rate * 100.0
        if median_rate > 0
        else None
    )

    caveats = [
        "This is a simple price-per-area comparison, not a formal valuation.",
        "Condition, rights, exact micro-location and lot composition may differ.",
        "Comparable selection quality must be reviewed; asking prices are not completed sales.",
    ]
    if len(usable) < 3:
        caveats.append("Fewer than three usable completed comparables; the estimate is fragile.")

    return _indicator(
        "property_specific_comparables",
        [record for record, _ in usable],
        {
            "subject_price_per_m2": subject_rate,
            "median_comparable_price_per_m2": median_rate,
            "indicative_discount_percent": discount_pct,
            "comparable_count": len(usable),
            "comparable_price_per_m2_min": min(rates),
            "comparable_price_per_m2_max": max(rates),
        },
        caveats,
    )


def _document_indicator(index: EvidenceIndex) -> Indicator:
    records, exclusions = index.for_kind("document_claim")
    claims: dict[str, list[tuple[Evidence, Any]]] = {}
    for record in records:
        field_name = str(record.value.get("field", "")).strip()
        if field_name and "claimed_value" in record.value:
            claims.setdefault(field_name, []).append(
                (record, record.value["claimed_value"])
            )

    contradictions: list[dict[str, Any]] = []
    used: list[Evidence] = []
    for field_name, entries in sorted(claims.items()):
        distinct: dict[str, Any] = {}
        for record, claimed_value in entries:
            distinct.setdefault(_canonical(claimed_value), claimed_value)
        if len(distinct) > 1:
            contradictions.append(
                {
                    "field": field_name,
                    "claims": [
                        {
                            "evidence_id": record.evidence_id,
                            "source": record.source,
                            "document_type": record.value.get("document_type"),
                            "claimed_value": claimed_value,
                        }
                        for record, claimed_value in entries
                    ],
                }
            )
            used.extend(record for record, _ in entries)

    if not records:
        return _unknown("document_contradictions", exclusions)

    return _indicator(
        "document_contradictions",
        used or records,
        {"contradictions": contradictions, "contradiction_count": len(contradictions)},
        [
            "A discrepancy is a verification question, not proof of defective title.",
            "Claims should be checked against the original documents and the exact lot.",
        ] + exclusions,
        status="contradiction" if contradictions else "informational",
    )


def _sale_history_indicator(index: EvidenceIndex) -> Indicator:
    records, exclusions = index.for_kind("sale_event")
    if not records:
        return _unknown("sale_event_history", exclusions)

    events = sorted(records, key=lambda item: (item.observed_on, item.evidence_id))
    failed = [
        record for record in events
        if str(record.value.get("event_type", "")).casefold() == "auction_failed"
    ]
    reserve_values: list[tuple[date, float, str]] = []
    for record in events:
        if str(record.value.get("event_type", "")).casefold() != "reserve_changed":
            continue
        amount = _finite_number(record.value.get("reserve_amount"))
        if amount is not None:
            reserve_values.append((record.observed_on, amount, record.evidence_id))

    reductions: list[dict[str, Any]] = []
    reserve_values.sort()
    for previous, current in zip(reserve_values, reserve_values[1:]):
        if current[1] < previous[1]:
            reductions.append(
                {
                    "from_amount": previous[1],
                    "to_amount": current[1],
                    "change_percent": (current[1] - previous[1]) / previous[1] * 100.0
                    if previous[1]
                    else None,
                    "from_evidence_id": previous[2],
                    "to_evidence_id": current[2],
                }
            )

    return _indicator(
        "sale_event_history",
        events,
        {
            "events": [
                {
                    "evidence_id": record.evidence_id,
                    "observed_on": record.observed_on.isoformat(),
                    "event_type": record.value.get("event_type"),
                    "amount": record.value.get("amount"),
                    "reserve_amount": record.value.get("reserve_amount"),
                }
                for record in events
            ],
            "explicit_auction_failure_count": len(failed),
            "observed_reserve_reductions": reductions,
        },
        [
            "Only an explicit auction_failed event is counted as a failed auction.",
            "Withdrawn, expired, removed or relisted records do not prove an auction was unsold.",
            "The event history may be incomplete.",
        ] + exclusions,
    )


def _feasibility_indicator(index: EvidenceIndex) -> Indicator:
    records, exclusions = index.for_kind("acquisition_requirement")
    if not records:
        return _unknown("acquisition_feasibility", exclusions)

    requirements = []
    for record in records:
        status = str(record.value.get("status", "unknown")).casefold()
        if status not in {"met", "unmet", "unknown"}:
            status = "unknown"
        requirements.append(
            {
                "evidence_id": record.evidence_id,
                "requirement": record.value.get("requirement"),
                "status": status,
                "deadline": record.value.get("deadline"),
                "notes": record.value.get("notes"),
            }
        )

    counts = {
        name: sum(item["status"] == name for item in requirements)
        for name in ("met", "unmet", "unknown")
    }
    return _indicator(
        "acquisition_feasibility",
        records,
        {"requirements": requirements, "status_counts": counts},
        [
            "This is an operational checklist, not legal clearance or confirmation "
            "that an offer can be submitted.",
            "Unlisted requirements may still apply.",
        ] + exclusions,
        status="incomplete" if counts["unmet"] or counts["unknown"] else "informational",
    )


def _observations_indicator(
    index: EvidenceIndex,
    kind: str,
    category: str,
    caveats: Sequence[str],
) -> Indicator:
    records, exclusions = index.for_kind(kind)
    if not records:
        return _unknown(category, exclusions)

    observations = [
        {
            "evidence_id": record.evidence_id,
            "source": record.source,
            "observed_on": record.observed_on.isoformat(),
            "value": dict(record.value),
            "confidence": record.confidence,
            "location_confidence": record.location_confidence,
            "resolution_m": record.resolution_m,
        }
        for record in sorted(records, key=lambda item: (item.observed_on, item.evidence_id))
    ]
    return _indicator(category, records, {"observations": observations}, list(caveats) + exclusions)


# ---------------------------------------------------------------------------
# Everything above this line is the owner's supplied code, unchanged. The
# supplied text ended inside _restoration_indicator; what follows was written
# to complete it from the owner's indicator table (what each indicator can
# and cannot establish).
# ---------------------------------------------------------------------------


def _restoration_indicator(index: EvidenceIndex) -> Indicator:
    records, exclusions = index.for_kind("restoration_observation")
    if not records:
        return _unknown("restoration_feasibility", exclusions)

    observations = []
    costs: list[float] = []
    for record in sorted(records, key=lambda item: (item.observed_on, item.evidence_id)):
        item = dict(record.value)
        cost = _finite_number(item.get("estimated_cost_eur"))
        if cost is not None and cost < 0:
            cost = None
        if cost is not None:
            costs.append(cost)
        observations.append(
            {
                "evidence_id": record.evidence_id,
                "source": record.source,
                "observed_on": record.observed_on.isoformat(),
                "factor": item.get("factor"),
                "finding": item.get("finding"),
                "estimated_cost_eur": cost,
                "confidence": record.confidence,
            }
        )

    caveats = [
        "Satellite greenness is not biodiversity; field survey is needed.",
        "Native regeneration, invasive cover, erosion, grazing and burn history "
        "indicate stewardship effort, not a restoration outcome.",
    ]
    if costs and len(costs) < len(observations):
        caveats.append("Some observations have no usable cost estimate; the cost range is partial.")
    return _indicator(
        "restoration_feasibility",
        records,
        {
            "observations": observations,
            "estimated_cost_eur_min": min(costs) if costs else None,
            "estimated_cost_eur_max": max(costs) if costs else None,
            "costed_observation_count": len(costs),
        },
        caveats + exclusions,
    )


# category, evidence kind, what the evidence cannot establish
_OBSERVATION_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("local_liquidity", "liquidity_observation", (
        "A removed advertisement does not prove a completed sale.",
        "Supply and marketing-duration figures describe the area, not this lot.",
    )),
    ("travel_access", "route_observation", (
        "Use road-network travel times; a short straight-line distance can hide a long journey.",
        "Travel times vary by season, time of day and road condition.",
    )),
    ("parcel_geometry", "parcel_observation", (
        "Mapped frontage does not establish a legal access right.",
        "Cadastral boundaries can be uncertain; usable area needs a survey.",
    )),
    ("historical_imagery", "aerial_observation", (
        "Vegetation or image appearance cannot prove occupancy or structural safety.",
        "Image resolution and date limit what can be seen; changes need inspection.",
    )),
    ("ecological_connectivity", "habitat_observation", (
        "Protection status alone does not establish habitat quality.",
        "Connectivity beyond the parcel does not grant any right over adjoining land.",
    )),
)


def build_indicators(
    subject: Mapping[str, Any],
    evidence: Iterable[Evidence | Mapping[str, Any]],
    today: date | None = None,
    *,
    min_confidence: float = 0.0,
    min_location_confidence: float = 0.0,
) -> dict[str, Any]:
    """Every research indicator for one lot, as a JSON-ready dict.

    `subject` needs `lot_id`; `price`, `area_m2`, `property_type` and
    `max_comparable_distance_m` are used for the comparable-price indicator.
    Each category is present: one without usable evidence is "unknown".
    Nothing here is a score, an eligibility decision or a bid.
    """
    lot_id = str(subject.get("lot_id") or "").strip()
    if not lot_id:
        raise ValueError("subject lot_id is required")
    today = today or datetime.now(timezone.utc).date()
    index = EvidenceIndex(lot_id, evidence, today, min_confidence, min_location_confidence)

    indicators = [
        _comparable_indicator(index, subject),
        _document_indicator(index),
        _sale_history_indicator(index),
        _feasibility_indicator(index),
    ]
    indicators += [
        _observations_indicator(index, kind, category, caveats)
        for category, kind, caveats in _OBSERVATION_CATEGORIES
    ]
    indicators.append(_restoration_indicator(index))
    return {
        "schema_version": SCHEMA_VERSION,
        "subject_lot_id": lot_id,
        "as_of": today.isoformat(),
        "indicators": [asdict(indicator) for indicator in indicators],
        "issues": list(index.issues),
    }
