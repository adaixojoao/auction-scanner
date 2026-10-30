"""Scanner errors found by hand on the 2026-10-30 audit of the top 177 listings."""
import pytest

from confirm import gone_reason
from db import load_listings, mark_duplicates
from sources.de import zvg_value


@pytest.mark.parametrize("text,value", [
    # "1.031,- €" was not read, so the first of four lots stood in for the sale.
    ("lfd. Nr. 1 1.031,- € lfd. Nr. 2 149.994,- € lfd. Nr. 3 45.874,- € lfd. Nr. 4 287.101,- €", 484000),
    ("lfd.Nr. 1:   10.000,-- EUR lfd.Nr. 2:  135.000,-- EUR", 145000),
    # A total the court states is the value, not one more lot to add.
    ("3.900,00 € (lfd. Nr. 3) und 4.000,00 € (lfd. Nr. 4) Gesamtverkehrswert: 7.900,00 €", 7900),
    ("12.000,00 €", 12000),
])
def test_zvg_values_with_dashes_and_stated_totals(text, value):
    assert zvg_value(text) == value


@pytest.mark.parametrize("title,desc", [
    ("VENDU EN 1 JOURNEE -EN EXCLUSIVITE, MONTJOYER PARCELLES", ""),
    ("Finca en Ontiñena", "RESERVADA / V14057 FINCA DE SECANO OPCIÓN USO GANADERO"),
])
def test_an_ad_that_opens_with_sold_or_reserved_is_gone(title, desc):
    assert gone_reason({"title": title, "description": desc}).startswith("the ad opens with")


@pytest.mark.parametrize("title,desc", [
    ("Maison", "Vendu meublé, belle maison de bourg"),
    ("Maison", "Belle maison, vendue louée"),
    ("Casa", "Vendo casa en San Miguel buen estado"),
])
def test_how_a_home_is_sold_is_not_a_sold_home(title, desc):
    assert gone_reason({"title": title, "description": desc}) is None


def test_the_same_plot_to_the_square_metre_on_two_sites_is_one(db, add):
    add("fotocasa", "176603798", country="ES", title="Terreno en Escariche", tipo="terreno",
        concelho="Escariche", price=22000, area_m2=76974, description="Suelo Rústico en Escariche, Guadalajara")
    add("solvia", "103487-80528", country="ES", title="Suelo en Escariche", tipo="terreno",
        concelho="Escariche", price=22000, area_m2=76974)
    # Same round size and price, same village: two lots of one seller, both kept.
    add("fotocasa", "lot-a", country="ES", title="Parcela 1", tipo="terreno",
        concelho="Escariche", price=9000, area_m2=10000)
    add("fotocasa", "lot-b", country="ES", title="Parcela 2", tipo="terreno",
        concelho="Escariche", price=9000, area_m2=10000)
    mark_duplicates(db)
    dup = dict(db.execute("SELECT id, duplicate_of FROM listings").fetchall())
    assert {dup["fotocasa:176603798"], dup["solvia:103487-80528"]} == {None, "fotocasa:176603798"} or \
        {dup["fotocasa:176603798"], dup["solvia:103487-80528"]} == {None, "solvia:103487-80528"}
    assert dup["fotocasa:lot-a"] is None and dup["fotocasa:lot-b"] is None
    shown = {it["id"] for it in load_listings(db, filters={}, apply_min_score=False, mode="land")}
    assert len(shown & {"fotocasa:176603798", "solvia:103487-80528"}) <= 1


def test_a_closed_listing_is_listed_but_not_scored(db, add, monkeypatch):
    import db as dbmod
    from db import set_listing_status
    add("eleiloes", "open", title="Moradia", price=20000)
    add("eleiloes", "ended", title="Moradia", price=20000, date_end="2020-01-01T10:00:00")
    add("eleiloes", "gone", title="Moradia", price=20000)
    set_listing_status(db, "eleiloes:gone", "dismissed", "closed")
    scored = []
    real = dbmod._score_one
    monkeypatch.setattr(dbmod, "_score_one", lambda item, *a, **k: scored.append(item["id"]) or real(item, *a, **k))
    every = {it["id"]: it for it in load_listings(db, filters={}, include_hidden=True, apply_min_score=False)}
    assert set(every) == {"eleiloes:open", "eleiloes:ended", "eleiloes:gone"}
    assert scored == ["eleiloes:open"]
    assert every["eleiloes:ended"]["hidden_reason"] == "expired" and every["eleiloes:ended"]["score"] == 0
    # Opened on its own, a closed listing is still scored in full.
    one = load_listings(db, include_hidden=True, where="id = ?", params=("eleiloes:gone",))
    assert one and "eleiloes:gone" in scored
