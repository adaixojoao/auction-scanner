"""Acceptance tests for the pure Live and invest eligibility evaluator.

Synthetic records only. A passing test shows the contract, not that a real
property is safe to buy.
"""
import ast
import copy
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

import eligibility
from eligibility import REQUIREMENT_KEYS, EligibilityError, evaluate

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
OBSERVED = "2026-10-01T12:00:00+00:00"
REVIEWED = "2026-10-02T12:00:00+00:00"
FINANCIAL = {"financial_policy_id": "fin-synth", "financial_policy_version": "1"}
POLICY_BIND = {"policy_id": "live-invest-synthetic", "policy_version": "1"}
HAZARDS = {
    "fire": {"reference": "layer-fire", "scale": "building", "applicable": True,
             "covered": True, "acceptable": True},
    "flood": {"reference": "layer-flood", "scale": "building", "applicable": True,
              "covered": True, "acceptable": True},
}
DETAILS = {
    "residential_identity": {"asset_kind": "residential"},
    "complete_ownership": {"right": "complete"},
    "vacant_possession": {"possession": "vacant"},
    "title_and_charges": {"charges_reviewed": True, "exposure": "none"},
    "legal_access": {"access": "legal"},
    "residential_use": {"use": "residential_supported"},
    "inspection": {"inspection": "adequate", "scope": "interior"},
    "work_scope": {"heavy_rebuild": False, "uncertainty": "bounded"},
    "floor_area": {"basis": "floor", "compatible": True},
    "building_location": {"precision": "building"},
    "sea_distance": {"km": 2.5, "basis": "building-pin", "water": "sea", **POLICY_BIND},
    "hazard_coverage": {"hazards": HAZARDS, **POLICY_BIND},
    "hazard_acceptability": {"acceptable": True, "hazards": HAZARDS, **POLICY_BIND},
    "specific_sale_available": {"specific_sale": True},
    "financial_evidence": {**FINANCIAL, "basis": "conservative-scenario"},
    "financial_margin": {**FINANCIAL, "meets_margin": True},
    "cash_fit": {**FINANCIAL, "fits_cash": True},
    "procedural_readiness": {"method": "sealed-letter", "deadline_known": True, "can_submit": True},
}


def make_policy(**over):
    freshness = {key: {"max_age_seconds": 86400 * 400} for key in REQUIREMENT_KEYS}
    freshness.update(over.pop("freshness", {}))
    policy = {
        "schema_version": "1",
        "policy_id": "live-invest-synthetic",
        "policy_version": "1",
        "mode": "live_and_invest",
        "sea_max_km": 10,
        "heavy_rebuild_excluded": True,
        "required_hazards": ["fire", "flood"],
        "financial_policy_id": "fin-synth",
        "financial_policy_version": "1",
        "freshness": freshness,
        "label": "synthetic example, not a production default",
    }
    policy.update(over)
    return policy


def finding(key, **kw):
    item = {
        "id": f"f-{key}",
        "requirement_key": key,
        "subject_id": "home-1",
        "sale_id": "sale-1",
        "finding": "meets",
        "provenance": "reviewed",
        "reference": f"note-{key}",
        "observed_at": OBSERVED,
        "reviewer_ref": "reviewer-alias",
        "reviewed_at": REVIEWED,
        "details": copy.deepcopy(DETAILS[key]),
    }
    item.update(kw)
    return item


def envelope(findings, **kw):
    item = {"schema_version": "1", "subject_id": "home-1", "sale_id": "sale-1", "findings": findings}
    item.update(kw)
    return item


def complete():
    return [finding(key) for key in REQUIREMENT_KEYS]


def row(result, key):
    return next(item for item in result["requirements"] if item["key"] == key)


def test_every_reviewed_positive_is_recommended_and_an_empty_file_is_unknown():
    ready = evaluate(envelope(complete()), make_policy(), NOW)
    assert ready["readiness"] == "recommended"
    assert ready["failed_keys"] == [] and ready["unknown_keys"] == []
    assert [item["state"] for item in ready["requirements"]] == ["pass"] * len(REQUIREMENT_KEYS)
    empty = evaluate(envelope([]), make_policy(), NOW)
    assert empty["readiness"] == "research_needed"
    assert empty["unknown_keys"] == list(REQUIREMENT_KEYS)
    assert all(item["reason_codes"] == ["evidence.missing"] for item in empty["requirements"])


@pytest.mark.parametrize("missing", REQUIREMENT_KEYS)
def test_removing_one_mandatory_positive_blocks_recommendation(missing):
    findings = [finding(key) for key in REQUIREMENT_KEYS if key != missing]
    result = evaluate(envelope(findings), make_policy(), NOW)
    assert result["readiness"] == "research_needed"
    assert row(result, missing)["state"] == "unknown"
    assert "evidence.missing" in row(result, missing)["reason_codes"]


