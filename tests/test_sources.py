import json
import urllib.parse

import scraper
from common import COUNTRY_NAMES
from conftest import FakeResponse
from sources import REGISTRY, load_all, run_source, sources_for
from sources._cards import CardSite, listing_id_from_url, scrape_cards


def test_registry_is_complete():
    load_all()
    assert len(REGISTRY) == 111
    for s in REGISTRY.values():
        assert s.country in COUNTRY_NAMES or s.country == "EU", s
        assert s.description, f"{s.name} needs a docstring"
    optional = {s.name for s in REGISTRY.values() if not s.default}
    # closed / login-only / already covered by another source: not in default scans
    # (and, since Sept 2026, the ones behind a bot wall or a broken certificate)
    assert optional == {"idealista", "courtbid", "novobanco", "aeat",
                        "sareb", "gobidreal", "biddit", "anaf", "cyprus", "greece",
                        "veilingbiljet", "justiz_auktion", "haya", "subastasactivas",
                        "zvg_de", "encheres_publiques"}
    # every country has at least one default source, except those whose only
    # source is walled off; PT runs first
    # Green-Acres covers FR, PT, ES and IT from one source, filed under "EU"
    assert {s.country for s in sources_for(None)} == set(COUNTRY_NAMES) - {"CY"} | {"EU"}   # GR: ETAD
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
        assert result["status"] in ("empty", "error", "blocked"), result
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


# ---------------------------------------------------------------------------
# F13 — scan scope tracking
# ---------------------------------------------------------------------------

def test_run_source_dict_return_passes_scope_to_scrape_log(db):
    """Sources returning a dict set scope/pages_seen in scrape_log (F13)."""
    from sources import Source, run_source

    def _partial_source(db, max_price=100000, **_):
        return {"count": 3, "scope": "partial", "pages_seen": 2}

    src = Source("_test_partial", "PT", _partial_source)
    result = run_source(db, src, max_price=100000)
    assert result["status"] == "ok"
    assert result["count"] == 3
    row = db.execute("SELECT scope, pages_seen FROM scrape_log WHERE source='_test_partial'").fetchone()
    assert row["scope"] == "partial"
    assert row["pages_seen"] == 2


def test_run_source_int_return_records_full_scope(db):
    """Legacy int return is treated as a full run (F13 backward compat)."""
    from sources import Source, run_source

    def _legacy_source(db, max_price=100000, **_):
        return 5

    src = Source("_test_legacy", "PT", _legacy_source)
    run_source(db, src, max_price=100000)
    row = db.execute("SELECT scope FROM scrape_log WHERE source='_test_legacy'").fetchone()
    assert row["scope"] == "full"


def test_run_source_error_records_partial_scope(db, fake_http):
    """Errors (and blocked) record scope='partial' so freshness clock does not advance (F13)."""
    from sources import Source, run_source, SourceUnavailable

    def _blocked_source(db, max_price=100000, **_):
        raise SourceUnavailable("bot wall")

    src = Source("_test_blocked", "PT", _blocked_source)
    run_source(db, src, max_price=100000)
    row = db.execute("SELECT scope, status FROM scrape_log WHERE source='_test_blocked'").fetchone()
    assert row["status"] == "blocked"
    assert row["scope"] == "partial"


def test_partial_scope_does_not_advance_freshness_clock(db):
    """last_ok_by_source excludes partial-scope runs (F13)."""
    from db import last_ok_by_source, record_scrape

    record_scrape(db, "mysrc", count=10, status="ok", scope="full",
                  timestamp="2026-01-01T00:00:00+00:00")
    full_ts = last_ok_by_source(db).get("mysrc")
    assert full_ts is not None

    record_scrape(db, "mysrc", count=5, status="ok", scope="partial",
                  timestamp="2026-06-01T00:00:00+00:00")
    after_partial_ts = last_ok_by_source(db).get("mysrc")
    assert after_partial_ts == full_ts, (
        "partial scope run must not advance the freshness clock"
    )


ELEILOES_PAGE = {
    "list": [
        {"id": 101, "titulo": "Moradia em Arganil", "subtipoId": 21, "valorBase": 30000,
         "lanceAtual": 15000, "valorMinimo": 25500, "moradaDistrito": "Coimbra",
         "moradaConcelho": "Arganil", "referencia": "LO101", "capa": "img/1.jpg",
         "dataFim": "2099-10-01T10:00:00"},
        {"id": 102, "titulo": "Terreno", "subtipoId": 27, "valorBase": 5000,
         "lanceAtual": None, "referencia": "NP102", "dataFim": "2099-10-02T10:00:00"},
        {"id": 103, "titulo": "Ford Focus 1.8 TDCi", "tipoId": 2, "subtipoId": 9, "valorBase": 900,
         "referencia": "LO103", "dataFim": "2099-10-02T10:00:00"},     # not property: not kept
    ],
    "pagination": {"total": 2},
}


# GET /api/Eventos/<referencia>, trimmed from a live answer (names invented).
ELEILOES_DETAIL = {"item": {
    "id": 101, "referencia": "LO101", "descricao": "Casa T3 devoluta", "areaTotal": 140.0,
    "moradaFreguesia": "Folques", "valorAbertura": 15000.0, "processoNumero": "123/24.0T8CBR",
    "processoTribunal": "Juízo de Execução de Coimbra", "gestorTipo": "Agente de Execução",
    "gestorNome": "Agente Exemplo", "gestorEmail": "agente@exemplo.pt",
    "executados": "Pessoa Executada",
    # The address fields named Arganil; the land registry says where the house is.
    "descPredial": [{"distritoDesc": "18 - Viseu", "concelhoDesc": "14 - Resende",
                     "freguesiaDesc": "05 - Felgueiras"}]}}


def test_eleiloes(db, fake_http):
    def handler(method, url, kw):
        if "/api/Eventos/?" in url:
            return FakeResponse(json_data=ELEILOES_PAGE)
        if url.endswith("/LO101"):                     # the reference, not the numeric id
            return FakeResponse(json_data=ELEILOES_DETAIL)
        return FakeResponse(json_data={"errorsList": [{"title": "Evento não disponível"}]})
    session = fake_http(handler)
    assert REGISTRY["eleiloes"].func(db, max_price=100000) == 2
    row = db.execute("SELECT * FROM listings WHERE id='eleiloes:101'").fetchone()
    assert row["url"] == "https://e-leiloes.pt/evento/LO101"
    assert row["current_bid"] == 15000 and row["tipo"] == "moradia"
    assert row["description"] == "Casa T3 devoluta" and row["area_m2"] == 140
    assert row["freguesia"] == "Felgueiras" and row["concelho"] == "Resende"
    raw = json.loads(row["raw_json"])
    assert raw["agente_email"] == "agente@exemplo.pt" and raw["processo"] == "123/24.0T8CBR"
    assert "Pessoa Executada" not in row["raw_json"]           # debtors are not stored
    assert db.execute("SELECT current_bid FROM listings WHERE id='eleiloes:102'").fetchone()[0] is None
    assert db.execute("SELECT COUNT(*) FROM listings WHERE id='eleiloes:103'").fetchone()[0] == 0

    # The next scan's thin list item keeps what the detail pass found; no second detail call.
    detail_calls = sum(1 for _, u, _ in session.calls if u.endswith("/LO101"))
    REGISTRY["eleiloes"].func(db, max_price=100000)
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='eleiloes:101'").fetchone()[0])
    assert raw["agente_email"] == "agente@exemplo.pt"
    assert sum(1 for _, u, _ in session.calls if u.endswith("/LO101")) == detail_calls
    place = db.execute("SELECT district, concelho, freguesia FROM listings WHERE id='eleiloes:101'").fetchone()
    assert tuple(place) == ("Viseu", "Resende", "Felgueiras")     # not Arganil, even after the rescan


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


def test_cards_can_keep_over_budget_listings(db):
    """BPI-style: a small bank portal where every flat is dearer than the budget."""
    html = """
    <a class="announce-details" href="/apartamento/a99">
      <span class="announce-title">T2 Lisboa</span>
      <span class="announce-price">760.000 €</span>
    </a>
    """
    site = CardSite(source="bpi", country="PT", base="https://bpi.example", path="/",
                    card_selector="a.announce-details", title_selector=".announce-title",
                    price_selector=".announce-price", page_param=None, id_pattern=r"/a(\d+)$",
                    keep_over_budget=True)
    import sources._cards as cards
    from conftest import FakeSession
    session = FakeSession(lambda m, u, kw: FakeResponse(html))
    orig = cards.make_session
    cards.make_session = lambda **k: session
    try:
        assert scrape_cards(db, site, max_price=100000) == 1
    finally:
        cards.make_session = orig
    row = db.execute("SELECT id, price FROM listings").fetchone()
    assert row["id"] == "bpi:99" and row["price"] == 760000


def test_zvg_parses_aktenzeichen_blocks():
    from sources.de import parse_zvg_results
    html = """
    <table>
      <tr><td>Aktenzeichen</td><td><a href="index.php?button=showZvg&amp;zvg_id=49073&amp;land_abk=by">0009 K 0099/2024 (Detailansicht)</a></td></tr>
      <tr><td>Amtsgericht</td><td>in Bayern</td></tr>
      <tr><td>Objekt/Lage</td><td>Eigentumswohnung : Ortsstrasse 16, 90602 Pyrbaum</td></tr>
      <tr><td>Verkehrswert in €</td><td>45.000,00 EUR</td></tr>
      <tr><td>Termin</td><td>15.11.2026</td></tr>
      <tr><td>Aktenzeichen</td><td><a href="index.php?button=showZvg&amp;zvg_id=49052&amp;land_abk=by">0004 K 0117/2024 (Detailansicht)</a></td></tr>
      <tr><td>Amtsgericht</td><td>in Bayern</td></tr>
      <tr><td>Objekt/Lage</td><td>Einfamilienhaus : Musterweg 1</td></tr>
      <tr><td>Verkehrswert in €</td><td>210.000,00 EUR</td></tr>
    </table>
    """
    rows = parse_zvg_results(html, "by")
    assert len(rows) == 2
    assert rows[0]["eid"] == "49073" and rows[0]["price"] == 45000
    assert "Pyrbaum" in rows[0]["title"]
    assert rows[0]["date_end"] and rows[0]["date_end"].startswith("2026-11-15")
    assert rows[1]["eid"] == "49052" and rows[1]["price"] == 210000


def test_zvg_value_skips_sheet_numbers_and_adds_lots():
    from sources.de import zvg_value
    assert zvg_value("Grundbuch von Stickenbüttel Blatt 649  lfd.Nr. 1: 258.000,00 €") == 258000
    assert zvg_value("zu 1: 235.000 EUR zu 2:  4.800 EUR zu 3: 4.100 EUR") == 243900
    assert zvg_value("Lfd. Nr. 1 Verkehrswert: 135.100,00 € davon entfällt auf Zubehör: 100,00 € "
                     "Lfd. Nr. 2 Verkehrswert: 1.400,00 € Bietsicherheit ... IBAN: DE51 6005") == 136500
    assert zvg_value("19.400,00 Bietsicherheit kann ... IBAN: DE51 6005 0101") == 19400
    assert zvg_value("6.000,00 €") == 6000


def test_poland_parses_licytacje_links():
    from sources.pl import parse_poland_list
    html = """
    <a href="/licytacje/82717/nieruchomosc-gruntowa-zabudowana">stacjonarna Opublikowano:19.08.2026</a>
    <a href="/licytacje/82717/nieruchomosc-gruntowa-zabudowana">dup</a>
    <a href="/licytacje/83126/dzialka-zabudowana-budynkiem-mieszkalnym">dom</a>
    <a href="/popularne-pytania">ignore</a>
    """
    rows = parse_poland_list(html)
    assert [r["eid"] for r in rows] == ["82717", "83126"]
    assert "Nieruchomosc gruntowa" in rows[0]["title"] or "nieruchomosc" in rows[0]["title"].lower()
    assert rows[0]["url"].endswith("/licytacje/82717/nieruchomosc-gruntowa-zabudowana")


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
    from sources.hr import fina_place, parse_fina_csv
    header = ("ID nadmetanja;Datum i vrijeme završetka nadmetanja;Vrsta predmeta prodaje;"
              "Početna cijena za nadmetanje;Opis;Nadležno tijelo;Poslovni broj spisa;"
              "Minimalna zakonska cijena ispod koje se predmet prodaje ne može prodati;"
              "Napomena uz detalje predmeta prodaje")
    rows = [
        "77;2099-01-01 12:00:00;Nekretnina;15000,50;Stan u Splitu;Općinski sud Split;P-1;7500,00;",
        "78;2000-01-01 12:00:00;Nekretnina;1000;Old;Sud;P-2;;",
        ";2099-01-01 12:00:00;Nekretnina;1000;Bez ID;Sud;P-3;;",
        "79;2099-01-01 12:00:00;Pokretnina;1000;Auto;Sud;P-4;;",
        "80;2099-01-01 12:00:00;Nekretnina;9000;Nekretnina k.o. Oljasi, kč.br. 600;"
        "Općinski sud u Zlataru;P-5;4500,00;",
    ]
    out = list(parse_fina_csv("\n".join([header] + rows), max_price=100000))
    assert [r["title"] for r in out] == ["Stan u Splitu", "Bez ID",
                                         "Nekretnina k.o. Oljasi, kč.br. 600"]
    assert out[0]["price"] == 15000.5 and out[0]["min_price"] == 7500
    assert out[0]["date_end"] == "2099-01-01T12:00:00"
    assert out[0]["district"] is None          # court is not a place
    assert json.loads(out[0]["raw_json"])["sud"] == "Općinski sud Split"
    assert len(out[1]["external_id"]) == 12   # md5 fallback, unchanged from before
    assert out[2]["concelho"] == "Oljasi"
    assert fina_place("k.o. 332437, Budanica kčbr. 1151") == "Budanica"
    assert fina_place("Zemljišnoknjižni odjel Virovitica, u zk") == "Virovitica"
    assert fina_place("k.o. 300605") is None
    assert fina_place("zk.ul. broj 2130 k.o. Hlevnica (ranije k.o. Hlevnica), čkbr. 1244/2") == \
        "Hlevnica"


