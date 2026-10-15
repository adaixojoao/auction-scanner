"""Tests for Milestone F — location capabilities and hazard-coverage contract (F03, F04)."""
from __future__ import annotations


from geo import BUILDING_EXACT, EXACT_ENOUGH, location_capability
from scoring import climate_score


# ─── location_capability — F04 ───────────────────────────────────────────────

def test_sale_pin_is_building_level():
    assert location_capability("sale") == "building"


def test_cadastre_pin_is_building_level():
    assert location_capability("cadastre") == "building"


def test_verified_pin_is_building_level():
    assert location_capability("verified") == "building"


def test_street_pin_is_research_only():
    """Street-centre pin is close but not building-level due diligence (F04)."""
    assert location_capability("street") == "research"


def test_advertised_pin_is_research_only():
    """Seller-supplied map pin aids research but can't satisfy building gate (F04)."""
    assert location_capability("advertised") == "research"


def test_village_pin_is_approximate():
    assert location_capability("village") == "approximate"


def test_municipality_pin_is_approximate():
    assert location_capability("municipality") == "approximate"


def test_none_precision_is_none():
    assert location_capability(None) == "none"
    assert location_capability("") == "none"


def test_building_exact_excludes_street_and_advertised():
    """Critical: street and advertised must not satisfy building-level checks."""
    assert "street" not in BUILDING_EXACT
    assert "advertised" not in BUILDING_EXACT
    assert "sale" in BUILDING_EXACT
    assert "cadastre" in BUILDING_EXACT


def test_building_exact_is_subset_of_exact_enough():
    """BUILDING_EXACT is a strict subset of EXACT_ENOUGH."""
    assert BUILDING_EXACT < EXACT_ENOUGH, f"BUILDING_EXACT extras: {BUILDING_EXACT - EXACT_ENOUGH}"


# ─── climate_score hazard coverage — F03 ─────────────────────────────────────

BENIGN = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1},
          "flood_m": 0.0, "fire_danger": {"high_days_2090": 0}}


def test_complete_hazard_data_allows_excellent_grade():
    got = climate_score({**BENIGN, "water_km": 0.5}, "rural_plot")
    assert got["grade"] in ("good", "excellent")
    assert got["confidence"] == "exact"


def test_missing_flood_layer_caps_grade_at_caution():
    """If flood_m is absent we cannot rule out flood risk — cap at caution (F03)."""
    c = {"hot_days": {"rcp45_2071-2100": 2}, "fire_danger": {"high_days_2090": 0}}
    got = climate_score(c, "home")
    assert got["grade"] in ("poor", "caution")
    assert got["confidence"] == "hazard_data_incomplete"
    assert any("flood" in r for r in got["reasons"])


def test_missing_fire_danger_layer_caps_grade_at_caution():
    """If fire_danger is absent we cannot assess future fire risk — cap at caution (F03)."""
    c = {"hot_days": {"rcp45_2071-2100": 2}, "flood_m": 0.0}
    got = climate_score(c, "home")
    assert got["grade"] in ("poor", "caution")
    assert got["confidence"] == "hazard_data_incomplete"
    assert any("fire" in r for r in got["reasons"])


def test_confirmed_poor_from_extreme_heat_survives_incomplete_hazard():
    """Grade stays 'poor' when a decisive regional hazard is present (F03/guardrail)."""
    c = {"hot_days": {"rcp45_2071-2100": 30}}  # extreme heat, no flood/fire layers
    got = climate_score(c, "home")
    assert got["grade"] == "poor"
    assert got["confidence"] == "hazard_data_incomplete"


def test_hazard_incomplete_reason_names_missing_layers():
    c = {"stress": {"stress_2080": 1}}  # missing both flood_m and fire_danger
    got = climate_score(c, "home")
    reason_text = " ".join(got["reasons"])
    assert "flood" in reason_text and "fire" in reason_text


def test_approximate_and_incomplete_confidence_is_hazard_data_incomplete():
    """When position is approximate AND hazard data is incomplete, surface the hazard gap."""
    c = {"hot_days": {"rcp45_2071-2100": 2}, "approx": True}  # no flood or fire_danger
    got = climate_score(c, "home")
    assert got["confidence"] == "hazard_data_incomplete"
