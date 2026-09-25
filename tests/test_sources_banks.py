"""Fixture tests for the bank portals and auction houses rewritten in Sept 2026.

Every fixture below is a trimmed copy of the live page or API shape, with
invented addresses and references.
"""
import json

import requests

from conftest import FakeResponse
from sources import REGISTRY, describe_error, load_all, run_source

load_all()


def rows(db, source):
    return {r["external_id"]: r for r in db.execute(
        "SELECT * FROM listings WHERE source=? ORDER BY external_id", (source,))}


# ─── Imobancos (JSON API) ───────────────────────────────────────────

def imobancos_hit(id_, price, purpose="Comprar", **extra):
    return {"id": id_, "available": True, "prop_purpose": purpose, "prop_price": price,
            "prop_title": f"Moradia {id_}", "prop_type": "Moradia", "prop_area": "120.00",
            "prop_district": "Viseu", "prop_county": "Armamar", "prop_parish": "Queimadela",
            "prop_description": "Moradia para recuperar.", "site_name": "Montepio",
            "photos": [{"photo_url": f"https://img.example.com/{id_}.jpg"}], **extra}


def test_imobancos_reads_the_api_and_pages(db, fake_http):
    pages = {
        1: {"hits": [imobancos_hit(1, 30000), imobancos_hit(2, 250000),
                     imobancos_hit(3, 0), imobancos_hit(4, 500, purpose="Arrendar")],
            "page": 1, "totalPages": 2},
        2: {"hits": [imobancos_hit(5, 45000, available=False), imobancos_hit(6, 60000)],
            "page": 2, "totalPages": 2},
    }
    detail = {   # the listing pages: 1 links to the bank, 3 has no link, 6 is down
        "1": '<a href="https://www.banco-exemplo.pt/imovel/99?uid=abc">Ver anúncio original</a>'
             '<a href="/imoveis">Voltar</a>',
        "3": '<a href="/contactos">Contactar</a>',
    }

    def handler(method, url, kw):
        if method == "POST":
            return FakeResponse(json_data=pages[kw["json"]["page"]])
        eid = url.rsplit("/", 1)[1]
        return FakeResponse(detail[eid]) if eid in detail else FakeResponse("down", status=503)

    session = fake_http(handler)
    assert REGISTRY["imobancos"].func(db, max_price=100000) == 3
    got = rows(db, "imobancos")
    assert set(got) == {"1", "3", "6"}                       # over budget, rent and sold left out
    one = got["1"]
    assert one["price"] == 30000 and one["area_m2"] == 120 and one["tipo"] == "moradia"
    assert (one["district"], one["concelho"], one["freguesia"]) == ("Viseu", "Armamar", "Queimadela")
    assert "Montepio" in one["description"]
    assert one["url"] == "https://www.banco-exemplo.pt/imovel/99?uid=abc"   # the bank's own page
    assert got["3"]["url"] == "https://imobancos.pt/imoveis/3"            # no original: Imobancos
    assert got["3"]["price"] is None                         # 0 is "no price", not a €0 bargain
    assert [c[2]["json"]["page"] for c in session.calls if c[0] == "POST"] == [1, 2]

    # Next scan: the bank's page stays; 1 and 3 are not fetched again, 6 (down) is retried.
    before = len(session.calls)
    REGISTRY["imobancos"].func(db, max_price=100000)
    assert rows(db, "imobancos")["1"]["url"] == "https://www.banco-exemplo.pt/imovel/99?uid=abc"
    pages_asked = [c[1] for c in session.calls[before:] if c[0] == "GET"]
    assert pages_asked == ["https://imobancos.pt/imoveis/6"]


# ─── Caixa Imobiliário (CGD API behind a key and token) ─────────────

CGD_HOME = '<html><body><app-root></app-root><script src="main-ABC123.js" type="module"></script></body></html>'
CGD_MAIN = 'import("./chunk-AAAA1111.js");import("./chunk-BBBB2222.js");var x=1;'
CGD_CHUNK = 'var P={production:!0,apigee:{tempTokenUrl:"https://www.caixaimobiliario.pt/bff/api/v1/auth/token",clientId:"test-client-id"},x:1};'