def test_licitor_list_title_splits_department_and_town():
    from sources.fr import parse_licitor_list_title
    title, town = parse_licitor_list_title("22PlémetUne maison d'habitationde 162,62 m²")
    assert town == "Plémet" and title.startswith("Une maison")
    assert "habitation de" in title
    title, town = parse_licitor_list_title("59 Solesmes Une maison d'habitation de 80 m²")
    assert town == "Solesmes" and "maison" in title.lower()
    title, town = parse_licitor_list_title("72Le MansUne maison à usage d'habitation")
    assert town == "Le Mans"
    title, town = parse_licitor_list_title("59Dunkerque (Petite-Synthe)Une maison individuelle")
    assert town == "Dunkerque (Petite-Synthe)"
    assert parse_licitor_list_title("Une maison sans code") == ("Une maison sans code", None)
    from sources.fr import licitor_department
    assert licitor_department("22PlémetUne maison") == "22"
    assert licitor_department("971 Pointe-à-Pitre Une maison") == "971"
    assert licitor_department("Une maison sans code") is None


def test_spain_clears_a_stored_bidding_step(db):
    from common import make_listing, price_to_pay
    from db import upsert_listing
    from sources.es import clear_step_min_price

    upsert_listing(db, make_listing(
        "spain", "SUB-JA-1", "ES", title="Piso", price=174300, min_price=1743))
    assert price_to_pay(dict(db.execute("SELECT * FROM listings WHERE id='spain:SUB-JA-1'").fetchone())) == 174300
    clear_step_min_price(db, {"id": "spain:SUB-JA-1"})
    row = db.execute("SELECT price, min_price FROM listings WHERE id='spain:SUB-JA-1'").fetchone()
    assert row["price"] == 174300 and row["min_price"] is None


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
    # A "puja mínima" that is the bidding step, not a price that buys it.
    step = html.replace("Sin puja mínima", "1.743,00 €").replace("36.163,00", "174.300,00")
    assert _spain_parse_detail(step)["min_price"] is None
    real = html.replace("Sin puja mínima", "25.000,00 €")
    assert _spain_parse_detail(real)["min_price"] == 25000


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


BOE_GENERAL = """<table><tr><th>Tipo de subasta</th><td>JUDICIAL EN VIA DE APREMIO</td></tr>
  <tr><th>Cuenta expediente</th><td>1234 0000 05 0456 24</td></tr>
  <tr><th>Valor subasta</th><td>90.000,00 €</td></tr>
  <tr><th>Importe del depósito</th><td>4.500,00 €</td></tr>
  <tr><th>Fecha de conclusión</th><td>30-09-2099 18:00:00 CET (ISO: 2099-09-30T18:00:00+02:00)</td></tr></table>"""
BOE_AUTHORITY = """<table><tr><th>Código</th><td>4109142003</td></tr>
  <tr><th>Descripción</th><td>JUZGADO DE PRIMERA INSTANCIA Nº 3 DE SEVILLA</td></tr>
  <tr><th>Dirección</th><td>AVDA. MENÉNDEZ PELAYO 2 ; 41071 SEVILLA</td></tr>
  <tr><th>Teléfono</th><td>955 000 000</td></tr>
  <tr><th>Correo electrónico</th><td>instancia3.sevilla@justicia.es</td></tr></table>"""
BOE_GOODS = """<table><tr><th>Descripción</th><td>Vivienda en calle Feria 10, Sevilla, 85 m²</td></tr>
  <tr><th>Localidad</th><td>Sevilla</td></tr><tr><th>Provincia</th><td>Sevilla</td></tr>
  <tr><th>Situación posesoria</th><td>Ocupado por el deudor</td></tr>
  <tr><th>Visitable</th><td>No</td></tr></table>"""


def test_spain_detail_tabs_give_the_court_and_occupancy():
    from sources.es import _spain_occupation, spain_details
    fields, extra = spain_details({"general": BOE_GENERAL, "authority": BOE_AUTHORITY, "goods": BOE_GOODS})
    assert fields["price"] == 90000 and fields["title"].startswith("Vivienda en calle Feria")
    assert fields["concelho"] == "Sevilla" and fields["area_m2"] == 85       # not the court's name
    assert extra["autoridad"] == "JUZGADO DE PRIMERA INSTANCIA Nº 3 DE SEVILLA"
    assert extra["autoridad_email"] == "instancia3.sevilla@justicia.es"
    assert extra["deposito"] == 4500 and extra["expediente"].startswith("1234")
    assert extra["occupation"] == "occupied" and extra["visitable"] == "No"
    assert _spain_occupation("No consta") is None and _spain_occupation("Libre de ocupantes") == "vacant"
    assert _spain_occupation("Ocupantes sin derecho de permanencia") == "occupied"
    assert _spain_occupation("Ocupante desconocido") == "occupied"
    assert _spain_occupation("Sin ocupantes") == "vacant"
    assert _spain_occupation("No consta (Según el decreto, ocupado)") is None


def test_spain_enrichment_fills_letters_and_scoring(db, add, fake_http):
    from db import load_listings
    from sources.es import enrich_spain_details
    add("spain", "SUB-JA-2099-1", "ES", title="Subasta SUB-JA-2099-1", tipo="inmueble")
    pages = {"1": BOE_GENERAL, "2": BOE_AUTHORITY, "3": BOE_GOODS}
    session = fake_http(lambda m, url, kw: FakeResponse(pages[str(kw["params"]["ver"])]))
    assert enrich_spain_details(db, session) == 1
    item = load_listings(db, include_hidden=True)[0]
    raw = json.loads(item["raw_json"])
    assert raw["autoridad_email"] == "instancia3.sevilla@justicia.es" and raw["detail_checked"]
    assert raw.get("occupation") == "occupied" and raw.get("visitable") == "No"
    assert item["reasons"] and item["reasons"][0].startswith("cannot be visited")
    assert enrich_spain_details(db, session) == 0 and len(session.calls) == 3   # checked once


def test_a_spain_sale_over_the_cap_is_not_opened(db, add, fake_http):
    from db import set_listing_status, unchecked_over_cap
    from sources.es import enrich_spain_details
    add("spain", "DEAR", "ES", title="Casa cara", price=150000, date_end="2099-01-01")
    add("spain", "CHEAP", "ES", title="Casa", price=40000, date_end="2099-01-01")
    add("spain", "UNKNOWN", "ES", title="Casa sem preco", date_end="2099-01-01")
    add("spain", "MINE", "ES", title="A minha", price=180000, date_end="2099-01-01")
    set_listing_status(db, "spain:MINE", "shortlisted")
    session = fake_http(lambda m, url, kw: FakeResponse("<html></html>"))
    enrich_spain_details(db, session)
    opened = {c[2]["params"]["idSub"] for c in session.calls}
    assert "DEAR" not in opened and {"CHEAP", "UNKNOWN", "MINE"} <= opened
    assert unchecked_over_cap(db) == (1, 100000)


BOE_LOTS_GENERAL = """<html><body><table>
<tr><th>Lotes</th><td>3</td></tr>
<tr><th>Valor subasta</th><td>Ver valor de subasta en cada lote (los lotes se subastan de forma independiente)</td></tr>
<tr><th>Puja mínima</th><td>Ver puja mínima de cada lote (adjudicación independiente)</td></tr>
<tr><th>Fecha de conclusión</th><td>14-10-2026 18:00:00 CET</td></tr>
</table></body></html>"""
BOE_LOT_1 = """<html><body><h4>Lote 1 FINCA REGISTRAL 19826</h4><table>
<tr><th>Valor Subasta</th><td>19.687,33 €</td></tr>
<tr><th>Valor de tasación</th><td>0,00 €</td></tr>
<tr><th>Puja mínima</th><td>Sin puja mínima</td></tr>
<tr><th>Descripción</th><td>FINCA 10 DEL PLANO UNO DEL PLANO GENERAL TERRENO DEDICADO A CULTIVO DE SECANO</td></tr>
<tr><th>Localidad</th><td>SANTA MARIA DE CAYON</td></tr>
<tr><th>Provincia</th><td>Cantabria</td></tr>
</table></body></html>"""


def test_spain_auction_in_lots_takes_the_first_lots_value():
    from sources.es import spain_details
    fields, _ = spain_details({"general": BOE_LOTS_GENERAL, "goods": BOE_LOT_1})
    assert fields["price"] == 19687.33 and fields.get("min_price") is None
    assert fields["concelho"] == "SANTA MARIA DE CAYON" and fields["district"] == "Cantabria"


def test_spain_details_read_never_checked_sales_first(db, add, fake_http):
    from sources.es import enrich_spain_details
    add("spain", "SUB-OLD", "ES", title="Subasta SUB-OLD", tipo="inmueble", date_end="2099-01-01",
        raw_json=json.dumps({"detail_checked": "2026-09-01"}))
    add("spain", "SUB-NEW", "ES", title="Subasta SUB-NEW", tipo="inmueble", date_end="2099-06-01")
    session = fake_http(lambda m, url, kw: FakeResponse("<html></html>"))
    enrich_spain_details(db, session, limit=1)
    assert {c[2]["params"]["idSub"] for c in session.calls} == {"SUB-NEW"}


LICITOR_ANNONCE = """<html><body>
<p class="Court">Tribunal Judiciaire de Nîmes (Gard)</p>
<p>Publiée le 3 septembre 2026</p>
<p>Vente aux enchères publiques · audience du jeudi 15 octobre 2026 à 14h30</p>
<h1>Une maison d'habitation</h1><p>Libre de toute occupation.</p>
<p>Mise à prix : 40&nbsp;000 €</p><p>Visite sur place le mardi 6 octobre 2026 de 10h à 11h.</p>
<div>Maître Jean-Pierre Dupont, Avocat au Barreau de Nîmes - Tél.: 04 66 12 34 56
<a href="mailto:jp.dupont@avocats-nimes.fr">Email</a></div>
<footer>contact@licitor.com</footer></body></html>"""


def test_licitor_annonce_parser():
    from sources.fr import parse_licitor_annonce
    d = parse_licitor_annonce(LICITOR_ANNONCE)
    assert d["tribunal"] == "Tribunal Judiciaire de Nîmes" and d["tribunal_ville"] == "Nîmes"
    assert d["audience"] == "2026-10-15T14:30:00"          # the hearing, not the publication date
    assert d["mise_a_prix"] == 40000 and d["occupation"] == "vacant"
    assert d["avocat_nom"] == "Jean-Pierre Dupont" and d["avocat_email"] == "jp.dupont@avocats-nimes.fr"
    assert d["avocat_tel"] == "04 66 12 34 56" and d["visite"].startswith("sur place le mardi 6 octobre")
    occupied = parse_licitor_annonce("<p>Un appartement occupé par le locataire. Mise à prix: 120.000 €</p>")
    assert occupied["occupation"] == "occupied" and occupied["mise_a_prix"] == 120000
    # The list line is truncated. The annonce has each part, the street and a
    # map pin. A sponsored price widget is not a field.
    page = """<article class="LegalAd"><div class="AdContent">
      <div class="SousLot"><h2>Un immeuble</h2>
        <p>sur deux étages, comprenant : trois appartements T3 partiellement en travaux Cour commune</p></div>
      <div class="SousLot"><h2>MAISON INDIVIDUELLE</h2><p>sur deux étages, de type F2</p></div>
      <p class="City">Cherbourg-en-Cotentin</p>
      <p class="Street">5, passage Lejuez (accès par le 3, impasse Lejuez)</p>
      <p class="Map"><a href="https://maps.google.fr/maps?q=49.6431999,-1.6362118&amp;z=13">plan</a></p>
      <div class="PartnerOfferValue">2 466 €/m²</div>
    </div></article>"""
    full = parse_licitor_annonce(page)
    assert "trois appartements T3" in full["description"] and "de type F2" in full["description"]
    assert full["street"].startswith("5, passage Lejuez") and full["city"] == "Cherbourg-en-Cotentin"
    assert full["pin"] == (49.6431999, -1.6362118)
    assert "2466" not in json.dumps(full)
    from common import counted_flats
    assert counted_flats(full["description"])[0] == {"T3": 3, "T2": 1}


