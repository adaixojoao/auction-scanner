import json

import scraper
from common import COUNTRY_NAMES
from conftest import FakeResponse
from sources import REGISTRY, load_all, run_source, sources_for
from sources._cards import CardSite, listing_id_from_url, scrape_cards


def test_registry_is_complete():
    load_all()
    assert len(REGISTRY) == 40
    for s in REGISTRY.values():
        assert s.country in COUNTRY_NAMES or s.country == "EU", s
        assert s.description, f"{s.name} needs a docstring"
    optional = {s.name for s in REGISTRY.values() if not s.default}
    assert optional == {"idealista", "courtbid"}
    # every country has at least one default source, PT runs first
    assert {s.country for s in sources_for(None)} == set(COUNTRY_NAMES)
    assert sources_for(None)[0].country == "PT"
    assert [s.name for s in sources_for(["PT"])][:4] == ["eleiloes", "leilosoc", "bcp", "citius"]
    # the CLI accepts every registered name
    parser = scraper.build_parser()
    for name in REGISTRY:
        assert parser.parse_args(["--source", name]).source == name


def test_every_scraper_survives_an_empty_site(db, fake_http):
    fake_http(lambda m, u, kw: FakeResponse("<html><body></body></html>", json_data={}))
    for source in sources_for(None, include_optional=False):
        result = run_source(db, source, max_price=100000)
        assert result["status"] in ("empty", "error"), result
        assert result["count"] == 0
    rows = db.execute("SELECT COUNT(*) FROM scrape_log").fetchone()[0]
    assert rows == len(sources_for(None))


def test_run_source_records_errors(db, fake_http):
    fake_http(lambda m, u, kw: FakeResponse("down", status=503))
    result = run_source(db, REGISTRY["bcp"], max_price=100000)
    assert result["status"] == "error" and "503" in result["message"]
    row = db.execute("SELECT status, message, duration_s FROM scrape_log").fetchone()
    assert row["status"] == "error" and row["duration_s"] is not None


def test_courtbid_without_token_is_an_error_not_a_silent_zero(db):
    result = run_source(db, REGISTRY["courtbid"], max_price=100000, config={})
    assert result["status"] == "error" and "apify_token" in result["message"]


ELEILOES_PAGE = {
    "list": [
        {"id": 101, "titulo": "Moradia em Arganil", "subtipoId": 21, "valorBase": 30000,
         "lanceAtual": 15000, "valorMinimo": 25500, "moradaDistrito": "Coimbra",
         "moradaConcelho": "Arganil", "referencia": "LO101", "capa": "img/1.jpg",
         "dataFim": "2099-10-01T10:00:00"},
        {"id": 102, "titulo": "Terreno", "subtipoId": 27, "valorBase": 5000,
         "lanceAtual": None, "referencia": "NP102", "dataFim": "2099-10-02T10:00:00"},
    ],
    "pagination": {"total": 2},
}


def test_eleiloes(db, fake_http):
    def handler(method, url, kw):
        if "/api/Eventos/?" in url:
            return FakeResponse(json_data=ELEILOES_PAGE)
        return FakeResponse(json_data={"verbas": [{"descricao": "Casa T3 devoluta", "area": 140}]})
    fake_http(handler)
    assert REGISTRY["eleiloes"].func(db, max_price=100000) == 2
    row = db.execute("SELECT * FROM listings WHERE id='eleiloes:101'").fetchone()
    assert row["url"] == "https://e-leiloes.pt/evento/LO101"
    assert row["current_bid"] == 15000 and row["tipo"] == "moradia"
    assert row["description"] == "Casa T3 devoluta" and row["area_m2"] == 140
    assert db.execute("SELECT current_bid FROM listings WHERE id='eleiloes:102'").fetchone()[0] is None


CITIUS_FORM = """
<select id="ctl00_ContentPlaceHolder1_ddlTribunais">
  <option value="0">Todos</option><option value="17">Juízo de Execução de Lisboa</option>
</select>
<input id="__VIEWSTATE" value="vs"><input id="__EVENTVALIDATION" value="ev">
<input id="__VIEWSTATEGENERATOR" value="gen">
"""
# Field order as the Citius page prints it (the parser's regexes depend on it).
CITIUS_RESULTS = """
<table id="ctl00_ContentPlaceHolder1_dlVenda"><tr><td>
Tipo de Bem: Imóvel Estado: Em venda Valor Base: 30.000,00 € Data da venda: 15/10/2099
Modalidade: Venda mediante propostas em carta fechada
Descrição do Bem: Moradia sita na freguesia de Safara, concelho de Moura, com 120 m2
Processo: 165/10.3TBMRA Espécie: Execução Comum
Tipo de Bem: Imóvel Estado: Em venda Valor Base: 2.000,00 € Modalidade: Negociação particular
Descrição do Bem: Terreno de cultura em Moura Processo: 165/10.3TBMRA Espécie: Execução Comum
</td></tr></table>
"""


def test_citius_keeps_every_bem_of_a_processo(db, fake_http):
    fake_http(lambda m, u, kw: FakeResponse(CITIUS_FORM if m == "GET" else CITIUS_RESULTS))
    assert REGISTRY["citius"].func(db, max_price=100000) == 2
    rows = {r["id"]: r for r in db.execute("SELECT * FROM listings")}
    assert set(rows) == {"citius:165103TBMRA", "citius:165103TBMRA-2"}   # first keeps the old ID
    first = rows["citius:165103TBMRA"]
    assert first["price"] == 30000 and first["concelho"] == "Moura" and first["freguesia"] == "Safara"
    assert first["area_m2"] == 120 and first["date_end"] == "2099-10-15T00:00:00"
    raw = json.loads(first["raw_json"])
    assert raw["tribunal"] == "Juízo de Execução de Lisboa"
    assert "carta fechada" in raw["modalidade"]


