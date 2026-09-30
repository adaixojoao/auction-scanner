"""How exact a listing's location is, verifying it by hand, and the Offers
location check (geo.location_confidence, geo.verify_location, dashboard)."""
import json

import pytest

import dashboard
import geo
from common import make_listing
from conftest import FakeResponse
from db import connect, upsert_listing

SALE = {"lat": 38.12, "lon": -7.45}          # coordinates given by the sale


class FakeSession:
    def __init__(self, json_data):
        self.json_data, self.calls = json_data, []

    def get(self, url, **kw):
        self.calls.append((url, kw.get("params")))
        return FakeResponse(json_data=self.json_data)


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "proponente": {"nome": "Test Person", "nif": "123",
                                                      "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"}})
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _raw(db, listing_id):
    return json.loads(db.execute("SELECT raw_json FROM listings WHERE id = ?", (listing_id,)).fetchone()[0] or "{}")


def test_each_position_says_how_exact_it_is():
    def level(**item):
        return geo.location_confidence({"country": "ES", **item})["level"]
    assert level(raw_json=json.dumps(SALE)) == "exact"
    assert level(raw_json=json.dumps({"geo": {"lat": 1.0, "lon": 2.0, "precision": "cadastre", "query": "x"}})) == "exact"
    assert level(raw_json=json.dumps({"geo": {"lat": 1.0, "lon": 2.0, "precision": "street",
                                              "query": "Calle Mayor, Zalla"}}), concelho="Zalla") == "street"
    assert level(raw_json=json.dumps({"geo": {"lat": 1.0, "lon": 2.0, "precision": "village",
                                              "query": "Aldea, Zalla"}}), concelho="Zalla") == "area"
    assert level(concelho="Zalla") == "municipality"
    assert level() == "unknown"
    town = geo.location_confidence({"country": "ES", "concelho": "Zalla"})
    assert town["label"] == "Municipality estimate" and "say nothing about this property" in town["reliable"]


def test_coordinates_are_read_from_text_or_a_maps_link():
    assert geo.parse_coordinates("38.7223, -9.1393") == (38.7223, -9.1393)
    assert geo.parse_coordinates("https://www.google.com/maps/@38.7223,-9.1393,17z") == (38.7223, -9.1393)
    assert geo.parse_coordinates("Rua Direita 5") is None
    assert geo.parse_coordinates("95.1, 10.2") is None


def test_a_verified_position_comes_first_keeps_the_sales_and_survives_a_rescrape(client, db, add):
    add("eleiloes", "v1", "ES", title="Casa", concelho="Zalla", price=20000,
        raw_json=json.dumps({**SALE, "water_check": {"found": []}}))
    r = client.post("/api/listing/location", json={"id": "eleiloes:v1", "method": "coordinates",
                                                   "value": "38.1234, -7.4567"}).get_json()
    assert r["ok"] and r["location"]["level"] == "exact" and r["location"]["verified"]
    assert r["location"]["scanner"]["lat"] == 38.12            # the sale's own position is kept
    raw = _raw(db, "eleiloes:v1")
    assert raw["lat"] == 38.12 and raw["verified_geo"]["lat"] == 38.1234
    assert "water_check" not in raw                             # asked again at the new spot

    # the next scrape brings fresh raw data: yours is carried over
    upsert_listing(db, make_listing("eleiloes", "v1", "ES", title="Casa", raw_json=json.dumps(SALE)))
    db.commit()
    assert _raw(db, "eleiloes:v1")["verified_geo"]["lon"] == -7.4567

    detail = client.get("/api/listing?id=eleiloes:v1").get_json()["location"]
    assert detail["source"].startswith("coordinates you entered") and detail["history"][0]["action"] == "verified"

    assert client.post("/api/listing/location/clear", json={"id": "eleiloes:v1"}).get_json()["ok"]
    after = client.get("/api/listing?id=eleiloes:v1").get_json()["location"]
    assert after["source"] == "coordinates given by the sale" and not after["verified"]
    assert [h["action"] for h in after["history"]] == ["cleared", "verified"]
    assert client.post("/api/listing/location/clear", json={"id": "eleiloes:v1"}).status_code == 400


def test_bad_input_is_refused_in_plain_words(client, add):
    add("eleiloes", "v2", "PT", title="Casa", price=20000)
    bad = client.post("/api/listing/location", json={"id": "eleiloes:v2", "method": "coordinates", "value": "here"})
    assert bad.status_code == 400 and "latitude and longitude" in bad.get_json()["error"]
    pt = client.post("/api/listing/location", json={"id": "eleiloes:v2", "method": "cadastre", "value": "123"})
    assert pt.status_code == 400 and "Only Spanish" in pt.get_json()["error"]
    assert client.post("/api/listing/location", json={"id": "nope", "method": "coordinates",
                                                      "value": "38.1, -7.4"}).status_code == 404
    assert client.post("/api/listing/location", json={"id": "eleiloes:v2", "method": "gps",
                                                      "value": "38.1, -7.4"}).status_code == 400


def test_a_doubtful_position_asks_before_it_is_kept(client, db, add):
    add("eleiloes", "v3", "ES", title="Casa", concelho="Zalla", price=20000)
    db.execute("INSERT INTO places (key, country, name, lat, lon, checked_at) VALUES (?,?,?,?,?,?)",
               (geo.town_key("ES", "Zalla"), "ES", "Zalla", 43.21, -3.13, "2026-09-30"))
    db.commit()
    far = {"id": "eleiloes:v3", "method": "coordinates", "value": "38.1, -7.4"}   # hundreds of km away
    ask = client.post("/api/listing/location", json=far).get_json()
    assert "km from Zalla" in ask["confirm"] and "verified_geo" not in _raw(db, "eleiloes:v3")
    assert client.post("/api/listing/location", json={**far, "confirm": True}).get_json()["ok"]
    assert _raw(db, "eleiloes:v3")["verified_geo"]["lat"] == 38.1