def test_france_enrichment_sets_the_hearing_date(db, add, fake_http):
    from datetime import datetime, timezone

    from db import load_listings
    from sources.fr import enrich_france_details
    add("france", "101", "FR", title="Une maison", url="https://www.licitor.com/annonce/101.html")
    session = fake_http(lambda m, url, kw: FakeResponse(LICITOR_ANNONCE))
    assert enrich_france_details(db, session) == 1
    item = load_listings(db, include_hidden=True, now=datetime(2026, 9, 1, tzinfo=timezone.utc))[0]
    assert item["date_end"] == "2026-10-15T14:30:00" and item["price"] == 40000
    assert item["district"] == "Nîmes" and "vacant (devoluto)" in item["reasons"]


def test_a_licitor_pin_is_kept_and_is_not_the_building(db, add, fake_http):
    from sources.fr import enrich_france_details
    html = """<p class="Court">Tribunal Judiciaire de Cherbourg-en-Cotentin (Manche)</p>
    <p>audience du mercredi 14 octobre 2026 à 15h30</p>
    <p>Mise à prix : 30 000 €</p>
    <article class="LegalAd"><div class="AdContent">
      <div class="SousLot"><h2>Un immeuble</h2><p>trois appartements T3</p></div>
      <p class="City">Cherbourg-en-Cotentin</p>
      <p class="Street">5, passage Lejuez</p>
      <p class="Map"><a href="https://maps.google.fr/maps?q=49.6431999,-1.6362118">plan</a></p>
    </div></article>"""
    add("france", "109989", "FR", title="Un immeuble", concelho="Cherbourg-en-Cotentin",
        url="https://www.licitor.com/annonce/109989.html",
        raw_json=json.dumps({"geo": {"lat": 49.64, "lon": -1.62, "precision": "municipality",
                                    "query": "Cherbourg-en-Cotentin"}, "climate": {"v": 1}}))
    session = fake_http(lambda m, url, kw: FakeResponse(html))
    assert enrich_france_details(db, session) == 1
    description, raw_json = db.execute(
        "SELECT description, raw_json FROM listings WHERE id='france:109989'").fetchone()
    raw = json.loads(raw_json)
    assert "passage Lejuez" in description and raw["morada"].startswith("5, passage")
    assert raw["geo"]["precision"] == "advertised" and raw["geo"]["lat"] == 49.6431999
    assert raw.get("lat") is None and raw.get("lon") is None
    assert raw.get("climate") is None and raw.get("water_check") is None
    import geo
    assert raw["geo"]["precision"] in geo.EXACT_ENOUGH


def _citius_block(html_id, proc, price, desc):
    return f"""<span><div class="resultadopubvenda"><strong>Tipo de Bem:</strong> Imóvel <br/>
      <strong>Estado:</strong> Em venda <br/><strong>Valor Base: </strong> {price} € <br/>
      <strong>Modalidade: </strong> Venda mediante proposta em carta fechada <br/>
      <strong>Descrição do Bem: </strong> {desc} <br/><strong>Processo: </strong> {proc} <br/>
      <strong>Espécie: </strong> Execução Ordinária <br/>
      <a onclick="javascript:Viewer.Abrir(this, {html_id}, 'ConsultasVenda.aspx/GetHtmlDetails', 'btnFechar','viewer')">ver mais</a>
      </div></span>"""


def _citius_page(blocks, last):
    pager = ('<input type="image" id="ctl00_ContentPlaceHolder1_Pager1_btnNextPage" '
             'name="ctl00$ContentPlaceHolder1$Pager1$btnNextPage"' + (' disabled="disabled"' if last else '') + '/>')
    return (f'<input type="hidden" name="__VIEWSTATE" value="p"/>{pager}'
            f'<span id="ctl00_ContentPlaceHolder1_dlVenda">{"".join(blocks)}</span>')


CITIUS_DETAIL = {"d": "<div>Detalhes do bem Tribunal: Viseu Processo: 468/17.6T8MBR Descrição do Bem: "
                      "Prédio urbano sito na Rua do Eiró, freguesia de Longa, concelho de Tabuaço, composto por "
                      "casa de habitação de quatro andares, com uma superficie coberta de 85 m2 Art.Matricial: 2º "
                      "Intervenientes Interveniente: Executado Nome: Pessoa Executada Morada: Rua Privada 1</div>"}


def test_citius_reads_every_page_and_the_full_details(db, fake_http):
    page1 = _citius_page([_citius_block(111850, "468/17.6T8MBR, Juízo de Execução de Viseu", "9 000,00",
                                        "Prédio urbano sito na Rua do Eiró, freguesia de Longa, concelho de Tabuaço, "
                                        "composto por casa de habitação de quatro andares, com uma ..."),
                          _citius_block(222, "10/20.0T8VIS, Juízo de Viseu", "95 000,00", "Moradia cara")], last=False)
    page2 = _citius_page([_citius_block(333, "10/20.0T8VIS, Juízo de Viseu", "500,00",
                                        "Prédio rústico de cultivo com 2760 m2")], last=True)

    def handler(method, url, kw):
        if method == "GET":
            return FakeResponse(CITIUS_FORM)
        if url.endswith("GetHtmlDetails"):
            assert kw["data"] == "{htmlId:111850}" or kw["data"] in ("{htmlId:333}",)
            return FakeResponse(json_data=CITIUS_DETAIL if "111850" in kw["data"] else {"d": ""})
        next_page = any(k.endswith("btnNextPage.x") for k in kw["data"])
        return FakeResponse(page2 if next_page else page1)

    fake_http(handler)
    assert REGISTRY["citius"].func(db, max_price=30000) == 2        # the €95,000 one is over budget
    rows = {r["id"]: r for r in db.execute("SELECT * FROM listings")}
    # page 2 was read, and the €500 plot keeps its place in the case although lot 1 was skipped
    assert set(rows) == {"citius:468176T8MBRJuzodeExecuodeViseu", "citius:10200T8VISJuzodeViseu-2"}
    house = rows["citius:468176T8MBRJuzodeExecuodeViseu"]
    assert "superficie coberta de 85 m2" in house["description"] and house["area_m2"] == 85
    assert "Pessoa Executada" not in house["description"] + house["raw_json"]   # owners are not stored
    assert house["concelho"] == "Tabuaço"

    # Later scans keep the full text and what else was learned (the map position).
    # The third scan used to lose it: the second dropped it from what was kept.
    raw = json.loads(house["raw_json"])
    raw["geo"] = {"lat": 41.1, "lon": -7.5, "precision": "parish"}
    db.execute("UPDATE listings SET raw_json=? WHERE id='citius:468176T8MBRJuzodeExecuodeViseu'", (json.dumps(raw),))
    db.commit()
    for _ in range(3):
        REGISTRY["citius"].func(db, max_price=30000)
    house = db.execute("SELECT description, raw_json FROM listings "
                       "WHERE id='citius:468176T8MBRJuzodeExecuodeViseu'").fetchone()
    assert "superficie coberta de 85 m2" in house[0]
    kept = json.loads(house[1])
    assert kept["geo"]["lat"] == 41.1 and "quatro andares" in kept["descricao_completa"]


def test_citius_place_keeps_abbreviations_and_drops_streets(db):
    from common import make_listing
    from db import upsert_listing
    from sources.pt import _citius_extract_location, _clear_implausible_place

    assert _citius_extract_location(
        "Prédio urbano, freguesia de S. João, concelho de S. João da Pesqueira, distrito de Viseu"
    ) == ("Viseu", "S. João da Pesqueira", "S. João")
    assert _citius_extract_location(
        "Prédio urbano sito na R. das Flores, freguesia de Longa, concelho de Tabuaço"
    )[1] == "Tabuaço"
    # "R." / "L." used to become the municipality, and a street address with them.
    assert _citius_extract_location("Prédio sito na R. das Flores, lugar de Bonvisinho")[1] is None
    assert _citius_extract_location("Prédio sito em L. de Cima, com 200 m2")[1] is None
    assert _citius_extract_location("Prédio sito na Rua do Canto das Naves, lugar de Bonvisinho")[1] is None
    assert _citius_extract_location("Prédio sito em Póvoa de Santarém, com casa de habitação")[1] == "Póvoa de Santarém"
    assert _citius_extract_location(
        "Fracção sito na Travessa Quinta dos Cubos, freguesia de Oliveira do Douro"
    ) == (None, None, "Oliveira do Douro")
    assert _citius_extract_location("Prédio sito no lugar de Lage, com área de 2760 m2")[1] is None
    assert _citius_extract_location(
        "Prédio rústico denominado Vale Piteira sito na freguesia de Saboia - Odemira "
        "constituido por terreno de cultura arvense com a área de 0,5 ha"
    ) == (None, "Odemira", "Saboia")

    upsert_listing(db, make_listing("citius", "bad", title="Prédio sito na R. das Flores", concelho="R"))
    upsert_listing(db, make_listing("citius", "street", title="Prédio", concelho="Rua do Canto das Naves"))
    upsert_listing(db, make_listing("citius", "lugar", title="Prédio", concelho="lugar de Lage"))
    upsert_listing(db, make_listing("citius", "good", title="Prédio", concelho="Moura"))
    for eid in ("bad", "street", "lugar", "good"):
        _clear_implausible_place(db, {"id": f"citius:{eid}", "concelho": None})
    places = dict(db.execute("SELECT id, concelho FROM listings WHERE source='citius'"))
    assert places["citius:bad"] is None
    assert places["citius:street"] is None
    assert places["citius:lugar"] is None
    assert places["citius:good"] == "Moura"


def test_a_cut_copy_of_a_text_does_not_replace_the_full_one(db):
    """Search pages shorten texts ("… Vila Franca do Cam.... Modalidade: …");
    the full text a detail page gave stays."""
    from common import make_listing
    from db import upsert_listing
    full = ("Prédio urbano sito á Canada da Galega, nº 22, concelho de Vila Franca do Campo, constituído por "
            "casa baixa telhada destinada a habitação com quintal. Modalidade: Venda em leilão eletrónico")
    cut = "Prédio urbano sito á Canada da Galega, nº 22, concelho de Vila Franca do Cam.... Modalidade: Venda"
    upsert_listing(db, make_listing("citius", "x", title=full[:120], description=full, price=23880))
    upsert_listing(db, make_listing("citius", "x", title=cut[:120], description=cut, price=23880))
    row = db.execute("SELECT title, description FROM listings WHERE id='citius:x'").fetchone()
    assert row[1] == full and "casa baixa" in row[0]
    changed = "Moradia T2 em bom estado, 90 m2, com jardim e garagem"
    upsert_listing(db, make_listing("citius", "x", description=changed, price=23880))
    assert db.execute("SELECT description FROM listings WHERE id='citius:x'").fetchone()[0] == changed


def test_eleiloes_steps_by_what_the_api_returns(db, fake_http):
    """The API sends 12 rows a page even when asked for 100."""
    pages = {}

    def handler(method, url, kw):
        if "/api/Eventos/?" in url:
            params = json.loads(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["tableParams"][0])
            first = params["first"]
            pages[first] = True
            rows = [{"id": 500 + i, "tipoId": 1, "titulo": f"Moradia {i}", "valorBase": 10000,
                     "referencia": f"LO{500 + i}"} for i in range(first, min(first + 12, 30))]
            return FakeResponse(json_data={"list": rows, "pagination": {"first": first, "rows": 12, "total": 30}})
        return FakeResponse(json_data={"errorsList": [{"title": "Evento não disponível"}]})
    fake_http(handler)
    assert REGISTRY["eleiloes"].func(db, max_price=100000) == 30
    assert sorted(pages) == [0, 12, 24]


def test_eleiloes_area_field_off_by_100():
    from sources.pt import eleiloes_detail_fields
    fields, _ = eleiloes_detail_fields({"areaTotal": 1695000.0, "descricao":
                                        "terreno rústico com cerca de 16.950m2, sito na Tapada da Parreira"})
    assert fields["area_m2"] == 16950
    fields, _ = eleiloes_detail_fields({"areaTotal": 140.0, "descricao": "Moradia com 120 m2 de área útil"})
    assert fields["area_m2"] == 140                                  # close enough: the field stays


