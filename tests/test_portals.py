"""Parsers for the public sale portals. Fixtures are the card shapes seen live."""
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

PARUVENDU = """<div class="blocAnnonce" data-id="1295984707">
<a href="/immobilier/vente/maison/1295984707A1" title="Maison - 3 pièce(s) - 77 m²">Maison</a>
<span>55 000 €</span></div>"""

ENTRE = """<a href="/annonces-immobilieres/maison/vente/loupia-11300/maison-de-village/ref-22653367">
Maison de village 115 m² à rénover</a><span>42 000 €</span>"""

IAD = """<a href="/annonce/maison-vente-3-pieces-la-ciotat-79m2/r2126785">Maison 79 m²</a>
<span>89 000 €</span>"""

ETRE = """<a href="/immobilier-26976112-vente-appartement-saint-etienne">Appartement</a>
<span>61 000 €</span>"""

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

TOPREALITY = """<div data-ga4-container-item_id="LT_9435745" data-ga4-container-item_name="Rodinny dom"
 data-ga4-container-price="98000" data-ga4-container-currency="EUR"
 data-ga4-container-location_id="Laksarska Nova Ves">
<a href="/predam/dom/rodinny-dom/9435745.html">dom</a></div>
<div data-ga4-container-item_id="LT_9416798" data-ga4-container-item_name="Restauracia"
 data-ga4-container-price="2000" data-ga4-container-currency="€/mesiac">
<a href="/prenajom/restauracia/1">rent</a></div>"""


def test_each_portal_reads_the_card_it_was_built_from():
    from sources._market import parse_portal
    cases = {
        "custojusto": (CUSTOJUSTO, "43910657", 42000, "Acores"),
        "casasapo": (CASASAPO, "81b60d9f-b8eb-11f1-90a1-060000000056", 38000, "Faro"),
        "paruvendu": (PARUVENDU, "1295984707", 55000, None),
        "entreparticuliers": (ENTRE, "22653367", 42000, None),
        "iadfrance": (IAD, "2126785", 89000, None),
        "etreproprio": (ETRE, "26976112", 61000, None),
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
    }
    assert len(cases) == 20
    for name, (html, eid, price, district) in cases.items():
        rows = parse_portal(name, html)
        assert rows and rows[0]["external_id"] == eid, name
        assert rows[0]["price"] == price, name
        assert rows[0]["url"].startswith("http"), name
        if district:
            assert rows[0]["district"] == district, name
        assert parse_portal(name, "<html></html>") == []
        assert parse_portal(name, "") == []


def test_custojusto_keeps_sales_under_the_budget_and_drops_the_rest(db, fake_http):
    from db import load_listings
    from sources.pt import scrape_custojusto
    fake_http(lambda m, u, kw: FakeResponse(CUSTOJUSTO))
    assert scrape_custojusto(db, max_price=100000) == 1
    rows = load_listings(db, include_hidden=True)
    assert len(rows) == 1 and rows[0]["source"] == "custojusto"
    assert rows[0]["price"] == 42000 and rows[0]["area_m2"] == 104
    assert "Madalena" in (rows[0]["concelho"] or "")


def test_portals_skip_a_foreign_willhaben_ad_and_tag_a_french_athome_home():
    from sources._market import parse_portal
    assert [r["external_id"] for r in parse_portal("willhaben", WILLHABEN)] == ["1889070517"]
    row = parse_portal("athome", ATHOME)[0]
    assert row["country"] == "FR" and row["concelho"] == "Mexy"
    german = parse_portal("wohnnet", WOHNNET.replace(
        "eigentumswohnung-1210-wien", "mehrfamilienhaus-deutschland"))[0]
    assert german["country"] == "DE"
    assert parse_portal("optimhome", OPTIM)[0]["tipo"] == "terreno"
    assert parse_portal("kvee", KVEE)[0]["area_m2"] == 47.7
    assert parse_portal("willhaben", WILLHABEN)[0]["area_m2"] == 49
