"""
evaluator.py — Pure eligibility evaluator for Live and Invest (Milestone A, shadow mode).

`evaluate(evidence, policy, now)` checks each acquisition requirement and returns
a structured decision.  It has no database, network, file, AI or ambient mutable
scoring-weight dependencies.  It is safe to call in tests without any of those
systems present.

Shadow mode: the result is not yet connected to recommendations, dashboards or
publication channels.  The existing discovery scorer and shortlist are unchanged.
Only after identity, evidence and financial prerequisites (Milestones B–F) are
in place will this replace the "recommended" label.

Reason-code prefixes:
  PREF_   — preference failure (coastal, size, climate level…); dangerous=False
  LEGAL_  — legal/title requirement; dangerous=True when stated
  PHYS_   — physical/access/condition; dangerous=True when stated
  FIN_    — financial; dangerous=False
  DATA_   — missing or unresolved evidence; dangerous=False
  PROC_   — procedural; dangerous=False
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ─── Outcome ────────────────────────────────────────────────────────────────

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"          # evidence missing; cannot evaluate; stay in research
NOT_APPLICABLE = "n/a"       # policy-authorised; e.g. owner has a professional on retainer


@dataclass
class Requirement:
    key: str
    outcome: str                            # PASS | FAIL | UNKNOWN | NOT_APPLICABLE
    code: str                               # stable reason code; never changes meaning
    message: str                            # human-readable explanation
    dangerous: bool = False                 # FAIL on this blocks finalist, not just research
    evidence_ids: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    next_evidence_needed: str | None = None


@dataclass
class Decision:
    state: str                              # "research" | "finalist" | "bid_ready"
    requirements: list[Requirement]
    provenance: dict[str, Any]             # policy_version, evaluated_at, evidence_snapshot_ids
    unresolved_conflicts: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Requirement]:
        return [r for r in self.requirements if r.outcome in (FAIL, UNKNOWN) and r.dangerous]

    @property
    def fails(self) -> list[Requirement]:
        return [r for r in self.requirements if r.outcome == FAIL]

    @property
    def unknowns(self) -> list[Requirement]:
        return [r for r in self.requirements if r.outcome == UNKNOWN]


# ─── Policy defaults ────────────────────────────────────────────────────────

DEFAULT_POLICY = {
    # Coastal preference (PREF_COASTAL): the owner's rule.
    "sea_max_km": 10.0,
    # Floor area range that qualifies as a residential unit.
    "floor_min_m2": 40,
    "floor_max_m2": 250,
    # Climate grade required (PREF_CLIMATE): "good" or "excellent".
    "min_climate_grade": "good",
    # Hazard layers that must have coverage before a finalist is confirmed.
    # Any layer listed here and absent causes a DATA_HAZARD_MISSING UNKNOWN result.
    "required_hazard_layers": ("flood_m", "fire_danger"),
    # Hazard severity levels that block regardless of preferences.
    "reject_flood_m": 1.0,           # JRC 100-year flood depth at the building
    "reject_fire_days": 90,           # days/year with FWI > 30 by 2079-2098 RCP4.5
    # Discount threshold above which an anomaly investigation is required.
    "anomaly_discount_pct": 70.0,
    # Minimum monetary margin for a resale scenario to pass FIN_MARGIN.
    # None = not enforced yet (calibration pending per audit §7).
    "min_resale_margin_eur": None,
    # Minimum available-liquidity reserve after all project costs.
    "min_liquidity_reserve_eur": None,
    # Location capabilities required for finalist building-level checks.
    "finalist_location_capabilities": ("building",),
}


# ─── Evidence helpers ────────────────────────────────────────────────────────

def _geo(evidence: dict) -> dict:
    raw = evidence.get("raw_json") or {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    geo = raw.get("geo") if isinstance(raw, dict) else {}
    return geo if isinstance(geo, dict) else {}


def _climate(evidence: dict) -> dict | None:
    c = evidence.get("climate")
    return c if isinstance(c, dict) else None


def _beach(evidence: dict) -> dict | None:
    b = evidence.get("beach")
    return b if isinstance(b, dict) else None


# ─── Individual requirement checks ──────────────────────────────────────────

def _req_residential_identity(evidence: dict, policy: dict) -> Requirement:
    """The legal unit has a confirmed residential identity (not speculative)."""
    ident = evidence.get("property_identity") or {}
    if not ident:
        return Requirement(
            key="residential_identity", outcome=UNKNOWN,
            code="DATA_IDENTITY_MISSING",
            message="No confirmed legal property identity linked to this listing.",
            next_evidence_needed="Confirm legal unit from registry or catastro.",
        )
    unit_type = ident.get("unit_type")
    if unit_type is None or unit_type == "unknown":
        return Requirement(
            key="residential_identity", outcome=UNKNOWN,
            code="DATA_IDENTITY_TYPE_UNKNOWN",
            message="Legal unit type is not established.",
            next_evidence_needed="Confirm the legal unit type from registry or catastro.",
        )
    if unit_type not in ("residential", "mixed"):
        return Requirement(
            key="residential_identity", outcome=FAIL,
            code="LEGAL_NOT_RESIDENTIAL",
            message=f"Legal unit type is '{unit_type}', not residential.",
            dangerous=True,
        )
    return Requirement(
        key="residential_identity", outcome=PASS,
        code="LEGAL_IDENTITY_OK",
        message="Residential legal identity confirmed.",
        evidence_ids=ident.get("evidence_ids") or [],
    )


def _req_full_ownership(evidence: dict, policy: dict) -> Requirement:
    """Screen the resolved interest sold; do not independently verify title.

    Only exact 'full' satisfies ownership scope. Missing/malformed inputs remain
    blockers. Title and charges are evaluated by a separate requirement.
    """
    ownership = evidence.get("ownership_type")
    if ownership is None:
        return Requirement(
            key="full_ownership", outcome=UNKNOWN,
            code="DATA_OWNERSHIP_MISSING",
            message="Ownership interest being sold is not determined.",
            dangerous=True,
            next_evidence_needed=(
                "Confirm the rights sold for this specific lot from the sale "
                "notice and appropriate supporting documents."
            ),
        )
    if ownership in ("share", "usufruct", "timeshare", "co_ownership"):
        return Requirement(
            key="full_ownership", outcome=FAIL,
            code="LEGAL_PARTIAL_OWNERSHIP",
            message=f"Resolved sale interest is {ownership}, not full ownership.",
            dangerous=True,
        )
    if ownership == "bare_ownership":
        return Requirement(
            key="full_ownership", outcome=FAIL,
            code="LEGAL_BARE_OWNERSHIP",
            message="Resolved sale interest is bare ownership, not full ownership.",
            dangerous=True,
        )
    if ownership != "full":
        return Requirement(
            key="full_ownership", outcome=UNKNOWN,
            code="DATA_OWNERSHIP_UNRECOGNISED",
            message="Ownership input is not a recognised resolved interest.",
            dangerous=True,
            next_evidence_needed=(
                "Resolve the rights sold for this specific lot from the sale "
                "notice and appropriate supporting documents; do not assume full ownership."
            ),
        )
    return Requirement(
        key="full_ownership", outcome=PASS,
        code="LEGAL_OWNERSHIP_OK",
        message=(
            "Resolved input states full ownership is being sold. "
            "This does not independently verify title or retained charges."
        ),
    )


def _req_possession(evidence: dict, policy: dict) -> Requirement:
    """Screen resolved occupancy and delivery without independently verifying them.

    Adverse tenant states retain the existing conservative exclusion. Explicit
    unrecognised delivery cannot be ignored merely because occupancy is vacant.
    """
    occupation = evidence.get("occupation")
    delivery = evidence.get("delivery_terms")
    if occupation == "unknown":
        occupation = None
    if delivery == "unknown":
        delivery = None

    if delivery == "with_tenant" or occupation == "tenant":
        return Requirement(
            key="possession", outcome=FAIL,
            code="PHYS_TENANT_NO_VACANT_POSSESSION",
            message=(
                "Resolved evidence indicates a tenant or delivery with a tenant. "
                "The conservative possession policy excludes this state, "
                "including a separate claim of vacant delivery."
            ),
            dangerous=True,
        )
    if occupation == "occupied" and delivery != "vacant_on_completion":
        return Requirement(
            key="possession", outcome=FAIL,
            code="PHYS_OCCUPIED_NO_DELIVERY",
            message="Property is classified as occupied without resolved vacant delivery on completion.",
            dangerous=True,
        )
    if delivery is not None and delivery != "vacant_on_completion":
        return Requirement(
            key="possession", outcome=UNKNOWN,
            code="DATA_DELIVERY_UNRECOGNISED",
            message="Delivery input is not a recognised resolved possession condition.",
            dangerous=True,
            next_evidence_needed=(
                "Resolve delivery conditions for this specific lot from the sale "
                "terms and appropriate supporting evidence."
            ),
        )
    if occupation is not None and occupation not in ("vacant", "occupied"):
        return Requirement(
            key="possession", outcome=UNKNOWN,
            code="DATA_POSSESSION_UNRECOGNISED",
            message="Occupancy input is not a recognised resolved state.",
            dangerous=True,
            next_evidence_needed=(
                "Resolve current occupancy for this specific lot; a delivery "
                "claim does not resolve malformed occupancy evidence."
            ),
        )
    if delivery == "vacant_on_completion":
        if occupation == "occupied":
            message = (
                "Occupied now; resolved delivery input states vacant possession "
                "on completion. This evaluator does not independently verify the promise."
            )
        elif occupation == "vacant":
            message = (
                "Resolved inputs state current vacancy and vacant delivery on completion. "
                "This evaluator does not independently verify either assertion."
            )
        else:
            message = (
                "Current occupancy is unknown; resolved delivery input states vacant "
                "possession on completion. The promise requires supporting verification."
            )
        return Requirement(
            key="possession", outcome=PASS,
            code="PHYS_POSSESSION_OK", message=message,
        )
    if occupation == "vacant":
        return Requirement(
            key="possession", outcome=PASS,
            code="PHYS_POSSESSION_OK",
            message=(
                "Current occupancy is classified as vacant; delivery terms remain unresolved. "
                "This satisfies the existing vacancy-only policy, not verified vacant delivery."
            ),
        )
    return Requirement(
        key="possession", outcome=UNKNOWN,
        code="DATA_POSSESSION_MISSING",
        message="Current occupancy and delivery conditions are unresolved.",
        dangerous=True,
        next_evidence_needed=(
            "Confirm current occupancy and completion/delivery conditions "
            "for this specific lot from appropriate evidence."
        ),
    )


def _req_title_and_charges(evidence: dict, policy: dict) -> Requirement:
    """Title is clear or retained charges are quantified and acceptable."""
    title_status = evidence.get("title_status")   # "clear" | "encumbered" | "unknown" | None
    if title_status is None:
        return Requirement(
            key="title_and_charges", outcome=UNKNOWN,
            code="DATA_TITLE_MISSING",
            message="Title status and retained charges not verified.",
            dangerous=True,
            next_evidence_needed="Obtain land registry certificate (certidão permanente).",
        )
    if title_status == "encumbered":
        charges = evidence.get("retained_charges_eur")
        if charges is None:
            return Requirement(
                key="title_and_charges", outcome=UNKNOWN,
                code="DATA_CHARGES_UNQUANTIFIED",
                message="Property has encumbrances; retained charges not quantified.",
                dangerous=True,
                next_evidence_needed="Quantify retained charges from registry certificate.",
            )
    if title_status == "defective":
        return Requirement(
            key="title_and_charges", outcome=FAIL,
            code="LEGAL_TITLE_DEFECTIVE",
            message="Title is defective.",
            dangerous=True,
        )
    if title_status == "unknown":
        return Requirement(
            key="title_and_charges", outcome=UNKNOWN,
            code="DATA_TITLE_STATUS_UNKNOWN",
            message="Title status is explicitly unknown; registry check needed.",
            dangerous=True,
            next_evidence_needed="Obtain land registry certificate (certidão permanente).",
        )
    if title_status not in ("clear", "encumbered"):
        # Unrecognised value — fail closed.
        return Requirement(
            key="title_and_charges", outcome=UNKNOWN,
            code="DATA_TITLE_UNRECOGNISED",
            message=f"Title status {title_status!r} is not a recognised value.",
            dangerous=True,
            next_evidence_needed="Confirm title status from land registry.",
        )
    return Requirement(
        key="title_and_charges", outcome=PASS,
        code="LEGAL_TITLE_OK",
        message="Title verified; any charges quantified.",
    )


def _req_legal_access(evidence: dict, policy: dict) -> Requirement:
    """Screen resolved lawful access, not map proximity or physical reachability.

    The evaluator does not independently establish a right of passage. Missing
    or unrecognised legal-access evidence is a critical acquisition blocker.
    """
    access = evidence.get("legal_access")
    needed = (
        "Establish the lawful access basis for this specific lot and the "
        "relevant restrictions using appropriate documentary evidence or "
        "qualified local verification. A visible route or physical inspection "
        "alone does not establish a right of passage."
    )
    if access is None or access == "unknown":
        return Requirement(
            key="legal_access", outcome=UNKNOWN,
            code="DATA_ACCESS_MISSING",
            message="Lawful access to this specific lot is unresolved.",
            dangerous=True,
            next_evidence_needed=needed,
        )
    if access == "denied":
        return Requirement(
            key="legal_access", outcome=FAIL,
            code="PHYS_LANDLOCKED",
            message=(
                "Resolved legal-access input is denied. Physical reachability "
                "does not override this adverse classification."
            ),
            dangerous=True,
        )
    if access != "confirmed":
        return Requirement(
            key="legal_access", outcome=UNKNOWN,
            code="DATA_ACCESS_UNRECOGNISED",
            message="Legal-access input is not a recognised resolved state.",
            dangerous=True,
            next_evidence_needed=needed,
        )
    return Requirement(
        key="legal_access", outcome=PASS,
        code="PHYS_ACCESS_OK",
        message=(
            "Resolved input states lawful access is confirmed. This evaluator "
            "does not independently verify access rights, restrictions or "
            "physical suitability for the intended use."
        ),
    )


def _req_location_building(evidence: dict, policy: dict) -> Requirement:
    """Building-level location independently confirmed (not street-centre or blurred)."""
    geo = _geo(evidence)
    precision = geo.get("precision") or evidence.get("location_precision")
    # "sale" means the pin is from the sale/court notice — considered building-level.
    # "building" and "sale" pass; "street", "advertised", "parish" etc. do not.
    building_precisions = {"sale", "building", "cadastre", "verified"}
    if precision is None:
        return Requirement(
            key="location_building", outcome=UNKNOWN,
            code="DATA_LOCATION_UNKNOWN",
            message="Building location not established.",
            next_evidence_needed="Resolve building coordinates from cadastre or verified source.",
        )
    if precision not in building_precisions:
        return Requirement(
            key="location_building", outcome=UNKNOWN,
            code="DATA_LOCATION_NOT_BUILDING",
            message=f"Location precision '{precision}' is not building-level.",
            next_evidence_needed=f"Upgrade location from {precision!r} to building level.",
        )
    return Requirement(
        key="location_building", outcome=PASS,
        code="DATA_LOCATION_OK",
        message=f"Building-level location confirmed (precision: {precision}).",
    )


def _req_coastal(evidence: dict, policy: dict) -> Requirement:
    """Sea within configured maximum distance."""
    sea_max_km = policy.get("sea_max_km", DEFAULT_POLICY["sea_max_km"])
    beach = _beach(evidence)
    if beach is None:
        return Requirement(
            key="coastal", outcome=UNKNOWN,
            code="DATA_BEACH_UNKNOWN",
            message="Sea distance not established.",
            next_evidence_needed="Establish sea distance from verified building location.",
        )
    km = beach.get("km")
    approx = beach.get("approx", False)
    if km is None or approx:
        return Requirement(
            key="coastal", outcome=UNKNOWN,
            code="DATA_BEACH_APPROX",
            message="Sea distance is approximate or not measured from the building.",
            next_evidence_needed="Establish precise sea distance from building-level pin.",
        )
    if km > sea_max_km:
        return Requirement(
            key="coastal", outcome=FAIL,
            code="PREF_NOT_COASTAL",
            message=f"Sea is {km:.1f} km away, beyond the {sea_max_km:.0f} km preference.",
            dangerous=False,
        )
    return Requirement(
        key="coastal", outcome=PASS,
        code="PREF_COASTAL_OK",
        message=f"Sea within {sea_max_km:.0f} km ({km:.1f} km).",
    )


def _req_floor_area(evidence: dict, policy: dict) -> Requirement:
    """Residential floor area between configured limits."""
    floor_min = policy.get("floor_min_m2", DEFAULT_POLICY["floor_min_m2"])
    floor_max = policy.get("floor_max_m2", DEFAULT_POLICY["floor_max_m2"])
    area_m2 = evidence.get("floor_m2")
    area_source = evidence.get("floor_source")    # "notice" | "catastro" | "stored" | None
    accepted_sources = {"notice", "catastro", "stored"}
    if area_m2 is None or area_source not in accepted_sources:
        return Requirement(
            key="floor_area", outcome=UNKNOWN,
            code="DATA_FLOOR_UNKNOWN",
            message="Residential floor area not resolved from an accepted source.",
            next_evidence_needed="Resolve floor area from sale notice or catastro record.",
        )
    if area_m2 < floor_min:
        return Requirement(
            key="floor_area", outcome=FAIL,
            code="PREF_FLOOR_TOO_SMALL",
            message=f"Floor {area_m2:.0f} m² < {floor_min} m² minimum.",
        )
    if area_m2 > floor_max:
        return Requirement(
            key="floor_area", outcome=FAIL,
            code="PREF_FLOOR_TOO_LARGE",
            message=f"Floor {area_m2:.0f} m² > {floor_max} m² maximum.",
        )
    return Requirement(
        key="floor_area", outcome=PASS,
        code="PREF_FLOOR_OK",
        message=f"Floor area {area_m2:.0f} m² from {area_source}.",
    )


def _req_hazard_coverage(evidence: dict, policy: dict) -> Requirement:
    """Require usable flood and fire metrics; adverse facts outrank missing data."""
    import math

    climate = _climate(evidence)
    required = policy.get("required_hazard_layers", DEFAULT_POLICY["required_hazard_layers"])
    reject_flood = policy.get("reject_flood_m", DEFAULT_POLICY["reject_flood_m"])
    reject_fire = policy.get("reject_fire_days", DEFAULT_POLICY["reject_fire_days"])
    if climate is None:
        return Requirement(key="hazard_coverage", outcome=UNKNOWN, code="DATA_HAZARD_NO_CLIMATE",
                           message="No climate data; hazard layers cannot be evaluated.",
                           next_evidence_needed="Run climate assessment for the building's position.")

    def number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and value >= 0 else None

    flood = number(climate.get("flood_m"))
    danger = climate.get("fire_danger")
    days = None
    if isinstance(danger, dict):
        for key in ("high_days_2090", "rcp45_2079-2098"):
            if key in danger:
                days = number(danger[key])
                # A present but invalid canonical metric must not be rescued by
                # an older contradictory metric. It remains unresolved.
                break
    if days is None and not (isinstance(danger, dict)
                             and any(k in danger for k in ("high_days_2090", "rcp45_2079-2098"))):
        days = number(climate.get("fire_danger_days"))

    if flood is not None and flood >= reject_flood:
        return Requirement(key="hazard_coverage", outcome=FAIL, code="PHYS_FLOOD_SEVERE",
                           message=f"JRC 100-year flood depth {flood:.1f} m at building "
                                   f"(reject threshold {reject_flood:.1f} m).", dangerous=True)
    if days is not None and days >= reject_fire:
        return Requirement(key="hazard_coverage", outcome=FAIL, code="PHYS_FIRE_SEVERE",
                           message=f"Fire danger {days:.0f} days/year at building "
                                   f"(reject threshold {reject_fire}).", dangerous=True)

    missing = []
    for layer in required:
        value = climate.get(layer)
        if layer == "flood_m":
            absent = flood is None
        elif layer == "fire_danger":
            absent = days is None
        else:
            absent = value is None or value in ({}, [], "")
        if absent:
            missing.append(layer)
    if missing:
        return Requirement(key="hazard_coverage", outcome=UNKNOWN, code="DATA_HAZARD_MISSING",
                           message=f"Required hazard layers missing or invalid: {', '.join(missing)}.",
                           next_evidence_needed=f"Obtain usable data for: {', '.join(missing)}.")
    return Requirement(key="hazard_coverage", outcome=PASS, code="DATA_HAZARD_OK",
                       message="Required hazard metrics present; no severe hazard at building.")


def _req_sale_availability(evidence: dict, policy: dict) -> Requirement:
    """The specific sale is confirmed available (not withdrawn, ended, or unverified)."""
    avail = evidence.get("sale_availability")   # "available" | "withdrawn" | "ended" | "unknown" | None
    avail_checked_at = evidence.get("sale_availability_checked_at")
    if avail is None or avail == "unknown":
        return Requirement(
            key="sale_availability", outcome=UNKNOWN,
            code="DATA_AVAILABILITY_UNKNOWN",
            message="Sale availability not confirmed from a source-specific check.",
            next_evidence_needed="Re-check sale availability from the source's own sale page.",
        )
    if avail in ("withdrawn", "ended"):
        return Requirement(
            key="sale_availability", outcome=FAIL,
            code="PROC_SALE_ENDED",
            message=f"Sale status: {avail}.",
        )
    if avail in ("reserved",):
        # Reserved is an adverse state — not available for a new bid.
        return Requirement(
            key="sale_availability", outcome=FAIL,
            code="PROC_SALE_RESERVED",
            message="Sale is reserved; not available for a new bid.",
        )
    if avail in ("blocked", "error"):
        # Network error or challenge page — cannot confirm availability; do not retain an old PASS.
        return Requirement(
            key="sale_availability", outcome=UNKNOWN,
            code="DATA_AVAILABILITY_CHECK_FAILED",
            message=f"Availability check result: {avail}. Source-specific confirmation required.",
            next_evidence_needed="Re-check sale availability from the source's own sale page.",
        )
    if avail != "available":
        # Unrecognised value — fail closed.
        return Requirement(
            key="sale_availability", outcome=UNKNOWN,
            code="DATA_AVAILABILITY_UNRECOGNISED",
            message=f"Sale availability value {avail!r} is not recognised.",
            next_evidence_needed="Re-check sale availability from the source's own sale page.",
        )
    return Requirement(
        key="sale_availability", outcome=PASS,
        code="PROC_AVAILABILITY_OK",
        message="Sale confirmed available.",
        expires_at=avail_checked_at,
    )


def _req_anomaly_investigation(evidence: dict, policy: dict) -> Requirement:
    """Extreme discounts must be investigated before financial evaluation proceeds."""
    discount_pct = evidence.get("apparent_discount_pct")   # float or None
    anomaly_threshold = policy.get("anomaly_discount_pct", DEFAULT_POLICY["anomaly_discount_pct"])
    if discount_pct is None:
        # No discount calculable yet — not a blocker at this stage
        return Requirement(
            key="anomaly_investigation", outcome=PASS,
            code="FIN_DISCOUNT_NOT_COMPUTED",
            message="Discount not yet computed (no benchmark).",
        )
    if discount_pct >= anomaly_threshold:
        investigated = evidence.get("anomaly_investigated", False)
        if not investigated:
            return Requirement(
                key="anomaly_investigation", outcome=UNKNOWN,
                code="FIN_ANOMALY_PENDING",
                message=(
                    f"Apparent discount {discount_pct:.0f}% exceeds the {anomaly_threshold:.0f}% "
                    "investigation threshold. Verify: price semantics, full ownership, occupancy, "
                    "correct floor, condition, access and benchmark relevance."
                ),
                next_evidence_needed=(
                    "Investigate anomaly: confirm price is for full ownership of this specific "
                    "unit, not a share or a placeholder."
                ),
            )
    return Requirement(
        key="anomaly_investigation", outcome=PASS,
        code="FIN_DISCOUNT_OK",
        message=(
            f"Discount {discount_pct:.0f}% within normal range."
            if discount_pct is not None else "Anomaly investigation not required."
        ),
    )


#: Valuation evidence types that support a finalist.  Anything else fails closed.
SUPPORTED_VALUATION_SOURCES = frozenset({"comparables", "appraisal", "city_index"})


def _req_financial_evidence(evidence: dict, policy: dict) -> Requirement:
    """Supported valuation evidence exists (not just a municipal median)."""
    val_source = evidence.get("valuation_source")
    # "municipal_median" alone is not sufficient for a finalist.
    # "comparables", "appraisal", "city_index" from a non-fallback source pass.
    if val_source is None:
        return Requirement(
            key="financial_evidence", outcome=UNKNOWN,
            code="DATA_VALUATION_MISSING",
            message="No valuation evidence; cannot assess financial case.",
            next_evidence_needed="Assemble comparable transactions or a supported valuation.",
        )
    if val_source == "municipal_median_fallback":
        return Requirement(
            key="financial_evidence", outcome=UNKNOWN,
            code="DATA_VALUATION_FALLBACK",
            message="Valuation relies only on a fallback city median, not property-specific comparables.",
            next_evidence_needed="Obtain transaction comparables matched by type, size and micro-location.",
        )
    if val_source not in SUPPORTED_VALUATION_SOURCES:
        return Requirement(
            key="financial_evidence", outcome=UNKNOWN,
            code="DATA_VALUATION_UNRECOGNISED",
            message=f"Valuation source {val_source!r} is not a supported evidence type.",
            next_evidence_needed="Assemble comparable transactions or a supported valuation.",
        )
    return Requirement(
        key="financial_evidence", outcome=PASS,
        code="FIN_VALUATION_OK",
        message=f"Valuation evidence from: {val_source}.",
    )


def _req_financial_margin(evidence: dict, policy: dict) -> Requirement:
    """Conservative margin is positive (or policy minimum not yet set)."""
    min_margin = policy.get("min_resale_margin_eur", DEFAULT_POLICY["min_resale_margin_eur"])
    if min_margin is None:
        # Policy not set yet — cannot evaluate.
        return Requirement(
            key="financial_margin", outcome=UNKNOWN,
            code="DATA_MARGIN_POLICY_UNSET",
            message="Minimum resale margin threshold not configured in policy.",
            next_evidence_needed="Set min_resale_margin_eur in policy after calibration.",
        )
    margin = evidence.get("conservative_margin_eur")
    if margin is None:
        return Requirement(
            key="financial_margin", outcome=UNKNOWN,
            code="DATA_MARGIN_NOT_COMPUTED",
            message="Conservative margin not computed.",
            next_evidence_needed="Complete financial scenario with after-work comparables.",
        )
    if margin < min_margin:
        return Requirement(
            key="financial_margin", outcome=FAIL,
            code="FIN_MARGIN_INSUFFICIENT",
            message=f"Conservative margin €{margin:,.0f} < policy minimum €{min_margin:,.0f}.",
        )
    return Requirement(
        key="financial_margin", outcome=PASS,
        code="FIN_MARGIN_OK",
        message=f"Conservative margin €{margin:,.0f} meets policy.",
    )


def _req_photo_suitability(evidence: dict, policy: dict) -> Requirement:
    """At least one listing photo shows the dwelling itself (not just a plot or a view).

    Task 10: missing shows_house (None/absent from the photo_check payload) must remain
    UNKNOWN, not treated as True.  An explicit False means photos confirmed as not showing
    the house — UNKNOWN until retaken.  Only an explicit True from a model run counts.
    """
    pc = evidence.get("photo_check") or {}
    if not isinstance(pc, dict) or not pc:
        return Requirement(
            key="photo_suitability", outcome=UNKNOWN,
            code="DATA_PHOTO_MISSING",
            message="No photo check has been run for this listing.",
            next_evidence_needed="Run photo check to confirm at least one photo shows the dwelling.",
        )
    shows_house = pc.get("shows_house")
    if shows_house is None:
        return Requirement(
            key="photo_suitability", outcome=UNKNOWN,
            code="DATA_PHOTO_SHOWS_HOUSE_UNKNOWN",
            message="Photo check ran but shows_house attribution is missing.",
            next_evidence_needed="Re-run photo check; model must explicitly report shows_house.",
        )
    if not (isinstance(shows_house, bool) and shows_house is True):
        # Only a real boolean True counts; strings ("true"), ints, lists do not.
        return Requirement(
            key="photo_suitability", outcome=UNKNOWN,
            code="DATA_PHOTO_NO_DWELLING",
            message="No photo shows the dwelling; condition cannot be assessed from photos.",
            next_evidence_needed="Obtain photos of the dwelling itself before assessment.",
        )
    return Requirement(
        key="photo_suitability", outcome=PASS,
        code="DATA_PHOTO_OK",
        message="At least one photo confirmed showing the dwelling.",
    )


def _req_cash_fit(evidence: dict, policy: dict) -> Requirement:
    """Total project cost (including required reserve) fits available cash."""
    cash = evidence.get("available_cash_eur")
    total_project = evidence.get("total_project_cost_eur")   # acquisition + taxes + works + fees + reserve
    min_reserve = policy.get("min_liquidity_reserve_eur", DEFAULT_POLICY["min_liquidity_reserve_eur"])
    if cash is None:
        return Requirement(
            key="cash_fit", outcome=UNKNOWN,
            code="DATA_CASH_NOT_SET",
            message="Available cash not configured.",
            next_evidence_needed="Set cash_on_hand in configuration.",
        )
    if total_project is None:
        return Requirement(
            key="cash_fit", outcome=UNKNOWN,
            code="DATA_TOTAL_COST_UNKNOWN",
            message="Total project cost (including reserve) not computed.",
            next_evidence_needed="Complete financial scenario with all cost components.",
        )
    reserve = min_reserve or 0
    if total_project + reserve > cash:
        return Requirement(
            key="cash_fit", outcome=FAIL,
            code="FIN_OVER_CASH",
            message=(
                f"Total project cost €{total_project:,.0f}"
                + (f" + reserve €{reserve:,.0f}" if reserve else "")
                + f" exceeds available cash €{cash:,.0f}."
            ),
        )
    return Requirement(
        key="cash_fit", outcome=PASS,
        code="FIN_CASH_OK",
        message=f"Project fits within available cash €{cash:,.0f}.",
    )


# ─── Main evaluator ─────────────────────────────────────────────────────────

#: The ordered list of requirement checks.  Later ones may be skipped if an
#: earlier hard failure already makes the state clear; but all are run by
#: default so the full picture is available for the research panel.
_REQUIREMENTS = [
    _req_residential_identity,
    _req_full_ownership,
    _req_possession,
    _req_title_and_charges,
    _req_legal_access,
    _req_location_building,
    _req_coastal,
    _req_floor_area,
    _req_hazard_coverage,
    _req_photo_suitability,
    _req_sale_availability,
    _req_anomaly_investigation,
    _req_financial_evidence,
    _req_financial_margin,
    _req_cash_fit,
]


def evaluate(
    evidence: dict,
    policy: dict | None = None,
    now: datetime | None = None,
) -> Decision:
    """Pure eligibility evaluation for a Live and Invest candidate.

    Parameters
    ----------
    evidence:
        Flat dict of resolved evidence for this listing.  Keys are documented
        on each _req_* function above.  Unrecognised keys are ignored.
    policy:
        Override any DEFAULT_POLICY threshold.  None uses all defaults.
    now:
        Evaluation timestamp.  None uses the current UTC time.  Passed
        through to provenance; not used for expiry decisions yet.

    Returns
    -------
    Decision with state "research", "finalist" or "bid_ready" and the full
    requirement list.  Missing critical evidence always yields "research".
    """
    if now is None:
        from datetime import timezone
        now = datetime.now(tz=timezone.utc)
    pol = {**DEFAULT_POLICY, **(policy or {})}

    reqs = [check(evidence, pol) for check in _REQUIREMENTS]

    # State machine:
    # - Any FAIL on a dangerous requirement → research (cannot be finalist)
    # - Any UNKNOWN on a dangerous requirement → research
    # - All requirements PASS or N/A → finalist (bid_ready requires additional
    #   procedural checks outside this function's current scope)
    dangerous_unmet = [r for r in reqs if r.outcome in (FAIL, UNKNOWN) and r.dangerous]
    any_fail = any(r.outcome == FAIL for r in reqs)
    any_unknown = any(r.outcome == UNKNOWN for r in reqs)

    if dangerous_unmet:
        state = "research"
    elif any_fail:
        state = "research"
    elif any_unknown:
        state = "research"
    else:
        state = "finalist"
        # bid_ready promotion is a separate procedural gate not yet implemented.

    return Decision(
        state=state,
        requirements=reqs,
        provenance={
            "evaluated_at": now.isoformat(),
            "policy_keys": sorted(pol.keys()),
            "milestone": "A-shadow",
            # evidence_revs supplied by caller (e.g. db.get_evidence_revisions) so
            # the decision record captures which revision of each evidence piece was
            # used — stale decisions can be detected without re-evaluating (F16).
            "evidence_revs": evidence.get("_evidence_revs") or {},
        },
    )