def test_a_confirmed_sea_miss_or_heavy_rebuild_is_a_preference_rejection():
    sea = complete()
    sea[REQUIREMENT_KEYS.index("sea_distance")] = finding(
        "sea_distance", finding="fails", details={"km": 12.0, "basis": "building-pin", "water": "sea", **POLICY_BIND})
    sea_result = evaluate(envelope(sea), make_policy(), NOW)
    assert sea_result["readiness"] == "rejected"
    assert "preference.sea_distance" in row(sea_result, "sea_distance")["reason_codes"]

    river = complete()
    river[REQUIREMENT_KEYS.index("sea_distance")] = finding(
        "sea_distance", finding="fails", details={"water": "river", "km": 1.0, "basis": "building-pin", **POLICY_BIND})
    assert "preference.sea_distance" in row(evaluate(envelope(river), make_policy(), NOW), "sea_distance")["reason_codes"]

    heavy = complete()
    heavy[REQUIREMENT_KEYS.index("work_scope")] = finding(
        "work_scope", finding="fails", details={"heavy_rebuild": True, "uncertainty": "bounded"})
    heavy_result = evaluate(envelope(heavy), make_policy(), NOW)
    assert heavy_result["readiness"] == "rejected"
    assert "preference.heavy_rebuild" in row(heavy_result, "work_scope")["reason_codes"]


@pytest.mark.parametrize("key,details,code", [
    ("complete_ownership", {"right": "fraction"}, "safety.complete_ownership"),
    ("vacant_possession", {"possession": "occupied"}, "safety.vacant_possession"),
    ("title_and_charges", {"charges_reviewed": True, "exposure": "adverse"}, "safety.title_and_charges"),
    ("legal_access", {"access": "none"}, "safety.legal_access"),
])
def test_confirmed_safety_failures_reject(key, details, code):
    findings = complete()
    findings[REQUIREMENT_KEYS.index(key)] = finding(key, finding="fails", details=details)
    result = evaluate(envelope(findings), make_policy(), NOW)
    assert result["readiness"] == "rejected"
    assert code in row(result, key)["reason_codes"]


def test_unknown_title_stays_research_needed():
    findings = [finding(key) for key in REQUIREMENT_KEYS if key != "title_and_charges"]
    result = evaluate(envelope(findings), make_policy(), NOW)
    assert result["readiness"] == "research_needed"
    assert row(result, "title_and_charges")["state"] == "unknown"
    assert result["failed_keys"] == []


def test_asserted_and_inferred_findings_neither_pass_nor_fail():
    asserted = [finding(key, provenance="asserted", finding="meets") for key in REQUIREMENT_KEYS]
    positive = evaluate(envelope(asserted), make_policy(), NOW)
    assert positive["readiness"] == "research_needed"
    assert positive["failed_keys"] == []
    assert all("evidence.unreviewed" in item["reason_codes"] for item in positive["requirements"])

    inferred = complete()
    inferred[REQUIREMENT_KEYS.index("legal_access")] = finding(
        "legal_access", provenance="inferred", finding="fails", details={"access": "none"})
    negative = evaluate(envelope(inferred), make_policy(), NOW)
    assert negative["readiness"] == "research_needed"
    assert row(negative, "legal_access")["state"] == "unknown"
    assert "safety.legal_access" not in row(negative, "legal_access")["reason_codes"]


def test_star_score_source_and_override_do_not_recommend():
    evidence = envelope([], starred=True, shortlisted=True, score=100, source="citius", override=True)
    result = evaluate(evidence, make_policy(), NOW)
    assert result["readiness"] == "research_needed"
    dumped = json.dumps(result)
    assert "citius" not in dumped and "shortlisted" not in dumped


def test_hazard_coverage_needs_every_hazard_and_ignores_a_favourable_grade():
    findings = complete()
    partial = copy.deepcopy(HAZARDS)
    del partial["fire"]
    findings[REQUIREMENT_KEYS.index("hazard_coverage")] = finding(
        "hazard_coverage", details={"grade": "excellent", "hazards": partial, **POLICY_BIND})
    covered = evaluate(envelope(findings), make_policy(), NOW)
    assert covered["readiness"] == "research_needed"
    assert row(covered, "hazard_coverage")["state"] == "unknown"

    graded = complete()
    graded[REQUIREMENT_KEYS.index("hazard_coverage")] = finding(
        "hazard_coverage", details={"grade": "excellent", **POLICY_BIND})
    graded[REQUIREMENT_KEYS.index("hazard_acceptability")] = finding(
        "hazard_acceptability", details={"grade": "excellent", "acceptable": True, **POLICY_BIND})
    assert evaluate(envelope(graded), make_policy(), NOW)["readiness"] == "research_needed"


