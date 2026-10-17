import copy
from datetime import datetime, timezone

import pytest

from evaluator import PASS, UNKNOWN, evaluate
from tests.test_evaluator import FULL_EVIDENCE


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def decision(discount=75, reviewed=False, threshold=70, **overrides):
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(
        apparent_discount_pct=discount,
        anomaly_investigated=reviewed,
        conservative_margin_eur=20000,
        available_cash_eur=200000,
        total_project_cost_eur=150000,
    )
    evidence.update(overrides)
    return evaluate(evidence, policy={
        "anomaly_discount_pct": threshold,
        "min_resale_margin_eur": 15000,
        "min_liquidity_reserve_eur": 0,
    }, now=NOW)


def anomaly(result):
    return next(req for req in result.requirements if req.key == "anomaly_investigation")


@pytest.mark.parametrize("reviewed", [
    False, None, "false", "true", 1, 0, [], [True], {}, {"reviewed": True},
])
def test_only_boolean_true_can_clear_extreme_anomaly(reviewed):
    result = decision(reviewed=reviewed)
    requirement = anomaly(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "FIN_ANOMALY_PENDING"
    assert requirement.next_evidence_needed
    assert result.state == "research"


def test_reviewed_extreme_anomaly_is_not_described_as_normal():
    result = decision(reviewed=True)
    requirement = anomaly(result)
    assert requirement.outcome == PASS
    assert requirement.code == "FIN_ANOMALY_REVIEWED"
    assert "anomaly remains extreme" in requirement.message
    assert "safe bargain" in requirement.message
    assert "within normal range" not in requirement.message
    assert result.state == "finalist"


@pytest.mark.parametrize("discount", [
    float("nan"), float("inf"), -float("inf"), 100.1,
    True, False, "75", [], {},
])
def test_invalid_discount_is_unresolved_even_if_reviewed(discount):
    result = decision(discount=discount, reviewed=True)
    assert anomaly(result).outcome == UNKNOWN
    assert anomaly(result).code == "DATA_ANOMALY_DISCOUNT_INVALID"
    assert result.state == "research"


@pytest.mark.parametrize("threshold", [
    None, 0, -1, 100.1, float("nan"), float("inf"), True, "70", [], {},
])
def test_invalid_threshold_cannot_disable_investigation(threshold):
    result = decision(threshold=threshold, reviewed=True)
    assert anomaly(result).outcome == UNKNOWN
    assert anomaly(result).code == "DATA_ANOMALY_POLICY_INVALID"
    assert result.state == "research"


@pytest.mark.parametrize("discount,outcome", [(69.9, PASS), (70, UNKNOWN), (70.1, UNKNOWN)])
def test_threshold_boundary_is_preserved(discount, outcome):
    assert anomaly(decision(discount=discount)).outcome == outcome


@pytest.mark.parametrize("discount", [-20, 0, 30])
def test_valid_below_threshold_inputs_remain_eligible(discount):
    requirement = anomaly(decision(discount=discount))
    assert requirement.outcome == PASS
    assert requirement.code == "FIN_DISCOUNT_OK"


def test_missing_percentage_does_not_claim_absence_of_anomaly():
    requirement = anomaly(decision(discount=None))
    assert requirement.outcome == PASS
    assert requirement.code == "FIN_DISCOUNT_NOT_COMPUTED"
    assert "no percentage-based anomaly assessment" in requirement.message


def test_review_does_not_override_partial_ownership():
    result = decision(reviewed=True, ownership_type="share")
    assert anomaly(result).outcome == PASS
    assert result.state == "research"
    assert any(req.key == "full_ownership" for req in result.blocking)


def test_review_does_not_override_unknown_access():
    result = decision(reviewed=True, legal_access=None)
    assert anomaly(result).outcome == PASS
    assert result.state == "research"
    assert any(req.key == "legal_access" and req.outcome == UNKNOWN for req in result.requirements)


def test_custom_threshold_is_respected():
    assert anomaly(decision(discount=75, threshold=80)).outcome == PASS
    assert anomaly(decision(discount=80, threshold=80)).outcome == UNKNOWN


def test_inputs_are_not_mutated():
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(apparent_discount_pct=75, anomaly_investigated={"reviewed": True})
    before = copy.deepcopy(evidence)
    first = evaluate(evidence, now=NOW)
    second = evaluate(evidence, now=NOW)
    assert evidence == before
    assert anomaly(first) == anomaly(second)
