import pytest
import bidcap


@pytest.mark.parametrize("fees", [float("nan"), float("inf"), -100, True, "100"])
def test_invalid_fees_are_not_a_silent_cap(monkeypatch, fees):
    monkeypatch.setattr(bidcap.costs, "fees", lambda item, bid: fees)
    with pytest.raises(ValueError):
        bidcap._largest_bid({}, 10000, 1)


@pytest.mark.parametrize("room,factor", [(float("nan"), 1), (10000, float("nan")), (10000, -1)])
def test_invalid_solver_inputs_raise(room, factor):
    with pytest.raises(ValueError):
        bidcap._largest_bid({}, room, factor)


def test_solver_rounds_down_and_respects_cost_constraint(monkeypatch):
    monkeypatch.setattr(bidcap.costs, "fees", lambda item, bid: bid * 0.1)
    bid = bidcap._largest_bid({}, 10000, 1)
    assert bid == 9000
    assert bid + 0.1 * bid <= 10000
    assert bid + 100 + 0.1 * (bid + 100) > 10000


def test_known_no_room_is_zero():
    assert bidcap._largest_bid({}, -1, 1) == 0


@pytest.mark.parametrize("bid", [float("nan"), float("inf"), True, "100", 0, -1])
def test_invalid_comparison_never_reports_match(bid):
    assert bidcap.compare({"absolute_max_bid": 10000, "recommended_bid": 9000}, bid) is None


def test_missing_recommendation_does_not_mean_matching_bid():
    assert bidcap.compare({"absolute_max_bid": 10000, "recommended_bid": None}, 9000) is None


def test_valid_comparison_remains_supported():
    cap = {"absolute_max_bid": 10000, "recommended_bid": 9000}
    assert bidcap.compare(cap, 9000)["level"] == "at_rec"
    assert bidcap.compare(cap, 11000)["level"] == "above_max"
