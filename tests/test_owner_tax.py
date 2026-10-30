"""Tax on rent and on a quick resale for an owner resident in Portugal
(costs.OWNER_TAX, checked 2026-10-30): shown in the reasons, never scored."""
import pytest

import costs

RENT = {"net_yield_pct": 10.0, "yield_pct": 14.0}


@pytest.mark.parametrize("country,kept", [
    ("ES", 8.1),    # 19% of the net rent
    ("PT", 7.5),    # 25%
    ("FR", 7.2),    # 20% + 7.5%
    ("DE", 7.5),    # at least 25%
    ("IT", 7.1),    # 21% of the rent itself: 10 - 14 x 0.21
])
def test_the_yield_after_the_tax_on_rent(country, kept):
    got, rule = costs.after_tax_yield(RENT, country)
    assert got == kept and rule


@pytest.mark.parametrize("country", ["BE", "NL", "LU"])
def test_where_the_tax_is_not_a_share_of_the_rent_the_rule_is_words(country):
    assert costs.after_tax_yield(RENT, country) is None
    assert costs.rent_tax_note(country).startswith("tax on the rent:")


def test_an_unknown_country_says_nothing():
    assert costs.after_tax_yield(RENT, "HR") is None
    assert costs.rent_tax_note("HR") is None and costs.resale_tax_note("HR") is None


@pytest.mark.parametrize("country,words", [
    ("IT", "26%"), ("ES", "19%"), ("BE", "16.5%"), ("DE", "10 years"), ("NL", "no tax on the gain"),
])
def test_the_resale_note_names_the_rule(country, words):
    assert words in costs.resale_tax_note(country)


def test_the_tax_lines_do_not_change_the_score():
    """Shown, not scored: the same home scores the same in Spain and with the
    tax table emptied."""
    import scoring
    home = {"title": "Casa", "tipo": "vivienda", "country": "ES", "source": "solvia", "concelho": "Lleida",
            "district": "Lleida", "area_m2": 80, "price": 30000, "description": "Casa en buen estado",
            "raw_json": "{}"}
    with_tax, reasons = scoring.score(dict(home), mode="invest")
    saved = dict(costs.OWNER_TAX)
    try:
        costs.OWNER_TAX.clear()
        without, _ = scoring.score(dict(home), mode="invest")
    finally:
        costs.OWNER_TAX.update(saved)
    assert with_tax == without
