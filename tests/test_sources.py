import json
import urllib.parse

import scraper
from common import COUNTRY_NAMES
from conftest import FakeResponse
from sources import REGISTRY, load_all, run_source, sources_for
from sources._cards import CardSite, listing_id_from_url, scrape_cards


def test_registry_is_complete():
    load_all()
    assert len(REGISTRY) == 46
    for s in REGISTRY.values():
        assert s.country in COUNTRY_NAMES or s.country == "EU", s
        assert s.description, f"{s.name} needs a docstring"
    optional = {s.name for s in REGISTRY.values() if not s.default}
    # closed / login-only / already covered by another source: not in default scans
    # (and, since Sept 2026, the ones behind a bot wall or a broken certificate)
    assert optional == {"idealista", "courtbid", "novobanco", "aeat",
                        "sareb", "gobidreal", "biddit", "anaf", "cyprus", "greece",
                        "veilingbiljet"}                  # the same lots as openbareverkoop.nl
    # every country has at least one default source, except those whose only
    # source is walled off; PT runs first
    # Green-Acres covers FR, PT, ES and IT from one source, filed under "EU"
    assert {s.country for s in sources_for(None)} == set(COUNTRY_NAMES) - {"BE", "CY", "GR", "RO"} | {"EU"}
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
    assert "occupied/tenanted" in item["reasons"]
    assert enrich_spain_details(db, session) == 0 and len(session.calls) == 3   # checked once


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

    upsert_listing(db, make_listing("citius", "bad", title="Prédio sito na R. das Flores", concelho="R"))
    upsert_listing(db, make_listing("citius", "street", title="Prédio", concelho="Rua do Canto das Naves"))
    upsert_listing(db, make_listing("citius", "good", title="Prédio", concelho="Moura"))
    for eid in ("bad", "street", "good"):
        _clear_implausible_place(db, {"id": f"citius:{eid}", "concelho": None})
    places = dict(db.execute("SELECT id, concelho FROM listings WHERE source='citius'"))
    assert places["citius:bad"] is None
    assert places["citius:street"] is None
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
    session = fake_http(lambda m, url, kw: FakeResponse(json_data={"data": [ALISEDA_ITEM], "last_page": 1}))
    assert scrape_aliseda(db, max_price=50000) == 2        # the same fake item as a home and as land
    assert {c[2]["params"]["precio"] for c in session.calls} == {"0-50000"}
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
    session = fake_http(lambda m, url, kw: FakeResponse(json_data={"totalResultados": "1",
                                                                    "minifichas": [ALTAMIRA_CARD]}))
    assert scrape_altamira(db, max_price=50000) == 2               # homes and land (same fake card)
    assert session.calls[0][2]["json"]["filtros"]["precioMaximo"] == 50000
    item = load_listings(db, include_hidden=True)[0]
    assert geo.position(item)["precision"] == "street"


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
    # two pages of one ad each, then done
    pages = {1: _fotocasa_html([FOTOCASA_AD], 2), 2: _fotocasa_html([{**FOTOCASA_AD, "id": 2}], 2)}
    session = fake_http(lambda m, url, kw: FakeResponse(pages[2 if url.endswith("/l/2") else 1]))
    assert scrape_fotocasa(db, max_price=50000) == 4 * len(FOTOCASA_PROVINCES)
    land = [c for c in session.calls if "/terrenos/" in c[1]]
    assert all(c[2]["params"] == {"maxPrice": 50000, "minSurface": 10000} for c in land)


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
    session = fake_http(lambda m, url, kw: FakeResponse(json_data={"total": 1, "realEstateAds": [BIENICI_AD]}))
    assert scrape_bienici(db, max_price=50000) == 4          # houses in 3 price bands, then land
    sent = [json.loads(c[2]["params"]["filters"]) for c in session.calls]
    assert [(f["minPrice"], f["maxPrice"]) for f in sent[:3]] == [(0, 25000), (25000, 37500), (37500, 50000)]
    assert sent[3]["propertyType"] == ["terrain"] and sent[3]["minArea"] == 10000
