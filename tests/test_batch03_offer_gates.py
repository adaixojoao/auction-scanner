"""Batch 03: legacy operational recording shares canonical gates and amount formatting.

Most tests fake the gate/log helpers to pin routing and amount identity; the
real-helper cases (and tests/test_batch_3_1_consistency.py) cover the actual
gates and persistence. Synthetic data only.
"""
import json

import pytest

import dashboard


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    return dashboard.app.test_client()


@pytest.mark.parametrize("value,expected", [
    (2500, 2500.0), (2500.5, 2500.5), ("2500.50", 2500.5),
    ("0.01", 0.01), (0, 0.0), (None, None), ("", None),
])
def test_amount_round_trip(value, expected):
    from letters import parse_bid
    amount, text = dashboard._legacy_euro_amount(value)
    assert amount == expected
    if expected is None:
        assert text == ""
    else:
        assert parse_bid(text) == expected


@pytest.mark.parametrize("value", [True, [], {}, "nan", "inf", -1, "1.001", "4.000,00", "not money"])
def test_invalid_amount_is_rejected(value):
    with pytest.raises(ValueError):
        dashboard._legacy_euro_amount(value)


def test_operational_uses_canonical_bid_and_gate_records(client, monkeypatch):
    calls = []
    item = {"id": "synthetic:a", "country": "PT"}
    gates = {"location_override": "test location reason", "cash_override": "test cash reason",
             "checklist": {"summary": "synthetic"}, "checklist_override": "test checklist reason"}
    monkeypatch.setattr(dashboard, "_listing", lambda identity: item)

    def gate(record, is_offer, data, bid):
        calls.append(("gate", record, is_offer, bid))
        return None, gates

    def log(record, **kwargs):
        calls.append(("log", record, kwargs))
        return 42

    monkeypatch.setattr(dashboard, "_offer_gates", gate)
    monkeypatch.setattr(dashboard, "_log_sent", log)
    monkeypatch.setattr(dashboard, "_record_offer_evidence", lambda *args: calls.append(("evidence", args)))
    response = client.post("/api/carta-log", json={"purpose": "operational", "listing_id": "synthetic:a",
                                                    "bid_amount": 2500.5, "method": "online"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "id": 42, "purpose": "operational"}
    from letters import parse_bid
    assert parse_bid(calls[0][3]) == 2500.5
    assert calls[1][2]["bid"] == calls[0][3]
    assert calls[1][2]["location_override"] == gates["location_override"]
    assert calls[1][2]["cash_override"] == gates["cash_override"]
    assert calls[2][1][1] == 42


@pytest.mark.parametrize("gate_name", ["cash_gate", "checklist_gate", "location_gate"])
def test_blocked_gate_never_records(client, monkeypatch, gate_name):
    from flask import jsonify
    monkeypatch.setattr(dashboard, "_listing", lambda identity: {"id": identity})

    def blocked(*args):
        return (jsonify({"error": "synthetic blocker", gate_name: True}), 409), None

    monkeypatch.setattr(dashboard, "_offer_gates", blocked)
    monkeypatch.setattr(dashboard, "_log_sent", lambda *a, **kw: pytest.fail("Blocked offer logged"))
    response = client.post("/api/carta-log", json={"purpose": "operational", "listing_id": "synthetic:a",
                                                    "bid_amount": 1000})
    assert response.status_code == 409
    assert response.get_json()[gate_name] is True


def test_missing_operational_listing_cannot_be_logged(client, monkeypatch):
    monkeypatch.setattr(dashboard, "_listing", lambda identity: None)
    monkeypatch.setattr(dashboard, "_offer_gates", lambda *a: pytest.fail("Missing listing gated"))
    response = client.post("/api/carta-log", json={"purpose": "operational", "listing_id": "missing:a",
                                                    "bid_amount": 1000})
    assert response.status_code == 404


@pytest.mark.parametrize("fields", [
    {"bid_amount": 0}, {"bid_amount": None},
    {"bid_amount": 1000, "is_offer": False},
    {"bid_amount": 1000, "outcome": "won"},
    {"bid_amount": 1000, "sent_date": "2030-01-01"},
])
def test_operational_contract_rejects_ambiguous_events(client, monkeypatch, fields):
    monkeypatch.setattr(dashboard, "_listing", lambda identity: {"id": identity})
    monkeypatch.setattr(dashboard, "_log_sent", lambda *a, **kw: pytest.fail("Invalid operation logged"))
    response = client.post("/api/carta-log", json={"purpose": "operational", "listing_id": "synthetic:a",
                                                    **fields})
    assert response.status_code == 400


def test_historical_case_only_import_is_explicitly_unvalidated(client, db, monkeypatch):
    monkeypatch.setattr(dashboard, "_offer_gates", lambda *a: pytest.fail("History uses current gates"))
    response = client.post("/api/carta-log", json={"processo": "synthetic-case", "bid_amount": 2500.5,
                                                    "sent_date": "2030-01-01", "outcome": "won"})
    assert response.status_code == 200
    result = response.get_json()
    assert result["purpose"] == "historical"
    row = db.execute("SELECT * FROM carta_log WHERE id=?", (result["id"],)).fetchone()
    assert row["bid_amount"] == 2500.5
    assert row["checklist_summary"] == dashboard.HISTORICAL_SUMMARY
    assert row["location_level"] is None
    assert row["letter_text"] is None


def test_historical_information_request_does_not_count_as_offer(client, db):
    response = client.post("/api/carta-log", json={"processo": "synthetic-request", "is_offer": False,
                                                    "bid_amount": 100})
    assert response.status_code == 200
    row = db.execute("SELECT is_offer, bid_amount FROM carta_log WHERE id=?",
                     (response.get_json()["id"],)).fetchone()
    assert tuple(row) == (0, None)


@pytest.mark.parametrize("body", [
    [], {"processo": 1}, {"processo": "   "},
    {"processo": "test", "notes": []},
    {"processo": "test", "country": "XX"},
    {"processo": "test", "outcome": "invented"},
    {"processo": "test", "method": ""},
])
def test_invalid_legacy_body_is_rejected(client, body):
    assert client.post("/api/carta-log", json=body).status_code == 400


def test_a_decimal_bid_amount_is_stored_and_capped_as_the_same_euros(client, db, add):
    # Real gates, log and evidence: the euros sent are the euros stored, now
    # that the gates and the log both get the canonical bid text.
    import config
    config.update_config({"cash_on_hand": 1_000_000})
    add("citius", "o1", title="Moradia", price=30000,
        raw_json=json.dumps({"processo": "1/20.0T", "modalidade": "carta fechada"}))
    r = client.post("/api/carta-log", json={"purpose": "operational", "listing_id": "citius:o1",
                                             "bid_amount": 26000.5,
                                             "checklist_override": "Checked with my lawyer by phone"})
    assert r.status_code == 200, r.get_json()
    row = db.execute("SELECT * FROM carta_log WHERE id=?", (r.get_json()["id"],)).fetchone()
    assert row["bid_amount"] == 26000.5 and row["is_offer"] == 1
    assert row["checklist_summary"] != dashboard.HISTORICAL_SUMMARY
