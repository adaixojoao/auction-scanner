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


# ─── Unit E — clearance binding without GET mutations ───────────────────────

def test_load_best_does_not_delete_stale_review_history(db, add, monkeypatch):
    import scoring
    monkeypatch.setattr(scoring, "local_price", lambda item: (500_000.0, "test series"))
    add(external_id="stale1", title="Moradia T3 em bom estado", price=30000, area_m2=80)
    lid = "eleiloes:stale1"
    store.record_evidence_revision(db, lid, "location", {"precision": "sale"})
    db.commit()
    rev = db.execute("SELECT SUM(rev) FROM evidence_revisions WHERE listing_id=?",
                     (lid,)).fetchone()[0]
    store.clear_extreme_discount(db, lid, note="reviewed", evidence_rev_sum=rev)
    store.record_evidence_revision(db, lid, "hazard", {"flood_m": 0.2})
    db.commit()
    ids = {it["id"] for it in store.load_best(db)}
    assert lid not in ids, "stale clearance must not count"
    row = db.execute("SELECT note, evidence_rev_sum FROM investigation_clearances"
                     " WHERE listing_id=?", (lid,)).fetchone()
    assert row is not None and row["note"] == "reviewed" and row["evidence_rev_sum"] == rev
    assert db.execute("SELECT COUNT(*) FROM investigation_clearances").fetchone()[0] == 1


def test_shadow_mapping_reads_stored_photo_payload():
    payload = {"shows_house": True, "condition": "good", "confidence": "high"}
    item = {"id": "x:1", "raw_json": json.dumps({"photo_check": payload})}
    assert store._shadow_evidence(item)["photo_check"] == payload
    # Top-level, when a caller sets one, wins.
    top = {"shows_house": False}
    assert store._shadow_evidence({**item, "photo_check": top})["photo_check"] == top
    # Nothing stored → None (UNKNOWN in the evaluator), not a crash.
    assert store._shadow_evidence({"id": "x:2", "raw_json": "{}"})["photo_check"] is None
    ev = {**deepcopy(FULL_EVIDENCE), "photo_check": store._shadow_evidence(item)["photo_check"]}
    dec = evaluator.evaluate(ev, now=NOW)
    assert next(r for r in dec.requirements if r.key == "photo_suitability").outcome == evaluator.PASS


# ─── Unit D — research selection wiring ─────────────────────────────────────

def _extreme_lead(db, add, monkeypatch, external_id="lead1"):
    import scoring
    import climate
    import geo
    # local 2000 €/m² × 80 m² = 160k against ≈11k all-in: an extreme discount.
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "city series"))
    # Live and invest needs a building pin, the sea close by and a good climate.
    monkeypatch.setattr(geo, "nearest_beach", lambda item, towns=None: {
        "km": 0.4, "approx": False, "name": "Praia", "text": "0.4 km"})
    monkeypatch.setattr(climate, "stored", lambda item: {
        "hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1},
        "flood_m": 0.0, "fire_danger": {"rcp45_2079-2098": 5}})
    add(external_id=external_id, title="Moradia T3", tipo="moradia", price=10000, area_m2=80,
        description="moradia em bom estado", url="https://example.test/" + external_id,
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    store.forget_scores(db)
    return f"eleiloes:{external_id}"


def test_slim_hidden_retains_investigation_metadata(db, add, monkeypatch):
    lid = _extreme_lead(db, add, monkeypatch)
    for key in ("extreme_discount", "invest_score", "live_score", "research_only",
                "investigation_cleared"):
        assert key in store._SLIM, key
    items = store.load_listings(db, filters={"min_score": 101}, include_hidden=True,
                                mode="both", slim_hidden=True)
    it = next(i for i in items if i["id"] == lid)
    assert it.get("_slim") and it["hidden_reason"]
    assert it.get("extreme_discount") is True
    assert "invest_score" in it and "live_score" in it
    assert "research_only" in it and "investigation_cleared" in it


@pytest.mark.parametrize("reason", [
    "cash €90,000 all-in is over the €50,000 you can lose (price)",
    "keyword 'ruina'",
    "district Faro not in filters.districts",
    "dismissed: not interested",
    "duplicate of eleiloes:x",
    "gone: withdrawn",
    "stale: gone from eleiloes since 2026-01-01",
    "expired",
])
def test_research_helper_does_not_bypass_cash_exclusion(reason):
    lead = {"id": "x:1", "extreme_discount": True, "invest_score": 80, "score": 80,
            "research_only": False, "hidden_reason": reason}
    assert not store.research_eligible(lead)
    assert store.research_selection([lead]) == ([], [])
    low = {**lead, "hidden_reason": "score 10 < filters.min_score 40"}
    assert store.research_eligible(low)
    # A plain (non-extreme) low-score row is not pulled in either.
    assert not store.research_eligible({**low, "extreme_discount": False})


def test_real_api_research_survives_request_score_cutoff(db, add, monkeypatch):
    import dashboard
    lid = _extreme_lead(db, add, monkeypatch)
    dashboard._LISTS.clear()
    dashboard.app.config["TESTING"] = True
    body = dashboard.app.test_client().get("/api/listings?mode=both&min_score=100").get_json()
    assert all(it["id"] != lid for it in body["items"])
    assert lid in [c["id"] for c in body["research"]["valued"]]


def test_enrich_order_includes_research_selected_rows(db, add, monkeypatch):
    import config
    import pipeline
    lid = _extreme_lead(db, add, monkeypatch)
    cfg = {**config.load_config(), "filters": {"min_score": 101}}
    order = pipeline.enrich_order(db, cfg)
    assert lid in [it["id"] for it in order]
