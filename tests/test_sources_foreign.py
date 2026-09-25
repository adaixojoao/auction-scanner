"""Fixture tests for the foreign sources rewritten in Sept 2026. Every fixture is
a trimmed copy of the live answer's shape, with invented addresses."""
import json

from conftest import FakeResponse
from sources import REGISTRY, load_all

load_all()


def _asta_lot(lid, price, tipologia="Abitazione di tipo civile", titolo="Via Roma 1", comune="Besano"):
    return {"id": lid, "friendlyId": f"{lid}-Abitazione-{comune}", "tipologia": tipologia, "titolo": titolo,
            "comune": comune, "provincia": "Varese", "prezzoNum": price, "prezzo": f"€ {price or 0:,.2f}",
            "offertaMinima": "€ 20.160,00", "dataAsta": "20/01/2027 - 11:30",
            "descrizione": "Appartamento di 85 mq al piano primo", "posizione": {"lat": 45.9, "lng": 8.9},
            "urlImmaginePrincipale": f"https://documents.astalegale.net/asta/0/{lid}",
            "proceduraNumeroAnno": "29/2025", "tribunale": "Varese"}


def test_astalegale_search_api_pages_and_masked_lots(db, fake_http):
    pages = {1: [_asta_lot("B1", 26880), _asta_lot("P2", None, tipologia="XXXXXXXXXX", titolo="XXX")],
             2: [_asta_lot("B3", 9300, comune="Cambiago")]}

    def handler(method, url, kw):
        assert url == "https://api.astalegale.net/Search" and kw["json"]["prezzoA"] == 30000
        page = kw["json"]["page"]
        return FakeResponse(json_data={"results": {"currentPage": pages.get(page, []), "pageSize": 2,
                                                   "totalResults": 3, "pageIndex": page}})
    fake_http(handler)
    assert REGISTRY["astalegale"].func(db, max_price=30000) == 2        # the masked PVP copy is left out
    rows = {r["external_id"]: r for r in db.execute("SELECT * FROM listings WHERE source='astalegale'")}
    house = rows["B1"]
    assert house["title"] == "Abitazione di tipo civile · Via Roma 1 · Besano"
    assert house["price"] == 26880 and house["min_price"] == 20160 and house["area_m2"] == 85
    assert house["date_end"] == "2027-01-20T11:30:00" and house["concelho"] == "Besano"
    assert house["url"] == "https://www.astalegale.net/Aste/Detail/B1-Abitazione-Besano"
    assert json.loads(house["raw_json"])["lat"] == 45.9


def _pvp_lot(lid, price, categoria="IMMOBILE_RESIDENZIALE", disponibilita=("LIBER",)):
    return {"id": lid, "tipoLotto": "IMMOBILI", "categoriaLotto": categoria, "prezzoBaseAsta": price,
            "offertaMinima": price * 0.75 if price else None, "dataOraVendita": "2026-10-08T18:00",
            "disponibilita": list(disponibilita), "procedura": "65", "tribunale": "Tribunale di FERMO",
            "descLotto": "Abitazione al piano primo di mq 90 con balcone",
            "indirizzo": {"citta": "Montegranaro", "provincia": "Fermo",
                          "coordinate": {"latitudine": 43.2, "longitudine": 13.6}}}


def test_pvp_search_api_sales_to_come_within_budget(db, fake_http):
    pages = {0: [_pvp_lot(1, 16537.5), _pvp_lot(2, 240000), _pvp_lot(3, 9000, categoria="IMMOBILE_COMMERCIALE")],
             1: [_pvp_lot(4, 18562.5, disponibilita=("OCCUP",))]}

    def handler(method, url, kw):
        assert url.endswith("/ric-ms/ricerca/vendite") and kw["json"] == {"tipoLotto": "IMMOBILI", "filtroAnnunci": 1}
        page = kw["params"]["page"]
        return FakeResponse(json_data={"body": {"content": pages.get(page, []), "totalPages": 2, "last": page >= 1}})
    fake_http(handler)
    assert REGISTRY["pvp_giustizia"].func(db, max_price=30000) == 2    # not the dear one, not the shop
    rows = {r["id"]: r for r in db.execute("SELECT * FROM listings WHERE source='pvp_giustizia'")}
    house = rows["pvp:1"]
    assert house["price"] == 16537.5 and house["concelho"] == "Montegranaro" and house["area_m2"] == 90
    assert house["date_end"] == "2026-10-08T18:00:00"
    assert house["url"] == "https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio=1"
    assert "Disponibilità: occupato" in rows["pvp:4"]["description"]        # the occupancy reject sees it


