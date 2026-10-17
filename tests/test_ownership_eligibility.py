import copy
from datetime import datetime, timezone

import pytest

from evaluator import FAIL, PASS, UNKNOWN, evaluate
from tests.test_evaluator import FULL_EVIDENCE


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def decision(value="full", *, omit=False, title_status="clear"):
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence.update(
        ownership_type=value,
        title_status=title_status,
        conservative_margin_eur=20000,
        available_cash_eur=200000,
        total_project_cost_eur=150000,
    )
    if omit:
        evidence.pop("ownership_type")
    return evaluate(
        evidence,
        policy={"min_resale_margin_eur": 15000, "min_liquidity_reserve_eur": 0},
        now=NOW,
    )


def ownership_requirement(result):
    return next(req for req in result.requirements if req.key == "full_ownership")


def test_exact_full_ownership_can_pass_otherwise_complete_evidence():
    result = decision()
    requirement = ownership_requirement(result)
    assert requirement.outcome == PASS
    assert requirement.code == "LEGAL_OWNERSHIP_OK"
    assert result.state == "finalist"
    assert "does not independently verify title" in requirement.message


@pytest.mark.parametrize("omit", [False, True])
def test_missing_ownership_is_in_the_blocker_summary(omit):
    result = decision(None, omit=omit)
    requirement = ownership_requirement(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_OWNERSHIP_MISSING"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert requirement.next_evidence_needed
    assert result.state == "research"


@pytest.mark.parametrize("value", ["share", "usufruct", "timeshare", "co_ownership"])
def test_existing_non_full_interests_remain_failures(value):
    result = decision(value)
    requirement = ownership_requirement(result)
    assert requirement.outcome == FAIL
    assert requirement.code == "LEGAL_PARTIAL_OWNERSHIP"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert result.state == "research"


def test_bare_ownership_is_explicitly_rejected():
    result = decision("bare_ownership")
    requirement = ownership_requirement(result)
    assert requirement.outcome == FAIL
    assert requirement.code == "LEGAL_BARE_OWNERSHIP"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert result.state == "research"


@pytest.mark.parametrize("value", [
    "", "unknown", "partial", "FULL", " full ", "freehold",
    True, False, 1, 0, [], ["full"], {}, {"type": "full"},
])
def test_unresolved_or_malformed_ownership_never_passes(value):
    result = decision(value)
    requirement = ownership_requirement(result)
    assert requirement.outcome == UNKNOWN
    assert requirement.code == "DATA_OWNERSHIP_UNRECOGNISED"
    assert requirement.dangerous
    assert requirement in result.blocking
    assert requirement.next_evidence_needed
    assert result.state == "research"


@pytest.mark.parametrize("title_status", [None, "unknown", "defective"])
def test_full_ownership_does_not_clear_unresolved_or_defective_title(title_status):
    result = decision("full", title_status=title_status)
    assert ownership_requirement(result).outcome == PASS
    title = next(req for req in result.requirements if req.key == "title_and_charges")
    assert title.outcome in (FAIL, UNKNOWN)
    assert title in result.blocking
    assert result.state == "research"


def test_evaluation_does_not_mutate_ownership_input():
    evidence = copy.deepcopy(FULL_EVIDENCE)
    evidence["ownership_type"] = {"type": "full"}
    before = copy.deepcopy(evidence)
    first = evaluate(evidence, now=NOW)
    second = evaluate(evidence, now=NOW)
    assert evidence == before
    assert ownership_requirement(first) == ownership_requirement(second)