def test_an_address_is_looked_up_once_and_a_less_exact_answer_asks_first(client, db, add, monkeypatch):
    add("eleiloes", "v4", "ES", title="Casa", concelho="Zalla", price=20000, raw_json=json.dumps(SALE))
    session = FakeSession([{"lat": "43.2", "lon": "-3.1", "addresstype": "village"}])
    monkeypatch.setattr(dashboard, "make_session", lambda: session)
    body = {"id": "eleiloes:v4", "method": "address", "value": "Aldea, Zalla"}
    ask = client.post("/api/listing/location", json=body).get_json()
    assert "less exact (village)" in ask["confirm"] and len(session.calls) == 1
    assert session.calls[0][1]["countrycodes"] == "es"
    saved = client.post("/api/listing/location", json={**body, "confirm": True}).get_json()
    assert saved["location"]["level"] == "area" and "the address you entered" in saved["location"]["source"]

    monkeypatch.setattr(dashboard, "make_session", lambda: FakeSession([]))
    missing = client.post("/api/listing/location", json={**body, "value": "Nowhere"})
    assert missing.status_code == 400 and "did not find" in missing.get_json()["error"]


def test_a_spanish_cadastral_reference_is_asked_of_the_catastro(client, db, add, monkeypatch):
    add("boe", "v5", "ES", title="Finca", concelho="Zalla", price=20000)
    catastro = {"Consulta_CPMRCResult": {"coordenadas": {"coord": [{"geo": {"xcen": "-3.12", "ycen": "43.2"}}]}}}
    monkeypatch.setattr(dashboard, "make_session", lambda: FakeSession(catastro))
    r = client.post("/api/listing/location", json={"id": "boe:v5", "method": "cadastre",
                                                   "value": "3589701UK6938N0001XY"}).get_json()
    assert r["location"]["level"] == "exact"
    assert _raw(db, "boe:v5")["verified_geo"]["input"] == "3589701UK6938N"
    bad = client.post("/api/listing/location", json={"id": "boe:v5", "method": "cadastre", "value": "12345"})
    assert bad.status_code == 400 and "14 characters" in bad.get_json()["error"]


def _citius(add, external_id, **over):
    import config
    config.update_config({"checklist": {"blocking": {"pt_court": []}}})   # only the location gate here
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada", "agente_email": "ae@solic.pt"}
    add("citius", external_id, title="Moradia", price=30000, area_m2=120, raw_json=json.dumps({**raw, **over}))
    return f"citius:{external_id}"


def _log(db, log_id):
    return db.execute("SELECT location_level, location_override FROM carta_log WHERE id = ?", (log_id,)).fetchone()


def test_offers_warn_by_default_and_record_the_location(client, db, add):
    lid = _citius(add, "g1")
    offers = client.get("/api/offers").get_json()
    view = offers["review"][0]
    assert offers["location_gate_mode"] == "warn" and view["location_gate"]
    assert view["location_check"]["level"] in ("municipality", "unknown")
    assert view["location"] is not None and not isinstance(view["location"], dict)   # the place's name
    sent = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00"}).get_json()
    assert tuple(_log(db, sent["log_id"]))[0] == view["location_check"]["level"]


def test_block_asks_a_reason_for_offers_but_never_for_information_requests(client, db, add):
    import config
    config.update_config({"location": {"gate": "block"}})
    lid = _citius(add, "g2")
    refused = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00"})
    assert refused.status_code == 409 and refused.get_json()["location_gate"]
    assert client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00",
                                                 "location_override": "ok"}).status_code == 409   # too short
    ok = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00",
                                               "location_override": "visited it, the court confirmed"}).get_json()
    assert _log(db, ok["log_id"])["location_override"] == "visited it, the court confirmed"
    assert client.get("/api/offers").get_json()["sent"][0]["offer"]["location_override"].startswith("visited")

    ask = _citius(add, "g3")
    info = client.post("/api/offers/sent", json={"id": ask, "type": "pt_info"})
    assert info.status_code == 200 and _log(db, info.get_json()["log_id"])["location_override"] is None

    exact = _citius(add, "g4", lat=38.12, lon=-7.45)                 # the sale gives the position
    assert client.post("/api/offers/sent", json={"id": exact, "bid": "26.000,00"}).status_code == 200


def test_the_location_check_setting(client):
    import config
    assert config.DEFAULTS["location"]["gate"] == "warn"
    assert client.get("/api/settings").get_json()["location"] == {"gate": "warn"}
    assert client.post("/api/settings", json={"location": {"gate": "sometimes"}}).status_code == 400
    assert client.post("/api/settings", json={"location": {"gate": "off"}}).get_json() == {"ok": True}
    assert config.load_config()["location"]["gate"] == "off"


def test_a_version_8_database_gets_the_location_history(tmp_path):
    path = str(tmp_path / "v8.db")
    conn = connect(path)
    conn.execute("DROP TABLE location_checks")
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM location_checks").fetchone()[0] == 0
    cols = {r[1] for r in conn.execute("PRAGMA table_info(carta_log)")}
    assert {"location_level", "location_override"} <= cols
    conn.close()
