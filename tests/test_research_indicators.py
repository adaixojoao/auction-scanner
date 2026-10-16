"""research_indicators: indicators report evidence with its limits. Missing
evidence is "unknown", never a negative finding, and nothing is a score or bid."""
import json
from datetime import date, timedelta

import pytest

from research_indicators import SUPPORTED_KINDS, Evidence, EvidenceIndex, build_indicators

TODAY = date(2026, 10, 16)
LOT = "eleiloes:LO123"
CATEGORIES = {
    "property_specific_comparables", "document_contradictions", "sale_event_history",
    "acquisition_feasibility", "local_liquidity", "travel_access", "parcel_geometry",
    "historical_imagery", "ecological_connectivity", "restoration_feasibility",
}


def ev(kind, value, n=[0], **kw):
    n[0] += 1
    row = {"evidence_id": kw.pop("evidence_id", f"e{n[0]}"), "kind": kind, "source": "test",
           "observed_on": kw.pop("observed_on", (TODAY - timedelta(days=10)).isoformat()),
           "lot_id": kw.pop("lot_id", LOT), "value": value,
           "confidence": kw.pop("confidence", 0.9), "location_confidence": kw.pop("location_confidence", 0.9)}
    row.update(kw)
    return row


def run(evidence, subject=None, **kw):
    out = build_indicators({"lot_id": LOT, **(subject or {})}, evidence, TODAY, **kw)
    return out, {i["category"]: i for i in out["indicators"]}


def sale(price, area, status="completed", distance=500, ptype="house", **kw):
    return ev("comparable_sale", {"transaction_status": status, "sale_price": price, "area_m2": area,
                                  "distance_m": distance, "property_type": ptype}, **kw)


# ── shape and safety ─────────────────────────────────────────────────────────

def test_every_category_is_present_and_unknown_without_evidence():
    out, cats = run([])
    assert set(cats) == CATEGORIES
    assert all(c["status"] == "unknown" and c["value"] == {} for c in cats.values())
    assert all("No current, usable evidence" in c["caveats"][0] for c in cats.values())


def test_output_is_json_and_carries_no_score_or_bid():
    out, _ = run([sale(100000, 100)], {"price": 50000, "area_m2": 100, "property_type": "house"})
    text = json.dumps(out)
    for word in ("score", "bid_cap", "max_bid", "eligible", "approved"):
        assert word not in text
    assert out["schema_version"] == 1 and out["subject_lot_id"] == LOT and out["as_of"] == "2026-10-16"


def test_subject_lot_is_required():
    with pytest.raises(ValueError):
        build_indicators({"lot_id": " "}, [], TODAY)


# ── evidence index ───────────────────────────────────────────────────────────

def test_other_lots_future_stale_and_low_confidence_are_excluded_with_reasons():
    rows = [
        ev("liquidity_observation", {"listings": 4}, evidence_id="other", lot_id="eleiloes:LO124"),
        ev("liquidity_observation", {"listings": 4}, evidence_id="future",
           observed_on=(TODAY + timedelta(days=1)).isoformat()),
        ev("liquidity_observation", {"listings": 4}, evidence_id="old",
           observed_on=(TODAY - timedelta(days=400)).isoformat()),
        ev("liquidity_observation", {"listings": 4}, evidence_id="tight", expires_after_days=5),
        ev("liquidity_observation", {"listings": 4}, evidence_id="weak", confidence=0.2),
    ]
    _, cats = run(rows, min_confidence=0.5)
    liquidity = cats["local_liquidity"]
    assert liquidity["status"] == "unknown"
    text = " ".join(liquidity["caveats"])
    for reason in ("other: excluded because lot_id", "future: observation date is in the future",
                   "old: stale (400 days", "tight: stale (10 days old; limit is 5",
                   "weak: confidence below"):
        assert reason in text


