import pytest
import evaluator as ev


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "100", [], {}])
def test_invalid_margin_cannot_pass(invalid):
    r = ev._req_financial_margin({"conservative_margin_eur": invalid}, {"min_resale_margin_eur": 0})
    assert r.outcome == ev.UNKNOWN


@pytest.mark.parametrize("field", ["available_cash_eur", "total_project_cost_eur"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "100", -1, 0])
def test_invalid_cash_or_total_is_unknown(field, invalid):
    evidence = {"available_cash_eur": 100000, "total_project_cost_eur": 50000}
    evidence[field] = invalid
    assert ev._req_cash_fit(evidence, {}).outcome == ev.UNKNOWN


@pytest.mark.parametrize("reserve", [-1, float("nan"), float("inf"), True, "100"])
def test_invalid_reserve_cannot_reduce_required_cash(reserve):
    assert ev._req_cash_fit(
        {"available_cash_eur": 100000, "total_project_cost_eur": 50000},
        {"min_liquidity_reserve_eur": reserve},
    ).outcome == ev.UNKNOWN


def test_valid_boundaries_remain_consistent():
    assert ev._req_cash_fit(
        {"available_cash_eur": 100000, "total_project_cost_eur": 90000},
        {"min_liquidity_reserve_eur": 10000},
    ).outcome == ev.PASS
    assert ev._req_cash_fit(
        {"available_cash_eur": 100000, "total_project_cost_eur": 90001},
        {"min_liquidity_reserve_eur": 10000},
    ).outcome == ev.FAIL
    assert ev._req_financial_margin(
        {"conservative_margin_eur": -100}, {"min_resale_margin_eur": 0}
    ).outcome == ev.FAIL


def test_missing_data_remains_unknown():
    assert ev._req_cash_fit({}, {}).outcome == ev.UNKNOWN
    assert ev._req_financial_margin({}, {}).outcome == ev.UNKNOWN
