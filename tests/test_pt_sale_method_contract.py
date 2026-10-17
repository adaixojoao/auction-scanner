"""Citius bidding method requires an explicit supported label."""
import json

import pytest

from letters import build_letter, channel, get_type, letter_types_for, pt_sale_kind, sale_kind


@pytest.mark.parametrize("label, expected", [
    ("Venda por propostas em carta fechada", "carta_fechada"),
    ("PROPOSTA EM CARTA FECHADA", "carta_fechada"),
    (" Carta\nfechada. ", "carta_fechada"),
    ("Venda por negociação particular", "negociacao"),
    ("negociacao_particular", "negociacao"),
    ("Leilão eletrónico", "leilao_eletronico"),
    ("Venda em leilão electrónico", "leilao_eletronico"),
    ("Leilão online", "leilao_eletronico"),
    ("Leilão presencial", "leilao_presencial"),
])
def test_explicit_labels(label, expected):
    assert pt_sale_kind(label) == expected


@pytest.mark.parametrize("label", [
    None, "", "Modalidade por confirmar", "Adjudicação", "Venda direta",
    "Não é carta fechada", "Carta fechada ou negociação particular",
    "Anteriormente carta fechada; agora leilão eletrónico", 42,
])
def test_uncertain_labels_are_unknown(label):
    assert pt_sale_kind(label) == "unknown"


def _item(label):
    return {"id": "citius:fixture", "external_id": "fixture", "source": "citius",
            "country": "PT", "title": "Moradia fixture", "price": 20000,
            "raw_json": json.dumps({"modalidade": label, "processo": "fixture"})}


@pytest.mark.parametrize("label, offer", [
    ("Carta fechada", "pt_carta_fechada"),
    ("Negociação particular", "pt_negociacao"),
    (None, None),
    ("Adjudicação", None),
    ("Leilão eletrónico", None),
    ("Leilão presencial", None),
    ("Carta fechada ou negociação particular", None),
])
def test_citius_exposes_only_the_supported_offer(label, offer):
    item = _item(label)
    types = letter_types_for(item)
    assert [t.key for t in types if t.is_offer] == ([offer] if offer else [])
    assert "pt_info" in [t.key for t in types]
    if offer is None:
        assert get_type("pt_carta_fechada", item) is None
        letter = build_letter(item, "", None, {"nome": "Fixture"})
        assert letter.type_key == "pt_info"
        assert not letter.is_offer
        assert letter.kind == pt_sale_kind(label)


@pytest.mark.parametrize("raw", ["not JSON", "[]", "null", "42"])
def test_malformed_metadata_cannot_enable_a_citius_offer(raw):
    item = {**_item(None), "raw_json": raw}
    assert [t.key for t in letter_types_for(item)] == ["pt_info"]
    assert not build_letter(item, "", None, {"nome": "Fixture"}).is_offer


def test_explicit_electronic_citius_method_uses_online_channel():
    assert channel(_item("Leilão eletrónico")) == "online"


def test_other_country_and_bank_behavior_is_preserved():
    assert sale_kind(None) == "carta_fechada"  # legacy API unchanged
    assert channel({"source": "spain", "country": "ES"}) == "online"
    bank = {"source": "cgd", "country": "PT", "raw_json": "{}"}
    assert [t.key for t in letter_types_for(bank)] == ["pt_banco"]
