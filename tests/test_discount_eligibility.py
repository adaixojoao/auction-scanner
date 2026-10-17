import copy

import pytest

import costs
import scoring


@pytest.mark.parametrize("cost,expected", [
    (70000, 0.3), (100000, 0.0), (120000, -0.2),
])
def test_valid_discount_preserves_existing_formula(cost, expected):
    assert scoring._investment_discount(
        100, "stored", cost, 1000, 1, planning_complete=True
    ) == pytest.approx(expected)


@pytest.mark.parametrize("source", ["stored", "notice", "catastro", "guess"])
def test_existing_allowed_floor_sources_remain_supported(source):
    assert scoring._investment_discount(
        100, source, 70000, 1000, 1, planning_complete=True
    ) == pytest.approx(0.3)


@pytest.mark.parametrize("source", ["capped", "", None, "land", "footprint"])
def test_ambiguous_floor_cannot_produce_discount(source):
    assert scoring._investment_discount(
        100, source, 70000, 1000, 1, planning_complete=True
    ) is None


@pytest.mark.parametrize("complete", [False, None, 0, 1, "true"])
def test_only_explicit_complete_planning_is_accepted(complete):
    assert scoring._investment_discount(
        100, "stored", 70000, 1000, 1, planning_complete=complete
    ) is None


@pytest.mark.parametrize("position", [0, 1, 2, 3])
@pytest.mark.parametrize("invalid", [0, -1, float("nan"), float("inf"), True, "100", None])
def test_invalid_numeric_inputs_remain_unavailable(position, invalid):
    values = [100, 70000, 1000, 1]
    values[position] = invalid
    assert scoring._investment_discount(
        values[0], "stored", values[1], values[2], values[3], planning_complete=True
    ) is None


def test_upward_adjustment_is_not_silently_accepted():
    assert scoring._investment_discount(
        100, "stored", 70000, 1000, 1.1, planning_complete=True
    ) is None


def test_existing_area_limit_is_preserved():
    assert scoring._investment_discount(
        300, "stored", 175000, 1000, 1, planning_complete=True
    ) == pytest.approx(0.3)


def test_overflowing_benchmark_is_unavailable():
    assert scoring._investment_discount(
        250, "stored", 70000, 1e308, 1, planning_complete=True
    ) is None


def test_more_cost_or_less_value_cannot_increase_discount():
    base = scoring._investment_discount(100, "stored", 70000, 1000, 1, planning_complete=True)
    more_cost = scoring._investment_discount(100, "stored", 80000, 1000, 1, planning_complete=True)
    lower_value = scoring._investment_discount(100, "stored", 70000, 900, 1, planning_complete=True)
    assert more_cost < base
    assert lower_value < base


@pytest.fixture
def controlled_investment(monkeypatch):
    state = {"source": "stored", "incomplete": False, "benchmark": 1000}
    monkeypatch.setattr(costs, "building_m2", lambda item: (100.0, state["source"]))
    monkeypatch.setattr(costs, "selected_acquisition", lambda item: {
        "amount": 70000, "kind": "asking", "label": "fixture price", "text": ""
    })
    monkeypatch.setattr(costs, "selected_planning", lambda *args, **kwargs: {
        "amount": 70000, "incomplete": state["incomplete"], "estimate": {"rent": None}
    })
    monkeypatch.setattr(scoring, "local_price", lambda item: (state["benchmark"], "fixture series"))
    monkeypatch.setattr(scoring, "local_value_factor", lambda *args: (1.0, []))
    monkeypatch.setattr(scoring, "_mention_sold_move", lambda *args: None)
    monkeypatch.setattr(scoring, "_population_outlook_points", lambda *args: 0.0)
    item = {
        "source": "eleiloes", "country": "PT", "title": "Moradia em bom estado",
        "description": "Synthetic fixture dwelling", "area_m2": 100,
        "price": 70000, "raw_json": "{}",
    }
    return state, item


def test_complete_costs_keep_discount_claim(controlled_investment):
    _, item = controlled_investment
    _, reasons = scoring.score(copy.deepcopy(item), mode="invest")
    assert any("30% below local prices all-in" in reason for reason in reasons)
    assert not any("discount not established" in reason for reason in reasons)


@pytest.mark.parametrize("incomplete,source", [(True, "stored"), (False, "capped")])
def test_real_scorer_does_not_reward_incomplete_or_capped_discount(
        controlled_investment, incomplete, source):
    state, item = controlled_investment
    valid_score, _ = scoring.score(copy.deepcopy(item), mode="invest")
    state.update(incomplete=incomplete, source=source)
    unresolved_score, reasons = scoring.score(copy.deepcopy(item), mode="invest")
    assert not any("below local prices all-in" in reason for reason in reasons)
    assert any("discount not established" in reason for reason in reasons)
    assert unresolved_score < valid_score


def test_existing_extreme_discount_path_remains_for_usable_inputs(controlled_investment):
    state, item = controlled_investment
    state["benchmark"] = 3000
    candidate = copy.deepcopy(item)
    _, reasons = scoring.score(candidate, mode="invest")
    assert candidate["extreme_discount"] is True
    assert any("check why" in reason for reason in reasons)


def test_invalid_benchmark_does_not_reach_discount_division(controlled_investment):
    state, item = controlled_investment
    state["benchmark"] = float("nan")
    _, reasons = scoring.score(copy.deepcopy(item), mode="invest")
    assert any("discount not established" in reason for reason in reasons)
    assert not any("below local prices all-in" in reason for reason in reasons)
