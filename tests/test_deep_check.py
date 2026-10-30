"""deep_check reads the file behind a lead. Each case is one read by hand on
2026-10-31 and what it turned out to be."""
import pytest

import perizia
from deep_check import debtor_at_property, text_findings, verdict


def home(**fields):
    return {"title": "Moradia", "tipo": "moradia", "country": "PT", "source": "eleiloes", "raw_json": "{}", **fields}


@pytest.mark.parametrize("desc,level,what", [
    ("Observações: ***Imóvel encontra-se habitado; ***Não foi autorizado o registo fotográfico do interior do "
     "imóvel, encontrando-se em condições deficitárias", "caution", "lived in"),
    ("A ocupante do bem imóvel não permitiu o registo fotográfico do interior. Nas traseiras do imóvel existem "
     "anexos que não constam na matriz urbana", "caution", "not on the registry"),
    ("Prédio urbano ... constituído por casa de rés-de-chão que se destina a serviços e quintal", "reject", "not a home"),
    ("Moradia de abegoaria e garagem com Área Total de 100,00 m²", "reject", "not a home"),
])
def test_the_notice_says_what_the_price_hides(desc, level, what):
    found = text_findings(home(description=desc))
    assert any(lv == level and what in reason for lv, reason in found)


def test_a_clean_notice_finds_nothing():
    found = text_findings(home(description="Moradia T3 em bom estado, livre de pessoas e bens."))
    assert found == [] and verdict(found) == "clean"


def test_a_debtor_registered_at_the_property_is_probably_living_there():
    """"Travessa Quinta dos Cubos" on the sale, "Travessa Quinta Cubos" on the debtor."""
    desc = "Fracção BL, 3º andar esquerdo, sito na Travessa Quinta dos Cubos, nº 184, Oliveira do Douro"
    detail = ("Intervenientes | Interveniente: | Executado | Nome: | X | Morada: | "
              "Travessa Quinta Cubos, 184 - 3º E, 4430-499 Vila Nova de Gaia |")
    assert debtor_at_property(detail, desc)
    elsewhere = detail.replace("Travessa Quinta Cubos, 184", "Rua do Almada, 12")
    assert not debtor_at_property(elsewhere, desc)


def test_owners_still_living_there_is_read_from_the_court_report():
    body = ("Circa l’occupazione, si evidenzia che allo stato l’immobile è occupato dagli attuali proprietari. "
            + "Descrizione dell'immobile e dello stato dei luoghi. " * 20)
    assert perizia.read(body).get("occupied")
    other = ("lateralmente disimpegno condominiale e altra unità immobiliare occupata dallo stesso esecutato. "
             + "Descrizione dell'immobile e dello stato dei luoghi. " * 20)
    assert not perizia.read(other).get("occupied")


def test_the_worst_finding_sets_the_verdict():
    assert verdict([("info", "a"), ("caution", "b")]) == "caution"
    assert verdict([("caution", "b"), ("reject", "c")]) == "reject"
