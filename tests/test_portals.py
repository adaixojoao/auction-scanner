"""Parsers for the public sale portals. Fixtures are the card shapes seen live."""
import pytest

from conftest import FakeResponse


CUSTOJUSTO = """<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{
"listItems":[
 {"listID":"43910657","title":"Moradia na ilha","body":"Moradia com 104m2 e lote 360m2","type":"sell",
  "price":42000,"url":"/acores/imobiliario/moradias/moradia-43910657",
  "imageFullURL":"https://images.example/moradia.jpg",
  "locationNames":{"district":"Acores","municipality":"Madalena","parish":"Criacao Velha"}},
 {"listID":"1","title":"Arrendamento","type":"let","price":500,"url":"/x"},
 {"listID":"2","title":"Cara","type":"sell","price":900000,"url":"/cara"}
]}}}</script></html>"""

CASASAPO = """<div class="property" id="property_81b60d9f-b8eb-11f1-90a1-060000000056">
<div class="property-media" data-title="Moradia T2">
<a href="https://gespub.casa.sapo.pt/counter?l=https://casa.sapo.pt/comprar-moradia-t2.html">foto</a>
</div>
<p>Aldeia, Distrito de Faro | 90m² | 38.000 €</p></div>"""

CASASAPO_DIRECT = """<div class="property" id="property_5d194c14-a70a-4c48-870d-f6165f73b7d8">
<div class="property-media" data-title="Moradia T3">
<a href="/comprar-moradia-t3-oleiros-estreito-vilar-barroco-5d194c14-a70a-4c48-870d-f6165f73b7d8.html">foto</a>
</div>
<div class="property-location">Estreito-Vilar Barroco, Oleiros, Distrito de Castelo Branco</div>
<div class="property-price-value">18.000 €</div></div>"""

PARUVENDU = """<div class="blocAnnonce" data-id="1295984707">
<a href="/immobilier/vente/maison/1295984707A1" title="Maison - 3 pièce(s) - 77 m²">Maison</a>
<span>55 000 €</span></div>"""

ENTRE = """<a href="/annonces-immobilieres/maison/vente/loupia-11300/maison-de-village/ref-22653367">
Maison de village 115 m² à rénover</a><span>42 000 €</span>"""

IAD = """<a href="/annonce/maison-vente-3-pieces-la-ciotat-79m2/r2126785">Maison 79 m²</a>
<span>89 000 €</span>"""

OPTIM = """<a href="/fr/immobilier/vente/terrain/credin/terrain-a-vendre-de-2-390-00-m2-credin-56/509927">
Terrain 2 390 m²</a><span>28 000 €</span>"""

LAFORET = """<a href="https://www.laforet.com/agence-immobiliere/ploermel/acheter/josselin/maison-5-pieces-52970436">
Maison 5 pièces</a><span>64 000 €</span>"""

KLEIN = """<article data-adid="3509674879" data-href="/s-anzeige/bauernhaus/3509674879-208-24008">
<a href="/s-anzeige/bauernhaus/3509674879-208-24008">Bauernhaus</a>
<p>45.000 €</p><p>Grundstücksfläche: ca. 410 m²</p></article>"""

IMMOWELT = """<a data-testid="card-mfe-covering-link-testid"
 href="https://www.immowelt.de/expose/50114b17-3480-4603-a5cd-fda74bebcc34"
 title="Mehrfamilienhaus zum Kauf - Malberg - 48.000 € - 202 m²"></a>"""

WILLHABEN = """<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"searchResult":{
"advertSummaryList":{"advertSummary":[
 {"attributes":{"attribute":[
   {"name":"HEADING","values":["Kleingartenhaus"]},
   {"name":"PRICE","values":["39000"]},
   {"name":"ADID","values":["1889070517"]},
   {"name":"SEO_URL","values":["immobilien/d/haus-kaufen/oberoesterreich/perg/haus-1889070517/"]},
   {"name":"ESTATE_SIZE/LIVING_AREA","values":["49"]},
   {"name":"STATE","values":["Oberösterreich"]},
   {"name":"LOCATION","values":["Perg"]},
   {"name":"COUNTRY","values":["Österreich"]},
   {"name":"BODY_DYN","values":["Holzbauweise, 49 m²"]}]},
  "id":"1"},
 {"attributes":{"attribute":[
   {"name":"HEADING","values":["Slowenien"]},
   {"name":"PRICE","values":["20000"]},
   {"name":"ADID","values":["999"]},
   {"name":"SEO_URL","values":["immobilien/d/x/999/"]},
   {"name":"COUNTRY","values":["Slowenien"]}]}}
]}}}}}</script>"""