def test_eleiloes_observations_join_the_description():
    """The agente's notes ("muito mau estado de conservação") are what the
    heavy-work and occupancy checks need to see."""
    from sources.pt import eleiloes_detail_fields
    fields, _ = eleiloes_detail_fields({"descricao": "Casa de habitação com oito divisões.",
                                        "observacoes": "O imóvel encontra-se em muito mau estado de conservação."})
    assert fields["description"] == ("Casa de habitação com oito divisões. Observações: "
                                     "O imóvel encontra-se em muito mau estado de conservação.")
    fields, _ = eleiloes_detail_fields({"descricao": "", "observacoes": "Devoluto."})
    assert fields["description"] == "Observações: Devoluto."
    fields, _ = eleiloes_detail_fields({"descricao": "Moradia"})
    assert fields["description"] == "Moradia"


ALISEDA_ITEM = {
    "id": "ant00038780217", "ConstructedArea": 54, "SuperficieTotal": 54, "SupParcela": 0, "posesion": "LIBRE",
    "RefCatastral": "B00301100TN69G0001XA", "provinciaUrl": "asturias", "Imagen": "https://img/a.jpg",
    "Description": "Vivienda ubicada en Ribera de Arriba, Asturias. 54 m² construidos, 2 habitaciones.",
    "address": {"Ciudad": "RIBERA DE ARRIBA", "TipoVia": "lugar", "StreetName": "LA MORTERA", "StreetNumber": "11",
                "Latitude": 43.298261614, "Longitude": -5.933324171},
    "operacion": {"Precio": 28300, "PrecioAnterior": 30995}, "imagenes": [{"Uri": "https://img/1.jpg"}],
}


def test_aliseda_gives_price_position_and_possession(db, fake_http):
    from db import load_listings
    import geo
    from sources.es import parse_aliseda, scrape_aliseda
    row = parse_aliseda(ALISEDA_ITEM, "vivienda")
    assert row["id"] == "aliseda:ant00038780217" and row["price"] == 28300 and row["area_m2"] == 54
    assert row["concelho"] == "Ribera De Arriba" and row["district"] == "Asturias"
    raw = json.loads(row["raw_json"])
    assert raw["occupation"] == "vacant" and raw["geo"]["lat"] == 43.298261614
    taken = parse_aliseda({**ALISEDA_ITEM, "posesion": "OCUPADO"}, "vivienda")
    assert json.loads(taken["raw_json"])["occupation"] == "occupied"
    from scoring import _skip_reason
    share = parse_aliseda({**ALISEDA_ITEM, "Proindiviso": 1}, "vivienda")     # Foz, 2026-10-06
    assert _skip_reason({}, share["title"], share["description"]).endswith("skip")
    land = {**ALISEDA_ITEM, "SuperficieTotal": 1_350_000, "SupParcela": 0,
            "Description": "Parcelas de 0,8 a 2,8 ha en Calella."}
    row = parse_aliseda(land, "terreno")
    assert row["area_m2"] == 28_000
    assert json.loads(row["raw_json"])["area_feed_m2"] == 1_350_000
    from scoring import property_kind
    rustic = {**ALISEDA_ITEM, "id": "tab0000063350", "SupParcela": 3962.2, "SuperficieTotal": 3962.2,
              "Description": "Conjunto de 7 parcelas en Centro Pumariño. Suman 3.962 m2.",
              "suelonave": {"SubUso": "RÚstico"}}
    row = parse_aliseda(rustic, "terreno")
    assert row["description"].startswith("Terreno rústico.")
    assert property_kind(row) == "rural_plot"
    residential = {**rustic, "suelonave": {"SubUso": "Residencial"}}
    assert property_kind(parse_aliseda(residential, "terreno")) == "urban_plot"
    session = fake_http(lambda m, url, kw: FakeResponse(json_data={"data": [ALISEDA_ITEM], "last_page": 1}))
    assert scrape_aliseda(db, max_price=50000) == 2        # the same fake item as a home and as land
    assert {c[2]["params"]["precio"] for c in session.calls} == {"0-50000", "0-100000"}      # homes; land up to the land limit
    item = next(i for i in load_listings(db, include_hidden=True))
    assert geo.position(item)["precision"] == "street"


ALTAMIRA_CARD = {"referencia": "01402631", "cinmueble": 157785, "precio": 30000, "preciovisible": -1,
                 "latitud": 43.52, "longitud": -5.66, "provinciaurl": "Asturias", "poblacionurl": "Gijon",
                 "poblacion": "Gijon", "calle": "POLLA, 12", "cp": "33900", "numhab": 3, "superficie": 85,
                 "tipologia": "Chalet", "riesgoocupacion": None, "sociedadpropietaria": "SUBASTAS ATLAS"}


def test_altamira_gives_price_position_and_link(db, fake_http):
    import geo
    from db import load_listings
    from sources.es import parse_altamira, scrape_altamira
    row = parse_altamira(ALTAMIRA_CARD, "vivienda")
    assert row["id"] == "altamira:01402631" and row["price"] == 30000 and row["area_m2"] == 85
    assert row["url"] == "https://www.altamirainmuebles.com/venta-de-chalet/asturias/gijon/segunda-mano/01402631/157785/1"
    assert json.loads(row["raw_json"])["geo"]["lat"] == 43.52
    risky = parse_altamira({**ALTAMIRA_CARD, "riesgoocupacion": "ALTO"}, "vivienda")
    assert json.loads(risky["raw_json"])["occupation"] == "occupied"
    shared = parse_altamira({**ALTAMIRA_CARD, "proindiviso": True}, "vivienda")
    assert "copropiedad" in shared["description"]
    from sources.es import parse_solvia
    solvia_share = parse_solvia({"id": "1-2-O", "precio": 10000, "mostrarPrecio": True, "m2": 80,
                                 "categoriaTipoVivienda": {"nombre": "Viviendas"},
                                 "tipoVivienda": {"nombre": "Piso"}, "poblacion": {"nombre": "Foz"},
                                 "provincia": {"nombre": "Lugo"}, "participacion": 40})
    assert "40%" in solvia_share["description"] and "copropiedad" in solvia_share["description"]
    session = fake_http(lambda m, url, kw: FakeResponse(json_data={"totalResultados": "1",
                                                                    "minifichas": [ALTAMIRA_CARD]}))
    assert scrape_altamira(db, max_price=50000) == 2               # homes and land (same fake card)
    assert session.calls[0][2]["json"]["filtros"]["precioMaximo"] == 50000
    item = load_listings(db, include_hidden=True)[0]
    assert geo.position(item)["precision"] == "street"


# The listing page of an Altamira home (excerpt, 2026-10): the card said only the address.
ALTAMIRA_PAGE = """<html><body>
<div class="marcas_agua"> <div class="marca mrcCondesp">Condiciones especiales</div>
 <div class="marca ocupado">Ocupado</div> </div>
<section class="descripcion"> <h3>Descripción</h3> <div id="capaDescripcion"> <div class="capaDescrHeight">
<p>Altamira pone a la venta una casa de pueblo de dos plantas. Se trata de una vivienda unifamiliar para
rehabilitar, tiene una superficie total de 190 metros cuadrados. Actualmente se encuentra di&aacute;fana,
sin separacion entre plantas.</p></div></div></section>
<div id="modal_full_2" class="condesp"> <div id="modal_2" class="condicionespeciales"> <div class="generic_modal_2">
<div id="modal_contenido_2"> <h4 class="titulo">Condiciones Especiales</h4>
<p>El inmueble se encuentra ocupado ilegalmente por terceros y sin los suministros debidamente contratados.</p>
</div></div></div></div></body></html>"""


def test_altamira_reads_the_listing_page_once(db, fake_http):
    from db import load_listings
    from scoring import score_detail
    from sources.es import altamira_detail, scrape_altamira
    detail = altamira_detail(ALTAMIRA_PAGE)
    assert detail["occupied"] is True and "para rehabilitar" in detail["description"]
    assert detail["conditions"].startswith("El inmueble se encuentra ocupado ilegalmente")

    def handler(method, url, kw):
        if method == "POST":
            return FakeResponse(json_data={"totalResultados": "1", "minifichas": [ALTAMIRA_CARD]})
        return FakeResponse(ALTAMIRA_PAGE)
    session = fake_http(handler)
    scrape_altamira(db, max_price=50000)
    pages = [c for c in session.calls if c[0] == "GET"]
    assert len(pages) == 1                                         # homes only, once
    row = db.execute("SELECT description, raw_json FROM listings WHERE id = 'altamira:01402631'").fetchone()
    assert "para rehabilitar" in row[0] and json.loads(row[1])["occupation"] == "occupied"
    scrape_altamira(db, max_price=50000)                           # the next scan keeps what the page said
    assert len([c for c in session.calls if c[0] == "GET"]) == 1
    item = load_listings(db, include_hidden=True)[0]
    assert "para rehabilitar" in item["description"]
    sc, why = score_detail(item)
    assert sc == 0 and "possession" in why[0]


IMOVIRTUAL_AD = {"id": 19285780, "title": "Olival com cerca de 200 oliveiras e água de nascente",
                 "slug": "olival-com-agua-de-nascente-ID1iW6Y", "estate": "TERRAIN", "areaInSquareMeters": 10296,
                 "totalPrice": {"value": 30000}, "hidePrice": False, "isPrivateOwner": True,
                 "images": [{"medium": "https://img/1.jpg"}],
                 "location": {"address": {"street": {"name": "Vassal"}}, "reverseGeocoding": {"locations": [
                     {"locationLevel": "district", "name": "Vila Real"},
                     {"locationLevel": "council", "name": "Valpaços"},
                     {"locationLevel": "parish", "name": "Vassal"}]}}}


