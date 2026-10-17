import copy
from datetime import datetime, timezone

import pytest

from evaluator import FAIL, PASS, UNKNOWN, evaluate
from tests.test_evaluator import FULL_EVIDENCE


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def decision(access=None, *, omit=False, hints=None):
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(
        legal_access=access,
        conservative_margin_eur=20000,
        available_cash_eur=200000,
        total_project_cost_eur=150000,
    )
    if omit:
        evidence.pop("legal_access")
    if hints:
        evidence.update(copy.deepcopy(hints))
    return evaluate(
        evidence,
        policy={"min_resale_margin_eur": 15000, "min_liquidity_reserve_eur": 0},
        now=NOW,
    )


def access_requirement(result):
    return next(req for req in result.requirements if req.key == "legal_access")


def test_exact_resolved_confirmation_preserves_existing_pass():
    result = decision("confirmed")
    requirement = access_requirement(result)
    assert requirement.outcome == PASS
    assert requirement.code == "PHYS_ACCESS_OK"
    assert requirement not in result.blocking
    assert result.state == "finalist"
    assert "does not independently verify" in requirement.message
    assert "physical suitability" in requirement.message


@pytest.mark.parametrize("access,omit", [
    (None, False), (None, True), ("unknown", False),
])
def test_missing_legal_access_is_a_critical_blocker(access, omit):
    result = decision(access, omit=omit)
    requirement = access_requirement(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_ACCESS_MISSING"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert requirement.next_evidence_needed
    assert result.state == "research"


def test_denied_access_retains_existing_adverse_code():
    result = decision("denied")
    requirement = access_requirement(result)
    assert requirement.outcome == FAIL
    assert requirement.code == "PHYS_LANDLOCKED"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert result.state == "research"
    assert "input is denied" in requirement.message


@pytest.mark.parametrize("access", [
    "", "yes", "road", "reachable", "public_road", "unverified",
    "CONFIRMED", " confirmed ", True, False, 1, 0,
    [], ["confirmed"], {}, {"status": "confirmed"},
])
def test_unresolved_or_malformed_access_never_passes(access):
    result = decision(access)
    requirement = access_requirement(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_ACCESS_UNRECOGNISED"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert requirement.next_evidence_needed
    assert result.state == "research"


@pytest.mark.parametrize("access", [None, "unknown", "denied"])
def test_physical_hints_do_not_override_legal_access(access):
    # Synthetic extra fields deliberately do not establish legal rights.
    result = decision(access, hints={
        "physical_access": "reachable",
        "road_distance_km": 0,
        "visible_track": True,
    })
    requirement = access_requirement(result)
    assert requirement.outcome == (FAIL if access == "denied" else UNKNOWN)
    assert requirement in result.blocking
    assert result.state == "research"


def test_requested_evidence_distinguishes_rights_from_inspection():
    requirement = access_requirement(decision(None))
    assert "specific lot" in requirement.next_evidence_needed
    assert "physical inspection alone does not establish" in requirement.next_evidence_needed
    assert "right of passage" in requirement.next_evidence_needed


def test_evaluation_does_not_mutate_access_input():
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence["legal_access"] = {"status": "unverified"}
    before = copy.deepcopy(evidence)
    first = evaluate(evidence, now=NOW)
    second = evaluate(evidence, now=NOW)
    assert evidence == before
    assert access_requirement(first) == access_requirement(second)
