"""Tax on rent and on a resale for an owner resident in the Netherlands
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


@pytest.mark.parametrize("country,years,tax", [
    ("IT", 1, 26.0), ("IT", 5, 0.0),          # 26% within 5 years, none after
    ("BE", 4, 16.5), ("BE", 5, 0.0),          # 16.5% within 5 years
    ("ES", 30, 19.0),                         # always 19%
    ("NL", 1, 0.0),                           # box 3 instead
    ("DE", 10, 0.0),                          # tax-free after 10 years
    ("FR", 1, 26.5), ("FR", 30, 0.0),         # 19% + 7.5%, none after 30 years
])
def test_the_tax_on_a_gain_of_100(country, years, tax):
    assert round(costs.gain_tax(country, 100.0, years), 2) == tax


@pytest.mark.parametrize("country,years", [("PT", 1), ("DE", 9), ("LU", 3), ("HR", 1)])
def test_a_tax_at_the_owner_s_own_rate_has_no_figure(country, years):
    assert costs.gain_tax(country, 100.0, years) is None


def test_france_reduces_the_tax_from_the_sixth_year():
    # Year 10: income tax cut by 30%, the levy by 8.25%.
    assert costs.gain_tax("FR", 100.0, 10) == pytest.approx(19 * 0.70 + 7.5 * (1 - 0.0825))
    assert costs.gain_tax("FR", 100.0, 22) == pytest.approx(7.5 * (1 - 0.28))


def test_the_profit_after_selling_costs_and_tax():
    # Bought for 50,000 all-in, worth 100,000: 95,000 after selling costs.
    assert costs.resale_after_tax("IT", 50000, 100000, 1) == pytest.approx(45000 * 0.74)
    assert costs.resale_after_tax("IT", 50000, 100000, 6) == pytest.approx(45000)
    assert costs.resale_after_tax("IT", 99000, 100000, 1) == pytest.approx(-4000)   # a loss is not taxed


def test_the_profit_note_skips_years_without_a_figure():
    note = costs.resale_profit_note("DE", 50000, 0.5)
    assert "after 10 years" in note and "after 1 year" not in note
    assert costs.resale_profit_note("PT", 50000, 0.5) is None
    assert costs.resale_profit_note("IT", 50000, 0.0) is None
    assert "€33,300 after 1 year" in costs.resale_profit_note("IT", 50000, 0.5)
