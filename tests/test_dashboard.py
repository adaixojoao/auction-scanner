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


def test_listings_by_kind_follow_the_settings(client, add):
    add("eleiloes", "r1", title="Prédio rústico com olival", tipo="terreno_rustico", area_m2=8000, price=2000)
    add("eleiloes", "h1", title="Moradia T2", tipo="moradia", price=20000)
    add("eleiloes", "s1", title="Loja", tipo="loja/escritorio", price=20000)
    # under 1 ha by default: pushed below the minimum score, so hidden
    assert client.get("/api/listings?kind=rural_plot").get_json()["items"] == []
    hidden = client.get("/api/listings?kind=rural_plot&show_hidden=1").get_json()["items"]
    assert hidden[0]["kind"] == "rural_plot" and hidden[0]["hidden_category"] == "low score"
    assert any("too small" in r for r in hidden[0]["reasons"])
    stats = client.get("/api/listings").get_json()["stats"]
    assert (stats["homes"], stats["rural_plots"]) == (1, 0)

    client.post("/api/settings", json={"filters": {"rural_min_m2": 5000, "rural_max_eur_m2": 0.5}})
    rural = client.get("/api/listings?kind=rural_plot").get_json()["items"][0]
    assert "big rural plot (8 000 m²)" in rural["reasons"] and "very cheap land (€0.25/m²)" in rural["reasons"]
    assert client.get("/api/settings").get_json()["filters"]["rural_min_m2"] == 5000


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

    monkeypatch.setattr("letters._unicode_fonts", lambda: None)   # no font download in tests
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


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((msg, to_addrs))


