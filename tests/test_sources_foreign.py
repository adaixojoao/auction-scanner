"""Fixture tests for the foreign sources rewritten in Sept 2026. Every fixture is
a trimmed copy of the live answer's shape, with invented addresses."""
import json

import pytest

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
    masked = _asta_lot("P2", None, tipologia="XXXXXXXXXX", titolo="XXX")
    pages = {1: [_asta_lot("B1", 26880), masked],
             2: [masked],                                   # a page of masked copies only is not the end
             3: [_asta_lot("B3", 9300, comune="Cambiago")]}

    def handler(method, url, kw):
        assert url == "https://api.astalegale.net/Search" and kw["json"]["prezzoA"] == 30000
        # The API refuses a request that does not name the public site.
        assert kw["headers"]["Origin"] == "https://www.astalegale.net"
        assert kw["headers"]["Referer"] == "https://www.astalegale.net/"
        page = kw["json"]["page"]
        return FakeResponse(json_data={"results": {"currentPage": pages.get(page, []), "pageSize": 2,
                                                   "totalResults": 6, "pageIndex": page}})
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
    assert json.loads(house["raw_json"])["occupation"] == "vacant"
    assert json.loads(rows["pvp:4"]["raw_json"])["occupation"] == "occupied"


def _servi_card(lid, price, text="Casa en venta en C. Mayor, 1, Requena, Valencia 110m 2 3 hab. 1 baño"):
    return f"""<div class="list-product-buscador product-item"><div class="row m-0">
      <div class="col-lg-6"><div class="carousel-item"><img class="img-car" data-src="https://imagenes.servihabitat.com/i/{lid}.jpg"/></div></div>
      <div class="col-lg-6 result-property-detail"><a class="features vivienda" href="/es/venta/vivienda-casa/valencia-x/{lid}">
        <p class="amount"><span id="price" class="price"> {price} € </span></p><p>{text}</p></a></div></div></div>"""


def test_servihabitat_cheapest_of_each_province(db, fake_http, monkeypatch):
    import sources.es as es
    monkeypatch.setattr(es, "SERVIHABITAT_PROVINCES", ("valencia", "teruel"))
    pages = {"valencia": _servi_card(60580509, "9.500") + _servi_card(60580510, "36.000"), "teruel": ""}

    detail = ("<html><body><h2>Descripción</h2><p>Oportunidad para inversores. Inmueble sin posesión sin "
              "posibilidad de visita ni financiación. Casa de 3 dormitorios.</p><h3>Descargas</h3></body></html>")
    calls = []

    def handler(method, url, kw):
        calls.append(url)
        if url.endswith("/60580509"):                       # the listing's own page
            return FakeResponse(detail)
        assert kw["params"] == {"o": 4}                     # cheapest first
        return FakeResponse(f'<div class="product-list">{pages[url.rsplit("/", 1)[1]]}</div>')
    fake_http(handler)
    assert REGISTRY["servihabitat"].func(db, max_price=30000) == 1
    row = db.execute("SELECT * FROM listings WHERE source='servihabitat'").fetchone()
    assert row["id"] == "servihabitat:60580509" and row["price"] == 9500 and row["area_m2"] == 110
    assert row["title"] == "Casa en venta en C. Mayor, 1, Requena, Valencia" and row["concelho"] == "Requena"
    assert row["url"] == "https://www.servihabitat.com/es/venta/vivienda-casa/valencia-x/60580509"
    assert row["image_url"] == "https://imagenes.servihabitat.com/i/60580509.jpg"
    # Its page is read once: "sin posesión" (occupied, no visits) reaches the description and the reject.
    assert row["description"].startswith("Oportunidad para inversores. Inmueble sin posesión")
    from scoring import score_detail
    # "sin posesión" is a hard reject in every tab, not only a penalty on My home.
    assert score_detail(dict(row))[0] == 0
    assert "possession" in score_detail(dict(row))[1][0]
    REGISTRY["servihabitat"].func(db, max_price=30000)
    assert sum(u.endswith("/60580509") for u in calls) == 1
    again = db.execute("SELECT description FROM listings WHERE source='servihabitat'").fetchone()[0]
    assert "sin posesión" in again                          # kept on the next scan