def cgd_result(ref, price, where, titulo="Moradia \n", objective="Comprar"):
    return {"field_preco_venda_int": str(price), "field_area_bruta_int": "142",
            "field_titulo": titulo, "field_localizacao": where, "nid": ref,
            "field_objective": objective, "field_url": f"/pt/comprar/{ref}",
            "field_morada_completa": "Rua Exemplo, 1", "field_tarja_tarja": "PREÇO REVISTO",
            "field_media_image": "<img src='x'>"}


def test_cgd_uses_the_sites_key_and_token(db, fake_http):
    api_pages = [
        {"pager": {"current_page": 0, "total_pages": 2},
         "results": [cgd_result("2024089", 55000, "Sanfins do Douro, Alijó, Vila Real"),
                     cgd_result("2028152", 200000, "Alcoentre, Azambuja, Lisboa")]},
        {"pager": {"current_page": 1, "total_pages": 2},
         "results": [cgd_result("2027624", 0, "Mértola, Beja", titulo="Prédio Rústico: Outros"),
                     cgd_result("2020001", 900, "Faro", objective="Arrendar")]},
    ]

    def handler(method, url, kw):
        if url.endswith("/pt"):
            return FakeResponse(CGD_HOME)
        if url.endswith("main-ABC123.js"):
            return FakeResponse(CGD_MAIN)
        if url.endswith("chunk-AAAA1111.js"):
            return FakeResponse("var nothing=1;")
        if url.endswith("chunk-BBBB2222.js"):
            return FakeResponse(CGD_CHUNK)
        if url.endswith("/auth/token"):
            return FakeResponse(json_data={"access_token": "tok123", "expires_in": "599"})
        assert kw["headers"]["x-api-key"] == "test-client-id"
        assert kw["headers"]["x-authorization"] == "Bearer tok123"
        return FakeResponse(json_data=api_pages[kw["params"]["page"]])

    fake_http(handler)
    assert REGISTRY["cgd"].func(db, max_price=100000) == 2
    got = rows(db, "cgd")
    assert set(got) == {"2024089", "2027624"}
    first = got["2024089"]
    assert (first["freguesia"], first["concelho"], first["district"]) == ("Sanfins do Douro", "Alijó", "Vila Real")
    assert first["title"] == "Moradia" and first["price"] == 55000 and first["area_m2"] == 142
    assert first["url"] == "https://www.caixaimobiliario.pt/pt/comprar/2024089"
    assert "PREÇO REVISTO" in first["description"] and "field_media_image" not in first["raw_json"]
    short = got["2027624"]                                    # two-part place, no price
    assert (short["freguesia"], short["concelho"], short["district"]) == (None, "Mértola", "Beja")
    assert short["price"] is None


def test_cgd_without_a_key_says_the_page_changed(db, fake_http):
    fake_http(lambda m, url, kw: FakeResponse(CGD_HOME if url.endswith("/pt") else "var a=1;"))
    result = run_source(db, REGISTRY["cgd"], max_price=100000)
    assert result["status"] == "error" and "API key not found" in result["message"]


# ─── Santander (HTML cards, page number in the path) ────────────────

def santander_card(id_, price, tipo="Moradia", concelho="Paredes de Coura", freguesia="Mozelos"):
    return f"""<li><div class="prop">
      <div class="prop-imgWrap"><a href="/detalhe/{id_}?distrito=viana-do-castelo&amp;cidade=x&amp;price=1">
        <div class="prop-imgArea"></div></a></div>
      <div class="prop-panel">
        <div class="prop-tag">{tipo} | Refª: 9{id_}</div>
        <div class="prop-tag prop-tag-sub">{price}&#160;000 €</div>
        <div class="prop-title"><span class="prop-titleFirst">{concelho}</span>
          <span class="prop-titleSecond">{freguesia}</span></div>
      </div>
      <div class="prop-description"><span class="prop-spec-label">Descrição:</span> Imóvel composto por 3 quarto(s).</div>
    </div></li>"""


