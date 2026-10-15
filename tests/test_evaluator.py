"""
Tests for evaluator.py — pure typed eligibility evaluator (Milestone A, shadow mode).

Covers:
- Each requirement in isolation (pass / fail / unknown)
- State machine: research vs finalist
- Missing critical evidence blocks finalist even when everything else passes
- Default policy applied when policy arg is None
- Policy overrides work
- Boundary values for sea_max_km and floor area
- No side-effects: repeated calls are idempotent
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from evaluator import (
    FAIL, NOT_APPLICABLE, PASS, UNKNOWN,
    Decision, Requirement,
    evaluate,
)

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)

# ─── Minimal evidence that satisfies every requirement with defaults ─────────

FULL_EVIDENCE = {
    # Identity
    "property_identity": {"unit_type": "residential", "evidence_ids": ["ident:1"]},
    "ownership_type": "full",
    # Possession
    "occupation": "vacant",
    "delivery_terms": "vacant_on_completion",
    # Title
    "title_status": "clear",
    # Access
    "legal_access": "confirmed",
    # Location — building level
    "raw_json": json.dumps({"lat": 37.1, "lon": -8.6, "precision": "sale"}),
    "location_precision": "sale",
    # Coastal
    "beach": {"km": 3.0, "approx": False, "name": "Praia", "text": "3.0 km"},
    # Floor area
    "floor_m2": 80,
    "floor_source": "catastro",
    # Hazards — all required layers present, non-severe values
    "climate": {
        "flood_m": 0.0,
        "fire_danger": {"rcp45_2079-2098": 10},
        "hot_days": {"rcp45_2071-2100": 3},
    },
    # Photo suitability (Task 10)
    "photo_check": {"shows_house": True, "condition": "good", "confidence": "high", "notes": ""},
    # Sale availability
    "sale_availability": "available",
    # Financial
    "apparent_discount_pct": 30.0,
    "anomaly_investigated": False,        # not needed at 30%
    "valuation_source": "comparables",
    "conservative_margin_eur": None,      # policy not set → UNKNOWN, but not dangerous
    "available_cash_eur": None,           # policy not set → UNKNOWN, not dangerous
    "total_project_cost_eur": None,
}


def _eval(overrides: dict | None = None, policy: dict | None = None) -> Decision:
    ev = {**FULL_EVIDENCE, **(overrides or {})}
    return evaluate(ev, policy=policy, now=NOW)


# ─── Finalist / research state ───────────────────────────────────────────────

class TestState:
    def test_all_pass_yields_finalist_when_margin_and_cash_policy_unset(self):
        # With default policy (min_resale_margin_eur=None, min_liquidity_reserve_eur=None),
        # financial_margin and cash_fit both yield UNKNOWN (not dangerous).
        # The evaluator should reach "finalist" if no dangerous unknowns/fails exist.
        # BUT financial_margin and cash_fit are UNKNOWN → state stays "research".
        dec = _eval()
        assert dec.state == "research"
        unknowns = {r.key for r in dec.unknowns}
        assert "financial_margin" in unknowns
        assert "cash_fit" in unknowns

    def test_all_pass_with_complete_financial_yields_finalist(self):
        dec = _eval(
            {
                "conservative_margin_eur": 20_000,
                "available_cash_eur": 200_000,
                "total_project_cost_eur": 150_000,
            },
            policy={
                "min_resale_margin_eur": 15_000,
                "min_liquidity_reserve_eur": 0,
            },
        )
        assert dec.state == "finalist"
        assert dec.fails == []
        assert dec.unknowns == []

    def test_dangerous_fail_blocks_finalist(self):
        dec = _eval({"ownership_type": "share"})
        assert dec.state == "research"
        fail_codes = {r.code for r in dec.fails}
        assert "LEGAL_PARTIAL_OWNERSHIP" in fail_codes

    def test_dangerous_unknown_blocks_finalist(self):
        dec = _eval({"title_status": None})
        assert dec.state == "research"
        unk_codes = {r.code for r in dec.unknowns}
        assert "DATA_TITLE_MISSING" in unk_codes

    def test_non_dangerous_fail_yields_research(self):
        # Coastal fail is not dangerous — but still goes to research because any fail → research.
        dec = _eval({"beach": {"km": 50.0, "approx": False, "name": "x", "text": "50 km"}},
                    policy={"min_resale_margin_eur": 1, "min_liquidity_reserve_eur": 0})
        assert dec.state == "research"
        fail_codes = {r.code for r in dec.fails}
        assert "PREF_NOT_COASTAL" in fail_codes


# ─── Residential identity ─────────────────────────────────────────────────────

class TestResidentialIdentity:
    def test_pass(self):
        r = _req("residential_identity", _eval())
        assert r.outcome == PASS

    def test_missing_yields_unknown(self):
        dec = _eval({"property_identity": None})
        r = _req("residential_identity", dec)
        assert r.outcome == UNKNOWN
        assert r.code == "DATA_IDENTITY_MISSING"

    def test_commercial_yields_fail(self):
        dec = _eval({"property_identity": {"unit_type": "commercial"}})
        r = _req("residential_identity", dec)
        assert r.outcome == FAIL
        assert r.code == "LEGAL_NOT_RESIDENTIAL"
        assert r.dangerous

    def test_mixed_passes(self):
        dec = _eval({"property_identity": {"unit_type": "mixed"}})
        r = _req("residential_identity", dec)
        assert r.outcome == PASS


# ─── Full ownership ───────────────────────────────────────────────────────────

class TestFullOwnership:
    def test_pass(self):
        assert _req("full_ownership", _eval()).outcome == PASS

    def test_usufruct_fails(self):
        dec = _eval({"ownership_type": "usufruct"})
        r = _req("full_ownership", dec)
        assert r.outcome == FAIL and r.dangerous

    def test_missing_yields_unknown(self):
        dec = _eval({"ownership_type": None})
        r = _req("full_ownership", dec)
        assert r.outcome == UNKNOWN and "DATA_OWNERSHIP_MISSING" == r.code

    @pytest.mark.parametrize("val", ["", "unknown", "nonempty_unrecognised", "partial", "xyz"])
    def test_unrecognised_ownership_is_unknown_not_pass(self, val):
        """Task 9: nonempty unrecognised ownership value must not fall through to PASS."""
        dec = _eval({"ownership_type": val})
        r = _req("full_ownership", dec)
        assert r.outcome == UNKNOWN, f"ownership={val!r} should be UNKNOWN, got {r.outcome}"
        assert r.dangerous, "unrecognised ownership should be dangerous"


# ─── Possession ───────────────────────────────────────────────────────────────

class TestPossession:
    def test_vacant_passes(self):
        dec = _eval({"occupation": "vacant", "delivery_terms": None})
        r = _req("possession", dec)
        assert r.outcome == PASS

    def test_occupied_no_delivery_fails(self):
        dec = _eval({"occupation": "occupied", "delivery_terms": None})
        r = _req("possession", dec)
        assert r.outcome == FAIL and r.dangerous

    def test_occupied_vacant_on_completion_passes(self):
        dec = _eval({"occupation": "occupied", "delivery_terms": "vacant_on_completion"})
        r = _req("possession", dec)
        assert r.outcome == PASS

    def test_both_missing_yields_unknown(self):
        dec = _eval({"occupation": None, "delivery_terms": None})
        r = _req("possession", dec)
        assert r.outcome == UNKNOWN and r.dangerous

    def test_tenant_without_delivery_fails(self):
        """Task 9: tenant occupation without vacant-on-completion delivery is FAIL, not PASS."""
        dec = _eval({"occupation": "tenant", "delivery_terms": None})
        r = _req("possession", dec)
        assert r.outcome == FAIL, "tenant + no delivery should be FAIL"
        assert r.dangerous

    def test_tenant_with_tenant_delivery_fails(self):
        """Task 9: with_tenant delivery explicitly signals no vacant possession."""
        dec = _eval({"occupation": "tenant", "delivery_terms": "with_tenant"})
        r = _req("possession", dec)
        assert r.outcome == FAIL and r.dangerous

    def test_with_tenant_delivery_alone_fails(self):
        """Task 9: delivery_terms='with_tenant' must fail even without occupation."""
        dec = _eval({"occupation": None, "delivery_terms": "with_tenant"})
        r = _req("possession", dec)
        assert r.outcome == FAIL and r.dangerous

    @pytest.mark.parametrize("occ", ["", "unknown", "xyz"])
    def test_unrecognised_occupation_is_unknown(self, occ):
        """Task 9: unrecognised occupation string must not pass."""
        dec = _eval({"occupation": occ, "delivery_terms": None})
        r = _req("possession", dec)
        assert r.outcome == UNKNOWN, f"occupation={occ!r} should be UNKNOWN"


# ─── Title ────────────────────────────────────────────────────────────────────

class TestTitle:
    def test_clear_passes(self):
        assert _req("title_and_charges", _eval()).outcome == PASS

    def test_encumbered_unquantified_is_unknown(self):
        dec = _eval({"title_status": "encumbered", "retained_charges_eur": None})
        r = _req("title_and_charges", dec)
        assert r.outcome == UNKNOWN and r.dangerous

    def test_encumbered_quantified_passes(self):
        dec = _eval({"title_status": "encumbered", "retained_charges_eur": 5000})
        r = _req("title_and_charges", dec)
        assert r.outcome == PASS

    def test_defective_fails(self):
        dec = _eval({"title_status": "defective"})
        r = _req("title_and_charges", dec)
        assert r.outcome == FAIL and r.dangerous

    def test_unknown_title_status_is_unknown_not_pass(self):
        """Task 9: title_status='unknown' must be UNKNOWN, not PASS (fall-through bug)."""
        dec = _eval({"title_status": "unknown"})
        r = _req("title_and_charges", dec)
        assert r.outcome == UNKNOWN, "title_status='unknown' must not fall through to PASS"
        assert r.dangerous

    @pytest.mark.parametrize("val", ["", "unverified", "xyz"])
    def test_unrecognised_title_status_is_unknown(self, val):
        """Task 9: unrecognised title_status must not pass."""
        dec = _eval({"title_status": val})
        r = _req("title_and_charges", dec)
        assert r.outcome == UNKNOWN, f"title_status={val!r} should be UNKNOWN"


# ─── Location ─────────────────────────────────────────────────────────────────

class TestLocationBuilding:
    def test_sale_precision_passes(self):
        assert _req("location_building", _eval()).outcome == PASS

    def test_street_precision_is_unknown(self):
        dec = _eval({"raw_json": json.dumps({"precision": "street"}), "location_precision": "street"})
        r = _req("location_building", dec)
        assert r.outcome == UNKNOWN
        assert "DATA_LOCATION_NOT_BUILDING" == r.code

    def test_advertised_precision_is_unknown(self):
        dec = _eval({"raw_json": json.dumps({"precision": "advertised"}), "location_precision": "advertised"})
        r = _req("location_building", dec)
        assert r.outcome == UNKNOWN

    def test_building_precision_passes(self):
        dec = _eval({"raw_json": json.dumps({"precision": "building"}), "location_precision": "building"})
        r = _req("location_building", dec)
        assert r.outcome == PASS

    def test_no_precision_is_unknown(self):
        dec = _eval({"raw_json": None, "location_precision": None})
        r = _req("location_building", dec)
        assert r.outcome == UNKNOWN and r.code == "DATA_LOCATION_UNKNOWN"


# ─── Coastal ─────────────────────────────────────────────────────────────────

class TestCoastal:
    def test_within_limit_passes(self):
        dec = _eval({"beach": {"km": 9.9, "approx": False, "name": "x", "text": ""}})
        assert _req("coastal", dec).outcome == PASS

    def test_at_limit_passes(self):
        dec = _eval({"beach": {"km": 10.0, "approx": False, "name": "x", "text": ""}})
        assert _req("coastal", dec).outcome == PASS

    def test_over_limit_fails(self):
        dec = _eval({"beach": {"km": 10.1, "approx": False, "name": "x", "text": ""}})
        r = _req("coastal", dec)
        assert r.outcome == FAIL and not r.dangerous

    def test_approx_distance_is_unknown(self):
        dec = _eval({"beach": {"km": 5.0, "approx": True, "name": "x", "text": ""}})
        r = _req("coastal", dec)
        assert r.outcome == UNKNOWN

    def test_missing_beach_is_unknown(self):
        dec = _eval({"beach": None})
        r = _req("coastal", dec)
        assert r.outcome == UNKNOWN

    def test_policy_override_sea_max(self):
        dec = _eval({"beach": {"km": 15.0, "approx": False, "name": "x", "text": ""}},
                    policy={"sea_max_km": 20})
        r = _req("coastal", dec)
        assert r.outcome == PASS


# ─── Floor area ───────────────────────────────────────────────────────────────

class TestFloorArea:
    def test_pass(self):
        assert _req("floor_area", _eval()).outcome == PASS

    def test_too_small_fails(self):
        dec = _eval({"floor_m2": 39, "floor_source": "catastro"})
        r = _req("floor_area", dec)
        assert r.outcome == FAIL and r.code == "PREF_FLOOR_TOO_SMALL"

    def test_at_minimum_passes(self):
        dec = _eval({"floor_m2": 40, "floor_source": "catastro"})
        assert _req("floor_area", dec).outcome == PASS

    def test_too_large_fails(self):
        dec = _eval({"floor_m2": 300, "floor_source": "stored"})
        r = _req("floor_area", dec)
        assert r.outcome == FAIL and r.code == "PREF_FLOOR_TOO_LARGE"

    def test_unaccepted_source_is_unknown(self):
        dec = _eval({"floor_m2": 80, "floor_source": "advertised"})
        r = _req("floor_area", dec)
        assert r.outcome == UNKNOWN

    def test_missing_area_is_unknown(self):
        dec = _eval({"floor_m2": None, "floor_source": "catastro"})
        r = _req("floor_area", dec)
        assert r.outcome == UNKNOWN


# ─── Hazard coverage (F03) ────────────────────────────────────────────────────

class TestHazardCoverage:
    def test_pass_both_layers_present(self):
        assert _req("hazard_coverage", _eval()).outcome == PASS

    def test_missing_flood_layer_is_unknown(self):
        dec = _eval({"climate": {"fire_danger": {"rcp45_2079-2098": 10}}})
        r = _req("hazard_coverage", dec)
        assert r.outcome == UNKNOWN
        assert "DATA_HAZARD_MISSING" == r.code
        assert "flood_m" in r.message

    def test_missing_fire_layer_is_unknown(self):
        dec = _eval({"climate": {"flood_m": 0.0}})
        r = _req("hazard_coverage", dec)
        assert r.outcome == UNKNOWN and "fire_danger" in r.message

    def test_no_climate_is_unknown(self):
        dec = _eval({"climate": None})
        r = _req("hazard_coverage", dec)
        assert r.outcome == UNKNOWN and r.code == "DATA_HAZARD_NO_CLIMATE"

    def test_severe_flood_fails(self):
        dec = _eval({"climate": {
            "flood_m": 1.5,
            "fire_danger": {"rcp45_2079-2098": 10},
        }})
        r = _req("hazard_coverage", dec)
        assert r.outcome == FAIL and r.dangerous and r.code == "PHYS_FLOOD_SEVERE"

    def test_severe_fire_fails(self):
        dec = _eval({"climate": {
            "flood_m": 0.0,
            "fire_danger": {"rcp45_2079-2098": 120},
        }})
        r = _req("hazard_coverage", dec)
        assert r.outcome == FAIL and r.dangerous and r.code == "PHYS_FIRE_SEVERE"

    def test_partial_climate_no_hazard_data_is_unknown(self):
        # F03: partial grade (hot_days only, no flood/fire) must not yield PASS
        dec = _eval({"climate": {"hot_days": {"rcp45_2071-2100": 3}}})
        r = _req("hazard_coverage", dec)
        assert r.outcome == UNKNOWN


# ─── Sale availability ────────────────────────────────────────────────────────

class TestSaleAvailability:
    def test_available_passes(self):
        assert _req("sale_availability", _eval()).outcome == PASS

    def test_withdrawn_fails(self):
        dec = _eval({"sale_availability": "withdrawn"})
        r = _req("sale_availability", dec)
        assert r.outcome == FAIL and r.code == "PROC_SALE_ENDED"

    def test_unknown_status_is_unknown(self):
        dec = _eval({"sale_availability": None})
        r = _req("sale_availability", dec)
        assert r.outcome == UNKNOWN

    def test_reserved_fails(self):
        """Task 9: reserved sale is adverse — FAIL, not PASS."""
        dec = _eval({"sale_availability": "reserved"})
        r = _req("sale_availability", dec)
        assert r.outcome == FAIL, "reserved availability must be FAIL"
        assert r.code == "PROC_SALE_RESERVED"

    @pytest.mark.parametrize("val", ["blocked", "error"])
    def test_blocked_or_error_is_unknown(self, val):
        """Task 9: network error / challenge page must not retain a passing state."""
        dec = _eval({"sale_availability": val})
        r = _req("sale_availability", dec)
        assert r.outcome == UNKNOWN, f"availability={val!r} should be UNKNOWN"
        assert r.code == "DATA_AVAILABILITY_CHECK_FAILED"

    @pytest.mark.parametrize("val", ["", "xyz", "pending"])
    def test_unrecognised_availability_is_unknown(self, val):
        """Task 9: unrecognised availability value must not pass."""
        dec = _eval({"sale_availability": val})
        r = _req("sale_availability", dec)
        assert r.outcome == UNKNOWN, f"availability={val!r} should be UNKNOWN"


# ─── Anomaly investigation (F05) ─────────────────────────────────────────────

class TestAnomalyInvestigation:
    def test_no_discount_computed_passes(self):
        dec = _eval({"apparent_discount_pct": None})
        r = _req("anomaly_investigation", dec)
        assert r.outcome == PASS

    def test_below_threshold_passes(self):
        dec = _eval({"apparent_discount_pct": 69.9})
        r = _req("anomaly_investigation", dec)
        assert r.outcome == PASS

    def test_above_threshold_uninvestigated_is_unknown(self):
        dec = _eval({"apparent_discount_pct": 75.0, "anomaly_investigated": False})
        r = _req("anomaly_investigation", dec)
        assert r.outcome == UNKNOWN and r.code == "FIN_ANOMALY_PENDING"

    def test_above_threshold_investigated_passes(self):
        dec = _eval({"apparent_discount_pct": 75.0, "anomaly_investigated": True})
        r = _req("anomaly_investigation", dec)
        assert r.outcome == PASS

    def test_exactly_at_threshold_uninvestigated_is_unknown(self):
        dec = _eval({"apparent_discount_pct": 70.0, "anomaly_investigated": False})
        r = _req("anomaly_investigation", dec)
        assert r.outcome == UNKNOWN


# ─── Financial evidence (F06) ─────────────────────────────────────────────────

class TestFinancialEvidence:
    def test_comparables_passes(self):
        assert _req("financial_evidence", _eval()).outcome == PASS

    def test_fallback_median_is_unknown(self):
        dec = _eval({"valuation_source": "municipal_median_fallback"})
        r = _req("financial_evidence", dec)
        assert r.outcome == UNKNOWN and r.code == "DATA_VALUATION_FALLBACK"

    def test_missing_is_unknown(self):
        dec = _eval({"valuation_source": None})
        r = _req("financial_evidence", dec)
        assert r.outcome == UNKNOWN and r.code == "DATA_VALUATION_MISSING"


# ─── Financial margin ─────────────────────────────────────────────────────────

class TestFinancialMargin:
    def test_policy_unset_is_unknown(self):
        r = _req("financial_margin", _eval())
        assert r.outcome == UNKNOWN and r.code == "DATA_MARGIN_POLICY_UNSET"

    def test_not_computed_is_unknown(self):
        dec = _eval({"conservative_margin_eur": None},
                    policy={"min_resale_margin_eur": 10_000})
        r = _req("financial_margin", dec)
        assert r.outcome == UNKNOWN and r.code == "DATA_MARGIN_NOT_COMPUTED"

    def test_below_minimum_fails(self):
        dec = _eval({"conservative_margin_eur": 9_999},
                    policy={"min_resale_margin_eur": 10_000})
        r = _req("financial_margin", dec)
        assert r.outcome == FAIL and r.code == "FIN_MARGIN_INSUFFICIENT"

    def test_above_minimum_passes(self):
        dec = _eval({"conservative_margin_eur": 10_001},
                    policy={"min_resale_margin_eur": 10_000})
        r = _req("financial_margin", dec)
        assert r.outcome == PASS


# ─── Cash fit ─────────────────────────────────────────────────────────────────

class TestCashFit:
    def test_cash_not_set_is_unknown(self):
        r = _req("cash_fit", _eval())
        assert r.outcome == UNKNOWN and r.code == "DATA_CASH_NOT_SET"

    def test_fits_passes(self):
        dec = _eval({"available_cash_eur": 200_000, "total_project_cost_eur": 150_000},
                    policy={"min_liquidity_reserve_eur": 0})
        r = _req("cash_fit", dec)
        assert r.outcome == PASS

    def test_over_budget_fails(self):
        dec = _eval({"available_cash_eur": 100_000, "total_project_cost_eur": 120_000},
                    policy={"min_liquidity_reserve_eur": 0})
        r = _req("cash_fit", dec)
        assert r.outcome == FAIL and r.code == "FIN_OVER_CASH"

    def test_reserve_included_in_check(self):
        dec = _eval({"available_cash_eur": 100_000, "total_project_cost_eur": 90_000},
                    policy={"min_liquidity_reserve_eur": 15_000})
        r = _req("cash_fit", dec)
        # 90_000 + 15_000 = 105_000 > 100_000 → FAIL
        assert r.outcome == FAIL


# ─── Provenance ───────────────────────────────────────────────────────────────

class TestProvenance:
    def test_provenance_milestone(self):
        dec = _eval()
        assert dec.provenance["milestone"] == "A-shadow"

    def test_provenance_timestamp(self):
        dec = _eval()
        assert "2026-10-15" in dec.provenance["evaluated_at"]

    def test_idempotent(self):
        ev = dict(FULL_EVIDENCE)
        dec1 = evaluate(ev, now=NOW)
        dec2 = evaluate(ev, now=NOW)
        states = [r.outcome for r in dec1.requirements]
        states2 = [r.outcome for r in dec2.requirements]
        assert states == states2
        assert ev == dict(FULL_EVIDENCE), "evaluate() must not mutate its input"


# ─── Helper ───────────────────────────────────────────────────────────────────

def _req(key: str, dec: Decision) -> Requirement:
    for r in dec.requirements:
        if r.key == key:
            return r
    raise KeyError(f"Requirement {key!r} not found in decision")
