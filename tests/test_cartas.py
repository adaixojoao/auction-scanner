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