def test_servihabitat_a_hung_sweep_is_not_a_full_scan(db, fake_http, monkeypatch):
    """One province answering and the rest timing out must not count as the whole site."""
    import sources.es as es
    monkeypatch.setattr(es, "SERVIHABITAT_PROVINCES", ("valencia", "teruel", "madrid", "sevilla"))

    def handler(method, url, kw):
        if url.rsplit("/", 1)[-1].split("?")[0] == "valencia":
            return FakeResponse(_servi_card(60580509, "9.500"))
        raise RuntimeError("timed out")

    fake_http(handler)
    result = REGISTRY["servihabitat"].func(db, max_price=30000)
    assert result == 1 and result.scope == "partial" and result.pages_seen == 1
    assert db.execute("SELECT COUNT(*) FROM listings WHERE source='servihabitat'").fetchone()[0] == 1


def test_servihabitat_the_first_province_failing_is_the_source_failing(db, fake_http, monkeypatch):
    import sources.es as es
    monkeypatch.setattr(es, "SERVIHABITAT_PROVINCES", ("valencia", "teruel"))

    def handler(method, url, kw):
        raise RuntimeError("timed out")

    fake_http(handler)
    with pytest.raises(RuntimeError, match="timed out"):
        REGISTRY["servihabitat"].func(db, max_price=30000)


def test_bot_walled_sources_are_out_of_the_default_scan():
    for name in ("sareb", "gobidreal", "biddit", "anaf", "cyprus", "greece", "haya"):
        assert REGISTRY[name].default is False, name


def test_subastasactivas_stays_off_default_and_reads_the_json_api(db, fake_http):
    from sources.es import subastasactivas_listing
    item = {
        "id": "bc59a498-b1af-4688-8b84-98346bc9c930",
        "title": "SUB-RC-2026-3100200101141",
        "category": "Viviendas",
        "propertyType": "Vivienda",
        "valorSubasta": 87626.72,
        "appraisalValue": 109699.2,
        "municipality": "Tudela",
        "province": "Navarra",
        "endDate": "2026-11-26T18:00:00.000Z",
        "detailPath": "/subastas/navarra/tudela/vivienda-calle-alberto-pelairea-n-2-1-c",
        "imageUrl": "/api/auction-image/x",
        "address": "C/ Alberto Pelairea, nº 2, 1º C, Tudela",
        "surfaceM2": 87.45,
        "source": "BOE",
        "status": "celebrandose",
        "latitude": 42.06,
        "longitude": -1.61,
    }
    row = subastasactivas_listing(item)
    assert row["id"] == "subastasactivas:bc59a498-b1af-4688-8b84-98346bc9c930"
    assert row["price"] == 87626.72 and row["tipo"] == "vivienda" and row["concelho"] == "Tudela"
    assert row["url"].endswith("/subastas/navarra/tudela/vivienda-calle-alberto-pelairea-n-2-1-c")
    assert '"lat": 42.06' in row["raw_json"]
    assert subastasactivas_listing({"id": "x"}) is None

    pages = []

    def handler(method, url, kw):
        pages.append(url)
        assert "/api/auctions" in url
        params = kw.get("params") or {}
        assert params.get("status") == "active"
        assert "Viviendas" in (params.get("categories") or "")
        page = int(params.get("page") or 1)
        rows = [item] if page == 1 else []
        return FakeResponse(json_data={
            "success": True,
            "data": rows,
            "pagination": {"page": page, "limit": 50, "hasMore": False, "totalCount": len(rows)},
        })

    fake_http(handler)
    assert REGISTRY["subastasactivas"].default is False
    assert REGISTRY["subastasactivas"].func(db, max_price=100000) == 1
    assert db.execute("SELECT COUNT(*) FROM listings WHERE source='subastasactivas'").fetchone()[0] == 1


def test_zvg_de_blocked_points_at_the_court_portal(db, fake_http):
    from sources import SourceUnavailable

    fake_http(lambda method, url, kw: FakeResponse(
        "Lieber Datenspezialist, vielen Dank… von unserem System gesperrt."))
    with pytest.raises(SourceUnavailable, match="zvg-portal"):
        REGISTRY["zvg_de"].func(db, max_price=100000)


def test_haya_blocked_by_firewall(db):
    from sources import SourceUnavailable

    with pytest.raises(SourceUnavailable, match="firewall"):
        REGISTRY["haya"].func(db, max_price=100000)


# ─── The Netherlands ────────────────────────────────────────────────

