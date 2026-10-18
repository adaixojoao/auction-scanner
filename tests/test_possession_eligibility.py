import copy
from datetime import datetime, timezone

import pytest

from evaluator import FAIL, PASS, UNKNOWN, evaluate
from tests.test_evaluator import FULL_EVIDENCE


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
# The shared fixture's availability window is for its own NOW; give this NOW one.
AVAILABILITY_TIMES = {
    "sale_availability_checked_at": "2029-12-31T23:00:00+00:00",
    "sale_availability_expires_at": "2030-01-01T23:00:00+00:00",
}


def decision(occupation=None, delivery=None):
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(AVAILABILITY_TIMES)
    evidence.update(
        occupation=occupation,
        delivery_terms=delivery,
        conservative_margin_eur=20000,
        available_cash_eur=200000,
        total_project_cost_eur=150000,
    )
    return evaluate(
        evidence,
        policy={"min_resale_margin_eur": 15000, "min_liquidity_reserve_eur": 0},
        now=NOW,
    )


def possession(result):
    return next(req for req in result.requirements if req.key == "possession")


@pytest.mark.parametrize("occupation,delivery,outcome", [
    (None, None, UNKNOWN),
    (None, "unknown", UNKNOWN),
    (None, "vacant_on_completion", PASS),
    (None, "with_tenant", FAIL),
    ("unknown", None, UNKNOWN),
    ("unknown", "vacant_on_completion", PASS),
    ("vacant", None, PASS),
    ("vacant", "unknown", PASS),
    ("vacant", "vacant_on_completion", PASS),
    ("vacant", "with_tenant", FAIL),
    ("occupied", None, FAIL),
    ("occupied", "unknown", FAIL),
    ("occupied", "vacant_on_completion", PASS),
    ("occupied", "with_tenant", FAIL),
    ("tenant", None, FAIL),
    ("tenant", "unknown", FAIL),
    ("tenant", "vacant_on_completion", FAIL),
    ("tenant", "with_tenant", FAIL),
])
def test_resolved_occupancy_delivery_matrix(occupation, delivery, outcome):
    result = decision(occupation, delivery)
    requirement = possession(result)
    assert requirement.outcome == outcome
    if outcome == PASS:
        assert result.state == "finalist"
        assert requirement not in result.blocking
    else:
        assert result.state == "research"
        assert requirement.dangerous
        assert requirement in result.blocking
        if outcome == UNKNOWN:
            assert requirement.next_evidence_needed


@pytest.mark.parametrize("delivery", [
    "", "pending", "unverified", "VACANT_ON_COMPLETION", " vacant_on_completion ",
    True, False, 1, 0, [], ["vacant_on_completion"], {}, {"terms": "vacant_on_completion"},
])
@pytest.mark.parametrize("occupation", [None, "vacant"])
def test_unrecognised_delivery_is_not_ignored(occupation, delivery):
    result = decision(occupation, delivery)
    requirement = possession(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_DELIVERY_UNRECOGNISED"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert result.state == "research"


@pytest.mark.parametrize("occupation", [
    "", "unverified", "VACANT", " vacant ", True, False, 1, 0,
    [], ["vacant"], {}, {"state": "vacant"},
])
@pytest.mark.parametrize("delivery", [None, "vacant_on_completion"])
def test_unrecognised_occupancy_cannot_be_rescued(occupation, delivery):
    result = decision(occupation, delivery)
    requirement = possession(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_POSSESSION_UNRECOGNISED"
    assert requirement in result.blocking
    assert result.state == "research"


def test_vacant_now_is_not_described_as_confirmed_delivery():
    requirement = possession(decision("vacant", None))
    assert requirement.outcome == PASS
    assert "delivery terms remain unresolved" in requirement.message
    assert "not verified vacant delivery" in requirement.message


def test_unknown_occupancy_remains_visible_in_delivery_pass_message():
    requirement = possession(decision(None, "vacant_on_completion"))
    assert requirement.outcome == PASS
    assert "Current occupancy is unknown" in requirement.message
    assert "requires supporting verification" in requirement.message


def test_adverse_delivery_outranks_vacancy_claim():
    requirement = possession(decision("vacant", "with_tenant"))
    assert requirement.outcome == FAIL
    assert requirement.code == "PHYS_TENANT_NO_VACANT_POSSESSION"


def test_occupied_without_valid_delivery_remains_adverse():
    requirement = possession(decision("occupied", "unverified"))
    assert requirement.outcome == FAIL
    assert requirement.code == "PHYS_OCCUPIED_NO_DELIVERY"


def test_tenant_exclusion_is_not_weakened_by_vacant_delivery_claim():
    requirement = possession(decision("tenant", "vacant_on_completion"))
    assert requirement.outcome == FAIL
    assert "conservative possession policy" in requirement.message


def test_missing_keys_are_unknown_not_vacant():
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(AVAILABILITY_TIMES)
    evidence.pop("occupation")
    evidence.pop("delivery_terms")
    requirement = possession(evaluate(evidence, now=NOW))
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_POSSESSION_MISSING"
    assert requirement.dangerous


def test_no_mutation_and_repeatable_result():
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(AVAILABILITY_TIMES)
    evidence["delivery_terms"] = {"terms": "unverified"}
    before = copy.deepcopy(evidence)
    first = evaluate(evidence, now=NOW)
    second = evaluate(evidence, now=NOW)
    assert evidence == before
    assert possession(first) == possession(second)
