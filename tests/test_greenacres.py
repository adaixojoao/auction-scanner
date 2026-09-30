"""Green-Acres: rural homes and land from agents in FR, PT, ES, IT."""
import base64
import json

from sources.eu import greenacres_detail, greenacres_query, parse_greenacres_page, scrape_greenacres
from conftest import FakeResponse


def _card(aid, url, title, price, place, tags):
    o = base64.b64encode(url.encode()).decode()
    tag_html = "".join(f'<div class="info-tag shown" title="{k}">{v}</div>' for k, v in tags.items())
    return (f'<div class="announce-card map" data-advertid="{aid}" data-o="{o}">'
            f'<img class="announce-card-img" src="https://lb1.green-acres.com/{aid}_1.jpg">'
            f'<div class="announce-info" title="{title}"><strong class="info-price">{price}</strong>'
            f'<div class="announce-localisation">{place}</div>'
            f'<div class="characteristics">{tag_html}</div></div></div>')


LAND = _card("Aland1", "https://www.green-acres.pt/fr/properties/terrain/montalegre/Aland1.htm",
             "Terrain rustique avec rivière, Montalegre", "18 000 €", "Pitões (Montalegre)",
             {"Terrain": "2,5 hectares de terrain", "Prix par m²": "1 €/m²"})
HOUSE = _card("Ahouse", "https://www.green-acres.fr/fr/properties/maison/huelgoat/Ahouse.htm",
              "Maison en pierre, Huelgoat", "45 000 €", "Huelgoat",
              {"Surface habitable": "90 m²", "Terrain": "1 200 m² de terrain"})
PAGE = f"<html><body><div class='adverts-listing'>{LAND}{HOUSE}</div></body></html>"
DETAIL = """<html><div class="description-container">Terrain rustique de 2,5 ha traversé par une rivière,
source et lameiro.</div><script>window.advert = { coordinates: { latitude: 41.84, longitude: -8.07, } }
isPreciseLocation=True</script></html>"""


def test_the_query_asks_for_the_budget_and_big_land():
    assert greenacres_query("hab_land-on", 50000, 10000) == "hab_land-on-mx_p-50000-mn_l_s-10000"
    assert greenacres_query("hab_house-on", 50000, None) == "hab_house-on-mx_p-50000"


def test_cards_give_price_town_sizes_and_kind():
    land, house = parse_greenacres_page(PAGE, "PT")
    assert land["id"] == "greenacres:Aland1" and land["price"] == 18000 and land["tipo"] == "terreno"
    assert land["area_m2"] == 25000 and land["concelho"] == "Montalegre"
    assert house["area_m2"] == 90 and house["tipo"] == "maison" and house["concelho"] == "Huelgoat"
    assert "terrain 1200 m²" in house["description"]
    assert json.loads(house["raw_json"])["land_m2"] == 1200


def test_detail_gives_the_text_and_the_map_position():
    d = greenacres_detail(DETAIL)
    assert "rivière" in d["descricao"] and d["geo"] == {"lat": 41.84, "lon": -8.07, "precision": "street"}


def test_scan_reads_each_detail_once(db, fake_http, monkeypatch):
    import sources.eu
    monkeypatch.setattr(sources.eu.time, "sleep", lambda s: None)
    from db import load_listings
    import geo
    def reply(method, url, kw):
        if "maison-a-vendre" in url:
            return FakeResponse(PAGE if kw["params"]["p_n"] == 1 else "<html></html>")
        return FakeResponse(DETAIL)
    session = fake_http(reply)
    assert scrape_greenacres(db, max_price=50000) == 8          # 2 cards × 4 countries (same fake page)
    details = [c for c in session.calls if "maison-a-vendre" not in c[1]]
    item = next(i for i in load_listings(db, include_hidden=True) if i["id"] == "greenacres:Aland1")
    assert "rivière" in item["description"] and geo.position(item)["lat"] == 41.84
    scrape_greenacres(db, max_price=50000)
    assert len([c for c in session.calls if "maison-a-vendre" not in c[1]]) == len(details)