def _servi_card(lid, price, text="Casa en venta en C. Mayor, 1, Requena, Valencia 110m 2 3 hab. 1 baño"):
    return f"""<div class="list-product-buscador product-item"><div class="row m-0">
      <div class="col-lg-6"><div class="carousel-item"><img class="img-car" data-src="https://imagenes.servihabitat.com/i/{lid}.jpg"/></div></div>
      <div class="col-lg-6 result-property-detail"><a class="features vivienda" href="/es/venta/vivienda-casa/valencia-x/{lid}">
        <p class="amount"><span id="price" class="price"> {price} € </span></p><p>{text}</p></a></div></div></div>"""


def test_servihabitat_cheapest_of_each_province(db, fake_http, monkeypatch):
    import sources.es as es
    monkeypatch.setattr(es, "SERVIHABITAT_PROVINCES", ("valencia", "teruel"))
    pages = {"valencia": _servi_card(60580509, "9.500") + _servi_card(60580510, "36.000"), "teruel": ""}

    def handler(method, url, kw):
        assert kw["params"] == {"o": 4}                     # cheapest first
        return FakeResponse(f'<div class="product-list">{pages[url.rsplit("/", 1)[1]]}</div>')
    fake_http(handler)
    assert REGISTRY["servihabitat"].func(db, max_price=30000) == 1
    row = db.execute("SELECT * FROM listings WHERE source='servihabitat'").fetchone()
    assert row["id"] == "servihabitat:60580509" and row["price"] == 9500 and row["area_m2"] == 110
    assert row["title"] == "Casa en venta en C. Mayor, 1, Requena, Valencia" and row["concelho"] == "Requena"
    assert row["url"] == "https://www.servihabitat.com/es/venta/vivienda-casa/valencia-x/60580509"
    assert row["image_url"] == "https://imagenes.servihabitat.com/i/60580509.jpg"


def test_bot_walled_sources_are_out_of_the_default_scan():
    for name in ("sareb", "gobidreal", "biddit", "anaf", "cyprus", "greece"):
        assert REGISTRY[name].default is False, name


# ─── The Netherlands ────────────────────────────────────────────────

OV_DETAIL = """<html><body><h1>Kerkstraat 18, OUD GASTEL</h1><p>Kerkstraat 18, OUD GASTEL woonpand met ondergrond, erf,
  tuin en verdere aanhorigheden, Kerkstraat 18 te 4751 HN Oud Gastel, perceel groot 305 m.) Voor een indicatie van de
  indeling wordt verwezen naar de schetsen.</p><p>Veiling 8 oktober 2026 vanaf 13:30 uur Internet-only Live</p>
  <p>Attentie pand is verhuurd</p><div>Kenmerken Type registergoed Woonhuis Gebruik Verhuurd Soort eigendom Vol
  eigendom Bouwjaar 1860 Oppervlakte wonen 228 m 2 Oppervlakte perceel 305 m 2 Inhoud 1000 m 3</div></body></html>"""