WOHNNET = """<a href="/immobilien/eigentumswohnung-1210-wien-floridsdorf-kauf-2-zimmer-297529477"
 data-id="297529477" title="Wohnung in Floridsdorf">Wohnung 52 m²</a><span>96.000 €</span>"""

ATHOME = """<a class="property-card-link" href="/en/buy/new-property/new-program/mexy/id-8604515.html">
New program in Mexy (FR)</a><span>From €135,000 to €240,000</span><span>From 43 to 73 m²</span>"""

MYHOME = """<div class="property-card"><a href="/residential/brochure/lough-belturbet-co-cavan/5032582">
Detached house, Cavan</a><span>€96,315</span></div>"""

KVEE = """<script type="application/ld+json">{"@type":"RealEstateListing","mainEntity":{
"itemListElement":[{"item":{"@type":"Apartment",
 "name":"Müüa korter, Narva","url":"https://www.kv.ee/korter-narva-po-3931053",
 "floorSize":{"value":47.7},"address":{"addressLocality":"Narva","addressRegion":"Ida-Virumaa",
 "addressCountry":"EE"},"offers":{"price":"26900","priceCurrency":"EUR"}}}]}}</script>"""

HABITACLIA = """<a href="/comprar/duplex/sagrado-corazon/lugo-capital/c96a81b0-4e10-4bd6-8c1e-64ebce68124d/d">
Dúplex en Lugo 70 m²</a><span>49.000 €</span>"""

ETUOVI = """<a href="/kohde/60645669?haku=M1" aria-label="Avaa kohteen tiedot: Välikankaankuja 1">
Välikankaankuja 1, 48 m²</a><span>52 000 €</span>"""

BAZOS = """<div><a href="/inzerat/196264135/investicny-byt.php">Investicny byt Kosice</a>
<span>49 990 €</span></div>"""

REALITYSK = """<div><a href="/byty/byt-kosice/Ju8uT8slPOU">Byt v Kosiciach</a>
<span>790 €/m²</span><span>Cena: 84 000 €</span></div>"""

IMMOVLAN = """<a href="https://immovlan.be/nl/detail/huis/te-koop/6183/trazegnies/vbe75212">
Huis te koop Trazegnies</a><span>99 000 €</span>
<a href="https://immovlan.be/nl/detail/grond/te-koop/6890/libin/vbe75070">Grond te koop</a>
<span>42 000 €</span>
<a href="/nl/detail/huis/te-huur/1000/brussel/vbe10001">Huis te huur</a><span>900 €</span>"""

SAFTI = """<div><a href="/annonces/achat/maison/charron-23700/1724756">Maison - 6 pièces - 154m² Charron ( 23700 ) 86 000 € Terrain 2 758m²</a></div>
<div><a href="/annonces/achat/terrain/flayat-23260/1725031">Terrain 5 200 m² Flayat 54 500 €</a></div>"""

CITYA = """<a href="/annonces/vente/maison/sedan-08200/TMAI376-965615">
Vente Maison 5 pièces 98.62m² à Sedan</a><span>122 000 €</span>
<a href="/annonces/vente/terrain/sedan-08200/TTER12-345678">
Terrain 2 000 m² à Sedan</a><span>18 000 €</span>
<a href="/annonces/vente/maison">Maisons</a><span>1 €</span>"""

IMMOWELT_AT = """<a href="https://www.immowelt.at/expose/3a8df362-4d8a-489d-9e31-c9d9d5602e3b"
 title="Einfamilienhaus zum Kauf - Dürnkrut - 69.000 € - 60 m², 612 m² Grundstück">Haus</a>
<a href="https://www.immowelt.at/expose/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
 title="Hinweis 120 €">Hinweis</a>"""

ERA = """<div><a href="/fr/a-vendre/huy/maison/bungalow-3-chambres-garage">En savoir plus sur Bungalow 3 chambres</a>
<span>€ 129 000</span></div>
<div><a href="/fr/a-vendre/libin/terrain/terrain-a-batir">Terrain à bâtir</a><span>€ 42 000</span></div>
<a href="/fr/a-louer/liege/maison/maison-a-louer">Maison à louer</a><span>€ 900</span>"""

