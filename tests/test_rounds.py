import json
from datetime import datetime, timedelta, timezone

import rounds
from db import load_listings

NOW = datetime.now(timezone.utc)
PAST = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT10:00:00")
LATER = (NOW + timedelta(days=12)).strftime("%Y-%m-%dT10:00:00")


def case(proc, **extra):
    return json.dumps({"processo": proc, **extra})


def test_case_numbers_are_compared_without_the_court():
    assert rounds.case_key({"processo": "165/10.3TBMRA, Juízo de Moura"}) == "165/10.3tbmra"
    assert rounds.case_key({"expediente": "1234 0000 05 0456 24"}) == "12340000050456 24".replace(" ", "")
    assert rounds.case_key({"processo": "x"}) is None and rounds.case_key({}) is None


def test_a_property_back_on_sale_after_an_unsold_round(db, add):
    add("eleiloes", "LO1", title="Moradia T3 em Seia", tipo="moradia", area_m2=120, price=40000,
        date_end=PAST, raw_json=case("123/20.1T8GRD"))
    add("eleiloes", "LO2", title="Moradia T3 em Seia", tipo="moradia", area_m2=118, price=30000,
        date_end=LATER, raw_json=case("123/20.1T8GRD"))
    item = {it["id"]: it for it in load_listings(db, include_hidden=True)}["eleiloes:LO2"]
    er = item["earlier_round"]
    assert er["id"] == "eleiloes:LO1" and er["price"] == 40000 and er["cheaper_pct"] == 25.0
    assert item["reasons"][0] == f"on sale before (ended {PAST[:10]} at €40,000) — not sold then"
    assert item["reasons"][1] == "25% cheaper than the last round"
    # the first round has no earlier round of its own
    assert {it["id"]: it for it in load_listings(db, include_hidden=True)}["eleiloes:LO1"]["earlier_round"] is None


def test_rounds_are_matched_across_sites_and_only_for_the_same_property(db, add):
    add("citius", "c1", title="Prédio urbano - moradia sita em Moura", tipo="imovel", price=50000,
        date_end=PAST, raw_json=case("165/10.3TBMRA, Juízo de Moura"))
    add("eleiloes", "LO9", title="Moradia sita em Moura", tipo="moradia", price=42000,
        date_end=LATER, raw_json=case("165/10.3TBMRA"))
    add("eleiloes", "LO8", title="Terreno rústico", tipo="terreno_rustico", area_m2=20000, price=5000,
        date_end=LATER, raw_json=case("165/10.3TBMRA"))        # another property of the same case
    items = {it["id"]: it for it in load_listings(db, include_hidden=True)}
    assert items["eleiloes:LO9"]["earlier_round"]["id"] == "citius:c1"
    assert items["eleiloes:LO8"]["earlier_round"] is None


def test_a_sale_on_the_other_site_that_ended_after_this_listing_appeared_is_the_same_round(db, add):
    # Citius shows the proposals deadline, e-leilões the auction's end: same round, two dates.
    add("citius", "c3", title="Moradia em Beja", tipo="imovel", area_m2=90, price=30000,
        date_end=(NOW - timedelta(days=2)).strftime("%Y-%m-%dT10:00:00"), raw_json=case("8/19.0T8BJA"))
    add("eleiloes", "LO6", title="Moradia em Beja", tipo="moradia", area_m2=90, price=30000,
        date_end=LATER, raw_json=case("8/19.0T8BJA"))
    db.execute("UPDATE listings SET first_seen = ? WHERE id = 'eleiloes:LO6'",
               ((NOW - timedelta(days=20)).isoformat(),))
    db.commit()
    items = {it["id"]: it for it in load_listings(db, include_hidden=True)}
    assert items["eleiloes:LO6"]["earlier_round"] is None


def test_two_sites_showing_the_same_round_are_not_an_earlier_round(db, add):
    add("citius", "c2", title="Moradia em Beja", tipo="imovel", area_m2=90, price=30000,
        date_end=LATER, raw_json=case("7/19.0T8BJA"))
    add("eleiloes", "LO7", title="Moradia em Beja", tipo="moradia", area_m2=90, price=30000,
        date_end=LATER, raw_json=case("7/19.0T8BJA"))
    assert all(it["earlier_round"] is None for it in load_listings(db, include_hidden=True))


def test_the_earlier_round_is_linked_from_the_listing_panel(db, add, monkeypatch):
    import dashboard
    add("eleiloes", "LO1", title="Moradia T3", tipo="moradia", area_m2=120, price=40000,
        date_end=PAST, raw_json=case("123/20.1T8GRD"), url="https://www.e-leiloes.pt/evento/LO1")
    add("eleiloes", "LO2", title="Moradia T3", tipo="moradia", area_m2=120, price=30000,
        date_end=LATER, raw_json=case("123/20.1T8GRD"))
    dashboard.app.config["TESTING"] = True
    related = dashboard.app.test_client().get("/api/listing?id=eleiloes:LO2").get_json()["related"]
    earlier = next(r for r in related if r["label"].startswith("Earlier round"))
    assert earlier["url"] == "https://www.e-leiloes.pt/evento/LO1" and "€40,000" in earlier["label"]
