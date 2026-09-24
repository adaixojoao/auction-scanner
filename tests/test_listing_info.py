"""The listing details panel: facts, related sites and the Citius finder (listing_info.py)."""
import json

import pytest

import dashboard
import listing_info
from conftest import FakeResponse

CITIUS_RAW = {"processo": "3841/03.3TBBRR, Juízo de Execução de Almada - Juiz 1",
              "tribunal": "Almada - Tribunal Judicial da Comarca de Lisboa",
              "modalidade": "Venda mediante proposta em carta fechada", "estado": "Em venda"}


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_citius_guide_names_the_court_and_the_case():
    item = {"source": "citius", "raw_json": json.dumps(CITIUS_RAW), "url": listing_info.CITIUS_SEARCH}
    guide = listing_info.how_to_find(item)
    copies = [s.get("copy") for s in guide["steps"] if s.get("copy")]
    assert copies == ["Almada - Tribunal Judicial da Comarca de Lisboa", "3841/03.3TBBRR"]
    assert any("Ignorar Datas" in s["text"] for s in guide["steps"])
    assert any("carta fechada" in s["text"] for s in guide["steps"])
    assert listing_info.how_to_find({"source": "eleiloes", "url": "https://e-leiloes.pt/evento/X"}) is None


def test_a_citius_case_links_to_the_same_sale_on_eleiloes(db, add):
    add("citius", "384103", title="Fracção B", raw_json=json.dumps(CITIUS_RAW))
    add("eleiloes", "LO9", title="Apartamento no Barreiro", url="https://e-leiloes.pt/evento/LO9",
        raw_json=json.dumps({"processo": "3841/03.3TBBRR", "agente_email": "a@exemplo.pt"}))
    citius = {"source": "citius", "raw_json": json.dumps(CITIUS_RAW)}
    links = listing_info.related(db, citius)
    assert links[0]["url"] == "https://e-leiloes.pt/evento/LO9" and "e-leilões" in links[0]["label"]


def test_facts_and_links_from_spanish_and_bank_data():
    spain = {"source": "spain", "price": 90000, "min_price": None, "raw_json": json.dumps({
        "expediente": "1234 0000 05 0456 24", "autoridad": "JUZGADO Nº 3 DE SEVILLA",
        "autoridad_email": "juzgado@example.es", "deposito": 4500, "situacion_posesoria": "Ocupado",
        "referencia_catastral": "9872023VH5797S0001WX"})}
    labels = {f["label"]: f["value"] for f in listing_info.facts(spain)}
    assert labels["Court"] == "JUZGADO Nº 3 DE SEVILLA" and labels["Deposit"] == "€4,500"
    assert labels["Occupancy"] == "Ocupado" and labels["Court e-mail"] == "juzgado@example.es"
    assert "rc1=9872023&rc2=VH5797S" in listing_info.catastro_url(spain)

    bank = {"source": "imobancos", "url": "https://banco.example.pt/1", "concelho": "Amadora",
            "raw_json": json.dumps({"site_name": "Montepio", "prop_bedrooms": 3,
                                    "prop_latitude": "38.75", "prop_longitude": "-9.23"})}
    labels = {f["label"]: f["value"] for f in listing_info.facts(bank)}
    assert labels["Seller"] == "Montepio" and labels["Bedrooms"] == "3"
    assert listing_info.map_url(bank).endswith("query=38.750000%2C-9.230000")


def test_scraped_links_are_not_trusted():
    item = {"source": "imobancos", "raw_json": json.dumps({"original_url": "javascript:alert(1)",
                                                           "publicUrl": "data:text/html,x"})}
    assert all(r["url"].startswith("https://") for r in listing_info.related(None, item))


def test_detail_api(client, db, add):
    add("citius", "384103", title="Fracção B <b>", price=0, raw_json=json.dumps(CITIUS_RAW),
        url=listing_info.CITIUS_SEARCH, description="Fracção autónoma B, 1º andar")
    d = client.get("/api/listing?id=citius:384103").get_json()
    assert d["title"] == "Fracção B <b>" and d["how_to_find"]["site"] == "Citius"   # escaped by the page
    assert {"label": "Case", "value": "3841/03.3TBBRR"} in d["facts"]
    assert "raw_json" not in d
    assert client.get("/api/listing?id=nope").status_code == 404


def test_telegram_alert_says_where_to_find_a_citius_sale():
    from telegram_alert import format_listing
    msg = format_listing({"source": "citius", "title": "Fracção", "score": 90, "reasons": [],
                          "raw_json": json.dumps(CITIUS_RAW), "url": listing_info.CITIUS_SEARCH})
    assert "Tribunal <b>Almada - Tribunal Judicial da Comarca de Lisboa</b>" in msg
    assert "Ctrl+F <code>3841/03.3TBBRR</code>" in msg


def test_ruins_marked_in_the_energy_field_score_as_ruins():
    from scoring import score
    from sources.pt import imobancos_listing
    hit = {"id": 7, "available": True, "prop_purpose": "Comprar", "prop_price": 8100,
           "prop_title": "Moradia T1", "prop_type": "Moradia", "prop_area": "50.00",
           "prop_description": "Moradia no coração do Centro Histórico de Nisa.",
           "prop_energy_rating": "Ruína", "site_name": "Caixa Imobiliário"}
    row = imobancos_listing(hit, 100000)
    sc, reasons = score({**row, "country": "PT"})
    assert "needs heavy work (ruin / full rebuild)" in reasons and sc <= 40
    assert "Ruína" not in imobancos_listing({**hit, "prop_energy_rating": "C"}, 100000)["description"]
    assert listing_info._energy("UNPARSED- isento") == "Isento" and listing_info._energy("detail-item") is None


