"""How low the law lets a forced sale go (2026-11-18): a Spanish court sale with
a small claim on a large valuation can normally be approved for the claim
(LEC art. 670.4), and a German sale whose value limits fell away can go for the
lowest bid. The owner wanted these found; they rank, they never bid."""
import json

import pytest

import scoring
from sale_floors import legal_floor, rank_share, spain_charges


def _es(claim=None, value=150000, cargas=None, tipo="JUDICIAL EN VIA DE APREMIO", **raw):
    data = {"tipo_subasta": tipo, "tasacion_eur": value, **raw}
    if claim is not None:
        data["claim_eur"] = claim
    if cargas is not None:
        data["cargas"] = cargas
    return {"source": "spain", "country": "ES", "title": "Vivienda en Almería, 90 m²",
            "description": "Vivienda unifamiliar", "tipo": "inmueble", "price": value,
            "raw_json": json.dumps(data)}


def test_a_small_claim_is_the_floor_when_no_charges_survive():
    floor = legal_floor(_es(claim=8000, cargas="No constan"))
    assert floor["basis"] == "claim" and floor["amount"] == 8000
    assert "670.4" in floor["rule"] and "pay the claim" in " ".join(floor["caveats"])
    share, why = rank_share(_es(claim=8000, cargas="No constan"), 150000)
    assert share == pytest.approx(8000 / 150000) and "€8 000 claimed" in why


def test_unknown_charges_rank_only_as_the_half_value_rule():
    item = _es(claim=8000)
    floor = legal_floor(item)
    assert floor["charges_state"] == "unknown"
    assert any("mortgage" in c for c in floor["caveats"])
    share, why = rank_share(item, 150000)
    assert share == pytest.approx(0.5) and "not stated" in why


def test_stated_charges_are_added_to_the_claim():
    share, _ = rank_share(_es(claim=8000, cargas="60.000,00 €"), 150000)
    assert share == pytest.approx(68000 / 150000)
    assert rank_share(_es(claim=8000, cargas="120.000,00 €"), 150000) is None   # no real gap left
    assert spain_charges({"cargas": "Sin cargas"}) == ("none", 0.0)
    assert spain_charges({"cargas": "Ver edicto"}) == ("unknown", 0.0)


def test_a_large_claim_leaves_the_seventy_percent_rule():
    floor = legal_floor(_es(claim=120000))
    assert floor["basis"] == "outright" and floor["amount"] == 105000
    assert rank_share(_es(claim=120000), 150000) is None        # shown, not ranked


def test_a_claim_over_several_lots_is_not_this_lots_floor():
    floor = legal_floor(_es(claim=8000, cargas="No", lotes=3))
    assert floor["basis"] == "outright" and "several lots" in floor["caveats"][0]


def test_tax_and_social_security_sales_on_the_boe_are_not_lec_670():
    assert legal_floor(_es(claim=8000, tipo="ADMINISTRATIVA AEAT")) is None


def test_a_live_bid_is_what_has_to_be_beaten():
    item = {**_es(claim=8000, cargas="No"), "current_bid": 60000}
    assert rank_share(item, 60000) is None


def test_german_limits_lifted_and_first_hearing():
    first = {"source": "zvg", "price": 100000, "title": "Einfamilienhaus", "description": "Verkehrswert: 100.000 €"}
    assert legal_floor(first)["amount"] == 70000
    assert rank_share(first, 100000) is None                    # the usual rule ranks nothing
    lifted = {**first, "description": "Einfamilienhaus. Die Wertgrenzen sind weggefallen."}
    floor = legal_floor(lifted)
    assert floor["basis"] == "limits_lifted" and floor["amount"] is None
    assert rank_share(lifted, 100000)[0] == 0.5


def test_portugal_court_and_italy_use_the_stated_minimum_when_there_is_one():
    assert rank_share({"source": "eleiloes", "price": 100000, "min_price": 85000}, 85000) is None
    assert legal_floor({"source": "citius", "price": 100000})["amount"] == 85000
    assert rank_share({"source": "citius", "price": 100000}, 100000) is None
    assert legal_floor({"source": "pvp_giustizia", "price": 100000})["amount"] == 75000
    assert "quarter" in legal_floor({"source": "pvp_giustizia", "price": 100000})["next_round"]
    assert "20%" in legal_floor({"source": "financas", "price": 30000})["next_round"]


def test_other_countries_name_the_rule_without_a_lower_figure():
    for source in ("france", "vench", "poland", "fina", "edikte", "netherlands"):
        floor = legal_floor({"source": source, "price": 50000})
        assert floor and floor["amount"] is None and floor["rule"], source
        assert rank_share({"source": source, "price": 50000}, 50000) is None
    assert legal_floor({"source": "imovirtual", "price": 50000}) is None


def test_the_floor_ranks_a_small_claim_above_the_same_sale_without_one():
    low, reasons = scoring.score(_es(claim=8000, cargas="No constan"), mode="invest")
    plain, _ = scoring.score(_es(), mode="invest")
    assert low > plain
    assert any("claimed" in r for r in reasons)


def test_the_floor_never_sets_a_bid():
    import bidcap
    import inspect
    assert "sale_floors" not in inspect.getsource(bidcap)


def test_the_ai_check_is_told_the_same_rule():
    for mode in ("home", "both", "invest", "land"):
        assert "amount claimed" in scoring.buyer_priorities({}, mode)


def test_listing_details_show_the_claim_and_the_rule():
    from listing_info import facts
    shown = {f["label"]: f["value"] for f in facts(_es(claim=8000, cargas="No constan"))}
    assert shown["Amount claimed"] == "€8,000"
    assert "670.4" in shown["How low it can go"]
    assert "pay the claim" in shown["Before you count on it"]


BOE_GENERAL_CLAIM = """<table><tr><th>Tipo de subasta</th><td>JUDICIAL EN VIA DE APREMIO</td></tr>
  <tr><th>Cantidad reclamada</th><td>8.214,55 €</td></tr>
  <tr><th>Valor subasta</th><td>150.000,00 €</td></tr>
  <tr><th>Tasación</th><td>150.000,00 €</td></tr></table>"""


def test_the_boe_general_tab_gives_the_claim_and_valuation():
    from sources.es import spain_details
    _, extra = spain_details({"general": BOE_GENERAL_CLAIM})
    assert extra["claim_eur"] == 8214.55 and extra["tasacion_eur"] == 150000


def test_sales_opened_before_the_claim_was_read_are_opened_once_more(db, add, fake_http):
    from conftest import FakeResponse
    from sources.es import enrich_spain_details
    add("spain", "SUB-OLD", "ES", title="Subasta SUB-OLD", tipo="inmueble", date_end="2099-01-01",
        raw_json=json.dumps({"detail_checked": "2026-09-01"}))
    session = fake_http(lambda m, url, kw: FakeResponse(BOE_GENERAL_CLAIM))
    enrich_spain_details(db, session)
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='spain:SUB-OLD'").fetchone()[0])
    assert raw["claim_eur"] == 8214.55
    calls = len(session.calls)
    enrich_spain_details(db, session)
    assert len(session.calls) == calls          # read once, not every scan
