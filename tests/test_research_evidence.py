"""research_evidence: the listing panel's research notes come only from data the
scanner stored, keep research_indicators' limits, and never touch the score."""
import json
from datetime import date

import pytest

import dashboard
import research_evidence
from db import load_listings

TODAY = date(2026, 10, 16)


def _set_raw(db, listing_id, **values):
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id=?", (listing_id,)).fetchone()[0] or "{}")
    raw.update(values)
    db.execute("UPDATE listings SET raw_json=? WHERE id=?", (json.dumps(raw), listing_id))
    db.commit()


def _item(db, listing_id):
    return load_listings(db, include_hidden=True, where="id = ?", params=(listing_id,))[0]


def _cats(panel):
    return {i["category"]: i for i in panel["indicators"]}


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_a_new_listing_has_only_its_first_price_as_evidence(db, add):
    row = add(external_id="a", title="Moradia", price=20000)
    panel = research_evidence.research_panel(db, _item(db, row["id"]), TODAY)
    cats = _cats(panel)
    assert [e["event_type"] for e in cats["sale_event_history"]["value"]["events"]] == ["price_observed"]
    assert {c for c, i in cats.items() if i["status"] != "unknown"} == {"sale_event_history"}
    assert panel["subject_lot_id"] == row["id"]


def test_price_history_and_a_withdrawn_link_are_sale_events_not_failures(db, add):
    row = add(external_id="a", title="Moradia", price=20000)
    db.execute("DELETE FROM price_history WHERE listing_id=?", (row["id"],))
    db.executemany("INSERT INTO price_history (listing_id, observed_at, price) VALUES (?,?,?)",
                   [(row["id"], "2026-09-01T00:00:00+00:00", 30000),
                    (row["id"], "2026-09-08T00:00:00+00:00", 30000),     # unchanged: not repeated
                    (row["id"], "2026-10-01T00:00:00+00:00", 20000)])
    db.commit()
    _set_raw(db, row["id"], link_check={"availability": "withdrawn", "why": "retirado",
                                        "at": "2026-10-10T00:00:00+00:00"})
    hist = _cats(research_evidence.research_panel(db, _item(db, row["id"]), TODAY))["sale_event_history"]
    types = [e["event_type"] for e in hist["value"]["events"]]
    assert types == ["price_observed", "price_observed", "withdrawn"]
    assert hist["value"]["explicit_auction_failure_count"] == 0
    assert any("do not prove an auction was unsold" in c for c in hist["caveats"])


def test_an_earlier_round_is_not_counted_as_a_failed_auction(db):
    item = {"id": "eleiloes:b", "earlier_round": {"id": "eleiloes:a", "ended": "2026-05-01", "price": 40000}}
    events = research_evidence._sale_events(db, item, TODAY)
    assert events[0]["value"]["event_type"] == "earlier_round_ended"
    assert events[0]["value"]["amount"] == 40000


def test_notice_area_against_the_spanish_cadastre_is_a_question(db, add):
    row = add("aeat", "c", "ES", title="Vivienda", price=20000, area_m2=120, tipo="imovel")
    _set_raw(db, row["id"], catastro={"built_m2": 85, "plot_m2": 400})
    doc = _cats(research_evidence.research_panel(db, _item(db, row["id"]), TODAY))["document_contradictions"]
    assert doc["status"] == "contradiction"
    claims = {c["document_type"]: c["claimed_value"] for c in doc["value"]["contradictions"][0]["claims"]}
    assert claims == {"listing": 120, "cadastre": 85}
    assert any("not proof of defective title" in c for c in doc["caveats"])


def test_site_check_is_habitat_evidence_with_its_limits(db, add):
    row = add(external_id="d", title="Terreno rústico", price=5000, tipo="terreno")
    _set_raw(db, row["id"], site_check={"protected": ["Natura 2000: Serra"], "forest_share": 0.6,
                                        "exact": False, "at": "2026-09-01T00:00:00+00:00"})
    hab = _cats(research_evidence.research_panel(db, _item(db, row["id"]), TODAY))["ecological_connectivity"]
    assert hab["status"] == "informational"
    obs = hab["value"]["observations"][0]
    assert obs["value"]["protected_areas"] == ["Natura 2000: Serra"] and obs["location_confidence"] <= 0.5
    assert any("does not establish habitat quality" in c for c in hab["caveats"])


def test_detail_api_shows_research_and_leaves_the_score_alone(client, db, add):
    row = add(external_id="e", title="Moradia", price=20000)
    before = _item(db, row["id"])["score"]
    body = client.get(f"/api/listing?id={row['id']}").get_json()
    assert len(body["research"]["indicators"]) == 10
    assert body["score"] == before
    assert "score" not in json.dumps(body["research"])


def test_a_research_failure_does_not_break_the_panel(client, add, monkeypatch):
    row = add(external_id="f", title="Moradia", price=20000)
    monkeypatch.setattr(research_evidence, "research_panel",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    resp = client.get(f"/api/listing?id={row['id']}")
    assert resp.status_code == 200 and resp.get_json()["research"] is None


def test_listing_page_renders_the_research_section(client):
    html = client.get("/").get_data(as_text=True)
    assert "function researchHtml" in html and "not a score or a bid" in html
