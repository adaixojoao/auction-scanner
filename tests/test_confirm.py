"""Opening the top links, and turning a dismissal into a test."""
import json

from confirm import page_gone
from db import load_listings


def test_a_sold_title_or_a_missing_page_leaves_the_list():
    assert page_gone(200, "<html><title>Piso en Torrelavega</title><h1>Casa</h1></html>") is None
    assert page_gone(200, "<html><title>Anuncio</title><h1>Vivienda vendida</h1></html>") == "vendida"
    assert page_gone(404, "<html><title>No encontrado</title></html>") == "page gone"
    # The word in the description of a live ad is not the ad itself.
    long = "<html><title>Casa en venta</title><h1>Casa</h1><p>" + ("jardin " * 80) + "vendido</p></html>"
    assert page_gone(200, long) is None


def test_a_confirmed_sale_is_hidden_with_the_reason(db, add):
    add("fotocasa", "s", "ES", title="Casa en Torrelavega", price=25000, area_m2=80,
        raw_json=json.dumps({"link_check": {"gone": True, "why": "vendido"}}))
    got = load_listings(db, include_hidden=True, apply_min_score=False)[0]
    assert got["hidden_reason"].startswith("gone: vendido")
    assert load_listings(db, apply_min_score=False) == []


def test_a_dismissal_with_a_reason_becomes_an_assertion():
    import sys
    sys.path.insert(0, "scripts")
    import rejections_to_tests
    body = rejections_to_tests.case_for("false price", {
        "source": "fotocasa", "country": "ES", "title": "EN SUBASTA", "description": "",
        "tipo": "vivienda", "price": 27000, "area_m2": 70, "raw_json": None})
    assert "portal_price_unusable" in body
    assert rejections_to_tests.case_for("other", {"title": "x"}) is None