REMAXNL = """<div><a href="/aanbod/geertruidaweg-3-9535">Geertruidaweg 3 SCHERPENISSE € 53.500,- v.o.n. 36 m²</a></div>
<div><a href="/aanbod/westvlietweg-1234-7937">Westvlietweg DEN HAAG € 145.000,- k.k.</a></div>
<div><a href="/aanbod/gording-92-4928">Verkocht onder voorbehoud Gording 92 HOORN Prijs op aanvraag</a>
<span>€ 25.000,-</span></div>"""

WORTIMMO = """<div><a href="/fr/vente-terrain-nord-biwisch-id_572965">Terrain à vendre à Biwisch</a>
<span>150 000 €</span></div>
<div><a href="/fr/vente-terrain-rheinland-pfalz-nittel-id_576212">Terrain à vendre à Nittel</a>
<span>68 000 €</span></div>"""

TOPREALITY = """<div data-ga4-container-item_id="LT_9435745" data-ga4-container-item_name="Rodinny dom"
 data-ga4-container-price="98000" data-ga4-container-currency="EUR"
 data-ga4-container-location_id="Laksarska Nova Ves">
<a href="/predam/dom/rodinny-dom/9435745.html">dom</a></div>
<div data-ga4-container-item_id="LT_9416798" data-ga4-container-item_name="Restauracia"
 data-ga4-container-price="2000" data-ga4-container-currency="€/mesiac">
<a href="/prenajom/restauracia/1">rent</a></div>"""


def test_paruvendu_sends_the_price_ceiling_the_site_applies():
    # pxm is ignored and the page leads with multi-million houses. px1 is the ceiling.
    from sources._market import PORTALS
    urls = PORTALS["paruvendu"].urls
    assert urls
    assert all("px1={max}" in url for url in urls)
    assert all("pxm=" not in url for url in urls)


def test_portals_send_the_price_ceiling_the_site_applies():
    from sources._market import PORTALS
    assert all("pe={max}" in u for u in PORTALS["custojusto"].urls)
    assert any("portugal/imobiliario/moradias-venda" in u for u in PORTALS["custojusto"].urls)
    assert all("gp={max}" in u for u in PORTALS["casasapo"].urls)
    assert any("comprar-moradias" in u for u in PORTALS["casasapo"].urls)
    assert any("comprar-terrenos" in u for u in PORTALS["casasapo"].urls)
    assert all("priceMax={max}" in u for u in PORTALS["iadfrance"].urls)
    assert any("/annonces/vente/maison" in u for u in PORTALS["iadfrance"].urls)
    assert any("/annonces/vente/terrain" in u for u in PORTALS["iadfrance"].urls)
    assert all("prixMax={max}" in u for u in PORTALS["citya"].urls)
    assert any("/annonces/vente/terrain?" in u for u in PORTALS["citya"].urls)
    assert all("/fr/achat/terrain" in u for u in PORTALS["optimhome"].urls)
    assert not any(u.rstrip("/").endswith("/vente") for u in PORTALS["optimhome"].urls)
    assert all("sort=price_asc" in u for u in PORTALS["laforet"].urls)
    assert all("cenado={max}" in u for u in PORTALS["bazos"].urls)
    assert all("prixMax={max}" in u for u in PORTALS["entreparticuliers"].urls)
    assert all("classified-search" in u and "priceMax={max}" in u for u in PORTALS["immowelt"].urls)
    assert any("estateTypes=House" in u for u in PORTALS["immowelt"].urls)
    assert any("estateTypes=Plot" in u for u in PORTALS["immowelt"].urls)
    assert all("classified-search" in u and "priceMax={max}" in u for u in PORTALS["immoweltat"].urls)


def test_habitaclia_reads_cheap_houses_across_inland_provinces():
    from sources._market import PORTALS
    urls = PORTALS["habitaclia"].urls
    assert len(urls) >= 15
    assert all("casas-baratas-provincia-" in u for u in urls)
    for prov in ("lugo", "ourense", "leon", "asturias", "zamora", "caceres"):
        assert any(f"provincia-{prov}.htm" in u for u in urls)


def test_safti_covers_more_than_the_original_ten_departments():
    from sources._market import PORTALS
    urls = PORTALS["safti"].urls
    depts = {u.rsplit("/", 1)[-1] for u in urls}
    assert "creuse-23" in depts and "aveyron-12" in depts and "orne-61" in depts
    assert any("/maison/" in u for u in urls) and any("/terrain/" in u for u in urls)
    assert len(depts) >= 20


