"""What the Portuguese research of 2026-10-31 found the scanner getting wrong:
Afurada's court house was a top Invest pick with both debtors living in it, a
court flat with no base value gave no reason for its score, the house was
hinted to a mobile home of the same price, and its two storeys were read as one."""
import json
import sqlite3

import db as D
import scoring
from sources import pt

# Names and addresses invented; the layout is the "ver mais" one Citius returns.
DETAIL = ("<div>Descrição do Bem: Prédio urbano, composto de casa de Rés-do-chão e andar, com a área "
          "coberta de 40m2, sito na Rua Vasco da Gama, nºs 6 e 10, na freguesia de Afurada</div>"
          "<div>Registo: 1 Art.Matricial: 2</div><div>Intervenientes</div>"
          "<div>Interveniente: Executado Nome: Ana Exemplo Morada: Rua Vasco da Gama, 6, 4400-000 Vila Nova de Gaia</div>"
          "<div>Interveniente: Encarregado da Venda Nome: Rui Teste Morada: Rua 25 de Abril, 13, 2625-000 Vialonga</div>")
DESC = ("Prédio urbano, composto de casa de Rés-do-chão e andar, com a área coberta de 40m2, sito na "
        "Rua Vasco da Gama, nºs 6 e 10, na freguesia de Afurada, concelho de Vila Nova de Gaia")


def test_a_debtor_at_the_sale_address_lives_in_it():
    assert pt.citius_debtor_lives_here(DETAIL, DESC) is True


def test_a_debtor_elsewhere_and_no_debtor_at_all():
    elsewhere = DETAIL.replace("Rua Vasco da Gama, 6,", "Urbanização Quinta Nova, Lote 136 - 2ª Esq.ª,")
    assert pt.citius_debtor_lives_here(elsewhere, DESC) is False
    # The same street, another door: no. 8 is not "nºs 6 e 10".
    assert pt.citius_debtor_lives_here(DETAIL.replace("Gama, 6,", "Gama, 8,"), DESC) is False
    assert pt.citius_debtor_lives_here(DETAIL.split("<div>Intervenientes")[0], DESC) is None


def test_the_names_and_addresses_are_not_kept():
    assert "Ana Exemplo" not in pt.citius_detail_text(DETAIL)


def _court_house(**raw):
    return {"source": "citius", "country": "PT", "title": DESC[:120], "description": DESC,
            "tipo": "imovel", "area_m2": 80, "price": 30000, "min_price": 30000,
            "concelho": "Vila Nova de Gaia", "district": "Porto", "raw_json": json.dumps(raw)}


def test_a_house_the_debtor_lives_in_is_occupied():
    lived, reasons = scoring.score_detail(_court_house(debtor_lives_here=True))
    assert "rejected: occupied" in reasons
    assert any("debtor's address in the court record is this house" in r for r in reasons)
    _, invest = scoring.score_detail(_court_house(debtor_lives_here=True), mode="invest")
    assert any("debtor's address" in r for r in invest)
    _, free = scoring.score_detail(_court_house(debtor_lives_here=False))
    assert "rejected: occupied" not in free


def test_an_unpriced_sale_says_why_on_invest():
    item = {**_court_house(), "price": 0, "min_price": 0}
    s, reasons = scoring.score_detail(item, mode="invest")
    assert any(r.startswith("no price published") for r in reasons)
    assert s <= scoring.NO_PRICE_CAP


def test_two_storeys_on_a_footprint_count_twice():
    assert pt._citius_area(DESC) == 80
    flat = "Fracção autónoma correspondente ao 1º andar, com a área coberta de 40m2"
    assert pt._citius_area(flat) == 40
    built = DESC.replace("com a área coberta de 40m2", "área bruta de construção de 95m2")
    assert pt._citius_area(built) == 95


def test_a_house_is_not_hinted_to_a_mobile_home(tmp_path):
    con = sqlite3.connect(tmp_path / "t.db")
    con.row_factory = sqlite3.Row
    D.init_db(con)
    rows = [
        ("citius:a", "citius", "Moradia T2 em Afurada", "Casa de rés-do-chão e andar", 40, 30000),
        ("imovirtual:b", "imovirtual", "Mobil Home T2 | Super moderna", "Mobil Home T2", 36, 29950),
        ("imovirtual:c", "imovirtual", "Moradia T2 em Afurada", "Moradia com quintal", 38, 29900),
    ]
    for lid, src, title, desc, area, price in rows:
        con.execute("INSERT INTO listings (id, source, country, external_id, title, description, tipo, area_m2, "
                    "price, concelho, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lid, src, "PT", lid, title, desc, "moradia", area, price, "Vila Nova de Gaia",
                     "2026-10-31", "2026-10-31"))
    D.mark_duplicates(con)
    hints = dict(con.execute("SELECT id, possible_match FROM listings").fetchall())
    assert "imovirtual:b" not in hints.values() and hints["imovirtual:b"] is None
    assert hints["imovirtual:c"] == "citius:a" or hints["citius:a"] == "imovirtual:c"