def test_lots_of_the_same_case_are_listed_together(db, add):
    add("citius", "366104TBVLN", title="Prédio urbano, casa de um pavimento, com área de 142 m2", price=7500,
        raw_json=json.dumps({"processo": "366/10.4TBVLN, Juízo de Valença"}))
    add("citius", "366104TBVLN-2", title="Prédio Rústico de cultivo com área de 2760 m2", price=500, area_m2=2760,
        raw_json=json.dumps({"processo": "366/10.4TBVLN, Juízo de Valença"}))
    add("citius", "999", title="Outro processo", price=100, raw_json=json.dumps({"processo": "9/99.9XXX"}))
    house = {"id": "citius:366104TBVLN", "source": "citius",
             "raw_json": json.dumps({"processo": "366/10.4TBVLN, Juízo de Valença"})}
    lots = listing_info.same_case_lots(db, house)
    assert [(lot["id"], lot["price"], lot["area"]) for lot in lots] == [("citius:366104TBVLN-2", 500, 2760)]


# ─── Official records ───────────────────────────────────────────────

def test_portuguese_land_register_numbers_from_the_sale_text():
    text = ("Prédio urbano sito no Lugar do Forno de Cima, composto de casa de rés-do-chão e andar; descrito na "
            "Conservatória do Registo Predial de Penafiel sob o nº 01469/11062003 e inscrito na matriz predial "
            "urbana sob o artigo nº 158.")
    rec = listing_info.official_records({"country": "PT", "source": "citius", "freguesia": "Rio de Moínhos",
                                         "description": text, "raw_json": "{}"})
    values = {c["label"].split(" (")[0]: c["value"] for c in rec["copy"]}
    assert values == {"Description no.": "01469/11062003", "Conservatória / concelho": "Penafiel",
                      "Tax article": "158"}
    assert rec["links"][0]["url"] == "https://www.predialonline.pt/PredialOnline/"
    assert any("€15" in s["text"] for s in rec["steps"]) and any("Rio de Moínhos" in s["text"] for s in rec["steps"])
    assert listing_info.official_records({"country": "PT", "description": "Moradia T2", "raw_json": "{}"}) is None


def test_eleiloes_land_register_entries():
    from sources.pt import land_register_entries
    entries = land_register_entries({"descPredial": [{"numero": 1231, "fracao": "", "distritoDesc": "14 - Santarém",
        "concelhoDesc": "13 - Mação", "freguesiaDesc": "08 - Penhascoso",
        "artigos": [{"numero": "67 - Secção 1S", "tipo": "rustica"}]}]})
    assert entries == [{"descricao": "1231", "fracao": None, "freguesia": "Penhascoso", "concelho": "Mação",
                        "artigos": [{"numero": "67 - Secção 1S", "tipo": "rustica"}]}]
    rec = listing_info.official_records({"country": "PT", "source": "eleiloes",
                                         "raw_json": json.dumps({"registo_predial": entries})})
    assert {"label": "Parish (freguesia)", "value": "Penhascoso"} in rec["copy"]


# Trimmed from Catastro's real answer for 9872023VH5797S0001WX (Sept 2026).
CATASTRO_ANSWER = {"consulta_dnprcResult": {"control": {"cudnp": 1, "cucons": 3}, "bico": {
    "bi": {"idbi": {"rc": {"pc1": "9872023", "pc2": "VH5797S"}}, "ldt": "CL GLORIA 51 13730 SANTA CRUZ DE MUDELA (CIUDAD REAL)",
           "debi": {"luso": "Residencial", "sfc": "308", "ant": "1980"}},
    "finca": {"dff": {"ss": "397"}},
    "lcons": [{"lcd": "VIVIENDA UNIFAMILIAR", "dfcons": {"stl": "109"}},
              {"lcd": "ANEJOS DE VIVIENDA Y LOCALES", "dfcons": {"stl": "187"}}]}}}


def test_catastro_answer_is_read_and_compared(db, add, fake_http):
    from sources.es import fetch_catastro, parse_catastro
    data = parse_catastro(CATASTRO_ANSWER)
    assert data["built_m2"] == 308 and data["plot_m2"] == 397 and data["year"] == 1980 and data["use"] == "Residencial"
    assert data["units"][0] == {"use": "VIVIENDA UNIFAMILIAR", "m2": 109}
    assert parse_catastro({"consulta_dnprcResult": {"lerr": [{"err": {"des": "NO EXISTE"}}]}}) is None

    add("spain", "SUB-1", "ES", title="Vivienda", area_m2=85,
        raw_json=json.dumps({"referencia_catastral": "9872023VH5797S0001WX"}))
    session = fake_http(lambda m, url, kw: FakeResponse(json_data=CATASTRO_ANSWER))
    assert fetch_catastro(db, session) == 1
    item = dict(db.execute("SELECT * FROM listings WHERE id='spain:SUB-1'").fetchone())
    item["country"] = "ES"
    rec = listing_info.official_records(item)
    assert "Catastro says 308 m² built" in rec["check"] and "differ" in rec["check"]
    assert {"label": "Year built", "value": "1980"} in rec["facts"]            # a year, not "1 980"
    assert fetch_catastro(db, session) == 0 and len(session.calls) == 1        # read once


def test_catastro_unreachable_is_retried(db, add, fake_http):
    import requests
    from sources.es import fetch_catastro

    def refuse(m, url, kw):
        raise requests.ConnectionError("Connection reset by peer")
    add("spain", "SUB-2", "ES", title="Vivienda", raw_json=json.dumps({"referencia_catastral": "9872023VH5797S0001WX"}))
    session = fake_http(refuse)
    assert fetch_catastro(db, session) == 0
    assert "catastro_checked" not in db.execute("SELECT raw_json FROM listings WHERE id='spain:SUB-2'").fetchone()[0]