def test_wortimmo_reads_later_land_pages_and_the_french_side():
    from sources._market import PORTALS
    urls = PORTALS["wortimmo"].urls
    assert any(u.endswith("/terrain") for u in urls)
    assert any("page=5" in u for u in urls)
    assert any(u.endswith("/terrain/france") for u in urls)


def test_each_portal_reads_the_card_it_was_built_from():
    from sources._market import parse_portal
    cases = {
        "custojusto": (CUSTOJUSTO, "43910657", 42000, "Acores"),
        "casasapo": (CASASAPO, "81b60d9f-b8eb-11f1-90a1-060000000056", 38000, "Faro"),
        "paruvendu": (PARUVENDU, "1295984707", 55000, None),
        "entreparticuliers": (ENTRE, "22653367", 42000, None),
        "iadfrance": (IAD, "2126785", 89000, None),
        "optimhome": (OPTIM, "509927", 28000, None),
        "laforet": (LAFORET, "52970436", 64000, None),
        "kleinanzeigen": (KLEIN, "3509674879", 45000, None),
        "immowelt": (IMMOWELT, "50114b17-3480-4603-a5cd-fda74bebcc34", 48000, None),
        "willhaben": (WILLHABEN, "1889070517", 39000, "Oberösterreich"),
        "wohnnet": (WOHNNET, "297529477", 96000, None),
        "athome": (ATHOME, "8604515", 135000, None),
        "myhome": (MYHOME, "5032582", 96315, None),
        "kvee": (KVEE, "3931053", 26900, "Ida-Virumaa"),
        "habitaclia": (HABITACLIA, "c96a81b0-4e10-4bd6-8c1e-64ebce68124d", 49000, None),
        "etuovi": (ETUOVI, "60645669", 52000, None),
        "bazos": (BAZOS, "196264135", 49990, None),
        "realitysk": (REALITYSK, "Ju8uT8slPOU", 84000, None),
        "topreality": (TOPREALITY, "LT_9435745", 98000, "Laksarska Nova Ves"),
        "immovlan": (IMMOVLAN, "vbe75212", 99000, None),
        "safti": (SAFTI, "1724756", 86000, None),
        "citya": (CITYA, "TMAI376-965615", 122000, None),
        "immoweltat": (IMMOWELT_AT, "3a8df362-4d8a-489d-9e31-c9d9d5602e3b", 69000, None),
        "era": (ERA, "huy/maison/bungalow-3-chambres-garage", 129000, None),
        "remaxnl": (REMAXNL, "9535", 53500, None),
        "wortimmo": (WORTIMMO, "572965", 150000, None),
    }
    assert len(cases) == 26
    for name, (html, eid, price, district) in cases.items():
        rows = parse_portal(name, html)
        assert rows and rows[0]["external_id"] == eid, name
        assert rows[0]["price"] == price, name
        assert rows[0]["url"].startswith("http"), name
        if district:
            assert rows[0]["district"] == district, name
        assert parse_portal(name, "<html></html>") == []
        assert parse_portal(name, "") == []


def test_casasapo_reads_the_direct_listing_link():
    from sources._market import parse_portal
    row = parse_portal("casasapo", CASASAPO_DIRECT)[0]
    assert row["external_id"] == "5d194c14-a70a-4c48-870d-f6165f73b7d8"
    assert row["price"] == 18000
    assert row["district"] == "Castelo Branco"
    assert "Usado" not in (row["district"] or "")
    assert row["url"].endswith("5d194c14-a70a-4c48-870d-f6165f73b7d8.html")


def test_custojusto_keeps_sales_under_the_budget_and_drops_the_rest(db, fake_http):
    from db import load_listings
    from sources.pt import scrape_custojusto
    fake_http(lambda m, u, kw: FakeResponse(CUSTOJUSTO))
    result = scrape_custojusto(db, max_price=100000)
    assert result == 1 and result.scope == "partial"
    rows = load_listings(db, include_hidden=True)
    assert len(rows) == 1 and rows[0]["source"] == "custojusto"
    assert rows[0]["price"] == 42000 and rows[0]["area_m2"] == 104
    assert "Madalena" in (rows[0]["concelho"] or "")


