import pytest

from evaluator import DEFAULT_POLICY, FAIL, PASS, UNKNOWN, _req_hazard_coverage


def check(climate):
    return _req_hazard_coverage({"climate": climate}, dict(DEFAULT_POLICY))


def test_real_producer_fire_key_is_checked():
    result = check({"flood_m": 0.0, "fire_danger": {"high_days_2090": 120}})
    assert result.outcome == FAIL
    assert result.code == "PHYS_FIRE_SEVERE"
    assert result.dangerous is True


def test_legacy_fire_key_remains_supported():
    result = check({"flood_m": 0.0, "fire_danger": {"rcp45_2079-2098": 120}})
    assert result.code == "PHYS_FIRE_SEVERE"


def test_zero_fire_days_are_not_replaced_by_fallback():
    result = check({"flood_m": 0.0, "fire_danger": {"high_days_2090": 0}, "fire_danger_days": 120})
    assert result.outcome == PASS


def test_known_severe_flood_still_fails_when_fire_is_missing():
    result = check({"flood_m": 2.0})
    assert result.code == "PHYS_FLOOD_SEVERE"


def test_known_severe_fire_still_fails_when_flood_is_missing():
    result = check({"fire_danger": {"high_days_2090": 120}})
    assert result.code == "PHYS_FIRE_SEVERE"


@pytest.mark.parametrize("bad", [None, {}, {"wrong_key": 10}, {"high_days_2090": None},
                                 {"high_days_2090": float("nan")}, {"high_days_2090": True},
                                 {"high_days_2090": -1}])
def test_missing_or_invalid_fire_metric_is_not_a_pass(bad):
    result = check({"flood_m": 0.0, "fire_danger": bad})
    assert result.outcome == UNKNOWN