def test_a_new_review_does_not_refresh_an_old_observation_and_the_boundary_is_stale():
    old = complete()
    old[REQUIREMENT_KEYS.index("sea_distance")] = finding(
        "sea_distance", observed_at="2026-09-01T12:00:00+00:00", reviewed_at="2026-10-13T12:00:00+00:00")
    rules = make_policy(freshness={"sea_distance": {"max_age_seconds": 5 * 86400}})
    stale = evaluate(envelope(old), rules, NOW)
    assert row(stale, "sea_distance")["state"] == "unknown"
    assert "evidence.stale" in row(stale, "sea_distance")["reason_codes"]

    boundary = complete()
    at_limit = (NOW - timedelta(seconds=3600)).isoformat()
    boundary[REQUIREMENT_KEYS.index("floor_area")] = finding(
        "floor_area", observed_at=at_limit, reviewed_at=at_limit, valid_until=NOW.isoformat())
    exact = evaluate(envelope(boundary), make_policy(), NOW)
    assert row(exact, "floor_area")["state"] == "unknown"

    inside = complete()
    inside[REQUIREMENT_KEYS.index("floor_area")] = finding(
        "floor_area", observed_at=at_limit, reviewed_at=at_limit,
        valid_until=(NOW + timedelta(seconds=1)).isoformat())
    assert row(evaluate(envelope(inside), make_policy(), NOW), "floor_area")["state"] == "pass"


@pytest.mark.parametrize("stamp", ["2026-10-20T12:00:00+00:00", "2026-10-01T12:00:00", "yesterday", ""])
def test_future_naive_and_malformed_times_are_rejected(stamp):
    findings = complete()
    findings[0] = finding(REQUIREMENT_KEYS[0], observed_at=stamp, reviewed_at=REVIEWED)
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)
    with pytest.raises(EligibilityError):
        evaluate(envelope([]), make_policy(), NOW.replace(tzinfo=None))


def test_a_zulu_timestamp_is_accepted():
    findings = complete()
    findings[0] = finding(REQUIREMENT_KEYS[0], observed_at="2026-10-01T12:00:00Z",
                          reviewed_at="2026-10-02T12:00:00Z")
    assert row(evaluate(envelope(findings), make_policy(), NOW), REQUIREMENT_KEYS[0])["state"] == "pass"


def test_another_property_or_sale_cannot_pass():
    wrong_sale = [finding(key, sale_id="sale-earlier") for key in REQUIREMENT_KEYS]
    sale = evaluate(envelope(wrong_sale), make_policy(), NOW)
    assert sale["readiness"] == "research_needed"
    assert all("evidence.mismatch" in item["reason_codes"] for item in sale["requirements"])

    wrong_home = complete()
    wrong_home[REQUIREMENT_KEYS.index("floor_area")] = finding("floor_area", subject_id="flat-2")
    home = evaluate(envelope(wrong_home), make_policy(), NOW)
    assert row(home, "floor_area")["state"] == "unknown"
    assert home["readiness"] == "research_needed"


def test_conflicts_do_not_depend_on_order_including_an_unreviewed_contradiction():
    fail = finding("legal_access", id="access-fail", finding="fails", details={"access": "none"})
    meet = finding("legal_access", id="access-meet")
    title = finding("title_and_charges", id="title-meet")
    inferred = finding("title_and_charges", id="title-inferred", provenance="inferred", finding="fails",
                       details={"exposure": "adverse", "charges_reviewed": True})
    rest = [finding(key) for key in REQUIREMENT_KEYS if key not in {"legal_access", "title_and_charges"}]
    forward = evaluate(envelope([fail, meet, title, inferred, *rest]), make_policy(), NOW)
    backward = evaluate(envelope([*reversed(rest), inferred, title, meet, fail]), make_policy(), NOW)
    assert forward == backward
    assert row(forward, "legal_access")["state"] == "unknown"
    assert "evidence.conflict" in row(forward, "legal_access")["reason_codes"]
    assert row(forward, "title_and_charges")["state"] == "unknown"
    assert "evidence.conflict" in row(forward, "title_and_charges")["reason_codes"]
    assert forward["readiness"] == "research_needed"