def test_wohnnet_search_uses_the_price_ceiling_query():
    from sources._market import PORTALS
    urls = PORTALS["wohnnet"].urls
    assert any("haeuser/oesterreich" in u and "preis=-{max}" in u for u in urls)
    assert any("grundstuecke/oesterreich" in u for u in urls)
    assert not any("haus-kaufen/preis-bis" in u for u in urls)


def test_portals_skip_a_foreign_willhaben_ad_and_tag_a_french_athome_home():
    from sources._market import parse_portal
    assert [r["external_id"] for r in parse_portal("willhaben", WILLHABEN)] == ["1889070517"]
    row = parse_portal("athome", ATHOME)[0]
    assert row["country"] == "FR" and row["concelho"] == "Mexy"
    german = parse_portal("wohnnet", WOHNNET.replace(
        "eigentumswohnung-1210-wien", "mehrfamilienhaus-deutschland"))[0]
    assert german["country"] == "DE"
    assert parse_portal("optimhome", OPTIM)[0]["tipo"] == "terreno"
    citya = parse_portal("citya", CITYA)
    assert [r["external_id"] for r in citya] == ["TMAI376-965615", "TTER12-345678"]
    assert citya[1]["tipo"] == "terreno" and citya[1]["price"] == 18000
    assert [r["external_id"] for r in parse_portal("immovlan", IMMOVLAN)] == ["vbe75212", "vbe75070"]
    assert parse_portal("immovlan", IMMOVLAN)[1]["tipo"] == "terreno"
    bouw = '<a href="/nl/detail/bouwgrond/te-koop/6698/grand-halleux/vwd12819">Bouwgrond</a><span>44 000 €</span>'
    assert parse_portal("immovlan", bouw)[0]["tipo"] == "terreno"
    safti = parse_portal("safti", SAFTI)
    assert safti[0]["tipo"] == "imovel" and safti[1]["external_id"] == "1725031"
    assert safti[1]["tipo"] == "terreno" and safti[1]["price"] == 54500
    assert parse_portal("immoweltat", IMMOWELT_AT)[0]["tipo"] == "imovel"
    assert len(parse_portal("immoweltat", IMMOWELT_AT)) == 1
    era = parse_portal("era", ERA)
    assert era[0]["title"].startswith("Bungalow")
    assert [r["external_id"] for r in era] == [
        "huy/maison/bungalow-3-chambres-garage", "libin/terrain/terrain-a-batir"]
    assert era[1]["tipo"] == "terreno" and era[1]["price"] == 42000
    remax = parse_portal("remaxnl", REMAXNL)
    assert [r["external_id"] for r in remax] == ["9535", "7937"]
    assert remax[0]["tipo"] == "woning" and remax[0]["area_m2"] == 36
    from scoring import property_kind
    assert property_kind(remax[0]) == "home"
    wort = parse_portal("wortimmo", WORTIMMO)
    assert wort[0]["tipo"] == "terreno" and wort[0]["country"] == "LU" and wort[0]["concelho"] == "Biwisch"
    assert wort[1]["country"] == "DE" and wort[1]["concelho"] == "Nittel" and wort[1]["price"] == 68000
    assert parse_portal("kvee", KVEE)[0]["area_m2"] == 47.7
    assert parse_portal("willhaben", WILLHABEN)[0]["area_m2"] == 49


def test_the_heading_wins_over_the_photo_link():
    from sources._market import parse_portal
    html = """<div>
    <a href="/s-anzeige/x/3337536333-207-4753">5</a>
    <a href="/s-anzeige/neues-baugebiet-in-bad-zwesten/3337536333-207-4753">NEUES BAUGEBIET in Bad Zwesten</a>
    <p>49.950 €</p></div>"""
    row = parse_portal("kleinanzeigen", html)[0]
    assert row["external_id"] == "3337536333" and row["price"] == 49950
    assert row["title"].startswith("NEUES BAUGEBIET")
    land = """<a href="https://www.laforet.com/agence-immobiliere/labaule/acheter/le-pouliguen/terrain-52968141">(nouvel onglet)</a>
    <span>49 900 €</span>"""
    plot = parse_portal("laforet", land)[0]
    assert plot["tipo"] == "terreno" and "pouliguen" in plot["title"]