def test_santander_cards_and_last_page(db, fake_http):
    pages = {
        "1": "<ul>" + santander_card(101, 15) + santander_card(102, 730) + "</ul>",
        "2": "<ul>" + santander_card(103, 45, tipo="Terrenos rústicos e quintas") + santander_card(101, 15) + "</ul>",
        "3": "<section class='list'><p>Sem resultados</p></section>",
    }
    session = fake_http(lambda m, url, kw: FakeResponse(pages[url.split("/imoveis/")[1].split("/")[0]]))
    assert REGISTRY["santander"].func(db, max_price=100000) == 2
    got = rows(db, "santander")
    assert set(got) == {"101", "103"}                         # 730k over budget, 101 not twice
    one = got["101"]
    assert one["price"] == 15000 and one["tipo"] == "moradia"
    assert (one["district"], one["concelho"], one["freguesia"]) == ("Viana Do Castelo", "Paredes de Coura", "Mozelos")
    assert one["title"] == "Moradia, Mozelos" and "3 quarto" in one["description"]
    assert len(session.calls) == 3                            # stops at the first empty page


# ─── BPI Expresso Imobiliário (card is the link) ────────────────────

BPI_PAGE = """
<a class="announce-details" href="/moradia/t3/viseu/viseu/abraveses/a15590001">
  <h3 class="announce-title">Moradia T3</h3>
  <div class="announce-item-price"><div class="announce-price"><strong>85 000 €</strong></div></div>
  <p class="announce-location">Viseu, Viseu</p></a>
<a class="announce-details" href="/apartamento/t3/lisboa/lisboa/alvalade/a15596911">
  <h3 class="announce-title">Apartamento T3</h3>
  <div class="announce-price"><strong>1 100 000 €</strong></div>
  <p class="announce-location">Lisboa, Lisboa</p></a>
"""


def test_bpi_cards_are_links_with_the_sites_id(db, fake_http):
    fake_http(lambda m, url, kw: FakeResponse(BPI_PAGE))
    assert REGISTRY["bpi"].func(db, max_price=100000) == 1
    row = rows(db, "bpi")["15590001"]                         # the "a…" ref, not a URL hash
    assert row["id"] == "bpi:15590001" and row["title"] == "Moradia T3"
    assert row["price"] == 85000 and row["concelho"] == "Viseu, Viseu"
    assert row["url"] == "https://bpiexpressoimobiliario.net/moradia/t3/viseu/viseu/abraveses/a15590001"


# ─── Bid Leiloeira (sale cards, value on the sale page) ─────────────

def bid_card(item, slug, lots, text, kind="Negociação Particular"):
    return f"""<a class="div_100 leiloes_divs leiloes_item" href="{slug}" id="item-{item}">
      <h3 class="subtitulos">{kind}</h3><h1 class="list_tit">Exemplo Lda</h1>
      <p class="list_txt"><span>\xad\xad<strong>{text}</strong><br/><br/>Rua Exemplo, Sabugal</span></p>
      <h4 class="list_subtit">Inicia Hoje</h4><h2>24 Setembro 2026</h2><div>Nº LOTES</div><div>{lots}</div></a>"""


def test_bidleiloeira_sales_and_opening_value(db, fake_http):
    list_page = ("<div class='columns leiloes_wrapLoader'>"
                 + bid_card(620, "exemplo-terreno-AbCd", 1, "Terreno Rústico, com 7590m2,")
                 + bid_card(621, "exemplo-lotes-EfGh", 5, "Moradia T3 e bens móveis")
                 + bid_card(622, "exemplo-caro-IjKl", 1, "Prédio urbano")
                 + "</div><a href='/leiloes?p=2'>2</a>")
    details = {"exemplo-terreno-AbCd": "<p>Valor abertura 2 500,00€</p>",
               "exemplo-caro-IjKl": "<p>Valor abertura 450\xa0000,00 €</p>"}

    def handler(method, url, kw):
        if url.endswith("/leiloes"):
            return FakeResponse("<p>Sem leilões</p>" if kw.get("params") else list_page)
        return FakeResponse(details[url.rsplit("/", 1)[1]])

    session = fake_http(handler)
    assert REGISTRY["bidleiloeira"].func(db, max_price=100000) == 2
    got = rows(db, "bidleiloeira")
    assert set(got) == {"620", "621"}                         # 622's €450k is over budget
    land = got["620"]
    assert land["title"] == "Terreno Rústico, com 7590m2" and land["price"] == 2500
    assert land["url"] == "https://www.bidleiloeira.pt/exemplo-terreno-AbCd"
    assert "Negociação Particular" in land["description"]
    assert got["621"]["price"] is None                        # five lots: no single value
    assert not any("exemplo-lotes" in c[1] for c in session.calls)


