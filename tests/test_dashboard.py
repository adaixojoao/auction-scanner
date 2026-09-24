import pytest

import dashboard
from db import record_scrape


@pytest.fixture
def client(db, monkeypatch):
    cfg = {"max_price": 100000, "filters": {}, "dashboard": {},
           "proponente": {"nome": "Test Person", "nif": "123", "morada": "Rua 1", "email": "t@x.pt"},
           "telegram": {"enabled": False}}
    monkeypatch.setattr(dashboard, "_config", lambda: cfg)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_pages_render(client):
    for path in ("/", "/health", "/map", "/cartas-review"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert b"Source health" in client.get("/").data


def test_listings_api_uses_shared_scoring_and_sanitises(client, db, add):
    add(external_id="a", title='<img src=x onerror=alert(1)>Moradia', price=20000,
        url="https://x.pt/1")
    db.execute("INSERT INTO listings (id, source, country, external_id, title, price, url, first_seen, last_seen) "
               "VALUES ('legacy:1','legacy','PT','1','Moradia',15000,'javascript:alert(1)','2026-01-01','2026-01-01')")
    db.commit()
    data = client.get("/api/listings?max_price=100000").get_json()
    by_id = {it["id"]: it for it in data["items"]}
    assert by_id["legacy:1"]["url"] is None
    assert "raw_json" not in by_id["eleiloes:a"]

    from scoring import score
    expected, _ = score({"title": '<img src=x onerror=alert(1)>Moradia', "price": 20000, "source": "eleiloes"})
    assert by_id["eleiloes:a"]["score"] == expected
    assert data["stats"]["new_today"] == 1   # the legacy row was first seen in January


def test_hidden_toggle(client, db, add):
    add(external_id="old", title="Moradia", price=20000, date_end="2000-01-01T10:00:00")
    assert client.get("/api/listings").get_json()["total"] == 0
    data = client.get("/api/listings?show_hidden=1").get_json()
    assert data["total"] == 1 and data["items"][0]["hidden_category"] == "expired"


def test_health_api(client, db):
    record_scrape(db, "bpi", count=0, status="error", message="boom")
    health = {h["source"]: h for h in client.get("/api/health").get_json()}
    assert health["bpi"]["state"] == "error" and health["bpi"]["country"] == "PT"
    assert health["eleiloes"]["state"] == "never run"


def test_proponente_from_config(client):
    assert client.get("/api/proponente").get_json()["nome"] == "Test Person"


def test_carta_log_roundtrip(client, db):
    assert client.post("/api/carta-log", json={}).status_code == 400
    r = client.post("/api/carta-log", json={"listing_id": "citius:1", "processo": "1/20.0T",
                                             "country": "PT", "bid_amount": "2500"})
    log_id = r.get_json()["id"]
    assert client.patch(f"/api/carta-log/{log_id}", json={"outcome": "won"}).status_code == 200
    assert client.patch("/api/carta-log/999", json={"outcome": "won"}).status_code == 404
    rows = client.get("/api/carta-log").get_json()
    assert rows[0]["outcome"] == "won" and rows[0]["country"] == "PT" and rows[0]["bid_amount"] == 2500


def test_cartas_candidates(client, add):
    import json
    add("citius", "p1", title="Moradia sita em Moura", price=30000,
        raw_json=json.dumps({"processo": "1/20.0T", "modalidade": "Negociação particular"}))
    cands = client.get("/api/cartas-candidates").get_json()
    assert cands[0]["id"] == "citius:p1" and cands[0]["modalidade"] == "NEGOCIACAO PARTICULAR"