def _imovirtual_html(ads, pages=1):
    data = {"props": {"pageProps": {"data": {"searchAds": {"items": ads, "pagination": {"totalPages": pages}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_imovirtual_reads_the_page_data(db, fake_http, monkeypatch):
    import sources.pt
    from sources.pt import IMOVIRTUAL_PLACES, parse_imovirtual, scrape_imovirtual
    monkeypatch.setattr(sources.pt.time, "sleep", lambda s: None)
    row = parse_imovirtual(IMOVIRTUAL_AD)
    assert row["id"] == "imovirtual:19285780" and row["price"] == 30000 and row["area_m2"] == 10296
    assert row["tipo"] == "terreno" and row["concelho"] == "Valpaços" and row["freguesia"] == "Vassal"
    assert row["url"] == "https://www.imovirtual.com/pt/anuncio/olival-com-agua-de-nascente-ID1iW6Y"
    assert parse_imovirtual({**IMOVIRTUAL_AD, "hidePrice": True}) is None
    session = fake_http(lambda m, url, kw: FakeResponse(_imovirtual_html([IMOVIRTUAL_AD])))
    assert scrape_imovirtual(db, max_price=50000) == 2 * len(IMOVIRTUAL_PLACES)
    land = [c for c in session.calls if "/terreno/" in c[1]]
    assert land and all(c[2]["params"]["areaMin"] == 10000 and c[2]["params"]["priceMax"] == 50000 for c in land)


# A home's listing page (trimmed, 2026-10): the results card had no text at all.
IMOVIRTUAL_HOME = {**IMOVIRTUAL_AD, "id": 19157575, "estate": "HOUSE", "areaInSquareMeters": 212,
                   "title": "Casa / Villa T2 em Portela de 212 m2", "slug": "casa-villa-t2-em-portela-ID1inL9"}
IMOVIRTUAL_PAGE_AD = {
    "description": "Oportunidade de Investimento: Moradia para Renovação em Portela!<br/>\n<br/>\n"
                   " - A propriedade necessita de total reabilitação para atingir o seu potencial m&aacute;ximo.",
    "target": {"Build_year": "1937", "Energy_certificate": ["f"]},
    "enrichment": {"aiParamsList": [{"key": "construction_status", "value": "to_renovation", "rejected": False}]}}


def test_imovirtual_reads_a_home_page_once(db, fake_http):
    from scoring import built_year, condition
    from sources.pt import imovirtual_detail, scrape_imovirtual
    page = ('<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"ad": IMOVIRTUAL_PAGE_AD}}}) + "</script></html>")
    detail = imovirtual_detail(page)
    assert detail["construction_status"] == "to_renovation" and detail["ano_construcao"] == 1937
    assert "<br" not in detail["description"] and "potencial máximo" in detail["description"]

    def handler(method, url, kw):
        return FakeResponse(page if "/anuncio/" in url else _imovirtual_html([IMOVIRTUAL_HOME]))
    session = fake_http(handler)
    scrape_imovirtual(db, max_price=50000)
    assert len([c for c in session.calls if "/anuncio/" in c[1]]) == 1
    row = dict(db.execute("SELECT * FROM listings WHERE id = 'imovirtual:19157575'").fetchone())
    assert "necessita de total reabilitação" in row["description"]
    assert condition(row) == "heavy" and built_year(row) == 1937
    scrape_imovirtual(db, max_price=50000)
    assert len([c for c in session.calls if "/anuncio/" in c[1]]) == 1
    assert "necessita" in db.execute("SELECT description FROM listings WHERE id = 'imovirtual:19157575'").fetchone()[0]


FOTOCASA_AD = {"id": 187909805, "rawPrice": 45000, "accuracy": False, "isOccupied": False,
               "buildingSubtype": "House_Chalet", "location": "ALTO DE URBIES, Zona Rural",
               "description": "Casa en una parcela de 200 m², 80 m² construidos, junto al río.",
               "address": {"municipality": "Mieres (Asturias)", "province": "Asturias"},
               "coordinates": {"latitude": 43.2146, "longitude": -5.6698},
               "features": [{"key": "surface", "value": 87}, {"key": "rooms", "value": 2}],
               "detail": {"es-ES": "/es/comprar/vivienda/mieres-(asturias)/parking-amueblado/187909805/d"},
               "multimedia": [{"type": "image", "src": "https://static.fotocasa.es/a.jpg"}]}


def _fotocasa_html(ads, count):
    props = {"counters": {"realEstates": count}, "initialSearch": {"result": {"realEstates": ads}}}
    return f'<html><script id="__initial_props__" type="application/json">{json.dumps(props)}</script></html>'


def test_fotocasa_reads_the_page_data(db, fake_http, monkeypatch):
    import sources.es
    from sources.es import FOTOCASA_PROVINCES, parse_fotocasa, scrape_fotocasa
    monkeypatch.setattr(sources.es.time, "sleep", lambda s: None)
    row = parse_fotocasa(FOTOCASA_AD, "vivienda")
    assert row["id"] == "fotocasa:187909805" and row["price"] == 45000 and row["area_m2"] == 87
    assert row["concelho"] == "Mieres" and row["title"].startswith("Casa en Mieres")
    assert row["image_url"] == "https://static.fotocasa.es/a.jpg"
    assert json.loads(row["raw_json"])["geo"]["precision"] == "village"
    taken = parse_fotocasa({**FOTOCASA_AD, "isOccupied": True}, "vivienda")
    assert json.loads(taken["raw_json"])["occupation"] == "occupied"
    bare = parse_fotocasa({**FOTOCASA_AD, "isBareOwnership": True}, "vivienda")
    assert json.loads(bare["raw_json"])["nuda_propiedad"] is True
    assert json.loads(row["raw_json"])["rooms"] == 2
    reform = parse_fotocasa({**FOTOCASA_AD, "features": FOTOCASA_AD["features"] + [
        {"key": "conservationStatus", "value": 4}, {"key": "bathrooms", "value": 1}]}, "vivienda")
    assert json.loads(reform["raw_json"])["conservation"] == 4 and json.loads(reform["raw_json"])["bathrooms"] == 1
    assert bare["description"].endswith("nuda propiedad")
    assert "Casa en una parcela" in bare["description"]
    # two pages of one ad each, then done
    pages = {1: _fotocasa_html([FOTOCASA_AD], 2), 2: _fotocasa_html([{**FOTOCASA_AD, "id": 2}], 2)}
    session = fake_http(lambda m, url, kw: FakeResponse(pages[2 if url.endswith("/l/2") else 1]))
    assert scrape_fotocasa(db, max_price=50000) == 4 * len(FOTOCASA_PROVINCES)
    land = [c for c in session.calls if "/terrenos/" in c[1]]
    assert all(c[2]["params"] == {"maxPrice": 100000, "minSurface": 10000} for c in land)


def test_fotocasa_detail_page_keeps_the_full_text():
    from sources.es import fotocasa_detail, with_fotocasa_detail, parse_fotocasa
    page = {"props": {"pageProps": {"realEstate": {
        "description": "Casa para reformar por completo, tejado hundido, en una parcela.",
        "features": [{"key": "conservationStatus", "value": 4}],
        "multimedia": [{"src": "https://static.fotocasa.es/full.jpg"}],
    }}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script>'
    detail = fotocasa_detail(html)
    assert detail["conservation"] == 4 and "tejado hundido" in detail["description"]
    row = with_fotocasa_detail(parse_fotocasa(FOTOCASA_AD, "vivienda"), detail)
    assert "tejado hundido" in row["description"]
    raw = json.loads(row["raw_json"])
    assert raw["detail_checked"] == 1 and raw["conservation"] == 4
    assert raw["fotos"] == ["https://static.fotocasa.es/full.jpg"]


BIENICI_AD = {"id": "ag1-2", "propertyType": "house", "price": 42000, "city": "Huelgoat", "postalCode": "29690",
              "departmentCode": "29", "surfaceArea": 90, "landSurfaceArea": 1500, "title": "Longère",
              "description": "Longère en pierre au bord de la rivière.", "isInTourismResidence": False,
              "blurInfo": {"type": "exact", "position": {"lat": 48.36, "lon": -3.74}},
              "photos": [{"url": "https://file.bienici.com/photo/1.jpg"}]}


def test_bienici_reads_the_search_service(db, fake_http, monkeypatch):
    import sources.fr
    from sources.fr import parse_bienici, scrape_bienici
    monkeypatch.setattr(sources.fr.time, "sleep", lambda s: None)
    row = parse_bienici(BIENICI_AD)
    assert row["id"] == "bienici:ag1-2" and row["price"] == 42000 and row["area_m2"] == 90
    assert row["url"] == "https://www.bienici.com/annonce/ag1-2" and "terrain 1500 m²" in row["description"]
    assert json.loads(row["raw_json"])["geo"] == {"lat": 48.36, "lon": -3.74, "precision": "street"}
    assert parse_bienici({**BIENICI_AD, "isInTourismResidence": True}) is None
    assert parse_bienici({**BIENICI_AD, "price": [None, 38000]})["price"] == 38000
    land = parse_bienici({**BIENICI_AD, "propertyType": "terrain", "landSurfaceArea": 20000})
    assert land["tipo"] == "terrain" and land["area_m2"] == 20000
    html_ad = parse_bienici({**BIENICI_AD, "energyClassification": "G",
                             "description": "<p>Des travaux sont<br>à prévoir&nbsp;: toiture.</p>"})
    assert "<" not in html_ad["description"] and "travaux sont à prévoir" in html_ad["description"]
    assert json.loads(html_ad["raw_json"])["dpe"] == "G"
    assert "dpe" not in json.loads(parse_bienici({**BIENICI_AD, "energyClassification": "NS"})["raw_json"])
    session = fake_http(lambda m, url, kw: FakeResponse(json_data={"total": 1, "realEstateAds": [BIENICI_AD]}))
    assert scrape_bienici(db, max_price=50000) == 4          # houses in 3 price bands, then land
    sent = [json.loads(c[2]["params"]["filters"]) for c in session.calls]
    assert [(f["minPrice"], f["maxPrice"]) for f in sent[:3]] == [(0, 25000), (25000, 37500), (37500, 50000)]
    assert sent[3]["propertyType"] == ["terrain"] and sent[3]["minArea"] == 10000


def _immoweb_ad(**over):
    ad = {"id": 21881855, "flags": {"main": None, "secondary": []},
          "property": {"type": "HOUSE", "subtype": "HOUSE", "title": "Maison à rénover", "netHabitableSurface": 120,
                       "landSurface": 400, "salesPitch": {"fr": "Avec jardin"},
                       "location": {"province": "Hainaut", "locality": "Hollain", "postalCode": "7620",
                                    "latitude": 50.54, "longitude": 3.42}},
          "transaction": {"certificate": "G"}, "price": {"type": "residential_sale", "mainValue": 45000}}
    ad.update(over)
    return ad


def test_immoweb_keeps_plain_sales_and_skips_annuities_and_options():
    from sources.be import immoweb_results, parse_immoweb
    row = parse_immoweb(_immoweb_ad(), "maison")
    assert row["id"] == "immoweb:21881855" and row["price"] == 45000 and row["area_m2"] == 120
    assert row["title"] == "Maison à Hollain (7620)" and "Avec jardin" in row["description"]
    assert '"lat": 50.54' in row["raw_json"] and row["url"].endswith("/fr/annonce/21881855")
    assert parse_immoweb(_immoweb_ad(price={"type": "annuity_monthly_amount", "mainValue": 400}), "maison") is None
    assert parse_immoweb(_immoweb_ad(flags={"main": "under_option", "secondary": []}), "maison") is None
    assert parse_immoweb(_immoweb_ad(flags={"main": None, "secondary": ["life_annuity"]}), "maison") is None
    assert immoweb_results(":results='[{&quot;id&quot;:1}]'") == [{"id": 1}]
def test_notaires_reads_an_ad_and_skips_annuities_and_promised_sales():
    from sources.fr import parse_notaires
    ad = {"annonceId": 2082120, "prixAffiche": 10000, "prixTotal": 10600, "typeBien": "MAI", "typeTransaction": "VENTE",
          "communeNom": "Berrien", "codePostal": "29690", "departementNom": "Finistère", "surface": 30.0,
          "surfaceTerrain": 2111, "descriptionFr": "Une ancienne maison<br>à rénover entièrement avec terrain",
          "urlDetailAnnonceFr": "https://www.immobilier.notaires.fr/fr/annonce-immo/vente/maison/berrien-29/2082120"}
    row = parse_notaires(ad)
    assert row["id"] == "notaires:2082120" and row["price"] == 10600          # notary fees included
    assert row["area_m2"] == 30 and row["concelho"] == "Berrien" and "<br>" not in row["description"]
    assert parse_notaires({**ad, "viager": "OUI"}) is None
    assert parse_notaires({**ad, "descriptionFr": "Sous compromis : maison"}) is None
    land = parse_notaires({**ad, "typeBien": "TER"})
    assert land["tipo"] == "terrain" and land["area_m2"] == 2111
    # When a maison has no built area, area_m2 must be None — not the plot area.
    no_built = parse_notaires({**ad, "surface": None})
    assert no_built["area_m2"] is None, f"got {no_built['area_m2']} instead of None"
TGSS_TABLE = """<div class="tablas-resultados"><table><caption>Finca Rústica - CANTABRIA - (03/10/2026)</caption>
<tbody><tr class="par">
<td><a href="/subastas/SubaSeControladorInter?opcion=13&amp;EMB_ID=901&amp;opcion2=1&amp;tipoOperacion=1">YERA  (VEGA DE PAS)</a></td>
<td>-</td><td class="moneda"> 16.863,24 &euro;</td><td class="moneda"> 2.000,00 &euro;</td><td>Lote: 1</td>
<td class="moneda"> 16.863,24 &euro;</td><td>15/12/2026 12:00</td></tr></tbody></table></div>"""

TGSS_DETAIL = """<div>Descripci&oacute;n General del Bien: Prado. TITULARIDAD: APELLIDO NOMBRE D.N.I. 12345678Z
100 % PROPIEDAD Superficie: 1,2089 (hect&aacute;reas) Localizaci&oacute;n: YERA (39728) VEGA DE PAS
Subasta Fecha: 15/12/2026 12:00</div>"""


def test_tgss_reads_the_table_adds_charges_and_drops_the_owner():
    from common import price_to_pay
    from sources.es import parse_tgss, tgss_detail, tgss_rows
    rows = tgss_rows(TGSS_TABLE)
    assert rows == [{"id": "901", "kind": "Finca Rústica", "address": "YERA (VEGA DE PAS)", "valuation": 16863.24,
                     "charges": 2000.0, "price": 16863.24, "date": "15/12/2026 12:00"}]
    listing = parse_tgss(rows[0], tgss_detail(TGSS_DETAIL))
    assert listing["id"] == "tgss:901" and listing["tipo"] == "terreno"
    assert listing["area_m2"] == 12089.0 and listing["concelho"] == "Vega De Pas"
    assert "12345678Z" not in listing["description"] and "NOMBRE" not in listing["description"]
    assert price_to_pay(listing) == 18863.24          # the debts stay with the land


def test_a_tgss_urban_finca_is_a_home_only_when_it_says_so():
    import scoring
    from sources.es import parse_tgss
    row = {"id": "82", "kind": "Finca Urbana", "address": "CL RIU SIURANA 53 (TARRAGONA)", "valuation": 15396.41,
           "price": 15396.41, "date": "02/12/2026 12:00"}
    garage = parse_tgss(row, {"description": "611/9 TOTALIDAD PARQUING EN TARRAGONA"})
    flat = parse_tgss(row, {"description": "VIVIENDA EN C CARMEN 1, Descripción Detallada: URBANA"})
    assert garage["tipo"] == "inmueble" and scoring.property_kind(garage) == "other"
    assert flat["tipo"] == "vivienda" and scoring.property_kind(flat) == "home"


PISOS_CARD = """<div id="675.105" class="ad-preview ad-preview--has-desc">
<div class="ad-preview__bottom"><div class="ad-preview__info">
<a href="/comprar/casa_rustica-ortigueira-675_105/" class="ad-preview__title">Casa r&#xFA;stica en calle Lugar Veiga, 4</a>
<p class="p-sm ad-preview__subtitle">Ortigueira</p>
<p class="ad-preview__char p-sm">4 habs.</p><p class="ad-preview__char p-sm">187 m&#xB2;</p>
<p class="ad-preview__description">Gran oportunidad en Barbos...</p></div>
<div class="contact-box" data-ad-id="675.105" data-ad-price="40000"></div></div>
<img src="https://fotos.imghs.net/a.jpg"></div>"""

PISOS_DETAIL = """<a data-src="/mapa?latitude=43.688&amp;longitude=-7.82411&amp;zoom=17"></a>
<div class="description__content">Gran oportunidad en Barbos, Ortigueira. Casa de 187 metros con parcela de 297 m.</div>"""


def test_pisos_town_is_the_municipality():
    from sources.es import pisos_municipality
    assert pisos_municipality("Baldedo (Allande)") == "Allande"
    assert pisos_municipality("Ourol (Casco Urbano)") == "Ourol"
    assert pisos_municipality("Ortigueira") == "Ortigueira"


def test_the_same_house_on_two_portals_shows_once(db):
    from common import make_listing
    from db import mark_duplicates, upsert_listing
    from sources.es import pisos_municipality
    upsert_listing(db, make_listing("fotocasa", "1", "ES", title="Casa en Ourol", price=22000, area_m2=294,
                                    concelho="Ourol", district="Lugo"))
    upsert_listing(db, make_listing("pisos", "2", "ES", title="Casa en Ourol", price=22000, area_m2=294,
                                    concelho=pisos_municipality("Ourol (Casco Urbano)"), district="Lugo"))
    db.commit()
    # identical price+area across sources → speculative possible_match hint, both visible
    assert mark_duplicates(db) == 0
    rows = db.execute("SELECT id, possible_match FROM listings WHERE possible_match IS NOT NULL").fetchall()
    assert len(rows) == 1
    ids = {rows[0]["id"], rows[0]["possible_match"]}
    assert ids == {"fotocasa:1", "pisos:2"}


def test_pisos_reads_cards_and_the_detail_page():
    from sources.es import parse_pisos, pisos_cards, pisos_detail
    cards = pisos_cards(PISOS_CARD)
    assert len(cards) == 1 and cards[0]["price"] == 40000 and cards[0]["area"] == 187
    row = parse_pisos(cards[0], "vivienda", "a_coruna", pisos_detail(PISOS_DETAIL))
    assert row["id"] == "pisos:675.105" and row["district"] == "A Coruña" and row["concelho"] == "Ortigueira"
    assert row["description"].startswith("Gran oportunidad en Barbos, Ortigueira")
    assert '"lat": 43.688' in row["raw_json"]
    assert row["url"] == "https://www.pisos.com/comprar/casa_rustica-ortigueira-675_105/"
    assert '"detail_checked"' in row["raw_json"]


THINKSPAIN_LIST = """<script id="item-list-structured-data" type="application/ld+json">
{"@context": "http://schema.org","@type": "ItemList","itemListElement": [{"@type": "ListItem","position":1,"item":
{"@type": "Product","name": "1 bedroom Townhouse for sale in Monforte de Lemos with garden - € 43,600 (Ref: 10009160)",
"image": "https://cdn.thinkwebcontent.com/a.jpg","description": "A traditional Galician stone house...",
"productID": "10009160","url": "https://www.thinkspain.com/property-for-sale/10009160",
"offers": {"@type": "Offer","priceCurrency": "EUR","price": "43600"}}}]}
</script>"""


def test_thinkspain_reads_the_item_list():
    from sources.es import parse_thinkspain, thinkspain_detail, thinkspain_items
    items = thinkspain_items(THINKSPAIN_LIST)
    assert len(items) == 1 and items[0]["town"] == "Monforte de Lemos" and items[0]["price"] == 43600
    text = thinkspain_detail('<div id="property-description"><p>Stone house. Built area of 94 m2. '
                             'The plot is 622 square meters, 2 minutes from town.</p></div>')
    row = parse_thinkspain(items[0], "lugo", text)
    assert row["id"] == "thinkspain:10009160" and row["district"] == "Lugo" and row["tipo"] == "vivienda"
    assert row["title"] == "1 bedroom Townhouse in Monforte de Lemos" and row["area_m2"] == 94


def test_solvia_reads_its_search_api():
    from sources.es import parse_solvia
    item = {"id": "192413-155113-O", "idVivienda": 192413, "precio": 22000.0, "m2": 120.0, "mostrarPrecio": True,
            "categoriaTipoVivienda": {"nombre": "Viviendas"}, "tipoVivienda": {"nombre": "Casa Planta Baja"},
            "poblacion": {"nombre": "Foz"}, "provincia": {"nombre": "Lugo"}, "tituloFicha": "Casa en Foz",
            "sinPosesion": True, "reformar": True, "enCosta": True, "enSubasta": False,
            "listaImagenesInmueble_vPC": ["https://cdnsolvproep.solvia.es/uploaded/x.jpg"]}
    row = parse_solvia(item)
    assert row["id"] == "solvia:192413-155113" and row["price"] == 22000 and row["concelho"] == "Foz"
    assert "inmueble ocupado" in row["description"] and "Para reformar" in row["description"]
    assert row["url"] == "https://www.solvia.es/es/propiedades/comprar/vivienda-192413-155113"
    assert parse_solvia({**item, "categoriaTipoVivienda": {"nombre": "Garajes"}}) is None


IMOT_CARD = """<div class="item  " id="ida1j1"><div class="photo"><a class="image"><img
src="//cdn3.focus.bg/imot/1j1_Je.jpg" class="pic"></a></div><div class="text"><div class="zaglavie">
<a href="//www.imot.bg/obiava-1j164942254773629-prodava-kashta-oblast-gabrovo-s-slaveykovo" class="title">Продава
КЪЩА<location>с. Славейково, област Габрово</location></a><div class="price"><div>30 000 €</div></div></div>
<div class="info">71 кв.м, двор 1550 кв.м, Продава къща до река Янтра ..., тел.: 0886 123 456</div></div></div>"""


def test_imot_reads_a_card_without_the_phone_number():
    from sources.bg import imot_cards, parse_imot
    cards, ads = imot_cards(IMOT_CARD)
    assert ads == 1 and len(cards) == 1
    row = parse_imot(cards[0], "house")
    assert row["id"] == "imot:1j164942254773629" and row["country"] == "BG" and row["price"] == 30000
    assert row["area_m2"] == 71 and row["concelho"] == "Славейково" and row["district"] == "Габрово"
    assert "0886" not in row["description"] and "тел" not in row["description"]
    assert row["image_url"] == "https://cdn3.focus.bg/imot/1j1_Je.jpg"
    assert parse_imot(cards[0], "terreno")["area_m2"] == 1550


def test_index_oglasi_reads_the_list_and_the_full_ad():
    from sources.hr import parse_index_ad
    ad = {"id": "ade74a32", "code": 7403059, "price": 22000, "title": "Prodaje se kuća – Potok Kalnički",
          "smartLink": "prodaje-se-kuca", "countyName": "Koprivničko-križevačka", "cityName": "Kalnik",
          "settlementName": "Potok Kalnički", "summary": {"area": 72}, "images": ["a/b.jpg"]}
    detail = {"description": "Kuća za potpunu adaptaciju podno Kalnika.", "latitude": 46.11, "longitude": 16.48,
              "isPreciseLocation": False, "cityWaterSupply": True, "yearBuilt": "1968-01-01T00:00:00Z", "area": 72}
    row = parse_index_ad(ad, "house", "prodaja-kuca", detail)
    assert row["id"] == "indexoglasi:ade74a32" and row["country"] == "HR" and row["price"] == 22000
    assert row["concelho"] == "Kalnik" and row["area_m2"] == 72 and "Gradski vodovod" in row["description"]
    assert '"precision": "village"' in row["raw_json"]
    assert row["image_url"] == "https://www.index.hr/oglasi/api/image/direct/a/b.jpg"
    assert parse_index_ad({**ad, "price": 0}, "house", "prodaja-kuca") is None       # price on request


def test_nehnutelnosti_reads_the_next_stream():
    import json as _json
    from sources.sk import nehnutelnosti_ads, nehnutelnosti_stream, parse_nehnutelnosti
    ad = {"advertisement": {"id": "Ju1", "title": "Rodinný dom Banská Štiavnica", "sefName": "rodinny-dom",
                            "description": "$2d", "location": {"county": "Banskobystrický kraj",
                                                              "district": "okres Banská Štiavnica",
                                                              "city": "Banská Štiavnica"},
                            "price": {"priceNum": 38000}, "parameters": {"area": 120, "realEstateState": "Pôvodný stav"},
                            "photos": [{"url": "https://img.example/1.jpg"}]}}
    flight = "2d:T" + format(len("Dom pri rieke".encode()), "x") + ",Dom pri rieke" + _json.dumps(ad, separators=(",", ":"))
    html = 'self.__next_f.push([1,' + _json.dumps(flight) + '])'
    stream = nehnutelnosti_stream(html)
    ads = nehnutelnosti_ads(stream)
    row = parse_nehnutelnosti(ads[0], "house", stream)
    assert row["id"] == "nehnutelnosti:Ju1" and row["country"] == "SK" and row["price"] == 38000
    assert row["concelho"] == "Banská Štiavnica" and row["description"].startswith("Dom pri rieke")
    assert row["url"] == "https://www.nehnutelnosti.sk/detail/Ju1/rodinny-dom"
    assert parse_nehnutelnosti({**ads[0], "price": {"priceNum": 1}}, "house") is None    # "on request"


def test_land_searches_go_up_to_the_land_limit():
    from common import LAND_MAX_PRICE, land_max_price
    assert land_max_price({}, 30000) == LAND_MAX_PRICE == 100000
    assert land_max_price({"land_max_price": 80000}, 30000) == 80000
    assert land_max_price({"land_max_price": 10000}, 30000) == 30000          # never below the house limit


def test_citius_is_verified_with_its_missing_intermediate(monkeypatch):
    """citius.mj.pt serves the wrong intermediate: any session (a scraper, the
    link check) verifies it with the bundled YR2, never with verify off."""
    import requests
    from common import make_session
    seen = {}
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kw: seen.setdefault(url, kw.get("verify")))
    s = make_session()
    s.get("https://www.citius.mj.pt/portal/x")
    s.get("https://example.org/")
    citius = seen["https://www.citius.mj.pt/portal/x"]
    assert isinstance(citius, str) and open(citius, "rb").read().count(b"BEGIN CERTIFICATE") > 100
    assert seen["https://example.org/"] is None


def test_a_citius_house_keeps_its_floor_not_its_land():
    """citius 189/10.0TBVFC (2026-10-27): 150 m² of land was stored as the area
    of an 85,45 m² house. A plot, with no floor, keeps its land."""
    from sources.pt import _citius_area
    house = ("casa baixa telhada destinada a habitação com quintal, área total do terreno de 150m2 "
             "área de implantação do edifício de 47,05m2 área bruta de construção de 85,45m2")
    assert _citius_area(house) == 85.45
    assert _citius_area("Terreno para construção com 1200 m2") == 1200
    assert _citius_area("Prédio rústico, área total: 4,6905 ha") == 46905


def test_fallcoaste_reads_a_home_card():
    from bs4 import BeautifulSoup
    from sources.it import fallcoaste_listing
    html = """<article class="hover-block auction horizontal-auction" data-auction-id="1642172"
      data-auction-data-termine="29/10/2026 10:00" data-auction-url="https://www.fallcoaste.it/vendita/x-1642172.html">
      <figure><img src="https://cdn/x.jpg"/></figure>
      <span class="price-block"><strong>11.812,50</strong></span>
      <div class="name_type"><h3>Porzione di fabbricato di 120 mq in Ballabio</h3>
      <p>Sincrona telematica</p><p>Tribunale di Lecco - Procedura n.3/2024</p>
      <ul><li>Porzione di fabbricato di 120 mq</li></ul></div></article>"""
    row = fallcoaste_listing(BeautifulSoup(html, "html.parser").article)
    assert row["id"] == "fallcoaste:1642172" and row["price"] == 11812.5
    assert row["district"] == "Lecco" and row["area_m2"] == 120
    assert row["date_end"] == "2026-10-29T10:00:00"


def test_vench_reads_a_sale_card():
    from bs4 import BeautifulSoup
    from sources.fr import vench_listing
    html = """<div class="featured-item"><img data-src="/vignettes/v1.jpg"/><div class="down-content">
      <a href="./vente-167055-une-maison-briare.html"><h3>UNE MAISON • Briare</h3></a>
      <li class="miseAPrixVignette">Mise à prix : 36 000.00 €</li>
      <li class="dateVenteVignette">Date de la vente : 17/12/26</li></div></div>"""
    row = vench_listing(BeautifulSoup(html, "html.parser").div)
    assert row["id"] == "vench:167055" and row["price"] == 36000 == row["min_price"]
    assert row["concelho"] == "Briare" and row["date_end"] == "2026-12-17"


def test_goauktion_reads_a_lot_and_skips_sold_ones():
    from bs4 import BeautifulSoup
    from sources.de import goauktion_listing
    lot = """<div data-jplist-item=""><span class="Sachsen" style="display:none;"></span>
      <span class="vermietet" style="display:none;"></span>
      <div class="katalogpos"><a href="/objekt/S26-04-003.html"><p>S26-04-003</p></a></div>
      <div class="verkaufsstatus"><p>{status}</p></div>
      <span class="auktionLimitAmoutForSorting">9000</span>
      <div class="objekt"><p class="addr-list"><a>Einfamilienhaus<br/>Hauptstr. 1<br/>01234 Zwickau</a></p>
      <div class="ortIco"><a data-lat="50.7" data-lon="12.5"><img class="EFHZFH"/></a></div></div></div>"""
    row = goauktion_listing(BeautifulSoup(lot.format(status="Aktuell"), "html.parser").div)
    assert row["id"] == "goauktion_de:S26-04-003" and row["price"] == 9000
    assert row["tipo"] == "moradia" and row["concelho"] == "Zwickau" and row["district"] == "Sachsen"
    assert json.loads(row["raw_json"])["rented"] is True
    assert goauktion_listing(BeautifulSoup(lot.format(status="Verkauft"), "html.parser").div) is None


def test_eactivos_reads_a_home_card():
    from bs4 import BeautifulSoup
    from sources.es import eactivos_card
    html = """<div class="liquidation-card card px-0" data-liquidation="25364">
      <span class="end-date">Finaliza el <span class="date">29/10/2026</span></span>
      <a class="img-link liquidation-card-image" href="https://www.eactivos.com/6K2P8GMK/subasta-de-vivienda">
        <img class="card-img-top img-main" src="https://eactivoscdn.azureedge.net/card/a.png"></a>
      <div class="card-title"><a href="https://www.eactivos.com/6K2P8GMK/subasta-de-vivienda">
        Subasta de vivienda en barriada Los Pajaritos, Sevilla</a></div>
      <div class="card-subtitle mb-3 d-flex"><i class="location"></i> Sevilla, Sevilla</div>
      <div class="text-value current-bid-amount" data-initial-price="0">0€</div>
      <div class="text-value" data-end-date="2026-10-29 12:00:00"></div>
      <span class="liquidation-price">25.000€</span></div>"""
    row = eactivos_card(BeautifulSoup(html, "html.parser").div)
    assert row["id"] == "eactivos:25364" and row["price"] == 25000 and row["min_price"] == 0
    assert row["concelho"] == "Sevilla" and row["date_end"] == "2026-10-29T12:00:00"
def test_hacienda_reads_home_lots_and_their_rounds():
    from sources.es import hacienda_lots
    text = ("LOTE 3 Descripción. VIVIENDA sita en PASEO DE LAS DELICIAS número 35, bajo, puerta B (28045 Madrid). "
            "Superficie construida de 21,5 m². Tipo de licitación y garantía. Valor de tasación: 99.000,00 euros. "
            "Subasta Primera Segunda Tercera Cuarta Tipo de licitación 99.000,00 84.150,00 71.257,50 60.798,38 "
            "Garantía 5% 4.950,00 LOTE 4 Descripción. Solar en calle Mayor. Tipo de licitación 1.000,00 900,00 "
            "Lote 5.- Casa en Magacela, de cincuenta y dos metros. Segunda Subasta: Tipo de licitación del lote: "
            "11.039,80 euros. Cuarta Subasta: Tipo de licitación del lote: 7.976,26 euros.")
    lots = hacienda_lots(text)
    assert [lot["lot"] for lot in lots] == ["3", "4", "5"]                  # a plot is kept too
    assert lots[0]["town"] == "Madrid" and lots[0]["price"] == 99000 and lots[0]["floor"] == 60798.38
    assert lots[1]["floor"] == 900 and lots[2]["town"] == "Magacela" and lots[2]["floor"] == 7976.26


def test_boe_public_picks_property_sales_of_other_public_bodies():
    from sources.es import boe_sale_notices, hacienda_lots
    item = lambda t: {"identificador": "BOE-B-1", "titulo": t, "url_html": "u"}
    summary = {"data": {"sumario": {"diario": [{"seccion": [{"codigo": "5B", "departamento": [
        {"nombre": "MINISTERIO DE TRABAJO", "item": [
            item("Anuncio del FOGASA por el que se convoca la venta en subasta pública de bienes inmuebles."),
            item("Anuncio de suspensión de subasta pública de bien inmueble.")]},
        {"nombre": "MINISTERIO DE HACIENDA", "item": [
            item("Anuncio de la Delegación de Economía y Hacienda de Valencia: subasta de inmuebles.")]}]}]}]}}}
    assert [n["body"] for n in boe_sale_notices(summary)] == ["MINISTERIO DE TRABAJO"]
    (lot,) = hacienda_lots("Se convoca subasta del inmueble. Vivienda sita en Vigo. El tipo de licitación es "
                           "61.296 € (sesenta y un mil).", single=True)
    assert lot["price"] == 61296
def test_avra_keeps_what_is_on_sale_with_its_position():
    from sources.es import avra_listing
    entry = {"referencia_inmueble": "3801-VP-022-400", "tipo_inmueble": "VIVIENDA", "municipio": "ALMERIA",
             "provincia": "ALMERIA", "mconstruidos": "100.11", "precio": "60920.3", "oferta": "S",
             "tipo_oferta": "SUBASTA", "centroide": "POINT(-2.150192 37.38906)"}
    row = avra_listing(entry)
    assert row["id"] == "avra:3801-VP-022-400" and row["price"] == 60920.3 and row["area_m2"] == 100.11
    assert row["concelho"] == "Almeria" and '"lat": 37.38906' in row["raw_json"]
    assert avra_listing({**entry, "oferta": "N"}) is None
def test_agorastore_reads_a_product():
    from sources.fr import agorastore_listing
    row = agorastore_listing({
        "productId": 435215, "productName": "Maison - 70 m² - Sainte-Livrade-sur-Lot (47)",
        "productPageUrl": "/vente-occasion/immobilier/maison/maison-70-m-sainte-livrade-sur-lot-47-435215.aspx",
        "subCategories": [{"name": "Maison"}], "sellerName": "Département du Lot-et-Garonne",
        "sale": {"endDate": "2026-12-10T16:00:00+01:00", "currentPrice": 14999.0, "totalBids": 0}})
    assert row["id"] == "agorastore:435215" and row["price"] == 14999 and row["current_bid"] is None
    assert row["concelho"] == "Sainte-Livrade-sur-Lot" and row["district"] == "47" and row["area_m2"] == 70
    assert row["date_end"] == "2026-12-10T16:00:00" and "Lot-et-Garonne" in row["description"]
def test_ran_reads_notary_notices_and_skips_court_sales():
    from sources.it import ran_listings
    html = """<input class="avvisolat" id="lat_64702" value=" 41.864558"><input id="lon_64702" value=" 12.486888">
    <a href="/beni-immobili/dettaglio-avviso/GAV/64702"><div class="divScheda"><div class="divSchedaInfo">
    <p>DISMISSIONE ENTE PUBBLICO - LVIII Bando ATER Roma. LOCALE DEPOSITO. Mq 37. Stato Libero.</p>
    <ul><li>Prezzo dell'Avviso: <span>€ 3.548,25</span></li>
    <li>Data e Ora fine raccolta offerte: <span>10/11/2026 17:00</span></li></ul></div>
    <div class="divSchedaDett"><small>Via D. Chiodo 1, Roma (Roma)</small></div></div></a>
    <a href="/beni-immobili/dettaglio-avviso/GAST/4627591"><div class="divSchedaInfo"><p>x</p></div></a>"""
    (row,) = ran_listings(html)
    assert row["id"] == "ran:64702" and row["price"] == 3548.25 and row["concelho"] == "Roma"
    assert row["date_end"] == "2026-11-10T17:00:00" and row["area_m2"] == 37
def test_bima_keeps_purchases_and_drops_placeholder_prices():
    from sources.de import bima_listing
    plot = bima_listing({"id": "1", "attributes": {"commercialization_type": "BUY", "title": "Baugrundstück",
                        "city": "Anklam", "federal_state": "mecklenburg_vorpommern", "buy_price": "15000.0",
                        "display_area": "508.0", "latitude": "53.85", "longitude": "13.69"}})
    assert plot["id"] == "bima:1" and plot["price"] == 15000 and plot["concelho"] == "Anklam"
    assert plot["district"] == "Mecklenburg Vorpommern"
    assert bima_listing({"id": "2", "attributes": {"commercialization_type": "BUY", "buy_price": "1.0"}})["price"] is None
    assert bima_listing({"id": "3", "attributes": {"commercialization_type": "RENT"}}) is None
def test_biedboek_keeps_sales_and_reads_dutch_parcel_sizes():
    from sources.nl import biedboek_listing, biedboek_m2
    assert biedboek_m2("1.68.40") == 16840 and biedboek_m2("5220") == 5220
    sale = {"shortId": "RpPhi3nO", "parcelSize": "5220", "latitude": "51.43", "longitude": "5.28",
            "translation": {"name": "Bosperceel", "city": "Vessem", "currentUsage": "Bos",
                            "realEstateParagraphs": [{"html": "<p>Het Rijksvastgoedbedrijf biedt te koop aan.</p>"}]},
            "biddingData": {"minimumAmount": 0}}
    row = biedboek_listing(sale)
    assert row["id"] == "biedboek:RpPhi3nO" and row["area_m2"] == 5220 and row["price"] is None
    lease = {**sale, "translation": {**sale["translation"], "name": "Pachtgronden Nederland"}}
    assert biedboek_listing(lease) is None
def test_estamo_reads_lots_but_not_the_visit_schedule():
    from sources.pt import estamo_lots
    html = """<p>CALENDARIZAÇÃO DA HASTA PÚBLICA N.º1 18 de março 2026 Visita ao Imóvel n.º3 (Prédio Rústico)
    30 de março 2026 Até às 17h00 Prazo limite para apresentação de propostas</p>
    <p>IMÓVEL N.º3 Prédio Rústico "Bouça das Cruzes" em Marco de Canaveses Terreno rústico com 1,3429 hectares
    VALOR DE ALIENAÇÃO 164.200,00€ VALOR BASE 159.200,00 € Documentação</p>
    <p>IMÓVEL N.º5 Parcelas de Terreno na Póvoa de Varzim VALOR DE ALIENAÇÃO Nao Arrematado VALOR BASE 337.000,00 €</p>
    <p>Documentos associados</p>"""
    lots = estamo_lots(html)
    assert [r["id"] for r in lots] == ["estamo:1-3", "estamo:1-5"]
    assert lots[0]["price"] == 159200 and lots[0]["date_end"] == "2026-03-30T17:00:00"
    assert lots[1]["price"] == 337000 and "alienação" not in lots[1]["description"]
def test_etad_keeps_sales_and_reads_the_facts():
    from sources.gr import etad_listing
    page = lambda kind: (f"<h1>Διαμέρισμα 4ου Οροφου</h1><div>Νομός: Αττική Πόλη: Αθήνα Τετραγωνικά Μέτρα Κτισμάτων: "
                         f"211.85 Κατηγορία Συναλλαγής: {kind} Χρόνος Λήξης: 02/11/2026 - 14:00:00 "
                         f"Τιμή Εκκίνησης: 45.000 € Βήμα Διαγωνισμού: 5 %</div>")
    row = etad_listing("21", page("Πώληση"))
    assert row["id"] == "etad:21" and row["price"] == 45000 and row["area_m2"] == 211.85
    assert row["concelho"] == "Αθήνα" and row["date_end"] == "2026-11-02T14:00:00"
    assert etad_listing("22", page("Εκμίσθωση")) is None


def test_gemeenteblad_keeps_open_bids_and_real_plots_only():
    from sources.nl import gemeenteblad_listing, gemeenteblad_m2
    assert gemeenteblad_m2("perceel van 1.250 m² en 2,5 ha") == 25000
    base = {"id": "gmb-2026-1", "town": "Dordrecht", "date": "2026-10-26"}
    bid = gemeenteblad_listing({**base, "title": "Voornemen tot verkoop woonwagen",
                                "text": "voornemen om aan de hoogste bieder te verkopen een woonwagen. Bieding kenbaar maken."})
    assert bid["id"] == "gemeenteblad:gmb-2026-1" and bid["tipo"] == "gebouw" and bid["date_end"] == "2026-11-15"
    plot = gemeenteblad_listing({**base, "title": "Voornemen tot verkoop van gronden",
                                 "text": "verkoop van een perceel van 8.985 m2. Serieuze gegadigden kunnen binnen 20 kalenderdagen reageren."})
    assert plot["area_m2"] == 8985 and "didam" in plot["raw_json"]
    strip = gemeenteblad_listing({**base, "title": "Voornemen verkoop groenstrook nabij Laan 16", "text": "verkoop 40 m2"})
    small = gemeenteblad_listing({**base, "title": "Voornemen tot verkoop grond", "text": "verkoop van 120 m2 grond"})
    assert strip is None and small is None


def test_drzavne_nekretnine_reads_every_lot_and_the_deadline():
    from sources.hr import drzavne_deadline, drzavne_lots
    assert drzavne_deadline("ROK ZA PODNOŠENJE PONUDA: 13. studenog 2026. godine do 12:00") == "2026-11-13"
    html = ("<div class='row'><div><img src='a.jpg'></div><div class='tab-pane_item'>"
            "<h2 class='h4'>ZAGREB - HUGA BADALIĆA 32 (STAN 4519)*</h2><dl>"
            "<dt>Površina (m<sup>2</sup>):</dt><dd>29,84</dd><dt>Početna cijena nekretnine:</dt><dd>41.600,00 EUR</dd>"
            "<dt>Jamčevina:</dt><dd>4.160,00 EUR</dd></dl><p>Opis nekretnine: Jednosobni stan u podrumu."
            " Tehnički podaci: Derutno stanje nekretnine</p></div></div>")
    [lot] = drzavne_lots(html, "https://hr-nekretnine.hr/natjecaji/k-4-26/", "2026-11-13")
    assert lot["id"] == "drzavne_nekretnine:STAN 4519" and lot["price"] == 41600 and lot["area_m2"] == 29.84
    assert lot["concelho"] == "Zagreb" and lot["tipo"] == "stan" and "Derutno" in lot["description"]


def test_ippatrimonio_reads_the_sales_layer():
    from sources.pt import ippatrimonio_listing
    point = {"attributes": {"id": 412, "titulo": "Antiga estação de Ervidel", "localizacao": "Aljustrel, Beja",
                            "preco_base": 18500.0, "data_limite": 1795046400000, "estado": "Em curso",
                            "dbpatrimonio.patrimoniodata.oport_alienacoes.area": 640.0, "link": "https://x/412"},
             "geometry": {"x": -8.08, "y": 37.94}}
    row = ippatrimonio_listing(point)
    assert row["id"] == "ippatrimonio:412" and row["price"] == 18500 and row["area_m2"] == 640
    assert row["concelho"] == "Aljustrel" and row["date_end"] == "2026-11-19" and '"lat": 37.94' in row["raw_json"]
    assert ippatrimonio_listing({"attributes": {**point["attributes"], "data_remocao": 1}}) is None


def test_casea1euro_keeps_recent_town_schemes():
    from datetime import datetime
    from sources.it import casea1euro_listing
    post = {"id": 9, "title": {"rendered": "NARO &#8211; Agrigento &#8211; Sicilia &#8211;"}, "categories": [3],
            "excerpt": {"rendered": "<p>Il Comune di Naro ha avviato il progetto Case a 1 Euro.</p>"},
            "link": "https://casea1euro.it/sicilia/naro/", "modified": "2026-09-23T10:00:00"}
    row = casea1euro_listing(post, now=datetime(2026, 10, 29))
    assert row["id"] == "casea1euro:9" and row["concelho"] == "Naro" and row["district"] == "Agrigento"
    assert row["price"] is None and "Naro" in row["description"]
    assert casea1euro_listing({**post, "modified": "2023-01-01"}, now=datetime(2026, 10, 29)) is None


def test_bvvg_keeps_sales_and_reads_hectares():
    from bs4 import BeautifulSoup
    from sources.de import bvvg_listing, bvvg_m2
    assert bvvg_m2("2,5924 ha") == 25924 and bvvg_m2("850 m²") == 850
    card = ("<div class='property'><h3 class='property-title'><a href='https://www.bvvg.de/objekte/ackerland-in-linthe/'>"
            "Ackerland in Linthe</a></h3><div class='property-subtitle'>Brandenburg, Potsdam-Mittelmark</div>"
            "<div class='dt'>Objektart</div><div class='dd'>Acker und Grünland</div>"
            "<div class='dt'>Objekt-Nr.</div><div class='dd'>BB69-1800-089526</div>"
            "<div class='dt'>Größe</div><div class='dd'>2,5924 ha</div>"
            "<div class='dt'>Orientierungswert (Kauf)</div><div class='dd'>nach Gebot</div>"
            "<p>Ausschreibung endet \n am 03.12.2026, um 08:00 Uhr</p></div>")
    row = bvvg_listing(BeautifulSoup(card, "html.parser").select_one(".property"))
    assert row["id"] == "bvvg:BB69-1800-089526" and row["area_m2"] == 25924 and row["price"] is None
    assert row["concelho"] == "Linthe" and row["district"] == "Potsdam-Mittelmark" and row["date_end"].startswith("2026-12-03")
    lease = card.replace("(Kauf)", "(Pacht)")
    assert bvvg_listing(BeautifulSoup(lease, "html.parser").select_one(".property")) is None


def test_landesimmobilien_reads_a_state_sale_page():
    from sources.de import land_property_listing
    html = ("<html><body><header><img src='/res/logo.png'></header><main><h1>Einfamilienhaus Georg-Mayer-Straße 1 in Roth</h1>"
            "<img src='/mam/header/header.jpg'><img src='/bilder/haus.jpg'>"
            "<p>Termin zur Angebotsabgabe: 13.11.2026</p><p>Grundstück 612 m², Wohnfläche 95 m²</p></main></body></html>")
    row = land_property_listing("BY", "https://www.immobilien.bayern.de/immobilien/verkaeufe/rv5/efh/index.html", html)
    assert row["concelho"] == "Roth" and row["date_end"].startswith("2026-11-13") and row["area_m2"] == 612
    assert row["image_url"].endswith("/bilder/haus.jpg") and row["district"] == "Bayern"
    nrw = html.replace("Einfamilienhaus Georg-Mayer-Straße 1 in Roth", "Verwaltungsgebäude, 45128 Essen Ruhrallee")
    assert land_property_listing("NW", "https://www.blb.nrw.de/x", nrw)["concelho"] == "Essen"


def test_placsp_keeps_open_property_sales_only():
    from sources.es import placsp_listing
    entry = ("<entry><id>https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/17978200</id>"
             '<link href="https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl=Az"/>'
             "<title>Enajenación de dos parcelas municipales, fincas 71342 y 71344</title>"
             "<updated>2026-10-28T15:03:35.518+01:00</updated>"
             "<cbc-place-ext:ContractFolderStatusCode listURI='x'>PUB</cbc-place-ext:ContractFolderStatusCode>"
             "<cac-place-ext:LocatedContractingParty><cac:Party><cac:PartyName><cbc:Name>Alcaldía del Ayuntamiento de Estepona</cbc:Name>"
             "</cac:PartyName></cac:Party><cac-place-ext:ParentLocatedParty><cac:PartyName><cbc:Name>Estepona</cbc:Name></cac:PartyName>"
             "</cac-place-ext:ParentLocatedParty></cac-place-ext:LocatedContractingParty>"
             "<cac:ProcurementProject><cbc:Name>x</cbc:Name><cbc:TypeCode listURI='x'>50</cbc:TypeCode>"
             "<cac:BudgetAmount><cbc:TaxExclusiveAmount currencyID='EUR'>48000.00</cbc:TaxExclusiveAmount></cac:BudgetAmount>"
             "<cbc:ItemClassificationCode listURI='x'>70120000</cbc:ItemClassificationCode>"
             "<cac:RealizedLocation><cbc:CountrySubentity>Málaga</cbc:CountrySubentity></cac:RealizedLocation></cac:ProcurementProject>"
             "<cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>2026-11-20</cbc:EndDate>"
             "</cac:TenderSubmissionDeadlinePeriod></cac:TenderingProcess></entry>")
    row = placsp_listing(entry, today="2026-10-29")
    assert row["id"] == "placsp:17978200" and row["price"] == 48000 and row["concelho"] == "Estepona"
    assert row["district"] == "Málaga" and row["date_end"] == "2026-11-20" and "&amp;" not in row["url"]
    assert placsp_listing(entry.replace(">PUB<", ">EV<"), today="2026-10-29") is None           # under evaluation
    assert placsp_listing(entry.replace(">50<", ">2<"), today="2026-10-29") is None              # a service, not a sale
    assert placsp_listing(entry.replace("Enajenación de dos parcelas", "Aprovechamiento de pastos"), today="2026-10-29") is None
    assert placsp_listing(entry, today="2026-12-01") is None                                     # deadline passed


def test_placsp_drops_game_sales_and_seller_names_as_towns():
    from sources.es import placsp_listing
    base = ("<entry><id>x/1</id><link href='https://x'/><title>{t}</title>"
            "<cbc-place-ext:ContractFolderStatusCode>PUB</cbc-place-ext:ContractFolderStatusCode>"
            "<cac-place-ext:LocatedContractingParty><cac:Party><cac:PartyName><cbc:Name>Junta de Andalucía</cbc:Name>"
            "</cac:PartyName></cac:Party><cac-place-ext:ParentLocatedParty><cac:PartyName><cbc:Name>{town}</cbc:Name>"
            "</cac:PartyName></cac-place-ext:ParentLocatedParty></cac-place-ext:LocatedContractingParty>"
            "<cac:ProcurementProject><cbc:TypeCode>50</cbc:TypeCode>"
            "<cbc:ItemClassificationCode>70120000</cbc:ItemClassificationCode></cac:ProcurementProject></entry>")
    assert placsp_listing(base.format(t="Venta, mediante subasta, de batidas en el coto", town="Granada"),
                          today="2026-10-29") is None
    row = placsp_listing(base.format(t="Enajenación de la parcela 66", town="AÑORA"), today="2026-10-29")
    assert row["concelho"] == "Añora"
    seller = placsp_listing(base.format(t="Enajenación de un solar", town="Junta de Andalucía"), today="2026-10-29")
    assert seller["concelho"] is None


def test_edikte_reads_an_austrian_court_auction():
    import os
    from sources.at import edikt_listing
    html = open(os.path.join(os.path.dirname(__file__), "fixtures", "edikte_notice.html"), encoding="utf-8").read()
    row = edikt_listing("https://edikte.justiz.gv.at/edikte/ex/exedi3.nsf/alldoc/ce33!OpenDocument", html)
    assert row["id"] == "edikte:ce33" and row["tipo"] == "Einfamilienhaus" and row["concelho"] == "Franzhausen"
    assert row["price"] == 80500 and row["area_m2"] == 100 and row["date_end"].startswith("2026-11-09")
    assert "Schätzwert 161,000" in row["description"]
    assert edikt_listing("u/x!OpenDocument", html.replace("Verschiebung - Einfamilienhaus", "Entfall des Termins")) is None


def test_intrum_gr_reads_a_repossession_and_its_kind():
    from scoring import property_kind
    from sources.gr import intrum_listing
    item = {"id": "2480", "commercialization": "sale", "acquired": False,
            "availability_status": {"slug": "available"}, "category": "land", "type": "landPlot",
            "title": [{"locale": "el", "value": "Οικόπεδο"}, {"locale": "en", "value": "Land plot of 1.523 sqm in Kassandra"}],
            "region": [{"locale": "en", "value": "Chalkidiki"}], "locations": ["chalkidiki-kassandra-afitos"],
            "coordinates": [40.1, 23.4], "size": 1523, "price": 8000, "proximity_beach_driving": 1200}
    row = intrum_listing(item)
    assert row["id"] == "intrum_gr:2480" and row["price"] == 8000 and row["area_m2"] == 1523
    assert row["concelho"] == "Afitos" and row["district"] == "Chalkidiki" and '"lat": 40.1' in row["raw_json"]
    assert "beach about 1.2 km" in row["description"] and property_kind(row) == "urban_plot"
    assert intrum_listing({**item, "price": 0})["price"] is None                      # price on request
    assert intrum_listing({**item, "acquired": True}) is None