OV_DETAIL = """<html><body><h1>Kerkstraat 18, OUD GASTEL</h1><p>Kerkstraat 18, OUD GASTEL woonpand met ondergrond, erf,
  tuin en verdere aanhorigheden, Kerkstraat 18 te 4751 HN Oud Gastel, perceel groot 305 m.) Voor een indicatie van de
  indeling wordt verwezen naar de schetsen.</p><p>Veiling 8 oktober 2026 vanaf 13:30 uur Internet-only Live</p>
  <p>Attentie pand is verhuurd</p><div>Kenmerken Type registergoed Woonhuis Gebruik Verhuurd Soort eigendom Vol
  eigendom Bouwjaar 1860 Oppervlakte wonen 228 m 2 Oppervlakte perceel 305 m 2 Inhoud 1000 m 3</div></body></html>"""


def test_openbareverkoop_list_and_each_lots_page_once(db, fake_http):
    lot = {"id": 4490, "kavelNaam": "Kerkstraat 18, OUD GASTEL", "woningtype": "Woonhuis", "url": "/kavel/4490/k",
           "lat": 51.58, "lng": 4.45, "zittingdatum": "/Date(1791459000000)/", "inzet": "", "afslag": "",
           "veilingwijze": "Internet-only Live", "internetonly": True, "image": "/img/4490.jpg"}
    hybrid = {"id": 4491, "kavelNaam": "Zaalstraat 1, BREDA", "woningtype": "Appartement", "url": "/kavel/4491/k",
              "veilingwijze": "Hybride (zaal en internet)", "internetonly": False, "zittingdatum": "/Date(1791459000000)/"}
    pages = []

    def handler(method, url, kw):
        pages.append(url)
        if url.endswith("/kavels/searchresults"):
            return FakeResponse(json_data={"results": [{"objectenPerRegio": [{"objects": [lot, hybrid]}]}]})
        return FakeResponse(OV_DETAIL if "/kavel/4490/" in url else "")
    fake_http(handler)
    assert REGISTRY["netherlands"].func(db, max_price=30000) == 2
    row = db.execute("SELECT * FROM listings WHERE id='netherlands:4490'").fetchone()
    assert row["concelho"] == "Oud Gastel" and row["area_m2"] == 228 and row["date_end"] == "2026-10-08T13:30:00"
    assert row["description"].startswith("woonpand met ondergrond") and "Gebruik: Verhuurd" in row["description"]
    raw = json.loads(row["raw_json"])
    assert raw["lat"] == 51.58 and raw["ano_construcao"] == "1860"
    assert raw["bieden_via_internet"] is True and raw["internetonly"] is True
    assert "bieden via internet" in row["description"]
    hyb = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='netherlands:4491'").fetchone()[0])
    assert hyb["bieden_via_internet"] is True and hyb["internetonly"] is False   # hybride still bids online
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
  {"id": 2646, "name": "31 appartementen Markneukirchen", "plaats": "Markneukirchen", "land": "de", "status": "open",
   "type_verkoop": "vrijwillige_veiling", "object_type": "Appartement"},
  {"id": 2700, "name": "Iets in Roemenië", "land": "ro", "status": "open"}]}}}</script></html>"""


def test_veilingnotaris_reads_its_own_list_only(db, fake_http):
    vn_list = ('<a href="https://veilingnotaris.nl/veilingen/2314/haarlem_kijkduinstraat_43/">Tussenwoning</a>'
               '<a href="https://veilingnotaris.nl/veilingen/2580/kerkrade_schifferheidestraat_1_3/">Woonhuis</a>')

    def handler(method, url, kw):
        if url == "https://veilingnotaris.nl/veilingen/":
            return FakeResponse(vn_list if not kw.get("params") else "")
        return FakeResponse("<p>Over het object Type Woonhuis Gebruikssituatie Leeg Bezichtigingen</p>")
    fake_http(handler)
    assert REGISTRY["veilingnotaris"].func(db, max_price=30000) == 2
    rows = {r["id"]: r for r in db.execute("SELECT * FROM listings")}
    assert set(rows) == {"veilingnotaris:2314", "veilingnotaris:2580"}
    assert rows["veilingnotaris:2580"]["title"] == "Kerkrade, Schifferheidestraat 1 3 (Woonhuis)"


def test_vastgoedveiling_is_its_own_source_with_auction_kind(db, fake_http):
    fake_http(lambda method, url, kw: FakeResponse(VGV_PAGE))
    assert REGISTRY["vastgoedveiling"].func(db, max_price=30000) == 2     # RO land skipped
    rows = {r["id"]: dict(r) for r in db.execute("SELECT * FROM listings")}
    assert set(rows) == {"vastgoedveiling:2314", "vastgoedveiling:2646"}
    haarlem = rows["vastgoedveiling:2314"]
    assert haarlem["area_m2"] == 114 and haarlem["price"] is None         # startbod is not a price
    assert haarlem["url"] == "https://vastgoedveiling.nl/veiling/2314/haarlem-kijkduinstraat-43"
    assert haarlem["date_end"] == "2026-10-01T07:35:00" and "Bouwjaar 1940" in haarlem["description"]
    raw = json.loads(haarlem["raw_json"])
    assert raw["type_verkoop"] == "executieveiling" and raw["bieden_via_internet"] is True
    assert json.loads(rows["vastgoedveiling:2646"]["raw_json"])["type_verkoop"] == "vrijwillige veiling"
    assert rows["vastgoedveiling:2646"]["country"] == "DE"


def test_vastgoedveiling_keeps_veilingnotaris_id_when_already_listed(db, fake_http, add):
    add("veilingnotaris", "2314", country="NL", title="Haarlem, Kijkduinstraat 43",
        concelho="Haarlem", url="https://veilingnotaris.nl/veilingen/2314/x/")
    fake_http(lambda method, url, kw: FakeResponse(VGV_PAGE))
    assert REGISTRY["vastgoedveiling"].func(db, max_price=30000) == 2
    rows = {r["id"]: dict(r) for r in db.execute("SELECT * FROM listings")}
    assert "vastgoedveiling:2314" not in rows                              # no new id
    assert rows["veilingnotaris:2314"]["area_m2"] == 114
    assert json.loads(rows["veilingnotaris:2314"]["raw_json"])["vastgoedveiling_url"].endswith(
        "/veiling/2314/haarlem-kijkduinstraat-43")
    assert "vastgoedveiling:2646" in rows                                  # DE-only lot is new


def test_vastgoedveiling_copy_is_marked_duplicate_of_veilingnotaris(db, add):
    add("veilingnotaris", "2314", country="NL", title="Haarlem, Kijkduinstraat 43",
        concelho="Haarlem", price=None)
    add("vastgoedveiling", "2314", country="NL", title="Haarlem, Kijkduinstraat 43",
        concelho="Haarlem", price=None)
    from db import mark_duplicates
    assert mark_duplicates(db) >= 1
    assert db.execute("SELECT duplicate_of FROM listings WHERE id='vastgoedveiling:2314'").fetchone()[0] \
        == "veilingnotaris:2314"


STORIA_HOUSE = {
    "id": 10681692, "title": "Casa la rosu de vanzare - Lipanesti",
    "slug": "casa-la-rosu-de-vanzare-lipanesti-IDIONm", "hidePrice": False, "estate": "HOUSE",
    "areaInSquareMeters": 160, "totalPrice": {"value": 72000, "currency": "EUR"},
    "images": [{"medium": "https://cdn.example/casa.jpg"}],
    "location": {"reverseGeocoding": {"locations": [
        {"locationLevel": "county", "name": "Prahova"},
        {"locationLevel": "commune", "name": "Lipanesti"},
        {"locationLevel": "village", "name": "Satu"},
    ]}},
}
STORIA_SKIP = [
    {"id": 2, "title": "Hidden", "slug": "hidden", "hidePrice": True,
     "totalPrice": {"value": 1000, "currency": "EUR"}},
    {"id": 3, "title": "Lei", "slug": "lei", "hidePrice": False,
     "totalPrice": {"value": 50000, "currency": "RON"}, "areaInSquareMeters": 40},
]


def _storia_html(items):
    blob = {"props": {"pageProps": {"data": {"searchAds": {"items": items}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(blob) + "</script>"


def test_storia_keeps_the_euro_house_and_drops_the_rest(db, fake_http):
    from sources.ro import parse_storia_ad
    assert parse_storia_ad(STORIA_SKIP[0], "house") is None
    assert parse_storia_ad(STORIA_SKIP[1], "house") is None
    land = parse_storia_ad({**STORIA_HOUSE, "terrainAreaInSquareMeters": 1400,
                            "areaInSquareMeters": 20}, "terreno")
    assert land["area_m2"] == 1400 and land["tipo"] == "terreno"
    bait = {**STORIA_HOUSE, "id": 9, "totalPrice": {"value": 16, "currency": "EUR"},
            "areaInSquareMeters": 2370}
    assert parse_storia_ad(bait, "terreno") is None

    def handler(method, url, kw):
        page = (kw.get("params") or {}).get("page")
        if "/casa/" in url and page == 1:
            return FakeResponse(_storia_html([STORIA_HOUSE, *STORIA_SKIP]))
        return FakeResponse(_storia_html([]))

    fake_http(handler)
    assert REGISTRY["storia"].func(db, max_price=100000) == 1
    row = db.execute("SELECT * FROM listings").fetchone()
    assert row["id"] == "storia:10681692" and row["price"] == 72000 and row["area_m2"] == 160
    assert row["district"] == "Prahova" and row["concelho"] == "Lipanesti" and row["freguesia"] == "Satu"
    assert row["url"].endswith("/ro/ad/casa-la-rosu-de-vanzare-lipanesti-IDIONm")
    assert row["image_url"] == "https://cdn.example/casa.jpg"


ARUODAS = """
<div class="list-row-v2" data-uid="11-1449945">
<h3><a href="https://www.aruodas.lt/sklypai-example-11-1449945/?search_pos=1">Širvintų r. sav., Diržioniškių k.</a></h3>
<div data-param-label="Plotas"><span class="list-detail-value-v2">10.31 a</span></div>
<span class="list-item-price-v2">27 000 €</span>
<img data-src="https://aruodas-img.dgn.lt/plot.jpg" src="https://static.aruodas.lt/nophoto.svg">
</div>
<div class="list-row-v2" data-uid="11-9">
<h3><a href="https://www.aruodas.lt/butai-nuomai-11-9/">Nuoma</a></h3>
<span class="list-item-price-v2">400 €</span></div>
"""


def test_aruodas_reads_ares_as_square_metres_and_skips_a_rental(db, fake_http):
    def handler(method, url, kw):
        if "/sklypai/" in url and "puslapis" not in url:
            return FakeResponse(ARUODAS)
        return FakeResponse("<html></html>")

    fake_http(handler)
    assert REGISTRY["aruodas"].func(db, max_price=100000) == 1
    row = db.execute("SELECT * FROM listings").fetchone()
    assert row["id"] == "aruodas:11-1449945" and row["price"] == 27000 and row["area_m2"] == 1031
    assert row["tipo"] == "terreno" and row["district"] == "Širvintų r. sav."
    assert row["concelho"] == "Diržioniškių k."
    assert row["url"] == "https://www.aruodas.lt/sklypai-example-11-1449945/"
    assert row["image_url"] == "https://aruodas-img.dgn.lt/plot.jpg"


def _twk_page(lots, total):
    blob = {"props": {"pageProps": {"lotsData": {"results": lots, "totalSize": total}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(blob) + "</script>"


def _twk_lot(display_id, title, city, country, cents):
    return {"displayId": display_id, "id": "uuid-" + display_id, "title": title,
            "urlSlug": f"lot-{display_id}", "location": {"city": city, "countryCode": country},
            "currentBidAmount": {"cents": cents, "currency": "EUR"}, "endDate": 1793303940,
            "image": {"url": "https://media.example/x"}, "biddingStatus": "BIDDING_OPEN"}


def test_troostwijk_reads_each_real_estate_category_once(db, fake_http):
    house = _twk_lot("A1-1-1", "Huis met 660 m2 grond in Voorbeelddorp", "Voorbeelddorp", "bg", 255000)
    land = _twk_lot("A1-2-1", "Loofbos 11 ha", "Bosdorp", "de", 95000)

    def handler(method, url, kw):
        if "/huizen/" in url:
            return FakeResponse(_twk_page([house], 1))
        if "/gronden/" in url:
            return FakeResponse(_twk_page([land, house], 2))    # a lot shown twice is one listing
        return FakeResponse(_twk_page([], 0))
    fake_http(handler)
    assert REGISTRY["troostwijk"].func(db) == 2
    rows = {r["id"]: r for r in db.execute("SELECT * FROM listings")}
    h, w = rows["troostwijk:A1-1-1"], rows["troostwijk:A1-2-1"]
    assert h["country"] == "BG" and h["tipo"] == "vastgoed" and h["current_bid"] == 2550 and h["price"] is None
    assert w["tipo"] == "terreno" and w["url"] == "https://www.troostwijkauctions.com/nl/l/lot-A1-2-1"
    assert h["date_end"] == "2026-10-29T19:59:00"
