"""Milestone H — F07 double-depreciation fix; F08 risk_reserve in all_in."""
from __future__ import annotations

import json

import bidcap
import costs
from scoring import climate_score


def _home(**over):
    row = {"id": "citius:h1", "source": "citius", "country": "PT", "kind": "home",
           "title": "Moradia em bom estado", "description": "Habitação em bom estado de conservação",
           "concelho": "Porto", "area_m2": 100, "price": 80000, "category": "imoveis"}
    row.update(over)
    return row


def _settings(**over):
    s = {"filters": {}, "bid_cap": {"max_all_in": 0, "margin_pct": 15, "contingency_pct": 10,
                                    "rural_reserve_per_ha": 500, "rural_reserve_fixed": 0,
                                    "require_exact": False, "adviser_reserve_eur": 1500,
                                    "adviser_reserve_by_country": {}}}
    s["bid_cap"].update(over)
    return s


def _exact_evidence(grade="good"):
    clim = climate_score(None, "home")
    clim = {**clim, "grade": grade, "confidence": "exact",
            "bid_multiplier": {"excellent": 1.0, "good": 1.0, "caution": 0.85, "poor": 0.6}.get(grade, 1.0)}
    return {"climate": clim, "location": {"level": "exact", "label": "Exact location"}}


# ─── F07: renovation deducted once, not twice ────────────────────────────────

def test_ruin_value_base_equals_sound_home_value_base():
    """After F07 fix, the estimated value base is the after-work market price for both (F07)."""
    sound = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    ruin = bidcap.calculate_bid_cap(
        _home(title="Moradia em ruínas", description="Em ruínas, precisa de reconstrução total"),
        _settings(), _exact_evidence())
    # Both reference the same raw market value (after renovation).
    assert sound["estimated_market_value"] == ruin["estimated_market_value"]
    # The ruin has higher renovation, so its bid cap is lower.
    assert ruin["renovation_high"] > sound["renovation_high"]
    assert ruin["recommended_bid"] < sound["recommended_bid"]


def test_bidcap_note_says_after_work_value():
    cap = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    assert cap["basis"] is not None
    assert "after-work" in (cap["basis"]["note"] or "").lower()


def test_condition_factor_no_longer_reduces_value_base():
    """condition factor must NOT appear in the value amount — only in renovation (F07)."""
    import scoring
    import bidcap as bc

    # A "heavy" condition home used to have value *= 0.3 (or similar factor).
    # After the fix, value = full market value; renovation carries the condition cost.
    ruin = _home(title="Ruin", description="Em ruínas, precisa de reconstrução total")
    sound = _home()
    cap_ruin = bc.calculate_bid_cap(ruin, _settings(), _exact_evidence())
    cap_sound = bc.calculate_bid_cap(sound, _settings(), _exact_evidence())
    # With F07 fix: same base value, condition in renovation
    assert cap_ruin["estimated_market_value"] == cap_sound["estimated_market_value"]
    # Ruin's all_in_high includes higher renovation
    assert (cap_ruin["all_in_high"] or 0) != (cap_sound["all_in_high"] or 0)


# ─── F08: risk_reserve included in all_in ────────────────────────────────────

def test_all_in_includes_risk_reserve():
    """all_in_low and all_in_high must include risk_reserve (F08)."""
    cap = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    reserve = cap["risk_reserve"] or 0
    rec = cap["recommended_bid"] or 0
    fees_low = cap["taxes_and_fees_low"] or 0
    reno_low = cap["renovation_low"] or 0
    reno_high = cap["renovation_high"] or 0
    fees_high = cap["taxes_and_fees_high"] or 0
    assert cap["all_in_low"] == round(rec + fees_low + reno_low + reserve)
    assert cap["all_in_high"] == round(rec + fees_high + reno_high + reserve)


def test_all_in_low_le_all_in_high():
    cap = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    assert (cap["all_in_low"] or 0) <= (cap["all_in_high"] or 0)


def test_reserve_nonzero_with_default_contingency():
    """Default contingency_pct=10 + adviser_reserve=1500 must produce a nonzero reserve."""
    cap = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    assert (cap["risk_reserve"] or 0) > 0


def test_all_in_changes_when_contingency_changes():
    """Raising contingency_pct must change all_in (reserve is included)."""
    low_cont = bidcap.calculate_bid_cap(_home(), _settings(contingency_pct=5), _exact_evidence())
    high_cont = bidcap.calculate_bid_cap(_home(), _settings(contingency_pct=20), _exact_evidence())
    assert low_cont["all_in_high"] != high_cont["all_in_high"]