def test_reviewed_supersession_resolves_a_contradiction():
    old = finding("legal_access", id="access-old", finding="fails", details={"access": "none"},
                  observed_at="2026-09-01T12:00:00+00:00", reviewed_at="2026-09-02T12:00:00+00:00")
    new = finding("legal_access", id="access-new", supersedes="access-old")
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"] + [old, new]
    result = evaluate(envelope(findings), make_policy(), NOW)
    assert row(result, "legal_access")["state"] == "pass"
    assert result["readiness"] == "recommended"


@pytest.mark.parametrize("bad", ["cycle", "cross", "absent", "inferred", "older"])
def test_invalid_supersession_is_rejected(bad):
    old = finding("legal_access", id="access-old", observed_at="2026-09-01T12:00:00+00:00",
                  reviewed_at="2026-09-02T12:00:00+00:00")
    new = finding("legal_access", id="access-new", supersedes="access-old")
    if bad == "cycle":
        old["supersedes"] = "access-new"
        findings = [old, new]
    elif bad == "cross":
        new["requirement_key"] = "sea_distance"
        new["details"] = copy.deepcopy(DETAILS["sea_distance"])
        findings = [old, new]
    elif bad == "absent":
        new["supersedes"] = "does-not-exist"
        findings = [new]
    elif bad == "inferred":
        new["provenance"] = "inferred"
        findings = [old, new]
    else:
        new["observed_at"] = "2026-08-01T12:00:00+00:00"
        new["reviewed_at"] = "2026-08-02T12:00:00+00:00"
        findings = [old, new]
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)


@pytest.mark.parametrize("field", ["reviewer_ref", "reviewed_at", "reference"])
def test_review_metadata_is_required(field):
    item = finding("legal_access")
    item[field] = "  " if field != "reviewed_at" else None
    findings = [finding(key) for key in REQUIREMENT_KEYS if key != "legal_access"] + [item]
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)


def test_a_review_cannot_predate_the_observation():
    item = finding("legal_access", reviewed_at="2026-09-01T12:00:00+00:00")
    findings = [finding(key) for key in REQUIREMENT_KEYS if key != "legal_access"] + [item]
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)


def test_mandatory_exemptions_and_disabled_requirements_cannot_recommend():
    skipped = finding("title_and_charges", details={"not_applicable": True, "charges_reviewed": True,
                                                    "exposure": "none"})
    findings = [finding(key) for key in REQUIREMENT_KEYS if key != "title_and_charges"] + [skipped]
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)
    with pytest.raises(EligibilityError):
        evaluate(envelope(complete()), make_policy(disabled_requirements=["title_and_charges"]), NOW)
    with pytest.raises(EligibilityError):
        evaluate(envelope(complete()), make_policy(require_title=False), NOW)
    with pytest.raises(EligibilityError):
        evaluate(envelope(complete()), make_policy(heavy_rebuild_excluded=False), NOW)


def test_a_different_financial_policy_is_unknown_and_a_reviewed_shortfall_rejects():
    cash = complete()
    wrong = copy.deepcopy(DETAILS["cash_fit"])
    wrong["financial_policy_version"] = "other"
    wrong["fits_cash"] = False
    cash[REQUIREMENT_KEYS.index("cash_fit")] = finding("cash_fit", details=wrong)
    mismatched = evaluate(envelope(cash), make_policy(), NOW)
    assert mismatched["readiness"] == "research_needed"
    assert "evidence.policy_mismatch" in row(mismatched, "cash_fit")["reason_codes"]
    assert "financial.cash_fit" not in row(mismatched, "cash_fit")["reason_codes"]

    short = complete()
    short[REQUIREMENT_KEYS.index("cash_fit")] = finding(
        "cash_fit", finding="fails", details={**FINANCIAL, "fits_cash": False})
    short[REQUIREMENT_KEYS.index("financial_margin")] = finding(
        "financial_margin", finding="fails", details={**FINANCIAL, "meets_margin": False})
    rejected = evaluate(envelope(short), make_policy(), NOW)
    assert rejected["readiness"] == "rejected"
    assert "financial.cash_fit" in row(rejected, "cash_fit")["reason_codes"]
    assert "financial.margin" in row(rejected, "financial_margin")["reason_codes"]


def test_a_failure_keeps_the_unknowns_and_their_research_needs():
    findings = [finding(key) for key in REQUIREMENT_KEYS if key != "title_and_charges"]
    findings = [item for item in findings if item["requirement_key"] != "sea_distance"]
    findings.append(finding("sea_distance", finding="fails",
                            details={"km": 18, "basis": "building-pin", "water": "sea", **POLICY_BIND}))
    result = evaluate(envelope(findings), make_policy(), NOW)
    assert result["readiness"] == "rejected"
    assert "sea_distance" in result["failed_keys"]
    assert "title_and_charges" in result["unknown_keys"]
    assert any("title_and_charges" in text for text in result["research_needs"])
    assert len(result["requirements"]) == len(REQUIREMENT_KEYS)


