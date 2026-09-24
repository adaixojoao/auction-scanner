import json
import os

import cartas

PROPONENTE = {"nome": "Teste Proponente", "nif": "123456789", "morada": "Rua A, 1\n6300-000 Guarda",
              "email": "t@x.pt", "localidade": "Guarda"}


def test_letter_body_has_accents():
    body = cartas._pt_letter_body("carta_fechada", nome="N", nif="1", morada="M", email="e",
                                  title="Moradia", loc="Moura", area="120 m²",
                                  valor="4.000,00", valor_texto="quatro mil euros")
    assert "aquisição do imóvel" in body and "PROPOSTA DE AQUISIÇÃO" in body
    assert "EUR 4.000,00 (quatro mil euros)" in body
    # Helvetica fallback still gets plain ASCII, as before
    assert "aquisicao do imovel" in cartas._safe_latin1(body)


def test_generate_cartas_uses_visible_listings_only(db, add, tmp_path, monkeypatch):
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada"}
    add("citius", "a", title="Moradia sita em Moura", price=30000, area_m2=120, concelho="Moura",
        raw_json=json.dumps(raw))
    add("citius", "b", title="Moradia antiga", price=30000, date_end="2000-01-01T10:00:00",
        raw_json=json.dumps({**raw, "processo": "9/99.9X"}))
    checked = {}
    monkeypatch.setattr(cartas, "check_citius_active",
                        lambda procs: checked.update(procs) or {p: "Em venda" for p in procs})

    out = cartas.generate_cartas(db, None, None, PROPONENTE, str(tmp_path / "cartas"), max_price=100000)
    assert list(checked) == ["165/10.3TBMRA"]          # the expired one never got checked
    assert len(out) == 1 and out[0]["valor"] == "4.000,00"
    assert os.path.exists(out[0]["filepath"])


def test_generate_cartas_needs_a_proponente(db, tmp_path):
    assert cartas.generate_cartas(db, None, None, {}, str(tmp_path)) == []


def test_por_extenso():
    cases = {1: "um euro", 100: "cem euros", 101: "cento e um euros", 1500: "mil e quinhentos euros",
             2345: "dois mil trezentos e quarenta e cinco euros", 2500: "dois mil e quinhentos euros",
             21000: "vinte e um mil euros", 1_000_000: "um milhão de euros",
             4000.5: "quatro mil euros e cinquenta cêntimos"}
    for value, words in cases.items():
        assert cartas.por_extenso(value) == words


def test_bid_formatting_round_trips():
    assert cartas.format_bid(4000) == "4.000,00"
    assert cartas.parse_bid("4.000,00") == 4000
    assert cartas.parse_bid("26000") == 26000


def test_one_builder_for_every_country():
    base = {"id": "x:1", "title": "Casa", "concelho": "Sevilla", "area_m2": 80,
            "raw_json": json.dumps({"processo": "SUB-1", "modalidade": "Negociación directa",
                                    "tribunal": "Juzgado 3"})}
    es = cartas.build_letter({**base, "country": "ES"}, "5.000,00", "", PROPONENTE)
    assert es.kind == "negociacao" and "Estimado/a" in es.text and "EUR 5.000,00" in es.text
    assert es.recipient[-1] == "Juzgado 3"
    pt = cartas.build_letter({**base, "country": "PT"}, "5.000,00", "", PROPONENTE)
    assert pt.text.startswith("Teste Proponente\nNIF: 123456789") and "Assunto:" in pt.text
    assert "cinco mil euros" in pt.text and pt.place_date.startswith("Guarda, ")
    # the old entry point is the same letter
    assert cartas.build_carta_for_country(base, json.loads(base["raw_json"]), "5.000,00", "cinco mil euros",
                                          PROPONENTE, "ES") == es.text


def test_classify_uses_whole_words():
    assert cartas.classify_property("Prédio rústico - Casal do Mato", "pinhal", 800) is None
    assert cartas.classify_property("Casa de habitação", "", 100) == "CASA"