# ─── Sources that cannot be scanned say why ─────────────────────────

def test_unavailable_sources_explain_themselves(db):
    for name, words in [("financas", "acesso.gov.pt"), ("novobanco", "no longer exists"),
                        ("aeat", "subastas.boe.es"), ("servihabitat", "per-province")]:
        result = run_source(db, REGISTRY[name], max_price=100000)
        assert result["status"] == "error" and words in result["message"], (name, result)


def test_sareb_bot_wall_is_named(db, fake_http):
    wall = '<html><head><script src="/_Incapsula_Resource?SWJIYLWA=1"></script></head></html>'
    fake_http(lambda m, url, kw: FakeResponse(wall))
    result = run_source(db, REGISTRY["sareb"], max_price=100000)
    assert result["status"] == "error" and "Incapsula" in result["message"]


def test_firewall_refusal_is_described():
    err = requests.ConnectionError(
        "HTTPSConnectionPool(host='www.haya.es', port=443): Max retries exceeded "
        "(Caused by NewConnectionError('Failed to establish a new connection: [WinError 10013] "
        "An attempt was made to access a socket in a way forbidden by its access permissions'))")
    err.request = requests.Request("GET", "https://www.haya.es/inmuebles/").prepare()
    assert describe_error(err) == "Could not connect to www.haya.es: blocked by this PC's firewall"


# ─── Leilosoc (the page's embedded data) ────────────────────────────

def leilosoc_page(lots, more=False):
    import json as _json
    data = {"props": {"pageProps": {"lots": {"items": lots, "hasNextPage": more}}}}
    links = "".join(f'<a href="/en/lot/{x["auctionId"]}/{x["batchId"]}-lote/">x</a>' for x in lots)
    return f'<html>{links}<script id="__NEXT_DATA__" type="application/json">{_json.dumps(data)}</script></html>'


def leilosoc_lot(auction, batch, title, base, currency="€", **extra):
    return {"auctionId": auction, "batchId": batch, "order": batch, "title": title, "currencySymbol": currency,
            "valueBase": base, "valueBasePublished": True, "valueOpen": base * 0.7, "valueMinimum": None,
            "valueMinimumPublished": False, "addressLocation": "Seia", "address": "Rua Exemplo",
            "auctionEndDate": "2099-10-12T15:00:00Z", "processNumber": "15029/23.2T8SNT",
            "description": "<p>Moradia com <b>120 m2</b></p>", "customFieldsValues": {"building_area": None},
            "auctionTypeCode": "leilao_online", "pictureDefault": "/leilosoc/batch/x.jpg", **extra}


def test_leilosoc_reads_the_embedded_data(db, fake_http):
    page = leilosoc_page([
        leilosoc_lot(45015, 141979, "Moradia · Seia", 20000),
        leilosoc_lot(45015, 141980, "Terreno · Seia", 3500),                       # same auction, 2nd lot
        leilosoc_lot(43041, 157610, "Espaço Comercial | Massango", 210480000, "Kz"),  # Angola: not kept
        leilosoc_lot(45100, 150000, "Moradia · Lisboa", 250000),                    # over budget
    ])
    fake_http(lambda m, url, kw: FakeResponse(page))
    assert REGISTRY["leilosoc"].func(db, max_price=30000) == 2
    got = rows(db, "leilosoc")
    assert set(got) == {"45015", "45015-141980"}         # the first lot keeps the old ID
    house = got["45015"]
    assert house["title"] == "Moradia · Seia" and house["price"] == 20000 and house["area_m2"] == 120
    assert house["concelho"] == "Seia" and house["date_end"] == "2099-10-12T15:00:00Z"
    assert house["url"] == "https://leilosoc.com/en/lot/45015/141979-lote/"
    assert "15029/23.2T8SNT" in house["raw_json"] and house["description"] == "Moradia com 120 m2"