def test_results_are_deterministic_across_repeats_and_permutations():
    findings = complete()
    policy = make_policy()
    first = evaluate(envelope(findings), policy, NOW)
    second = evaluate(envelope(list(reversed(findings))), policy, NOW)
    assert first == second
    assert evaluate(envelope(findings), policy, NOW) == first
    local = datetime(2026, 10, 14, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert evaluate(envelope(findings), policy, local)["evaluated_at"] == first["evaluated_at"]


def test_inputs_are_unchanged_and_the_result_is_strict_json():
    evidence = envelope(complete(), starred=True)
    policy = make_policy()
    before_evidence, before_policy = copy.deepcopy(evidence), copy.deepcopy(policy)
    result = evaluate(evidence, policy, NOW)
    assert evidence == before_evidence and policy == before_policy
    json.dumps(result, allow_nan=False)
    assert all(item["state"] != "not_applicable" for item in result["requirements"])


@pytest.mark.parametrize("mutate", ["enum", "schema", "duplicate", "nan", "infinity", "boolean"])
def test_malformed_contracts_are_rejected(mutate):
    evidence = envelope(complete())
    policy = make_policy()
    if mutate == "enum":
        evidence["findings"][0]["finding"] = "pass"
    elif mutate == "schema":
        evidence["schema_version"] = "9"
    elif mutate == "duplicate":
        evidence["findings"][1]["id"] = evidence["findings"][0]["id"]
    elif mutate == "nan":
        policy["sea_max_km"] = math.nan
    elif mutate == "infinity":
        policy["sea_max_km"] = math.inf
    else:
        policy["sea_max_km"] = True
    with pytest.raises(EligibilityError):
        evaluate(evidence, policy, NOW)


def test_a_boolean_distance_is_rejected():
    findings = complete()
    findings[REQUIREMENT_KEYS.index("sea_distance")] = finding(
        "sea_distance", details={"km": True, "basis": "building-pin", "water": "sea"})
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)