def test_cards_dedupe_and_price_from_currency(db):
    html = """
    <div class="property-card"><div class="property-inner">
        <a href="/imoveis/2024/lisboa/555">Apartamento T2</a><h3>Apartamento T2</h3>
        <span>Lisboa</span><span>85.000 €</span></div></div>
    <div class="property-card"><a href="/imoveis/2024/porto/556">x</a><h3>Moradia</h3>
        <span>250.000 €</span></div>
    """
    site = CardSite(source="t", country="PT", base="https://t.pt", path="/imoveis",
                    card_selector="div.property-card, div[class*='property']", page_param=None)

    import sources._cards as cards
    from conftest import FakeSession
    session = FakeSession(lambda m, u, kw: FakeResponse(html))
    orig = cards.make_session
    cards.make_session = lambda **k: session
    try:
        assert scrape_cards(db, site, max_price=100000) == 1   # 250k is over budget
    finally:
        cards.make_session = orig
    row = db.execute("SELECT * FROM listings").fetchone()
    assert row["id"] == "t:555" and row["price"] == 85000   # not "2024", not €2


def test_listing_id_from_url():
    assert listing_id_from_url("https://x.pt/imoveis/2024/lisboa/12345") == "12345"
    assert listing_id_from_url("https://x.pt/imoveis/12345/") == "12345"
    assert len(listing_id_from_url("https://x.pt/imovel-sem-numero")) == 16


def test_cyprus_ids_are_stable_across_runs(db, fake_http):
    html = "<table><tr><th>h</th></tr><tr><td><a href='/s/1'>Plot in Paphos</a></td><td>x</td><td>€ 20.000</td></tr></table>"
    fake_http(lambda m, u, kw: FakeResponse(html))
    REGISTRY["cyprus"].func(db, max_price=100000)
    REGISTRY["cyprus"].func(db, max_price=100000)
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 1


def test_fina_csv_parser():
    from sources.hr import parse_fina_csv
    header = ("ID nadmetanja;Datum i vrijeme završetka nadmetanja;Vrsta predmeta prodaje;"
              "Početna cijena za nadmetanje;Opis;Nadležno tijelo;Poslovni broj spisa;"
              "Minimalna zakonska cijena ispod koje se predmet prodaje ne može prodati;"
              "Napomena uz detalje predmeta prodaje")
    rows = [
        "77;2099-01-01 12:00:00;Nekretnina;15000,50;Stan u Splitu;Općinski sud Split;P-1;7500,00;",
        "78;2000-01-01 12:00:00;Nekretnina;1000;Old;Sud;P-2;;",
        ";2099-01-01 12:00:00;Nekretnina;1000;Bez ID;Sud;P-3;;",
        "79;2099-01-01 12:00:00;Pokretnina;1000;Auto;Sud;P-4;;",
    ]
    out = list(parse_fina_csv("\n".join([header] + rows), max_price=100000))
    assert [r["title"] for r in out] == ["Stan u Splitu", "Bez ID"]
    assert out[0]["price"] == 15000.5 and out[0]["min_price"] == 7500
    assert out[0]["date_end"] == "2099-01-01T12:00:00"
    assert len(out[1]["external_id"]) == 12   # md5 fallback, unchanged from before


def test_spain_detail_parser():
    from sources.es import _spain_parse_detail
    html = """<div id="contenido"><table>
      <tr><th>Valor subasta</th><td>36.163,00 €</td></tr>
      <tr><th>Puja mínima</th><td>Sin puja mínima</td></tr>
      <tr><th>Fecha de conclusión</th><td>30-09-2099 18:00:00 CET (ISO: 2099-09-30T18:00:00+02:00)</td></tr>
      <tr><th>Localidad</th><td>Vilamarxant</td></tr><tr><th>Provincia</th><td>Valencia</td></tr>
      <tr><th>Descripción</th><td>Vivienda situada en Vilamarxant, planta baja, 85 m²</td></tr>
    </table></div>"""
    d = _spain_parse_detail(html)
    assert d["price"] == 36163 and d["min_price"] is None
    assert d["date_end"] == "2099-09-30T18:00:00+02:00"
    assert d["concelho"] == "Vilamarxant" and d["district"] == "Valencia"
    assert d["area_m2"] == 85 and d["title"].startswith("Vivienda")


def test_netherlands_and_croatia_mappers():
    from sources.hr import _croatia_to_listing
    from sources.nl import netherlands_listing
    nl = netherlands_listing({"id": 5, "kavelNaam": "Kerkstraat 1", "woningtype": "Woonhuis",
                              "inzet": "€ 150.000", "url": "/kavel/5", "veilingwijze": "online"})
    assert nl["price"] == 150000 and nl["url"] == "https://www.openbareverkoop.nl/kavel/5"
    assert netherlands_listing({"kavelNaam": "no id"}) is None
    assert _croatia_to_listing({"uuid": "u1", "title": "Oglas o prodaji nekretnine"})["country"] == "HR"
    assert _croatia_to_listing({"uuid": "u2", "title": "Poziv vjerovnicima"}) is None


def test_errors_are_described_in_plain_words(db, fake_http):
    import requests
    from sources import describe_error

    fake_http(lambda m, u, kw: FakeResponse("gone", status=404))
    result = run_source(db, REGISTRY["bcp"], max_price=100000)
    assert result["message"].startswith("HTTP 404")

    err = requests.ConnectionError("boom")
    err.request = requests.Request("GET", "https://x.pt/a").prepare()
    assert describe_error(err) == "Could not connect to x.pt (site down, moved, or blocked)"
