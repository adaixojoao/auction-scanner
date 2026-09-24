"""Map positions and Street View (geo.py)."""
import json

import pytest

import geo
import listing_info
from conftest import FakeResponse, FakeSession


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(geo.time, "sleep", lambda *_: None)


def test_the_sales_own_coordinates_come_first():
    item = {"raw_json": json.dumps({"lat": 41.060071, "lon": -7.931145})}
    assert geo.position(item) == {"lat": 41.060071, "lon": -7.931145, "precision": "sale"}
    assert geo.position({"raw_json": json.dumps({"prop_latitude": "38.75", "prop_longitude": "-9.23"})})["lat"] == 38.75
    assert geo.position({"raw_json": "{}"}) is None


def test_lookups_go_from_the_street_to_the_town():
    item = {"country": "PT", "freguesia": "Longa", "concelho": "Tabuaço",
            "title": "Prédio urbano sito na Rua do Eiró, freguesia de Longa, concelho de Tabuaço", "raw_json": "{}"}
    assert geo.queries(item) == ["Rua do Eiró, Longa, Tabuaço", "Longa, Tabuaço", "Tabuaço"]
    answers = {"Rua do Eiró, Longa, Tabuaço": [],
               "Longa, Tabuaço": [{"lat": "41.0598", "lon": "-7.5930", "addresstype": "village"}]}
    session = FakeSession(lambda m, url, kw: FakeResponse(json_data=answers.get(kw["params"]["q"], [])))
    found = geo.geocode(session, item)
    assert found == {"lat": 41.0598, "lon": -7.593, "precision": "village", "query": "Longa, Tabuaço"}
    assert [c[2]["params"]["q"] for c in session.calls] == ["Rua do Eiró, Longa, Tabuaço", "Longa, Tabuaço"]
    assert all(c[2]["params"]["countrycodes"] == "pt" and "auction-scanner" in c[2]["headers"]["User-Agent"]
               for c in session.calls)


def test_pending_listings_are_located_once_and_kept(db, add):
    from db import load_listings
    add("citius", "1", title="Casa na Canada da Galega, freguesia de Ribeira das Tainhas", freguesia="Ribeira das Tainhas",
        concelho="Vila Franca do Campo", price=20000)
    add("eleiloes", "2", title="Moradia", price=20000, raw_json=json.dumps({"lat": 41.0, "lon": -7.9}))
    hit = [{"lat": "37.7184", "lon": "-25.4105", "addresstype": "road"}]
    session = FakeSession(lambda m, url, kw: FakeResponse(json_data=hit))
    items = load_listings(db, include_hidden=True)
    assert geo.geocode_pending(db, session, items) == 1          # the e-leilões one has its own position
    assert geo.geocode_pending(db, session, load_listings(db, include_hidden=True)) == 0
    assert len(session.calls) == 1
    row = db.execute("SELECT raw_json FROM listings WHERE id='citius:1'").fetchone()[0]
    assert json.loads(row)["geo"]["precision"] == "street"


def test_a_failed_lookup_is_tried_next_scan(db, add):
    import requests
    from db import load_listings
    add("citius", "1", title="Casa", freguesia="Longa", concelho="Tabuaço", price=20000)

    def down(m, url, kw):
        raise requests.ConnectionError("offline")
    assert geo.geocode_pending(db, FakeSession(down), load_listings(db, include_hidden=True)) == 0
    assert "geo_checked" not in (db.execute("SELECT raw_json FROM listings WHERE id='citius:1'").fetchone()[0] or "")


def test_street_view_links_and_the_embed_only_when_the_position_is_exact():
    exact = {"raw_json": json.dumps({"lat": 41.060071, "lon": -7.931145})}
    sv = listing_info.street_view(exact, "KEY123")
    assert sv["url"] == "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=41.060071%2C-7.931145"
    assert "basemap=satellite" in sv["satellite"] and "key=KEY123" in sv["embed"]
    assert listing_info.street_view(exact, "")["embed"] is None          # no key: a link only
    village = {"raw_json": json.dumps({"geo": {"lat": 41.05, "lon": -7.59, "precision": "village"}})}
    sv = listing_info.street_view(village, "KEY123")
    assert sv["embed"] is None and sv["precision"].startswith("approximate")


def test_public_facts_in_the_portuguese_records():
    rec = listing_info.official_records({
        "country": "PT", "source": "citius", "concelho": "Oliveira de Azeméis",
        "description": "terreno para construção, descrito na Conservatória do Registo Predial de Oliveira de Azemeis "
                       "sob o n.º 3605/20050418, com o valor patrimonial tributável de 17.670,00 Euros",
        "raw_json": "{}"})
    assert {"label": "Tax value (valor patrimonial tributário)", "value": "€17.670,00"} in rec["facts"]


def test_no_lookup_without_a_real_municipality(tmp_path, monkeypatch):
    import csv

    import prices
    path = tmp_path / "pt.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=prices.COLUMNS)
        w.writeheader()
        for name in ("Vila do Bispo", "Paços de Ferreira", "Vila Pouca de Aguiar"):
            w.writerow({"municipality": name, "eur_m2": 1000, "period": "Q", "source": "INE"})
    monkeypatch.setattr(prices, "PT_FILE", str(path))
    # a street in the municipality field, and no town in the text: no pin rather than a wrong one
    assert geo.queries({"country": "PT", "concelho": "lugar de Lage", "freguesia": "lugar de Lage",
                        "title": "Casa de um pavimento, sito no lugar de Lage", "raw_json": "{}"}) == []
    # the postcode names the town
    assert geo.queries({"country": "PT", "concelho": "largo de São Vicente", "raw_json": json.dumps(
        {"descricao_completa": "Fracção C, sito no largo de São Vicente, nº 13 - 8650 - 408 Vila do Bispo"})}) \
        == ["largo de São Vicente, Vila do Bispo", "Vila do Bispo"]
    # boundaries are not the address
    q = geo.queries({"country": "PT", "concelho": "PAÇOS DE FERREIRA", "freguesia": "MEIXOMIL", "raw_json": "{}",
                     "title": "URBANO - CASAS TERREAS CONFRONTAÇÕES: SUL - CAMINHO PUBLICO NASCENTE - X"})
    assert q == ["MEIXOMIL, PAÇOS DE FERREIRA", "PAÇOS DE FERREIRA"]
