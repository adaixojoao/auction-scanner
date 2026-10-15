"""Independent contract regressions for repair units A–F."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import pytest
import costs
import db as store
import evaluator
from tests.test_evaluator import FULL_EVIDENCE

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)


def requirement(key, overrides):
    evidence = deepcopy(FULL_EVIDENCE)
    evidence.update(deepcopy(overrides))
    decision = evaluator.evaluate(
        evidence,
        policy={"min_resale_margin_eur": 0, "min_liquidity_reserve_eur": 0},
        now=NOW,
    )
    return next(r for r in decision.requirements if r.key == key)


# ─── Unit F — typed evaluator checks ────────────────────────────────────────

def test_full_evidence_baseline_passes_typed_checks():
    for key in ("photo_suitability", "residential_identity", "legal_access",
                "financial_evidence", "hazard_coverage", "possession",
                "sale_availability"):
        assert requirement(key, {}).outcome == evaluator.PASS, key


@pytest.mark.parametrize("value", ["false", "true", 0, 1, [], {}, "yes", 1.0])
def test_photo_shows_house_requires_real_boolean(value):
    r = requirement("photo_suitability",
                    {"photo_check": {"shows_house": value, "condition": "good"}})
    assert r.outcome != evaluator.PASS


@pytest.mark.parametrize("unit_type", ["unknown", None, ""])
def test_unknown_identity_unit_type_does_not_pass(unit_type):
    r = requirement("residential_identity",
                    {"property_identity": {"unit_type": unit_type, "evidence_ids": ["x"]}})
    assert r.outcome != evaluator.PASS


@pytest.mark.parametrize("access", ["maybe", "probably", "yes", "true", "pending", "n/a"])
def test_arbitrary_access_strings_do_not_pass(access):
    assert requirement("legal_access", {"legal_access": access}).outcome != evaluator.PASS


@pytest.mark.parametrize("label", ["guess", "asking_price", "municipal_median", "ai", "unknown"])
def test_unsupported_valuation_labels_do_not_pass(label):
    assert requirement("financial_evidence", {"valuation_source": label}).outcome != evaluator.PASS


@pytest.mark.parametrize("climate", [
    {"flood_m": None, "fire_danger": {"rcp45_2079-2098": 10}},
    {"flood_m": 0.0, "fire_danger": None},
    {"flood_m": 0.0, "fire_danger": {}},
    {"flood_m": 0.0, "fire_danger": []},
    {},
])
def test_null_or_empty_hazard_layers_do_not_pass(climate):
    assert requirement("hazard_coverage", {"climate": climate}).outcome != evaluator.PASS


@pytest.mark.parametrize("occupation", [None, "unknown"])
def test_delivery_unknown_does_not_confirm_possession(occupation):
    r = requirement("possession", {"occupation": occupation, "delivery_terms": "unknown"})
    assert r.outcome != evaluator.PASS


@pytest.mark.parametrize("avail", ["withdrawn", "ended", "reserved", "blocked", "error", "sold"])
def test_known_adverse_availability_does_not_pass(avail):
    assert requirement("sale_availability", {"sale_availability": avail}).outcome != evaluator.PASS
