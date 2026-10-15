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


# ─── F07: renovation deducted once, not twice; unknown MV blocks bid cap ─────

def test_unknown_market_value_blocks_bid_cap():
    """When market value is unknown, the bid cap must not be computed (F07 — unknown MV blocks)."""
    # No local price series → value is None → bid cap blocked
    cap = bidcap.calculate_bid_cap(_home(concelho="NoSuchPlace_XYZ"), _settings(), _exact_evidence())
    assert cap["recommended_bid"] is None
    assert cap["basis"] is None
    assert cap["market_value_confidence"] == "unknown"
    assert any("market value is unknown" in r.lower() for r in cap["reasons"])


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


# ─── F08: over_cash uses the same scenario as bidcap (reserve in filter) ─────

def test_over_cash_includes_reserve_when_settings_given():
    """over_cash with settings must include reserve; without settings it must not (F08)."""
    item = _home(price=80000)
    # money_out (no reserve) gives the baseline all-in
    baseline = costs.money_out(item)["amount"]
    # With reserve the total must be higher
    reserve = costs.risk_reserve(item, _settings()["bid_cap"])
    assert reserve > 0, "test requires nonzero reserve"
    result_with = costs.over_cash(item, baseline, settings=_settings())
    result_without = costs.over_cash(item, baseline)
    # Without reserve the item is exactly at the cash limit — not hidden
    assert result_without is None, "without reserve, baseline cash should pass"
    # With reserve the total exceeds baseline cash — item is hidden
    assert result_with is not None, "with reserve, same cash limit should fail"
    assert "reserve" in result_with


def test_over_cash_reserve_matches_bidcap_scenario():
    """reserve used in over_cash must equal the bidcap-style risk_reserve (F08 — one scenario)."""
    item = _home(price=80000)
    bid_cap_reserve = costs.risk_reserve(item, _settings()["bid_cap"])
    baseline = costs.money_out(item)["amount"]
    # Cash set to exactly baseline + reserve should pass
    assert costs.over_cash(item, baseline + bid_cap_reserve, settings=_settings()) is None
    # Cash set to one euro below baseline + reserve should fail
    assert costs.over_cash(item, baseline + bid_cap_reserve - 1, settings=_settings()) is not None


def test_risk_reserve_base_matches_bidcap_base():
    """risk_reserve and bidcap must use the same market-value base (F08 — unified scenario).

    Both use market_value_estimate when available; both fall back to price when not.
    This ensures the shortlist cash filter and the bid cap display are one scenario.
    """
    import scoring
    item = _home(price=80000)
    mv = scoring.market_value_estimate(item)
    if mv is not None:
        # When MV is available, risk_reserve base == MV
        reserve_from_costs = costs.risk_reserve(item, _settings()["bid_cap"])
        expected = mv * _settings()["bid_cap"]["contingency_pct"] / 100 + _settings()["bid_cap"]["adviser_reserve_eur"]
        assert abs(reserve_from_costs - expected) < 0.01, (
            f"risk_reserve={reserve_from_costs:.2f}, expected MV-based={expected:.2f}")
        # bidcap's internal reserve field must equal risk_reserve
        cap = bidcap.calculate_bid_cap(item, _settings(), _exact_evidence())
        assert cap["risk_reserve"] is not None
        assert abs(cap["risk_reserve"] - reserve_from_costs) < 1, (
            f"bidcap reserve={cap['risk_reserve']}, risk_reserve={reserve_from_costs:.2f}")
    else:
        # When MV is not available, risk_reserve falls back to acquisition price
        from common import price_to_pay
        price = float(price_to_pay(item) or item.get("price") or 0)
        reserve_from_costs = costs.risk_reserve(item, _settings()["bid_cap"])
        expected = price * _settings()["bid_cap"]["contingency_pct"] / 100 + _settings()["bid_cap"]["adviser_reserve_eur"]
        assert abs(reserve_from_costs - expected) < 0.01


def test_rural_plot_reserve_includes_land_component():
    """costs.risk_reserve must include rural_reserve_per_ha for plots (Task 5).

    bidcap adds (ha * rural_reserve_per_ha + rural_reserve_fixed) to the reserve for rural_plot.
    over_cash uses costs.risk_reserve, so omitting the land component lets a plot purchase
    pass the cash check with more cash than actually required.
    """
    plot = {"id": "citius:p1", "source": "citius", "country": "PT", "kind": "rural_plot",
            "title": "Terreno rústico", "area_m2": 20000, "price": 5000, "category": "imoveis"}
    settings = _settings(rural_reserve_per_ha=500, rural_reserve_fixed=200)
    ha = 20000 / 10000   # 2 ha
    land_component = ha * 500 + 200   # 1200
    reserve = costs.risk_reserve(plot, settings["bid_cap"])
    # Must include the land component on top of contingency + adviser
    assert reserve >= land_component, (
        f"rural reserve {reserve:.0f} does not include land component {land_component:.0f}")

    # over_cash must reject a purchase when the land reserve pushes it over cash
    out = costs.money_out(plot)
    if out:
        # cash set to exactly price+fees with no room for land reserve should now fail
        barely = out["amount"] + reserve - 1
        assert costs.over_cash(plot, barely, settings=settings) is not None, \
            "over_cash should fail when land reserve pushes all-in over cash"
        # cash set to price+fees+reserve should pass
        enough = out["amount"] + reserve + 1
        assert costs.over_cash(plot, enough, settings=settings) is None, \
            "over_cash should pass when cash covers land reserve"
