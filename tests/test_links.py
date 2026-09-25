"""A Citius sale and its e-leilões auction are one sale (links.py)."""
import json

from db import load_listings, mark_duplicates
from links import link_court_sales


def _citius(add, eid, proc, price, concelho, modalidade="Venda em leilão eletrónico"):
    return add("citius", eid, title="Prédio urbano, casa baixa com quintal", price=price, min_price=price,
               concelho=concelho, url="https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx",
               description=f"Casa baixa telhada com quintal, 85 m2. Modalidade: {modalidade}",
               raw_json=json.dumps({"processo": proc, "modalidade": modalidade}))


def _eleiloes(add, eid, price, concelho, processo=None):
    raw = {"valorBase": price}
    if processo:
        raw["processo"] = processo
    return add("eleiloes", eid, title="Moradia", price=price, min_price=price * 0.85, current_bid=price * 0.5,
               concelho=concelho, url=f"https://e-leiloes.pt/evento/NP{eid}", date_end="2099-10-20T14:30:00",
               image_url=f"https://e-leiloes.pt/api/img/{eid}.jpg", raw_json=json.dumps(raw))


def test_joined_by_the_case_number(db, add):
    _citius(add, "c1", "189/10.0TBVFC, Juízo de Vila Franca do Campo", 23880, "Vila Franca do Campo",
            modalidade="Venda mediante proposta em carta fechada")
    _eleiloes(add, "1", 23880, "Vila Franca do Campo", processo="189/10.0TBVFC")
    assert link_court_sales(db) == 1
    row = db.execute("SELECT * FROM listings WHERE id='citius:c1'").fetchone()
    assert row["url"] == "https://e-leiloes.pt/evento/NP1" and row["date_end"] == "2099-10-20T14:30:00"
    assert row["min_price"] == 23880 * 0.85 and row["current_bid"] == 23880 * 0.5
    assert row["image_url"].endswith("1.jpg") and "casa baixa" in row["description"].lower()
    # Listed once: the e-leilões copy is the duplicate.
    mark_duplicates(db)
    ids = [it["id"] for it in load_listings(db, apply_min_score=False)]
    assert "citius:c1" in ids and "eleiloes:1" not in ids


def test_joined_by_place_and_base_value_only_for_an_e_auction_and_only_when_unique(db, add):
    _citius(add, "c2", "1/20.0T8XYZ", 15000, "Moura")
    _eleiloes(add, "2", 15000, "MOURA")
    _citius(add, "c3", "2/20.0T8XYZ", 15000, "Moura", modalidade="Venda por negociação particular")
    _citius(add, "c4", "3/20.0T8XYZ", 9000, "Beja")
    _eleiloes(add, "4", 9000, "Beja")
    _eleiloes(add, "5", 9000, "Beja")                   # two candidates: no guess
    assert link_court_sales(db) == 1
    linked = {r[0]: json.loads(r[1]).get("eleiloes_id") for r in
              db.execute("SELECT id, raw_json FROM listings WHERE source='citius'")}
    assert linked == {"citius:c2": "eleiloes:2", "citius:c3": None, "citius:c4": None}
