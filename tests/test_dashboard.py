import json

import pytest

import dashboard
from db import record_scrape


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "proponente": {"nome": "Test Person", "nif": "123",
                                                      "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_every_page_renders_with_the_shared_layout(client):
    for path in ("/", "/offers", "/map", "/sources", "/settings"):
        r = client.get(path)
        assert r.status_code == 200, path
        html = r.get_data(as_text=True)
        assert 'class="topbar"' in html and "Scan now" in html, path
    assert client.get("/cartas-review").status_code == 302   # old bookmarks still work
    assert client.get("/health").headers["Location"].endswith("/sources")
    assert client.get("/static/app.js").status_code == 200


def test_listings_api_uses_shared_scoring_and_sanitises(client, db, add):
    add(external_id="a", title='<img src=x onerror=alert(1)>Moradia', price=20000, url="https://x.pt/1")
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


def test_hidden_view_shows_only_hidden_with_reasons(client, add):
    add(external_id="old", title="Moradia", price=20000, date_end="2000-01-01T10:00:00")
    add(external_id="live", title="Moradia", price=20000)
    open_ = client.get("/api/listings").get_json()
    assert [it["external_id"] for it in open_["items"]] == ["live"]
    assert open_["stats"]["hidden"] == {}   # expired rows are pre-filtered out of the open view
    hidden = client.get("/api/listings?show_hidden=1").get_json()
    assert [it["hidden_category"] for it in hidden["items"]] == ["expired"]


def test_shortlist_and_dismiss(client, add):
    add(external_id="a", title="Moradia", price=20000)
    add(external_id="b", title="Moradia", price=20000)
    assert client.post("/api/listings/status", json={"id": "eleiloes:a", "status": "shortlisted"}).status_code == 200
    assert client.post("/api/listings/status", json={"id": "eleiloes:b", "status": "dismissed"}).status_code == 200
    assert client.post("/api/listings/status", json={"id": "eleiloes:a", "status": "bogus"}).status_code == 400
    assert client.post("/api/listings/status", json={"id": "nope", "status": None}).status_code == 404

    data = client.get("/api/listings").get_json()
    assert [it["id"] for it in data["items"]] == ["eleiloes:a"]
    assert data["stats"]["shortlisted"] == 1 and data["stats"]["hidden"] == {"dismissed": 1}
    only = client.get("/api/listings?status=shortlisted").get_json()
    assert [it["id"] for it in only["items"]] == ["eleiloes:a"]
    # restore
    client.post("/api/listings/status", json={"id": "eleiloes:b", "status": None})
    assert client.get("/api/listings").get_json()["total"] == 2


def test_offer_flow_review_letter_pdf_sent_won(client, db, add, monkeypatch):
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada", "agente_email": "ae@solic.pt"}
    add("citius", "p1", title="Moradia sita em Moura", price=30000, area_m2=120, concelho="Moura",
        raw_json=json.dumps(raw))

    offers = client.get("/api/offers").get_json()
    assert [o["id"] for o in offers["review"]] == ["citius:p1"]
    o = offers["review"][0]
    assert o["modalidade"] == "CARTA FECHADA" and o["bid"] == "4.000,00" and o["offer"] is None

    letter = client.get("/api/offers/letter?id=citius:p1&bid=4.000,00").get_json()
    assert "Test Person" in letter["text"] and "Juízo de Moura" in letter["text"]
    assert "EUR 4.000,00 (quatro mil euros)" in letter["text"]
    assert letter["to"] == "ae@solic.pt" and letter["bid_text"] == "quatro mil euros"
    assert "below the minimum of EUR 25,500" in letter["warning"]
    ok = client.get("/api/offers/letter?id=citius:p1&bid=26.000,00").get_json()
    assert ok["warning"] is None and "vinte e seis mil euros" in ok["text"]

    monkeypatch.setattr("cartas._unicode_fonts", lambda: None)   # no font download in tests
    pdf = client.get("/api/offers/letter.pdf?id=citius:p1&bid=4.000,00")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert "carta_165-10.3TBMRA.pdf" in pdf.headers["Content-Disposition"]

    sent = client.post("/api/offers/sent", json={"id": "citius:p1", "bid": "4.000,00"}).get_json()
    offers = client.get("/api/offers").get_json()
    assert offers["review"] == [] and offers["sent"][0]["key"] == f"log:{sent['log_id']}"
    assert offers["sent"][0]["offer"]["bid"] == "4.000,00"

    assert client.patch(f"/api/carta-log/{sent['log_id']}", json={"outcome": "maybe"}).status_code == 400
    assert client.patch(f"/api/carta-log/{sent['log_id']}", json={"outcome": "won"}).status_code == 200
    offers = client.get("/api/offers").get_json()
    assert offers["sent"] == [] and offers["closed"][0]["offer"]["outcome"] == "won"
    listing = client.get("/api/listings").get_json()["items"][0]
    assert listing["offer_outcome"] == "won"


def test_shortlisted_listing_joins_offers_even_below_the_candidate_score(client, add):
    add("zvg", "k1", "DE", title="Garage", tipo="imovel", price=5000)   # parking: low score
    assert client.get("/api/offers").get_json()["review"] == []
    client.post("/api/listings/status", json={"id": "zvg:k1", "status": "shortlisted"})
    review = client.get("/api/offers").get_json()["review"]
    assert [o["id"] for o in review] == ["zvg:k1"]
    client.post("/api/listings/status", json={"id": "zvg:k1", "status": "dismissed"})
    offers = client.get("/api/offers").get_json()
    assert offers["review"] == [] and [o["id"] for o in offers["rejected"]] == ["zvg:k1"]


def test_scan_api(client, monkeypatch):
    started = []
    monkeypatch.setattr(dashboard, "_start_scan", lambda **kw: started.append(kw))
    assert client.get("/api/scan").get_json()["running"] is False
    assert client.post("/api/scan", json={"countries": ["PT"]}).status_code == 202
    assert client.post("/api/scan", json={"source": "citius"}).status_code == 202
    assert started == [{"countries": ["PT"], "source_names": None},
                       {"countries": None, "source_names": ["citius"]}]
    assert client.post("/api/scan", json={"countries": ["XX"]}).status_code == 400
    assert client.post("/api/scan", json={"source": "nope"}).status_code == 400


def test_health_api(client, db):
    record_scrape(db, "bpi", count=0, status="error", message="boom")
    health = {h["source"]: h for h in client.get("/api/health").get_json()}
    assert health["bpi"]["state"] == "error" and health["bpi"]["country"] == "PT"
    assert health["eleiloes"]["state"] == "never run" and health["eleiloes"]["description"]


def test_settings_roundtrip_keeps_other_keys(client):
    import config
    config.update_config({"apify_token": "keep-me"})
    s = client.get("/api/settings").get_json()
    assert s["proponente"]["nome"] == "Test Person"
    r = client.post("/api/settings", json={
        "max_price": 60000,
        "filters": {"countries": ["PT", "ES"], "exclude_keywords": ["ocupado"], "min_score": 50},
        "telegram": {"enabled": True, "token": "abc", "chat_id": "1", "min_score": None},
        "dashboard": {"debug": True},       # not editable from the page: ignored
    })
    assert r.status_code == 200
    saved = config.load_user_config()
    assert saved["max_price"] == 60000 and saved["filters"]["countries"] == ["PT", "ES"]
    assert saved["apify_token"] == "keep-me" and "dashboard" not in saved
    assert "min_score" not in saved["telegram"]      # None means "leave as is"
    assert config.load_config()["telegram"]["min_score"] == 75
    assert client.post("/api/settings", json={"filters": {"countries": ["XX"]}}).status_code == 400


def test_background_task_is_windows_only(client):
    assert client.get("/api/background").get_json()["supported"] is False
    assert client.post("/api/background", json={"enabled": True}).status_code == 400


def test_export_report(client, add):
    add(external_id="a", title="Moradia", price=20000)
    r = client.get("/export/report.md")
    assert r.status_code == 200 and b"EU Investment Scanner Report" in r.data
    assert client.get("/export/report.exe").status_code == 404


def test_heartbeat_and_ping(client):
    assert client.get("/api/ping").get_json() == {"app": "auction-scanner"}
    dashboard.app.config["LAST_HEARTBEAT"] = None
    client.post("/api/heartbeat")
    assert dashboard.app.config["LAST_HEARTBEAT"] is not None


def test_foreign_origins_are_refused(db, monkeypatch):
    import config
    config.save_config({})
    dashboard.app.config["TESTING"] = False
    try:
        c = dashboard.app.test_client()
        base = {"base_url": "http://127.0.0.1:8050"}
        assert c.get("/api/ping", **base).status_code == 200
        assert c.get("/api/ping", base_url="http://evil.example:8050").status_code == 403   # DNS rebinding
        assert c.post("/api/heartbeat", headers={"Origin": "https://evil.example"}, **base).status_code == 403
        assert c.post("/api/heartbeat", headers={"Origin": "http://127.0.0.1:8050"}, **base).status_code == 200
    finally:
        dashboard.app.config["TESTING"] = True


def test_carta_log_legacy_api(client):
    assert client.post("/api/carta-log", json={}).status_code == 400
    r = client.post("/api/carta-log", json={"listing_id": "citius:1", "processo": "1/20.0T",
                                             "country": "PT", "bid_amount": "2500"})
    assert r.get_json()["ok"]
    assert client.get("/api/carta-log").get_json()[0]["bid_amount"] == 2500
