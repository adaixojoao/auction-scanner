"""Pure Live and invest eligibility.

evaluate(evidence, policy, now) judges supplied findings. It does not rank a
listing, read settings, or collect evidence. A result is decision support:
review metadata is a record of a claimed review, not proof of who reviewed it
or of the underlying document.

No mandatory requirement in this profile can be skipped.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

EVALUATOR_VERSION = "1"
SCHEMA_VERSION = "1"
MODE = "live_and_invest"

REQUIREMENT_KEYS = (
    "residential_identity",
    "complete_ownership",
    "vacant_possession",
    "title_and_charges",
    "legal_access",
    "residential_use",
    "inspection",
    "work_scope",
    "floor_area",
    "building_location",
    "sea_distance",
    "hazard_coverage",
    "hazard_acceptability",
    "specific_sale_available",
    "financial_evidence",
    "financial_margin",
    "cash_fit",
    "procedural_readiness",
)

_FINANCIAL = {"financial_evidence", "financial_margin", "cash_fit"}
_FINDINGS = {"meets", "fails", "undetermined"}
_PROVENANCE = {"reviewed", "asserted", "inferred"}

_LIMITATIONS = (
    "This is decision support, not a legal, engineering, or valuation certificate.",
    "Review metadata records a claimed review. It does not authenticate the reviewer or the document.",
    "Asserted and inferred findings are research leads, not verification.",
    "A star, shortlist, score, or source does not change eligibility.",
    "Rejected is an evaluation result. It does not dismiss, hide, or delete a listing.",
)

_RESEARCH = {
    "evidence.missing": "No finding was supplied for {key}.",
    "evidence.stale": "Reviewed evidence for {key} is stale.",
    "evidence.conflict": "Current findings for {key} conflict.",
    "evidence.mismatch": "Evidence for {key} refers to a different property or sale.",
    "evidence.insufficient": "Reviewed evidence for {key} does not establish the requirement.",
    "evidence.unreviewed": "Only asserted or inferred findings were supplied for {key}.",
    "evidence.policy_mismatch": "The financial finding for {key} was made under a different policy.",
}


class EligibilityError(ValueError):
    """The evidence or policy contract is malformed.

    An unresolved fact about the property is not this error. That is an
    unknown requirement. This error means the caller built the input wrongly.
    """


def evaluate(evidence: dict, policy: dict, now: datetime) -> dict:
    """Eligibility of one property sale at an explicit time.

    `evidence` and `policy` are JSON-compatible dictionaries. `now` must be
    timezone-aware. The result is JSON-compatible and does not depend on
    finding order. Inputs are not modified.
    """
    moment = _require_aware(now, "now")
    rules = _policy(policy)
    findings = _evidence(evidence, moment)
    _supersession(findings)
    rows = [_requirement(key, findings, evidence, rules, moment) for key in REQUIREMENT_KEYS]
    failed = [row["key"] for row in rows if row["state"] == "fail"]
    unknown = [row["key"] for row in rows if row["state"] == "unknown"]
    if failed:
        readiness = "rejected"
    elif unknown:
        readiness = "research_needed"
    else:
        readiness = "recommended"
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "policy_id": rules["policy_id"],
        "policy_version": rules["policy_version"],
        "mode": MODE,
        "subject_id": evidence["subject_id"],
        "sale_id": evidence["sale_id"],
        "evaluated_at": moment.isoformat(),
        "readiness": readiness,
        "requirements": rows,
        "reason_codes": _unique(code for row in rows for code in row["reason_codes"]),
        "failed_keys": failed,
        "unknown_keys": unknown,
        "research_needs": [row["research_need"] for row in rows if row["research_need"]],
        "limitations": list(_LIMITATIONS),
    }


def _requirement(key: str, findings: list[dict], evidence: dict, rules: dict, now: datetime) -> dict:
    mine = [item for item in findings if item["requirement_key"] == key]
    in_scope = [item for item in mine if _in_scope(item, evidence)]
    out_of_scope = [item for item in mine if not _in_scope(item, evidence)]
    active = [item for item in in_scope if item["id"] not in _superseded_ids(findings)]
    current = [item for item in active if not _is_stale(item, rules["freshness"][key], now)]
    stale = [item for item in active if _is_stale(item, rules["freshness"][key], now)]

    usable = []
    mismatched = []
    for item in current:
        if key in _FINANCIAL and not _same_financial_policy(item, rules):
            mismatched.append(item)
        else:
            usable.append(item)

    reviewed_pass = [item for item in usable if _role(key, item, rules) == "pass"]
    reviewed_fail = [item for item in usable if _role(key, item, rules) == "fail"]
    conflict = _conflicts(reviewed_pass, reviewed_fail, usable)
    historic = _unique(_historic_code(key, item, rules) for item in stale)

    if conflict:
        state, codes = "unknown", ["evidence.conflict"]
    elif reviewed_fail:
        state, codes = "fail", _unique(_adverse_code(key, item.get("details") or {}, rules) for item in reviewed_fail)
    elif reviewed_pass:
        state, codes = "pass", []
    else:
        state, codes = "unknown", _unknown_codes(in_scope, out_of_scope, stale, mismatched, usable)

    shown = in_scope if in_scope else out_of_scope
    research = None
    if state == "unknown":
        research = _research_text(key, codes, historic)
    return {
        "key": key,
        "state": state,
        "reason_codes": codes,
        "historic_reason_codes": historic,
        "evidence": [{"id": item["id"], "provenance": item["provenance"]}
                     for item in sorted(shown, key=lambda item: item["id"])],
        "research_need": research,
    }


def _conflicts(passes: list[dict], fails: list[dict], usable: list[dict]) -> bool:
    if passes and fails:
        return True
    if passes and any(item["finding"] == "fails" and item["provenance"] != "reviewed" for item in usable):
        return True
    if fails and any(item["finding"] == "meets" and item["provenance"] != "reviewed" for item in usable):
        return True
    return False


def _unknown_codes(in_scope, out_of_scope, stale, mismatched, usable) -> list[str]:
    if not in_scope and not out_of_scope:
        return ["evidence.missing"]
    codes = []
    if mismatched:
        codes.append("evidence.policy_mismatch")
    if out_of_scope and not in_scope:
        codes.append("evidence.mismatch")
    if stale:
        codes.append("evidence.stale")
    reviewed = [item for item in usable if item["provenance"] == "reviewed"]
    unreviewed = [item for item in usable if item["provenance"] != "reviewed"]
    if reviewed:
        codes.append("evidence.insufficient")
    elif unreviewed and "evidence.policy_mismatch" not in codes:
        codes.append("evidence.unreviewed")
    if not codes:
        codes.append("evidence.insufficient")
    return _unique(codes)


def _research_text(key: str, codes: list[str], historic: list[str]) -> str:
    parts = [_RESEARCH[code].format(key=key) for code in codes if code in _RESEARCH]
    text = " ".join(parts) if parts else f"More evidence is needed for {key}."
    if historic:
        text += " Earlier review: " + ", ".join(historic) + "."
    return text


def _historic_code(key: str, item: dict, rules: dict) -> str:
    if item["provenance"] != "reviewed":
        return ""
    if item["finding"] == "fails":
        return _adverse_code(key, item.get("details") or {}, rules) or ""
    if item["finding"] == "meets" and _supports_pass(key, item.get("details") or {}, rules):
        return "evidence.historic_pass"
    return ""


def _role(key: str, item: dict, rules: dict) -> str:
    if item["provenance"] != "reviewed":
        return "unreviewed"
    details = item.get("details") or {}
    if item["finding"] == "meets" and _supports_pass(key, details, rules):
        return "pass"
    if item["finding"] == "fails" and _adverse_code(key, details, rules):
        return "fail"
    return "insufficient"


def _supports_pass(key: str, details: dict, rules: dict) -> bool:
    if not isinstance(details, dict):
        return False
    if key == "residential_identity":
        return details.get("asset_kind") == "residential" and details.get("bundle") is not True
    if key == "complete_ownership":
        return details.get("right") == "complete"
    if key == "vacant_possession":
        return details.get("possession") == "vacant"
    if key == "title_and_charges":
        return details.get("charges_reviewed") is True and details.get("exposure") in {"none", "reviewed"}
    if key == "legal_access":
        return details.get("access") == "legal"
    if key == "residential_use":
        return details.get("use") == "residential_supported"
    if key == "inspection":
        return details.get("inspection") == "adequate" and _text(details.get("scope"))
    if key == "work_scope":
        return details.get("heavy_rebuild") is False and details.get("uncertainty") == "bounded"
    if key == "floor_area":
        return details.get("basis") == "floor" and details.get("compatible") is True
    if key == "building_location":
        return details.get("precision") == "building"
    if key == "sea_distance":
        km = details.get("km")
        return (details.get("water") == "sea" and _number(km) and 0 <= km <= rules["sea_max_km"]
                and _text(details.get("basis")))
    if key == "hazard_coverage":
        return _hazards(details, rules, "covered")
    if key == "hazard_acceptability":
        return details.get("acceptable") is True and _hazards(details, rules, "acceptable")
    if key == "specific_sale_available":
        return details.get("specific_sale") is True and details.get("generic_url") is not True
    if key == "financial_evidence":
        return _text(details.get("basis"))
    if key == "financial_margin":
        return details.get("meets_margin") is True
    if key == "cash_fit":
        return details.get("fits_cash") is True
    if key == "procedural_readiness":
        return (_text(details.get("method")) and details.get("deadline_known") is True
                and details.get("can_submit") is True)
    return False


def _adverse_code(key: str, details: dict, rules: dict) -> str:
    if not isinstance(details, dict):
        return ""
    if key == "residential_identity" and (details.get("asset_kind") in {"land", "movable", "bundle"}
                                          or details.get("bundle") is True):
        return "safety.residential_identity"
    if key == "complete_ownership" and details.get("right") in {"fraction", "bare"}:
        return "safety.complete_ownership"
    if key == "vacant_possession" and details.get("possession") in {"occupied", "refused"}:
        return "safety.vacant_possession"
    if key == "title_and_charges" and details.get("exposure") == "adverse":
        return "safety.title_and_charges"
    if key == "legal_access" and details.get("access") == "none":
        return "safety.legal_access"
    if key == "residential_use" and details.get("use") == "prohibited":
        return "safety.residential_use"
    if key == "inspection" and details.get("inspection") == "inadequate":
        return "safety.inspection"
    if key == "work_scope" and details.get("heavy_rebuild") is True:
        return "preference.heavy_rebuild"
    if key == "work_scope" and details.get("uncertainty") == "unbounded":
        return "safety.work_scope"
    if key == "floor_area" and details.get("basis") in {"land", "footprint"}:
        return "safety.floor_area"
    if key == "building_location" and details.get("precision") in {"street", "town", "municipality", "advertised"}:
        return "safety.building_location"
    if key == "sea_distance" and (details.get("water") in {"river", "lake"} or _over_sea(details, rules)):
        return "preference.sea_distance"
    if key == "hazard_coverage" and _hazard_negative(details, rules, "covered"):
        return "safety.hazard_coverage"
    if key == "hazard_acceptability" and (details.get("acceptable") is False
                                           or _hazard_negative(details, rules, "acceptable")):
        return "safety.hazard_acceptability"
    if key == "specific_sale_available" and (details.get("specific_sale") is False
                                              or details.get("generic_url") is True):
        return "execution.specific_sale_available"
    if key == "financial_evidence" and details.get("basis_ok") is False:
        return "financial.evidence"
    if key == "financial_margin" and details.get("meets_margin") is False:
        return "financial.margin"
    if key == "cash_fit" and details.get("fits_cash") is False:
        return "financial.cash_fit"
    if key == "procedural_readiness" and (details.get("can_submit") is False
                                           or details.get("deadline_known") is False):
        return "execution.procedural_readiness"
    return ""


def _over_sea(details: dict, rules: dict) -> bool:
    km = details.get("km")
    return bool(_number(km) and km > rules["sea_max_km"])


def _hazards(details: dict, rules: dict, flag: str) -> bool:
    hazards = details.get("hazards")
    if not isinstance(hazards, dict) or not rules["required_hazards"]:
        return False
    for name in rules["required_hazards"]:
        entry = hazards.get(name)
        if not isinstance(entry, dict):
            return False
        if not _text(entry.get("reference")) or not _text(entry.get("scale")):
            return False
        if entry.get("applicable") is not True or entry.get(flag) is not True:
            return False
    return True


def _hazard_negative(details: dict, rules: dict, flag: str) -> bool:
    hazards = details.get("hazards")
    if not isinstance(hazards, dict):
        return False
    return any(isinstance(hazards.get(name), dict) and hazards[name].get(flag) is False
               for name in rules["required_hazards"])


def _same_financial_policy(item: dict, rules: dict) -> bool:
    details = item.get("details") or {}
    return (details.get("financial_policy_id") == rules["financial_policy_id"]
            and details.get("financial_policy_version") == rules["financial_policy_version"])


def _is_stale(item: dict, rule: dict, now: datetime) -> bool:
    moments = []
    if item.get("valid_until") is not None:
        moments.append(item["valid_until"])
    if "max_age_seconds" in rule:
        moments.append(item["observed_at"] + timedelta(seconds=rule["max_age_seconds"]))
    if not moments:
        return False
    return now >= min(moments)


def _in_scope(item: dict, evidence: dict) -> bool:
    return item["subject_id"] == evidence["subject_id"] and item["sale_id"] == evidence["sale_id"]


def _superseded_ids(findings: list[dict]) -> set[str]:
    return {item["supersedes"] for item in findings if item.get("supersedes")}


def _policy(policy: dict) -> dict:
    if not isinstance(policy, dict):
        raise EligibilityError("policy must be an object")
    _reject_nonfinite(policy, "policy")
    if policy.get("schema_version") != SCHEMA_VERSION:
        raise EligibilityError("unsupported policy schema")
    if policy.get("mode") != MODE:
        raise EligibilityError("policy mode must be live_and_invest")
    _bypass(policy)
    sea = policy.get("sea_max_km")
    if not _number(sea) or sea <= 0:
        raise EligibilityError("sea_max_km must be a finite number greater than 0")
    hazards = policy.get("required_hazards")
    if not isinstance(hazards, list) or not hazards or len(hazards) != len(set(hazards)):
        raise EligibilityError("required_hazards must list each critical hazard once")
    if not all(isinstance(name, str) and name.strip() for name in hazards):
        raise EligibilityError("required_hazards must be non-empty strings")
    freshness = policy.get("freshness")
    if not isinstance(freshness, dict) or set(freshness) != set(REQUIREMENT_KEYS):
        raise EligibilityError("freshness must name every mandatory requirement and no others")
    parsed = {key: _freshness_rule(key, freshness[key]) for key in REQUIREMENT_KEYS}
    if policy.get("heavy_rebuild_excluded") is not True:
        raise EligibilityError("heavy rebuild exclusion cannot be turned off")
    return {
        "policy_id": _require_text(policy.get("policy_id"), "policy_id"),
        "policy_version": _require_text(policy.get("policy_version"), "policy_version"),
        "sea_max_km": sea,
        "required_hazards": tuple(hazards),
        "freshness": parsed,
        "financial_policy_id": _require_text(policy.get("financial_policy_id"), "financial_policy_id"),
        "financial_policy_version": _require_text(policy.get("financial_policy_version"),
                                                  "financial_policy_version"),
    }


def _freshness_rule(key: str, rule: dict) -> dict:
    if not isinstance(rule, dict):
        raise EligibilityError(f"freshness for {key} must be an object")
    immutable = rule.get("immutable") is True
    has_age = "max_age_seconds" in rule
    if immutable == has_age:
        raise EligibilityError(f"freshness for {key} needs a duration or an immutable rule")
    if immutable:
        return {"immutable": True}
    age = rule.get("max_age_seconds")
    if not _number(age) or age <= 0:
        raise EligibilityError(f"freshness for {key} needs a positive finite duration")
    return {"max_age_seconds": age}


def _bypass(policy: dict) -> None:
    if "disabled_requirements" in policy or "exemptions" in policy or "optional_requirements" in policy:
        raise EligibilityError("a mandatory requirement cannot be switched off")
    if policy.get("require_title") is False:
        raise EligibilityError("a mandatory requirement cannot be switched off")


def _evidence(evidence: dict, now: datetime) -> list[dict]:
    if not isinstance(evidence, dict):
        raise EligibilityError("evidence must be an object")
    _reject_nonfinite(evidence, "evidence")
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise EligibilityError("unsupported evidence schema")
    _require_text(evidence.get("subject_id"), "subject_id")
    _require_text(evidence.get("sale_id"), "sale_id")
    raw = evidence.get("findings")
    if not isinstance(raw, list):
        raise EligibilityError("findings must be a list")
    findings = [_finding(item, now) for item in raw]
    ids = [item["id"] for item in findings]
    if len(ids) != len(set(ids)):
        raise EligibilityError("finding ids must be unique")
    return findings


def _finding(item: dict, now: datetime) -> dict:
    if not isinstance(item, dict):
        raise EligibilityError("a finding must be an object")
    if item.get("finding") == "not_applicable" or (isinstance(item.get("details"), dict)
                                                   and (item["details"].get("not_applicable") is True
                                                        or item["details"].get("exempt") is True)):
        raise EligibilityError("a mandatory requirement cannot be marked not applicable")
    key = item.get("requirement_key")
    if key not in REQUIREMENT_KEYS:
        raise EligibilityError("unknown requirement")
    kind = item.get("finding")
    if kind not in _FINDINGS:
        raise EligibilityError("unknown finding value")
    provenance = item.get("provenance")
    if provenance not in _PROVENANCE:
        raise EligibilityError("unknown provenance")
    observed = _parse_time(item.get("observed_at"), "observed_at")
    if observed > now:
        raise EligibilityError("observed_at is in the future")
    reviewed_at = None
    reviewer = None
    if provenance == "reviewed":
        reviewer = _require_text(item.get("reviewer_ref"), "reviewer_ref")
        reviewed_at = _parse_time(item.get("reviewed_at"), "reviewed_at")
        if reviewed_at < observed:
            raise EligibilityError("reviewed_at is before observed_at")
        if reviewed_at > now:
            raise EligibilityError("reviewed_at is in the future")
    valid_until = None
    if item.get("valid_until") is not None:
        valid_until = _parse_time(item.get("valid_until"), "valid_until")
        if valid_until < observed:
            raise EligibilityError("valid_until is before observed_at")
    supersedes = item.get("supersedes")
    if supersedes is not None and not _text(supersedes):
        raise EligibilityError("supersedes must name a finding")
    details = item.get("details")
    if details is not None and not isinstance(details, dict):
        raise EligibilityError("details must be an object")
    if isinstance(details, dict) and isinstance(details.get("km"), bool):
        raise EligibilityError("boolean is not a number")
    return {
        "id": _require_text(item.get("id"), "id"),
        "requirement_key": key,
        "subject_id": _require_text(item.get("subject_id"), "subject_id"),
        "sale_id": _require_text(item.get("sale_id"), "sale_id"),
        "finding": kind,
        "provenance": provenance,
        "reference": _require_text(item.get("reference"), "reference"),
        "observed_at": observed,
        "reviewer_ref": reviewer,
        "reviewed_at": reviewed_at,
        "valid_until": valid_until,
        "supersedes": supersedes,
        "details": details,
    }


def _supersession(findings: list[dict]) -> None:
    by_id = {item["id"]: item for item in findings}
    for item in findings:
        target_id = item.get("supersedes")
        if not target_id:
            continue
        target = by_id.get(target_id)
        if target is None:
            raise EligibilityError("supersedes an unknown finding")
        if (target["requirement_key"] != item["requirement_key"]
                or target["subject_id"] != item["subject_id"]
                or target["sale_id"] != item["sale_id"]):
            raise EligibilityError("supersession crosses requirement, property, or sale")
        if target["provenance"] == "reviewed" and item["provenance"] != "reviewed":
            raise EligibilityError("an unreviewed finding cannot supersede a review")
        if item["observed_at"] < target["observed_at"]:
            raise EligibilityError("an older observation cannot supersede a newer one")
        seen = {item["id"]}
        cursor = target
        while cursor.get("supersedes"):
            nxt = cursor["supersedes"]
            if nxt in seen:
                raise EligibilityError("supersession cycle")
            seen.add(nxt)
            cursor = by_id.get(nxt)
            if cursor is None:
                break


def _require_aware(now: datetime, field: str) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise EligibilityError(f"{field} must be timezone-aware")
    return now.astimezone(timezone.utc)


def _parse_time(value, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EligibilityError(f"{field} must be an aware timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EligibilityError(f"{field} must be an aware timestamp") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise EligibilityError(f"{field} is naive")
    return parsed.astimezone(timezone.utc)


def _require_text(value, field: str) -> str:
    if not _text(value):
        raise EligibilityError(f"{field} must be a non-empty string")
    return value.strip()


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _reject_nonfinite(value, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise EligibilityError(f"{path} is not a finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_nonfinite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{path}[{index}]")


def _unique(codes) -> list[str]:
    return sorted({code for code in codes if code})