# ─── Whitestar (cards, one search up to the budget) ─────────────────

def whitestar_card(id_, typology, location, price, area, desc="Imóvel em bom estado", badge=""):
    return f"""<a class="wsi-asset-link" href="/Assets/Details/{id_}"><div class="wsi-asset">{badge}
      <p class="wsi-asset-typology">{typology}</p><p class="wsi-asset-location">{location}</p>
      <p class="wsi-asset-price">{price} €</p><div class="wsi-asset-description"><p>{desc}</p></div>
      <div class="wsi-asset-specs"><table><thead><tr><td>Estado</td><td>Áreas</td></tr></thead>
      <tbody><tr><td>Usado</td><td>{area} m<sup>2</sup></td></tr></tbody></table></div></div></a>"""


def test_whitestar_cards_and_paging(db, fake_http):
    pages = {
        "1": "<p>7 imóveis</p>" + whitestar_card(1, "Moradia Isolada T3", "Vila Real, Chaves, VIDAGO", "27 000", 160)
             + whitestar_card(2, "Terreno", "Faro, Castro Marim, ODELEITE", "8 750", "9 680")
             + whitestar_card(3, "Moradia T2", "Viseu, Tondela, TONDELA", "15 000", 90, badge="<span>Reservado</span>"),
        "2": "<p>7 imóveis</p>" + whitestar_card(4, "Apartamento T1", "Lisboa, Amadora, VENTEIRA", "29 000", 45),
        "3": "<p>7 imóveis</p>",
    }
    details = """<h3>Detalhes</h3> Referência 73734 Data Publicação 2020-02-21 Tipo Moradia Isolada Tipologia T3
      Estado Novo Ano Construção 1937 Área ( m 2 ) 160 Class. Energética F Localidade Distrito Vila Real
      Concelho Chaves Freguesia VIDAGO Morada Rua da Fonte, n.º 3 Código Postal 5425-301 Nas Proximidades"""
    with_details = ["/1"]
    session = fake_http(lambda m, url, kw: FakeResponse(
        (details if url.endswith(tuple(with_details)) else "") if m.upper() == "GET"
        else pages.get(kw["data"]["PageNumber"], "")))
    assert REGISTRY["whitestar"].func(db, max_price=30000) == 3
    got = rows(db, "whitestar")
    assert set(got) == {"1", "2", "4"}                  # the reserved one is left out
    house = got["1"]
    assert house["title"] == "Moradia Isolada T3, Vidago" and house["price"] == 27000 and house["area_m2"] == 160
    assert (house["district"], house["concelho"], house["freguesia"]) == ("Vila Real", "Chaves", "VIDAGO")
    assert got["2"]["area_m2"] == 9680 and "Estado: Usado" in house["description"]
    assert all(call[2]["data"]["maxPrice"] == "30000" for call in session.calls if call[0].upper() == "POST")
    # The detail page: year built, publication date and address, kept once.
    raw = json.loads(house["raw_json"])
    assert raw == {"data_publicacao": "2020-02-21", "ano_construcao": "1937", "energia": "F",
                   "morada": "Rua da Fonte, n.º 3", "codigo_postal": "5425-301"}
    assert got["4"]["raw_json"] is None                   # nothing on its page: nothing kept
    # Read once; a listing that already has a map position keeps it.
    db.execute("""UPDATE listings SET raw_json = '{"geo": {"lat": 41.6}}' WHERE id = 'whitestar:4'""")
    db.commit()
    session.calls.clear()
    with_details.append("/4")
    REGISTRY["whitestar"].func(db, max_price=30000)
    assert [c[1] for c in session.calls if c[0] == "GET"] == [
        "https://www.whitestarproperties.pt/Assets/Details/2", "https://www.whitestarproperties.pt/Assets/Details/4"]
    kept = json.loads(rows(db, "whitestar")["4"]["raw_json"])
    assert kept["geo"] == {"lat": 41.6} and kept["ano_construcao"] == "1937"