def test_openbareverkoop_list_and_each_lots_page_once(db, fake_http):
    lot = {"id": 4490, "kavelNaam": "Kerkstraat 18, OUD GASTEL", "woningtype": "Woonhuis", "url": "/kavel/4490/k",
           "lat": 51.58, "lng": 4.45, "zittingdatum": "/Date(1791459000000)/", "inzet": "", "afslag": "",
           "veilingwijze": "Internet-only Live", "image": "/img/4490.jpg"}
    pages = []

    def handler(method, url, kw):
        pages.append(url)
        if url.endswith("/kavels/searchresults"):
            return FakeResponse(json_data={"results": [{"objectenPerRegio": [{"objects": [lot]}]}]})
        return FakeResponse(OV_DETAIL if url.endswith("/kavel/4490/k") else "")
    fake_http(handler)
    assert REGISTRY["netherlands"].func(db, max_price=30000) == 1
    row = db.execute("SELECT * FROM listings WHERE id='netherlands:4490'").fetchone()
    assert row["concelho"] == "Oud Gastel" and row["area_m2"] == 228 and row["date_end"] == "2026-10-08T13:30:00"
    assert row["description"].startswith("woonpand met ondergrond") and "Gebruik: Verhuurd" in row["description"]
    raw = json.loads(row["raw_json"])
    assert raw["lat"] == 51.58 and raw["ano_construcao"] == "1860"
    from scoring import score_detail
    assert "rejected: occupied" in score_detail(dict(row))[1]           # "verhuurd" is let
    REGISTRY["netherlands"].func(db, max_price=30000)
    assert sum(u.endswith("/kavel/4490/k") for u in pages) == 1          # the page once


VGV_PAGE = """<html><script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"auctions": [
  {"id": 2314, "name": "Haarlem, Kijkduinstraat 43", "plaats": "Haarlem", "provincie": "Noord-Holland",
   "land": "nl", "status": "open", "object_type": "Tussenwoning", "startbod": 1000000,
   "oppervlakte_object": "BAG: 114", "oppervlakte_perceel": "92", "bouwjaar": "BAG: 1940",
   "gebruikssituatie": "huurbeding_is_niet_ingeroepen", "eindtijd": "2026-10-01T07:35:00+00:00",
   "thumb": "https://cdn.example/1.webp", "latitude": 52.37, "longitude": 4.61, "type_verkoop": "executieveiling",
   "kavelbeschrijving": "Het woonhuis met ondergrond en verder toebehoren te Haarlem"},
  {"id": 2646, "name": "31 appartementen Markneukirchen", "plaats": "Markneukirchen", "land": "de", "status": "open"},
  {"id": 2700, "name": "Iets in Roemenië", "land": "ro", "status": "open"}]}}}</script></html>"""


def test_veilingnotaris_and_vastgoedveiling_are_one_platform(db, fake_http):
    vn_list = ('<a href="https://veilingnotaris.nl/veilingen/2314/haarlem_kijkduinstraat_43/">Tussenwoning</a>'
               '<a href="https://veilingnotaris.nl/veilingen/2580/kerkrade_schifferheidestraat_1_3/">Woonhuis</a>')

    def handler(method, url, kw):
        if url == "https://vastgoedveiling.nl/veilingen":
            return FakeResponse(VGV_PAGE)
        if url == "https://veilingnotaris.nl/veilingen/":
            return FakeResponse(vn_list if not kw.get("params") else "")
        return FakeResponse("<p>Over het object Type Woonhuis Gebruikssituatie Leeg Bezichtigingen</p>")
    fake_http(handler)
    assert REGISTRY["veilingnotaris"].func(db, max_price=30000) == 3       # 2314 once, 2580, the German one
    rows = {r["id"]: r for r in db.execute("SELECT * FROM listings")}
    assert set(rows) == {"veilingnotaris:2314", "veilingnotaris:2580", "veilingnotaris:2646"}
    haarlem = rows["veilingnotaris:2314"]
    assert haarlem["area_m2"] == 114 and haarlem["price"] is None          # "startbod" is the Dutch-auction start
    assert haarlem["url"] == "https://vastgoedveiling.nl/veiling/2314/haarlem-kijkduinstraat-43"
    assert haarlem["date_end"] == "2026-10-01T07:35:00" and "Bouwjaar 1940" in haarlem["description"]
    assert rows["veilingnotaris:2646"]["country"] == "DE"
    assert rows["veilingnotaris:2580"]["title"] == "Kerkrade, Schifferheidestraat 1 3 (Woonhuis)"