def test_era_reads_the_price_line_not_the_reference_number():
    from sources._market import parse_portal
    card = """<article>
    <a href="/fr/a-vendre/durbuy/maison/01461087">Maison</a>
    <div class="field field--price">€ 95 000</div>
    <span>01461087 € 95 000</span></article>"""
    row = parse_portal("era", card)[0]
    assert row["price"] == 95000
    assert row["external_id"] == "durbuy/maison/01461087"
    on_request = """<article>
    <a href="/fr/a-vendre/huy/maison/une-maison">Maison</a>
    <div class="field field--price on-demand">Prix sur demande</div>
    <span>123456 € 10</span></article>"""
    assert parse_portal("era", on_request) == []


def test_era_asks_for_homes_under_the_budget_and_stops_at_a_short_page(db, fake_http):
    from db import load_listings
    from sources._market import ERA_PAGE_SIZE
    from sources.be import scrape_era
    cheap = """<article><a href="/fr/a-vendre/libin/maison/maison-a-renover">Maison à rénover</a>
    <div class="field field--price">€ 80 000</div></article>"""
    numbered = """<article><a href="/fr/a-vendre/durbuy/maison/01461087">Maison</a>
    <div class="field field--price">€ 95 000</div><span>01461087 € 95 000</span></article>"""
    dear = """<article><a href="/fr/a-vendre/spa/maison/villa">Villa</a>
    <div class="field field--price">€ 450 000</div></article>"""

    def handler(method, url, kw):
        assert "property_index" in url
        params = kw["params"]
        assert params["filter[price]"] == "(min:1;max:100000)"
        assert params["filter[property_type]"] == "46,44,63"
        assert params["sort"] == "price"
        offset = params["pager[offset]"]
        if offset == "0":
            cards = [cheap] * (ERA_PAGE_SIZE - 1) + [dear]
        elif offset == "36":
            cards = [numbered]
        else:
            raise AssertionError(offset)
        return FakeResponse(json_data={"data": [{"attributes": {"teaser": c}} for c in cards]})

    fake_http(handler)
    result = scrape_era(db, max_price=100000)
    assert result == 2 and result.scope == "full" and result.pages_seen == 2
    rows = {r["price"]: r for r in load_listings(db, include_hidden=True)}
    assert set(rows) == {80000, 95000}
    assert rows[95000]["id"] == "era:durbuy/maison/01461087"


def test_era_a_later_page_failing_is_not_a_full_scan(db, fake_http):
    import requests
    from sources._market import ERA_PAGE_SIZE
    from sources.be import scrape_era
    cheap = """<article><a href="/fr/a-vendre/libin/maison/maison-a-renover">Maison</a>
    <div class="field field--price">€ 80 000</div></article>"""

    def handler(method, url, kw):
        if kw["params"]["pager[offset]"] == "0":
            return FakeResponse(json_data={"data": [{"attributes": {"teaser": cheap}}] * ERA_PAGE_SIZE})
        return FakeResponse(status=503)

    fake_http(handler)
    result = scrape_era(db, max_price=100000)
    assert result == 1 and result.scope == "partial" and result.pages_seen == 1

    def down(method, url, kw):
        return FakeResponse(status=503)

    fake_http(down)
    with pytest.raises(requests.HTTPError):
        scrape_era(db, max_price=100000)


def test_a_fee_or_per_m2_is_not_the_asking_price():
    from sources._market import parse_portal
    fee = """<a href="/annonces-immobilieres/maison/vente/loupia-11300/maison-de-village/ref-22653367">
    Maison</a><span>12 €</span><span>42 000 €</span>"""
    assert parse_portal("entreparticuliers", fee)[0]["price"] == 42000
    rate = """<div><a href="/inzerat/196264135/investicny-byt.php">Byt</a>
    <span>790 €/m²</span><span>Cena: 49 990 €</span></div>"""
    assert parse_portal("bazos", rate)[0]["price"] == 49990


def test_a_card_with_no_euro_amount_is_still_kept(db, fake_http):
    from db import load_listings
    from sources._market import parse_portal, scrape_named
    html = """<a href="/annonces-immobilieres/maison/vente/loupia-11300/maison-de-village/ref-22653367">
    Maison de village à rénover, prix sur demande</a>"""
    row = parse_portal("entreparticuliers", html)[0]
    assert row["external_id"] == "22653367" and row["price"] is None
    fake_http(lambda m, u, kw: FakeResponse(html))
    result = scrape_named(db, "entreparticuliers", 100000)
    assert result == 1 and result.scope == "partial"
    kept = load_listings(db, include_hidden=True)
    assert len(kept) == 1 and kept[0]["price"] is None