def test_copies_of_one_source_count_once():
    rows = [ev("route_observation", {"minutes": 12}, evidence_id="orig", source_chain_id="osrm-1"),
            ev("route_observation", {"minutes": 12}, evidence_id="copy", source_chain_id="osrm-1")]
    _, cats = run(rows)
    assert cats["travel_access"]["evidence_ids"] == ["orig"]
    assert any("copy: duplicate source chain" in c for c in cats["travel_access"]["caveats"])


def test_malformed_records_become_issues_not_crashes():
    rows = [{"kind": "sale_event"},
            ev("sale_event", {}, confidence=2),
            ev("not_a_kind", {}),
            ev("sale_event", "not an object"),
            ev("sale_event", {}, observed_on="16/10/2026")]
    out, cats = run(rows)
    assert len(out["issues"]) == 5 and cats["sale_event_history"]["status"] == "unknown"


def test_evidence_objects_are_accepted_directly():
    record = Evidence.from_mapping(ev("parcel_observation", {"frontage_m": 12}))
    index = EvidenceIndex(LOT, [record], TODAY)
    assert index.for_kind("parcel_observation")[0] == [record]
    assert "parcel_observation" in SUPPORTED_KINDS


# ── 1. comparables: asking prices are not completed transactions ─────────────

def test_comparables_use_only_completed_sales_of_the_same_type_in_range():
    rows = [sale(100000, 100), sale(120000, 100), sale(110000, 100),
            sale(500000, 100, status="asking"),           # an asking price, not a sale
            sale(900000, 100, ptype="flat"),               # another type
            sale(900000, 100, distance=80000),             # too far
            sale(float("nan"), 100), sale(100000, 0)]      # unusable
    _, cats = run(rows, {"price": 55000, "area_m2": 100, "property_type": "house"})
    comp = cats["property_specific_comparables"]
    assert comp["status"] == "informational" and comp["value"]["comparable_count"] == 3
    assert comp["value"]["median_comparable_price_per_m2"] == 1100
    assert comp["value"]["indicative_discount_percent"] == pytest.approx(50.0)
    assert any("not a formal valuation" in c for c in comp["caveats"])
    assert any("asking prices are not completed sales" in c for c in comp["caveats"])


def test_only_asking_prices_leave_comparables_unknown():
    _, cats = run([sale(100000, 100, status="asking")], {"price": 50000, "area_m2": 100})
    assert cats["property_specific_comparables"]["status"] == "unknown"


def test_few_comparables_are_flagged_fragile_and_no_area_is_unknown():
    _, cats = run([sale(100000, 100)], {"price": 50000, "area_m2": 100, "property_type": "house"})
    assert any("fragile" in c for c in cats["property_specific_comparables"]["caveats"])
    _, cats = run([sale(100000, 100)], {"price": 50000})
    assert cats["property_specific_comparables"]["status"] == "unknown"


# ── 1. documents: a discrepancy is not defective title ───────────────────────

def test_document_contradictions_are_listed_as_questions():
    rows = [ev("document_claim", {"field": "area_m2", "claimed_value": 120, "document_type": "notice"}),
            ev("document_claim", {"field": "area_m2", "claimed_value": 95, "document_type": "cadastre"}),
            ev("document_claim", {"field": "use", "claimed_value": "housing"}),
            ev("document_claim", {"field": "use", "claimed_value": "housing"})]
    _, cats = run(rows)
    doc = cats["document_contradictions"]
    assert doc["status"] == "contradiction" and doc["value"]["contradiction_count"] == 1
    assert doc["value"]["contradictions"][0]["field"] == "area_m2"
    assert any("not proof of defective title" in c for c in doc["caveats"])


def test_consistent_documents_are_informational():
    rows = [ev("document_claim", {"field": "use", "claimed_value": {"a": 1, "b": 2}}),
            ev("document_claim", {"field": "use", "claimed_value": {"b": 2, "a": 1}})]
    _, cats = run(rows)
    assert cats["document_contradictions"]["status"] == "informational"


