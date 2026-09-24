import json

import pytest

import letters

ME = {"nome": "Teste Proponente", "nif": "123456789", "morada": "Rua A, 1\n6300-000 Guarda",
      "email": "t@x.pt", "telefone": "+351 912 000 000", "localidade": "Guarda"}


def item(source, country, **fields):
    raw = fields.pop("raw", {})
    return {"id": f"{source}:1", "source": source, "country": country, "title": "Imóvel",
            "raw_json": json.dumps(raw), **fields}


def keys(it):
    return [t.key for t in letters.letter_types_for(it)]


def test_letters_follow_how_each_sale_is_bid():
    carta = {"modalidade": "Venda mediante propostas em carta fechada"}
    assert keys(item("citius", "PT", raw=carta)) == ["pt_carta_fechada", "pt_info"]
    assert keys(item("citius", "PT", raw={"modalidade": "Negociação particular"}))[0] == "pt_negociacao"
    assert keys(item("novobanco", "PT")) == ["pt_banco"]
    assert keys(item("eleiloes", "PT")) == ["pt_info"]          # the bid itself goes in online
    assert keys(item("financas", "PT")) == []                   # online only, nothing to write
    assert keys(item("idealista", "PT")) == ["pt_carta_fechada"]  # unknown PT seller: plain offer
    assert keys(item("spain", "ES")) == ["es_info"]
    assert keys(item("sareb", "ES")) == ["es_oferta"]
    assert keys(item("france", "FR")) == ["fr_mandat", "fr_info"]
    assert keys(item("astegiudiziarie", "IT")) == ["generic_offer"]

    assert letters.channel(item("spain", "ES")) == "online"
    assert letters.channel(item("france", "FR")) == "lawyer"
    assert letters.channel(item("citius", "PT")) == "letter"
    assert "subastas.boe.es" in letters.guidance(item("spain", "ES"))
    assert "lawyer" in letters.guidance(item("france", "FR"))


def test_spanish_information_request_goes_to_the_court():
    it = item("spain", "ES", external_id="SUB-JA-2026-123", title="Vivienda en Sevilla",
              concelho="Sevilla", price=90000,
              raw={"autoridad": "Juzgado de Primera Instancia nº 3 de Sevilla",
                   "autoridad_email": "juzgado3@justicia.es", "expediente": "456/2024"})
    letter = letters.build_letter(it, "", "", ME)
    assert letter.type_key == "es_info" and not letter.is_offer
    assert letter.recipient == ["Sr./Sra. Letrado/a de la Administración de Justicia",
                                "Juzgado de Primera Instancia nº 3 de Sevilla"]
    assert letter.to_email == "juzgado3@justicia.es"
    assert "subasta SUB-JA-2026-123, expediente 456/2024" in letter.body
    assert "situación posesoria" in letter.body and "Asunto: Solicitud de información" in letter.text
    month = letter.place_date.split(" de ")[1]
    assert letter.place_date.startswith("Guarda, ") and month in letters._MONTH_NAMES["ES"]
    assert letter.filename == "solicitud-info_SUB-JA-2026-123.pdf"


def test_spanish_offer_to_a_servicer():
    it = item("sareb", "ES", title="Piso en Valencia", price=60000, area_m2=75, url="https://sareb.es/1")
    t = letters.get_type(None, it)
    assert t.public(it)["suggested"] == "45.000,00"                 # 75% of the price
    assert t.public(it)["presets"][0] == "30.000,00"
    letter = letters.build_letter(it, "45.000,00", "", ME)
    assert letter.recipient == ["Sareb", "Departamento comercial"]
    assert "Importe ofertado: 45.000,00 €" in letter.body and "https://sareb.es/1" in letter.body
    assert "Rua A, 1, 6300-000 Guarda" in letter.body                # address on one line


def test_french_letters_to_lawyers():
    it = item("france", "FR", title="Maison d'habitation", concelho="Nîmes", price=40000,
              date_end="2026-10-15T14:00:00", url="https://www.licitor.com/annonce/1.html",
              raw={"tribunal": "Tribunal Judiciaire de Nîmes", "tribunal_ville": "Nîmes",
                   "audience": "2026-10-15T14:00:00", "avocat_nom": "Jean Dupont",
                   "avocat_email": "dupont@avocats.fr"})
    mandat = letters.build_letter(it, "55.000,00", "", ME, "fr_mandat")
    assert mandat.is_offer and mandat.to_email == ""                 # your own lawyer, you choose
    assert mandat.recipient[-1] == "Avocat au Barreau de Nîmes"
    assert "l'audience du 15 octobre 2026 à 14h00" in mandat.body
    assert "montant maximum de 55 000 €" in mandat.body and "Mise à prix : 40 000 €" in mandat.body
    assert mandat.text.count("Objet : ") == 1 and mandat.place_date.startswith("Guarda, le ")

    info = letters.build_letter(it, "", "", ME, "fr_info")
    assert not info.is_offer and info.to_email == "dupont@avocats.fr"
    assert info.recipient[0] == "Maître Jean Dupont" and "cahier des conditions de vente" in info.body
    assert info.filename == "demande-info_france-1.pdf"


def test_portuguese_bank_and_information_letters():
    bank = letters.build_letter(item("novobanco", "PT", title="Apartamento T2", price=80000),
                                "60.000,00", "", ME)
    assert bank.recipient == ["Exmos. Senhores", "Novo Banco", "Departamento de Imóveis"]
    assert "EUR 60.000,00 (sessenta mil euros)" in bank.body

    it = item("citius", "PT", raw={"processo": "165/10.3TBMRA", "tribunal": "Juízo de Moura",
                                   "agente_email": "ae@solic.pt"})
    info = letters.build_letter(it, "", "", ME, "pt_info")
    assert not info.is_offer and info.to_email == "ae@solic.pt"
    assert "processo n.º 165/10.3TBMRA, que corre termos no Juízo de Moura" in info.body
    assert "Telefone: +351 912 000 000" in info.body


def test_a_letter_type_must_fit_the_sale():
    with pytest.raises(ValueError):
        letters.build_letter(item("spain", "ES"), "1.000,00", "", ME, "fr_mandat")


def test_pdfs_without_the_unicode_font(monkeypatch):
    monkeypatch.setattr(letters, "_unicode_fonts", lambda: None)
    it = item("france", "FR", title="Maison à Nîmes", price=40000)
    pdf = letters.letter_pdf(letters.build_letter(it, "50.000,00", "", ME, "fr_mandat"))
    assert pdf.startswith(b"%PDF")
    assert letters._safe_latin1("40 000 €") == "40 000 EUR"


def test_amounts_per_country():
    assert letters.format_amount(40000, "FR") == "40 000 €"
    assert letters.format_amount(40000.5, "FR") == "40 000,50 €"
    assert letters.format_amount(40000, "ES") == "40.000,00"
    assert letters.format_amount(None, "ES") == "____"
