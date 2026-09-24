from datetime import datetime, timedelta, timezone

from scoring import categorize, market_value_estimate, score


def item(**kw):
    base = {"source": "eleiloes", "country": "PT", "title": "", "description": ""}
    base.update(kw)
    return base


def test_fractional_share_with_spaces_scores_zero():
    # This listing was #2 in the old report with a score of 95.
    sc, reasons = score(item(title="1 / 2 (Um Meio) Prédio Urbano, casas baixas",
                             price=30900, current_bid=15450))
    assert sc == 0 and "fractional" in reasons[0]


def test_dates_in_titles_are_not_fractions():
    sc, _ = score(item(title="Moradia penhorada em 11/2023", price=20000))
    assert sc > 0


def test_vacant_is_not_occupied():
    vacant, r1 = score(item(title="Moradia", description="Imóvel devoluto e desocupado", price=20000))
    occupied, r2 = score(item(title="Moradia", description="Imóvel arrendado", price=20000))
    negated, r3 = score(item(title="Moradia", description="O imóvel não se encontra arrendado", price=20000))
    assert "vacant (devoluto)" in r1 and "occupied/tenanted" not in r1
    assert "occupied/tenanted" in r2
    assert "occupied/tenanted" not in r3
    assert vacant > negated > occupied


def test_casal_is_not_a_house():
    _, reasons = score(item(title="Prédio rústico denominado Fontainhas - Casal do Mato",
                            source="citius"))
    assert "full dwelling" not in reasons


def test_no_minimum_bonus_counted_once():
    _, reasons = score(item(title="Prédio", source="citius", price=None))
    assert "Citius no-minimum court sale" in reasons
    assert "no minimum bid" not in reasons


def test_urgency_works_with_naive_dates():
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat()
    _, reasons = score(item(title="Moradia", price=20000, date_end=soon))
    assert any("urgent" in r for r in reasons)


def test_price_drop_bonus():
    base, _ = score(item(title="Moradia", price=20000))
    dropped, reasons = score(item(title="Moradia", price=20000, price_drop_pct=30))
    assert dropped == base + 10 and any("price cut" in r for r in reasons)


def test_market_value_matches_place_exactly():
    assert market_value_estimate(item(area_m2=100, concelho="Porto")) == 310000
    assert market_value_estimate(item(area_m2=100, concelho="Porto de Mós")) is None
    assert market_value_estimate(item(area_m2=100, concelho="Setúbal")) == 220000
    assert market_value_estimate(item(area_m2=100, concelho="Lisboa (Santa Maria Maior)")) == 420000
    assert market_value_estimate(item(area_m2=100, concelho="a")) is None


def test_categorize():
    assert categorize(item(title="Prédio com auditório")) == "imoveis"
    assert categorize(item(title="Audi A4 2015")) == "outros"
    assert categorize(item(title="Anel em ouro")) == "ouro_joias"
    assert categorize(item(title="Loja", tipo="loja")) == "imoveis"
    assert categorize(item(title="Vivienda en Loja", tipo="inmueble")) == "imoveis"


def test_score_bounds():
    sc, _ = score(item(title="Moradia devoluta", source="citius", price=5000, area_m2=200,
                       description="venda por propostas em carta fechada"))
    assert 0 <= sc <= 100
