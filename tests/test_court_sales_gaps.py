"""Court sales the scanner read badly (2026-10-31): the municipality written as
a registry office it did not recognise, the sale agent the court names but the
scanner never kept, the court's own e-mail, and the debtor still living in the
house in the words of a notice anywhere in Europe."""
import json

import courts
import geo
import scoring
from sources import pt


import pytest

import prices

TOWNS = ["Barreiro", "Amadora", "Nordeste", "Sesimbra", "Gouveia", "Porto de Mós", "Vimioso", "Beja", "Porto"]


@pytest.fixture(autouse=True)
def municipalities(monkeypatch):
    """The scanner checks a place against INE's 308; these are the ones used here."""
    monkeypatch.setattr(prices, "pt_table",
                        lambda path=None: {prices.place_key(t): (1000, "test", None, None) for t in TOWNS})


def _pt(text):
    return {"country": "PT", "description": text}


def test_the_registry_or_tax_office_names_the_municipality():
    assert geo.municipality(_pt("descrito na C.R.P do Barreiro sob o nº10541")) == "Barreiro"
    assert geo.municipality(_pt("na 1ªConservatória Predial da Amadora sob o nº 00774")) == "Amadora"
    assert geo.municipality(_pt("descrito na conservatória de Registo Predial de Nordeste sob o")) == "Nordeste"
    assert geo.municipality(_pt("descritas na conservatória do Registo Predial de Sesimbra sobre a ficha")) == "Sesimbra"
    assert geo.municipality(_pt("inscrito no Serviço de Finanças de Gouveia sob o artigo")) == "Gouveia"
    assert geo.municipality(_pt("Conservatória do Registo Predial de Porto de Mós - freguesia de Pedreiras")) == "Porto de Mós"


def test_the_place_after_sito_counts_only_as_a_whole_piece():
    assert geo.municipality(_pt("sito em Redondelha, Vimioso, com a àrea total")) == "Vimioso"
    assert geo.municipality(_pt("sito na Rua do Moinho, nº 14 em Trigaches - Beja. Descrição")) == "Beja"
    # A place called Porto in Oliveira do Bairro is not the city.
    assert geo.municipality(_pt("Prédio rústico situado em Porto Composição e confrontações: Vinha")) is None


# Names and addresses invented; the layout is the "ver mais" one Citius returns.
DETAIL = ("<div>Descrição do Bem: Fracção autónoma R, 4º andar B</div><div>Intervenientes</div>"
          "<div>Interveniente: Executado Nome: Ana Exemplo Morada: Rua Um, 1, 1000-000 Lisboa</div>"
          "<div>Interveniente: Fiel Depositário Nome: Rui Teste Morada: Rua Dois, 2, 2000-000 Odivelas</div>"
          "<div>Interveniente: Encarregado da Venda Nome: Joana Agente Morada: Rua 25 de Abril, 13, 2625-000 Vialonga</div>")


def test_the_sale_agent_is_kept_and_no_one_else():
    agent = pt.citius_sale_agent(DETAIL)
    assert agent == {"agente_papel": "Encarregado da Venda", "agente_nome": "Joana Agente",
                     "agente_morada": "Rua 25 de Abril, 13, 2625-000 Vialonga"}
    assert pt.citius_sale_agent(DETAIL.replace("Encarregado da Venda", "Exequente")) == {}


def test_the_court_email_is_the_published_one(tmp_path):
    table = tmp_path / "emails.csv"
    table.write_text("comarca,juizo,email,source\n"
                     "lisboa,Juízo de Execução de Almada,almada.judicial@tribunais.org.pt,x\n", encoding="utf-8")
    courts._table.cache_clear()
    sale = {"raw_json": json.dumps({"processo": "2270/09.0TBBRR, Juízo de Execução de Almada - Juiz 2",
                                    "tribunal": "Almada - Tribunal Judicial da Comarca de Lisboa"})}
    assert courts.court_email(sale, path=str(table)) == "almada.judicial@tribunais.org.pt"
    other = {"raw_json": json.dumps({"processo": "1/24.0T8LRS, Juízo Local Cível de Loures",
                                     "tribunal": "Loures - Tribunal Judicial da Comarca de Lisboa Norte"})}
    assert courts.court_email(other, path=str(table)) is None      # not listed: no address made up


def _home(source, country, text):
    return {"source": source, "country": country, "title": "Vivienda", "description": text,
            "tipo": "vivienda", "area_m2": 80, "price": 40000, "raw_json": "{}"}


def test_a_notice_that_says_the_debtor_lives_there_is_occupied_everywhere():
    for source, country, text in (
            ("eleiloes", "PT", "Observações: O imóvel está ocupado pelo executado."),
            ("spain", "ES", "Piso en Los Alcázares (vivienda habitual de los demandados)"),
            ("pvp_giustizia", "IT", "Appartamento occupato dal debitore esecutato"),
            ("france", "FR", "Maison occupée par la partie saisie"),
            ("edikte", "AT", "Büro und Werkstatt sind vermietet, Wohnung der Eigentümerin")):
        item = _home(source, country, text)
        assert scoring._occupation(item) == "occupied", source
        assert scoring._main_home_note(item), source


def test_a_field_answered_no_is_not_the_debtors_home():
    item = _home("spain", "ES", "Vivienda habitual del ejecutado: No. Ocupantes: No consta")
    assert not scoring._debtor_lives_here(item)


def test_a_german_valuation_that_says_rented_is_occupied():
    assert scoring.has_term("Die Wohnung ist zum Wertermittlungsstichtag vermietet", scoring.OCCUPANCY_PATTERNS)
    assert not scoring.has_term("Die Wohnung ist nicht vermietet", scoring.OCCUPANCY_PATTERNS)
    assert scoring.has_term("teilweise sanierungsbedürftig, leer stehend", scoring.VACANT_PATTERNS, negations=False)


def test_the_court_directory_page_is_read_block_by_block():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "update_court_emails", os.path.join(os.path.dirname(__file__), "..", "scripts", "update_court_emails.py"))
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    agueda = ("Home / A Comarca / Núcleos/Juízos / Águeda Juízo de Execução de Águeda Competência Territorial: "
              "Águeda, Anadia Morada: R. Fernando Caldeira - 3750-147 Águeda Contactos: Telefone: 234610310 "
              "Email: agueda.execucao@tribunais.org.pt Juízo do Trabalho de Águeda Morada: R. X Email: "
              "agueda.trabalho@tribunais.org.pt CONTACTOS Sede")
    assert script.juizos(agueda) == [("Juízo de Execução de Águeda", "agueda.execucao@tribunais.org.pt"),
                                     ("Juízo do Trabalho de Águeda", "agueda.trabalho@tribunais.org.pt")]
    loures = ("Home / A Comarca / Núcleos / Juízos / Loures Unidade Central Contactos: Email: loures.judicial@tribunais.org.pt "
              "Juízo Central Cível Competência Territorial: comarca de Lisboa Norte Morada: Palácio da Justiça "
              "Contactos: Email: loures.judicial@tribunais.org.pt")
    assert script.juizos(loures) == [("Juízo Central Cível de Loures", "loures.judicial@tribunais.org.pt")]