def test_evaluation_does_not_touch_the_clock_or_scoring_weights():
    source = open(eligibility.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        if isinstance(node, ast.Attribute) and node.attr == "now":
            raise AssertionError("eligibility reads the clock")
    assert imported.isdisjoint({"scoring", "db", "requests", "config", "smtplib", "urllib", "pathlib"})
    assert "open(" not in source and "os.environ" not in source

    baseline = evaluate(envelope(complete()), make_policy(), NOW)
    import scoring
    token = scoring._WEIGHTS.set({"beach": 0})
    try:
        assert evaluate(envelope(complete()), make_policy(), NOW) == baseline
    finally:
        scoring._WEIGHTS.reset(token)


def _swap(key, **kw):
    findings = complete()
    findings[REQUIREMENT_KEYS.index(key)] = finding(key, **kw)
    return findings


def test_a_financial_basis_cannot_pass_while_marked_invalid():
    # On 72d3d51 this returned recommended / pass.
    details = {**FINANCIAL, "basis": "conservative-scenario", "basis_ok": False}
    result = evaluate(envelope(_swap("financial_evidence", details=details)), make_policy(), NOW)
    assert result["readiness"] == "research_needed"
    assert row(result, "financial_evidence")["state"] == "unknown"
    assert "evidence.inconsistent" in row(result, "financial_evidence")["reason_codes"]
    assert "financial.evidence" not in row(result, "financial_evidence")["reason_codes"]


def test_contradictory_hazard_and_sale_details_are_not_a_pass_or_a_fail():
    hazards = copy.deepcopy(HAZARDS)
    hazards["fire"] = {**hazards["fire"], "acceptable": False}
    hazard = evaluate(envelope(_swap(
        "hazard_acceptability",
        details={"acceptable": True, "hazards": hazards, **POLICY_BIND})), make_policy(), NOW)
    assert row(hazard, "hazard_acceptability")["state"] == "unknown"
    assert "evidence.inconsistent" in row(hazard, "hazard_acceptability")["reason_codes"]

    sale = evaluate(envelope(_swap(
        "specific_sale_available",
        details={"specific_sale": True, "generic_url": True})), make_policy(), NOW)
    assert row(sale, "specific_sale_available")["state"] == "unknown"
    assert "evidence.inconsistent" in row(sale, "specific_sale_available")["reason_codes"]
    assert sale["readiness"] == "research_needed"


def test_a_negative_claim_with_positive_details_is_not_a_verified_failure():
    result = evaluate(envelope(_swap("legal_access", finding="fails", details={"access": "legal"})),
                      make_policy(), NOW)
    assert result["readiness"] == "research_needed"
    assert row(result, "legal_access")["state"] == "unknown"
    assert "evidence.inconsistent" in row(result, "legal_access")["reason_codes"]
    assert "safety.legal_access" not in row(result, "legal_access")["reason_codes"]


def test_undetermined_adverse_details_block_another_positive():
    # On 72d3d51 a clean positive beside this undetermined record was recommended.
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"]
    findings.append(finding("legal_access", id="access-open", finding="undetermined", details={"access": "none"}))
    findings.append(finding("legal_access", id="access-ok", details={"access": "legal"}))
    result = evaluate(envelope(findings), make_policy(), NOW)
    assert result["readiness"] == "research_needed"
    assert row(result, "legal_access")["state"] == "unknown"
    assert "evidence.inconsistent" in row(result, "legal_access")["reason_codes"]


def test_a_clean_financial_rejection_still_rejects():
    result = evaluate(envelope(_swap(
        "financial_evidence", finding="fails", details={**FINANCIAL, "basis_ok": False})), make_policy(), NOW)
    assert result["readiness"] == "rejected"
    assert "financial.evidence" in row(result, "financial_evidence")["reason_codes"]


@pytest.mark.parametrize("km", [10, 10.0])
def test_the_version_1_sea_maximum_is_ten_kilometres(km):
    assert evaluate(envelope(complete()), make_policy(sea_max_km=km), NOW)["readiness"] == "recommended"


@pytest.mark.parametrize("km", [0, -1, 50, 100])
def test_a_relaxed_or_empty_sea_maximum_is_rejected(km):
    # On 72d3d51 sea_max_km 100 with a 50 km finding returned recommended.
    with pytest.raises(EligibilityError):
        evaluate(envelope(complete()), make_policy(sea_max_km=km), NOW)


def test_sea_and_hazard_reviews_must_name_this_policy():
    sea = _swap("sea_distance", details={"km": 2.5, "basis": "building-pin", "water": "sea",
                                         "policy_id": "other", "policy_version": "1"})
    sea_result = evaluate(envelope(sea), make_policy(), NOW)
    assert sea_result["readiness"] == "research_needed"
    assert "evidence.policy_mismatch" in row(sea_result, "sea_distance")["reason_codes"]
    assert "preference.sea_distance" not in row(sea_result, "sea_distance")["reason_codes"]

    hazards = copy.deepcopy(HAZARDS)
    hazards["fire"] = {**hazards["fire"], "acceptable": False}
    hazard = _swap("hazard_acceptability", finding="fails",
                   details={"acceptable": False, "hazards": hazards, "policy_id": "live-invest-synthetic",
                            "policy_version": "other"})
    hazard_result = evaluate(envelope(hazard), make_policy(), NOW)
    assert row(hazard_result, "hazard_acceptability")["state"] == "unknown"
    assert "safety.hazard_acceptability" not in row(hazard_result, "hazard_acceptability")["reason_codes"]


def test_an_unreviewed_supersession_cannot_erase_a_warning():
    # On 72d3d51 this returned recommended and kept the asserted warning out of the conflict.
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"]
    findings.append(finding("legal_access", id="access-ok"))
    findings.append(finding("legal_access", id="access-warn", provenance="asserted", finding="fails",
                            details={"access": "none"}))
    findings.append(finding("legal_access", id="access-erase", provenance="inferred", finding="undetermined",
                            details={}, supersedes="access-warn"))
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)


def test_an_undetermined_review_cannot_erase_a_negative():
    # On 72d3d51 this returned recommended.
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"]
    findings.append(finding("legal_access", id="access-ok"))
    findings.append(finding("legal_access", id="access-bad", finding="fails", details={"access": "none"}))
    findings.append(finding("legal_access", id="access-blank", finding="undetermined", details={},
                            supersedes="access-bad"))
    with pytest.raises(EligibilityError):
        evaluate(envelope(findings), make_policy(), NOW)


def test_supersession_cycles_and_multiple_replacements_are_rejected():
    self_loop = finding("legal_access", id="access-self", supersedes="access-self")
    with pytest.raises(EligibilityError):
        evaluate(envelope([self_loop]), make_policy(), NOW)

    left = finding("legal_access", id="access-left", supersedes="access-right")
    right = finding("legal_access", id="access-right", supersedes="access-left")
    with pytest.raises(EligibilityError):
        evaluate(envelope([left, right]), make_policy(), NOW)

    older = finding("legal_access", id="access-old", finding="fails", details={"access": "none"},
                    observed_at="2026-09-01T12:00:00+00:00", reviewed_at="2026-09-02T12:00:00+00:00")
    first = finding("legal_access", id="access-a", supersedes="access-old")
    second = finding("legal_access", id="access-b", supersedes="access-old")
    with pytest.raises(EligibilityError):
        evaluate(envelope([older, first, second]), make_policy(), NOW)