def test_spanish_information_request_by_email(client, add, monkeypatch):
    raw = {"autoridad": "Juzgado nº 3 de Sevilla", "autoridad_email": "juzgado3@justicia.es"}
    add("spain", "SUB-JA-2099-1", "ES", title="Vivienda en Sevilla", tipo="inmueble", price=90000,
        date_end="2099-01-01T10:00:00", raw_json=json.dumps(raw))
    assert client.get("/api/offers").get_json()["review"] == []       # online sale: only if shortlisted
    client.post("/api/listings/status", json={"id": "spain:SUB-JA-2099-1", "status": "shortlisted"})
    o = client.get("/api/offers").get_json()["review"][0]
    assert o["channel"] == "online" and o["sale"] == "Online auction"
    assert [t["key"] for t in o["letter_types"]] == ["es_info"] and not o["letter_types"][0]["is_offer"]
    assert o["contact"] == {"role": "Court / authority", "name": "Juzgado nº 3 de Sevilla",
                            "email": "juzgado3@justicia.es", "phone": ""}

    letter = client.get("/api/offers/letter?id=spain:SUB-JA-2099-1&type=es_info").get_json()
    assert letter["to"] == "juzgado3@justicia.es" and letter["is_offer"] is False
    assert letter["warning"] is None and "Solicitud de información" in letter["subject"]
    assert client.get("/api/offers/letter?id=spain:SUB-JA-2099-1&type=fr_mandat").status_code == 400

    body = {"id": "spain:SUB-JA-2099-1", "type": "es_info", "to": "juzgado3@justicia.es"}
    no_smtp = client.post("/api/offers/email", json=body)
    assert no_smtp.status_code == 400 and "not set up" in no_smtp.get_json()["error"]

    client.post("/api/settings", json={"notifications": {"smtp_host": "smtp.example.com", "smtp_port": 587,
                                                         "smtp_user": "me@example.com", "smtp_password": "pw"}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    FakeSMTP.sent = []
    assert client.post("/api/offers/email", json={**body, "to": "not an address"}).status_code == 400
    sent = client.post("/api/offers/email", json=body).get_json()
    msg, to_addrs = FakeSMTP.sent[0]
    assert to_addrs == ["juzgado3@justicia.es", "me@example.com"] and msg["Reply-To"] == "t@x.pt"
    pdf = [p for p in msg.iter_attachments()][0]
    assert pdf.get_content_type() == "application/pdf" and pdf.get_filename().startswith("solicitud-info_")

    offers = client.get("/api/offers").get_json()
    assert offers["review"] == [] and offers["sent"][0]["offer"]["is_offer"] is False
    assert offers["sent"][0]["offer"]["sent_to"] == "juzgado3@justicia.es"
    listing = client.get("/api/listings").get_json()["items"][0]
    assert listing["offer_outcome"] is None and listing["status"] == "shortlisted"   # not an offer

    # an answer puts it back under To review, ready for the actual bid
    client.patch(f"/api/carta-log/{sent['log_id']}", json={"outcome": "answered"})
    offers = client.get("/api/offers").get_json()
    assert [o["id"] for o in offers["review"]] == ["spain:SUB-JA-2099-1"]
    assert offers["closed"][0]["offer"]["outcome"] == "answered"

    logged = client.post("/api/offers/sent", json={"id": "spain:SUB-JA-2099-1", "bid": "70.000,00",
                                                   "method": "online"}).get_json()
    offer = client.get("/api/offers").get_json()["sent"][0]["offer"]
    assert offer["log_id"] == logged["log_id"] and offer["method"] == "online" and offer["bid"] == "70.000,00"


def test_sent_letters_are_kept_as_sent(client, add, monkeypatch):
    import config
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    add("france", "f1", "FR", title="Maison", price=40000, date_end="2099-01-01T14:00:00")
    q = {"id": "france:f1", "type": "fr_mandat", "bid": "55.000,00"}
    text = client.get("/api/offers/letter", query_string=q).get_json()["text"]
    edited = text.replace("Maître ________________", "Maître Claire Martin")

    pdf = client.post("/api/offers/letter.pdf", json={**q, "text": edited})
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    sent = client.post("/api/offers/sent", json={**q, "text": edited, "method": "lawyer"}).get_json()

    # later changes to your details or the listing do not rewrite what was sent
    config.update_config({"proponente": {"nome": "Someone Else"}})
    offer = client.get("/api/offers").get_json()["sent"][0]["offer"]
    assert "Maître Claire Martin" in offer["letter_text"] and "Test Person" in offer["letter_text"]
    assert offer["method"] == "lawyer" and offer["letter_subject"].startswith("Demande de représentation")
    log_pdf = client.get(f"/api/offers/log/{sent['log_id']}.pdf")
    assert log_pdf.status_code == 200 and "lettre_f1.pdf" in log_pdf.headers["Content-Disposition"]


def test_letters_logged_before_they_were_stored_are_rebuilt(client, db, add, monkeypatch):
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    add("citius", "p9", title="Moradia", price=30000,
        raw_json=json.dumps({"processo": "1/20.0T", "modalidade": "carta fechada"}))
    log_id = client.post("/api/carta-log", json={"listing_id": "citius:p9", "processo": "1/20.0T",
                                                 "bid_amount": 26000}).get_json()["id"]
    assert client.get("/api/offers").get_json()["sent"][0]["offer"]["letter_text"] == ""
    pdf = client.get(f"/api/offers/log/{log_id}.pdf")
    assert pdf.status_code == 200 and "carta_1-20.0T.pdf" in pdf.headers["Content-Disposition"]
    assert client.get("/api/offers/log/999.pdf").status_code == 404


def test_bid_warnings_follow_the_sale(client, add):
    add("novobanco", "b1", title="Apartamento T2", price=80000)
    add("france", "f1", "FR", title="Maison", price=40000)
    add("eleiloes", "e1", title="Moradia", price=30000)
    bank = client.get("/api/offers/letter?id=novobanco:b1&bid=10.000,00").get_json()
    assert bank["type"] == "pt_banco" and bank["warning"] is None           # no 85% rule for banks
    low = client.get("/api/offers/letter?id=france:f1&type=fr_mandat&bid=30.000,00").get_json()
    assert "mise à prix" in low["warning"]
    assert client.get("/api/offers/warning?id=eleiloes:e1&bid=1.000,00&type=online").get_json()["warning"]
    assert client.get("/api/offers/warning?id=eleiloes:e1&bid=26.000,00&type=online").get_json()["warning"] is None

    add("pvp_giustizia", "i1", "IT", title="Appartamento", price=40000)
    add("zvg", "z1", "DE", title="Haus", tipo="imovel", price=100000)
    warn = lambda lid, bid: client.get(f"/api/offers/warning?id={lid}&bid={bid}&type=online").get_json()["warning"]
    assert "offerta minima of EUR 30,000" in warn("pvp_giustizia:i1", "29.000,00")
    assert warn("pvp_giustizia:i1", "30.000,00") is None
    assert "EUR 50,000 (half)" in warn("zvg:z1", "60.000,00") and warn("zvg:z1", "70.000,00") is None
    client.post("/api/listings/status", json={"id": "zvg:z1", "status": "shortlisted"})
    z = next(o for o in client.get("/api/offers").get_json()["review"] if o["id"] == "zvg:z1")
    assert z["sale"] == "Court hearing (in person)" and z["bid_card"]["title"] == "Bid at the hearing"
    assert [t["key"] for t in z["letter_types"]] == ["de_info"]


def test_sale_dates_export_to_a_calendar(client, add):
    add("france", "f1", "FR", title="Maison", price=40000, date_end="2099-10-15T14:00:00",
        raw_json=json.dumps({"tribunal": "Tribunal Judiciaire de Nîmes", "avocat_nom": "Jean Dupont"}))
    add("zvg", "z1", "DE", title="Haus", price=90000, date_end="2099-11-12")
    add("citius", "old", title="Moradia", price=9000, date_end="2000-01-01T10:00:00")
    add("citius", "nodate", title="Moradia sem data", price=9000)

    one = client.get("/api/offers/calendar.ics?id=france:f1")
    text = one.get_data(as_text=True)
    assert one.mimetype == "text/calendar" and "sale-france-f1.ics" in one.headers["Content-Disposition"]
    assert "DTSTART:20991015T120000Z" in text              # 14:00 in Paris
    assert "SUMMARY:Court hearing (FR): Maison" in text and "LOCATION:Tribunal Judiciaire de Nîmes" in text
    assert "Seller's lawyer: Jean Dupont" in text.replace("\r\n ", "") and "TRIGGER:-P1D" in text
    assert client.get("/api/offers/calendar.ics?id=citius:nodate").status_code == 404

    for lid in ("france:f1", "zvg:z1", "citius:old"):
        client.post("/api/listings/status", json={"id": lid, "status": "shortlisted"})
    everything = client.get("/api/offers/calendar.ics").get_data(as_text=True)
    assert everything.count("BEGIN:VEVENT") == 2 and "DTSTART;VALUE=DATE:20991112" in everything
    assert "citius:old" not in everything                   # past sales are left out


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


def test_background_task_is_windows_only(client, monkeypatch):
    monkeypatch.setattr(dashboard.sys, "platform", "linux")
    assert client.get("/api/background").get_json()["supported"] is False
    assert client.post("/api/background", json={"enabled": True}).status_code == 400


def test_background_task_toggle_on_windows(client, monkeypatch):
    import scheduler
    calls = []
    monkeypatch.setattr(dashboard.sys, "platform", "win32")
    monkeypatch.setattr(scheduler, "install_task", lambda: calls.append("install"))   # never a real task
    monkeypatch.setattr(scheduler, "remove_task", lambda: calls.append("remove"))
    monkeypatch.setattr(dashboard, "_task_installed", lambda: calls[-1:] == ["install"])
    assert client.get("/api/background").get_json() == {"supported": True, "installed": False}
    assert client.post("/api/background", json={"enabled": True}).get_json()["installed"] is True
    assert client.post("/api/background", json={"enabled": False}).get_json()["installed"] is False
    assert calls == ["install", "remove"]


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
