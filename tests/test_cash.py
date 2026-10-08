"""Cash you can lose: hide a purchase you cannot pay, and refuse the offer."""
import json

import pytest

import config
import costs
import dashboard
from db import connect, hidden_category, load_listings, set_listing_status


@pytest.fixture
def client(db, monkeypatch):
    config.save_config({"filters": {}, "checklist": {"blocking": {"pt_court": []}},
                        "proponente": {"nome": "Test Person", "nif": "123",
                                       "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_the_low_end_of_repairs_counts_toward_the_cash():
    house = {"title": "Moradia T3", "kind": "home", "country": "PT", "source": "citius",
             "price": 20000, "area_m2": 100}
    out = costs.money_out(house)
    assert out["amount"] == 40610          # 20,000 + 610 of tax and fees + €200/m²
    assert costs.over_cash(house, 40000)
    assert costs.over_cash(house, 0) is None
    sound = {**house, "description": "em bom estado", "price": 8000, "area_m2": 50}
    assert costs.money_out(sound)["amount"] < 40000
    assert "5%" in costs.sealed_cheque(house, 20000)
    assert costs.sealed_cheque({**house, "source": "financas"}, 20000) is None


def test_a_purchase_over_the_cash_is_hidden_unless_shortlisted(db, add):
    config.save_config({"cash_on_hand": 40000})
    dear = add("eleiloes", "dear", title="Moradia", price=20000, area_m2=100)
    cheap = add("eleiloes", "cheap", title="Moradia em bom estado", price=8000, area_m2=50)
    filters = {"min_score": 0}
    visible = {it["id"] for it in load_listings(db, filters=filters)}
    assert cheap["id"] in visible and dear["id"] not in visible
    hidden = {it["id"]: it for it in load_listings(db, filters=filters, include_hidden=True)}
    assert hidden[dear["id"]]["hidden_reason"].startswith("cash")
    assert hidden_category(hidden[dear["id"]]["hidden_reason"]) == "over cash"
    set_listing_status(db, dear["id"], "shortlisted")
    db.commit()
    assert dear["id"] in {it["id"] for it in load_listings(db, filters=filters)}


def test_unset_cash_hides_nothing_and_blocks_an_offer(client, db, add):
    add("citius", "p1", title="Moradia", price=20000, area_m2=100,
        raw_json=json.dumps({"processo": "1/20.0TBMRA", "modalidade": "carta fechada",
                             "agente_email": "ae@solic.pt"}))
    listed = client.get("/api/listings?min_score=0").get_json()
    assert listed["cash"] == {"set": False, "amount": None}
    hidden = client.get("/api/listings?show_hidden=1&min_score=0").get_json()["items"]
    found = [it for it in listed["items"] + hidden if it["id"] == "citius:p1"]
    assert found and not (found[0].get("hidden_reason") or "").startswith("cash")
    refused = client.post("/api/offers/sent", json={"id": "citius:p1", "bid": "20.000,00"})
    assert refused.status_code == 409 and refused.get_json()["cash_gate"] == "unset"
    info = client.post("/api/offers/sent", json={"id": "citius:p1", "type": "pt_info"})
    assert info.status_code == 200


def test_an_offer_over_the_cash_needs_a_reason(client, db, add):
    config.update_config({"cash_on_hand": 40000})
    add("citius", "p1", title="Moradia", price=20000, area_m2=100,
        raw_json=json.dumps({"processo": "1/20.0TBMRA", "modalidade": "carta fechada",
                             "agente_email": "ae@solic.pt"}))
    letter = client.get("/api/offers/letter?id=citius:p1&bid=20.000,00").get_json()
    assert letter["cash"]["over"] is True
    assert "€1,000" in letter["cash"]["deposit"]
    refused = client.post("/api/offers/sent", json={"id": "citius:p1", "bid": "20.000,00",
                                                    "cash_override": "no"})
    assert refused.status_code == 409 and refused.get_json()["cash_gate"] is True
    sent = client.post("/api/offers/sent", json={"id": "citius:p1", "bid": "20.000,00",
                                                 "cash_override": "repairs are already paid for"}).get_json()
    row = db.execute("SELECT cash_override FROM carta_log WHERE id = ?", (sent["log_id"],)).fetchone()
    assert row["cash_override"] == "repairs are already paid for"
    within = client.get("/api/offers/letter?id=citius:p1&bid=4.000,00&type=pt_carta_fechada").get_json()
    assert within["cash"]["over"] is False and within["cash"]["deposit"]


def test_settings_reject_a_negative_cash_figure(client):
    assert client.post("/api/settings", json={"cash_on_hand": -1}).status_code == 400
    assert client.post("/api/settings", json={"cash_on_hand": 40000}).status_code == 200
    assert config.load_config()["cash_on_hand"] == 40000


def test_a_version_16_database_keeps_the_override_column(tmp_path):
    path = str(tmp_path / "old.db")
    conn = connect(path)
    conn.execute("PRAGMA user_version = 16")
    conn.commit()
    conn.close()
    conn = connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(carta_log)")}
    assert "cash_override" in cols
    conn.close()