def test_a_chain_of_reviewed_replacements_keeps_only_the_latest():
    first = finding("legal_access", id="access-1", finding="fails", details={"access": "none"},
                    observed_at="2026-09-01T12:00:00+00:00", reviewed_at="2026-09-02T12:00:00+00:00")
    second = finding("legal_access", id="access-2", supersedes="access-1",
                     observed_at="2026-09-10T12:00:00+00:00", reviewed_at="2026-09-11T12:00:00+00:00")
    third = finding("legal_access", id="access-3", finding="fails", details={"access": "none"},
                    supersedes="access-2",
                    observed_at="2026-09-20T12:00:00+00:00", reviewed_at="2026-09-21T12:00:00+00:00")
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"]
    result = evaluate(envelope([*findings, first, second, third]), make_policy(), NOW)
    assert row(result, "legal_access")["state"] == "fail"
    dispositions = {item["id"]: item["disposition"] for item in row(result, "legal_access")["evidence"]}
    assert dispositions["access-3"] == "supporting"
    assert dispositions["access-1"] == "superseded"
    assert dispositions["access-2"] == "superseded"
    assert result["readiness"] == "rejected"


def test_an_older_review_cannot_supersede_a_newer_one():
    current = finding("legal_access", id="access-now", finding="fails", details={"access": "none"},
                      observed_at="2026-09-01T12:00:00+00:00", reviewed_at="2026-10-10T12:00:00+00:00")
    older = finding("legal_access", id="access-back", supersedes="access-now",
                    observed_at="2026-09-15T12:00:00+00:00", reviewed_at="2026-09-20T12:00:00+00:00")
    with pytest.raises(EligibilityError):
        evaluate(envelope([current, older]), make_policy(), NOW)


def test_a_stale_or_policy_mismatched_replacement_does_not_resolve():
    stale_new = finding(
        "legal_access", id="access-stale", supersedes="access-old",
        observed_at="2026-10-13T12:00:00+00:00", reviewed_at="2026-10-13T12:00:00+00:00",
        valid_until=NOW.isoformat())
    old = finding("legal_access", id="access-old", finding="fails", details={"access": "none"},
                  observed_at="2026-10-13T12:00:00+00:00", reviewed_at="2026-10-13T12:00:00+00:00",
                  valid_until="2026-10-20T12:00:00+00:00")
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"]
    stale = evaluate(envelope([*findings, old, stale_new]), make_policy(), NOW)
    assert row(stale, "legal_access")["state"] == "fail"
    stale_dispositions = {item["id"]: item["disposition"] for item in row(stale, "legal_access")["evidence"]}
    assert stale_dispositions["access-old"] == "supporting"
    assert stale_dispositions["access-stale"] == "stale"

    sea_old = finding("sea_distance", id="sea-old")
    sea_other = finding(
        "sea_distance", id="sea-other", finding="fails", supersedes="sea-old",
        observed_at="2026-10-10T12:00:00+00:00", reviewed_at="2026-10-11T12:00:00+00:00",
        details={"km": 12, "basis": "building-pin", "water": "sea",
                 "policy_id": "other-policy", "policy_version": "1"})
    sea_findings = [item for item in complete() if item["requirement_key"] != "sea_distance"]
    mismatched = evaluate(envelope([*sea_findings, sea_old, sea_other]), make_policy(), NOW)
    assert row(mismatched, "sea_distance")["state"] == "pass"
    sea_dispositions = {item["id"]: item["disposition"] for item in row(mismatched, "sea_distance")["evidence"]}
    assert sea_dispositions["sea-old"] == "supporting"
    assert sea_dispositions["sea-other"] == "policy_mismatch"
    assert mismatched["readiness"] == "recommended"