# ── 1. sale history: expired or relisted does not prove unsold ───────────────

def test_only_an_explicit_failed_auction_counts_as_failed():
    rows = [ev("sale_event", {"event_type": t}) for t in ("expired", "withdrawn", "relisted", "removed")]
    _, cats = run(rows)
    hist = cats["sale_event_history"]
    assert hist["value"]["explicit_auction_failure_count"] == 0
    assert any("do not prove an auction was unsold" in c for c in hist["caveats"])
    _, cats = run(rows + [ev("sale_event", {"event_type": "auction_failed"})])
    assert cats["sale_event_history"]["value"]["explicit_auction_failure_count"] == 1


def test_reserve_reductions_are_reported_in_date_order():
    rows = [ev("sale_event", {"event_type": "reserve_changed", "reserve_amount": 80000},
               observed_on="2026-06-01"),
            ev("sale_event", {"event_type": "reserve_changed", "reserve_amount": 100000},
               observed_on="2026-01-01")]
    _, cats = run(rows)
    cut = cats["sale_event_history"]["value"]["observed_reserve_reductions"]
    assert len(cut) == 1 and cut[0]["from_amount"] == 100000 and cut[0]["change_percent"] == -20.0


# ── 1. feasibility: a checklist, not clearance ───────────────────────────────

def test_feasibility_is_incomplete_until_every_requirement_is_met():
    rows = [ev("acquisition_requirement", {"requirement": "deposit", "status": "met"}),
            ev("acquisition_requirement", {"requirement": "lawyer", "status": "maybe"})]
    _, cats = run(rows)
    feas = cats["acquisition_feasibility"]
    assert feas["status"] == "incomplete" and feas["value"]["status_counts"]["unknown"] == 1
    assert any("not legal clearance" in c for c in feas["caveats"])
    _, cats = run(rows[:1])
    assert cats["acquisition_feasibility"]["status"] == "informational"


def test_requirements_older_than_thirty_days_are_stale():
    _, cats = run([ev("acquisition_requirement", {"status": "met"},
                      observed_on=(TODAY - timedelta(days=31)).isoformat())])
    assert cats["acquisition_feasibility"]["status"] == "unknown"


# ── 2 and 3. observations carry what they cannot establish ───────────────────

@pytest.mark.parametrize("kind, category, limit", [
    ("liquidity_observation", "local_liquidity", "removed advertisement does not prove a completed sale"),
    ("route_observation", "travel_access", "straight-line distance can hide a long journey"),
    ("parcel_observation", "parcel_geometry", "does not establish a legal access right"),
    ("aerial_observation", "historical_imagery", "cannot prove occupancy or structural safety"),
    ("habitat_observation", "ecological_connectivity", "does not establish habitat quality"),
])
def test_observation_indicators_state_their_limits(kind, category, limit):
    _, cats = run([ev(kind, {"note": "seen"}, resolution_m=10)])
    ind = cats[category]
    assert ind["status"] == "informational" and ind["value"]["observations"][0]["resolution_m"] == 10
    assert any(limit in c for c in ind["caveats"])


def test_restoration_reports_a_cost_range_and_its_limits():
    rows = [ev("restoration_observation", {"factor": "invasive cover", "estimated_cost_eur": 4000}),
            ev("restoration_observation", {"factor": "erosion", "estimated_cost_eur": 9000}),
            ev("restoration_observation", {"factor": "burns", "estimated_cost_eur": -5}),
            ev("restoration_observation", {"factor": "grazing"})]
    _, cats = run(rows)
    rest = cats["restoration_feasibility"]
    assert (rest["value"]["estimated_cost_eur_min"], rest["value"]["estimated_cost_eur_max"]) == (4000, 9000)
    assert rest["value"]["costed_observation_count"] == 2
    assert any("Satellite greenness is not biodiversity" in c for c in rest["caveats"])
    assert any("cost range is partial" in c for c in rest["caveats"])