def test_inadequate_evidence_is_not_a_property_rejection():
    cases = [
        ("specific_sale_available", "fails", {"generic_url": True}, "execution.specific_sale_available"),
        ("specific_sale_available", "fails", {"specific_sale": False}, "execution.specific_sale_available"),
        ("building_location", "fails", {"precision": "street"}, "safety.building_location"),
        ("floor_area", "fails", {"basis": "land"}, "safety.floor_area"),
        ("procedural_readiness", "fails",
         {"method": "sealed-letter", "deadline_known": False, "can_submit": True},
         "execution.procedural_readiness"),
    ]
    for key, kind, details, code in cases:
        result = evaluate(envelope(_swap(key, finding=kind, details=details)), make_policy(), NOW)
        assert result["readiness"] == "research_needed", key
        assert row(result, key)["state"] == "unknown"
        assert code not in row(result, key)["reason_codes"]

    hazards = copy.deepcopy(HAZARDS)
    hazards["fire"] = {**hazards["fire"], "covered": False}
    covered = evaluate(envelope(_swap(
        "hazard_coverage", finding="fails", details={"hazards": hazards, **POLICY_BIND})), make_policy(), NOW)
    assert covered["readiness"] == "research_needed"
    assert "safety.hazard_coverage" not in row(covered, "hazard_coverage")["reason_codes"]


def test_established_unavailability_hazard_and_submission_failures_still_reject():
    sold = evaluate(envelope(_swap(
        "specific_sale_available", finding="fails",
        details={"specific_sale": True, "availability": "sold"})), make_policy(), NOW)
    assert "execution.specific_sale_available" in row(sold, "specific_sale_available")["reason_codes"]

    hazards = copy.deepcopy(HAZARDS)
    hazards["flood"] = {**hazards["flood"], "acceptable": False}
    hazard = evaluate(envelope(_swap(
        "hazard_acceptability", finding="fails",
        details={"acceptable": False, "hazards": hazards, **POLICY_BIND})), make_policy(), NOW)
    assert "safety.hazard_acceptability" in row(hazard, "hazard_acceptability")["reason_codes"]

    blocked = evaluate(envelope(_swap(
        "procedural_readiness", finding="fails",
        details={"method": "sealed-letter", "deadline_known": True, "can_submit": False})), make_policy(), NOW)
    assert "execution.procedural_readiness" in row(blocked, "procedural_readiness")["reason_codes"]

    missing_floor = evaluate(envelope(_swap(
        "floor_area", finding="fails", details={"usable_floor_absent": True})), make_policy(), NOW)
    assert "safety.floor_area" in row(missing_floor, "floor_area")["reason_codes"]
    assert missing_floor["readiness"] == "rejected"


def test_evidence_dispositions_separate_support_from_discarded_records():
    old = finding("legal_access", id="access-old", finding="fails", details={"access": "none"},
                  observed_at="2026-09-01T12:00:00+00:00", reviewed_at="2026-09-02T12:00:00+00:00")
    new = finding("legal_access", id="access-new", supersedes="access-old")
    findings = [item for item in complete() if item["requirement_key"] != "legal_access"]
    result = evaluate(envelope([*findings, old, new]), make_policy(), NOW)
    dispositions = {item["id"]: item["disposition"] for item in row(result, "legal_access")["evidence"]}
    assert dispositions["access-new"] == "supporting"
    assert dispositions["access-old"] == "superseded"
    assert row(result, "legal_access")["state"] == "pass"

    fail = finding("title_and_charges", id="title-fail", finding="fails",
                   details={"charges_reviewed": True, "exposure": "adverse"})
    meet = finding("title_and_charges", id="title-meet")
    rest = [item for item in complete() if item["requirement_key"] != "title_and_charges"]
    conflict = evaluate(envelope([*rest, fail, meet]), make_policy(), NOW)
    names = {item["id"]: item["disposition"] for item in row(conflict, "title_and_charges")["evidence"]}
    assert names["title-fail"] == "conflicting"
    assert names["title-meet"] == "conflicting"


@pytest.mark.parametrize("mutate", ["hazards", "exposure", "finding", "provenance", "age", "integer", "bundle"])
def test_malformed_shapes_raise_eligibility_error(mutate):
    evidence = envelope(complete())
    policy = make_policy()
    if mutate == "hazards":
        policy["required_hazards"] = ["fire", {"name": "flood"}]
    elif mutate == "exposure":
        evidence["findings"][REQUIREMENT_KEYS.index("title_and_charges")]["details"] = {
            "charges_reviewed": True, "exposure": ["adverse"]}
    elif mutate == "finding":
        evidence["findings"][0]["finding"] = {"value": "meets"}
    elif mutate == "provenance":
        evidence["findings"][0]["provenance"] = ["reviewed"]
    elif mutate == "age":
        policy["freshness"]["sea_distance"] = {"max_age_seconds": 10 ** 18}
    elif mutate == "integer":
        policy["sea_max_km"] = 10 ** 1000
    else:
        evidence["findings"][REQUIREMENT_KEYS.index("residential_identity")]["details"]["bundle"] = "true"
    with pytest.raises(EligibilityError):
        evaluate(evidence, policy, NOW)
