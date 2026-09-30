"""The climate resilience grade (scoring.climate_score) and where it shows:
the Climate panel, the Listings filters and the optional bid guardrail."""
import json

import pytest

import dashboard
from scoring import CLIMATE_BID_MULTIPLIER, climate_score, score_detail

PLOT = dict(source="eleiloes", country="PT", title="Prédio rústico", tipo="terreno", area_m2=40000, price=9000)
MILD = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1}}


def test_water_with_low_flood_risk_is_a_plus():
    dry = climate_score(MILD, "rural_plot")
    wet = climate_score({**MILD, "water_km": 0.2, "flood_m": 0}, "rural_plot")
    assert wet["score"] > dry["score"] and "permanent_water" in wet["flags"]
    assert wet["grade"] in ("good", "excellent") and wet["confidence"] == "exact"


def test_water_that_floods_is_no_plus():
    # The acceptance case: water nearby but under the 100-year flood must not
    # grade better than the same place without the flood.
    safe = climate_score(MILD, "rural_plot")
    flooded = climate_score({**MILD, "water_km": 0.2, "flood_m": 1.5}, "rural_plot")
    assert flooded["score"] <= safe["score"]
    assert "water_but_floods" in flooded["flags"] and "permanent_water" not in flooded["flags"]
    assert flooded["grade"] == "poor" and flooded["bid_multiplier"] == CLIMATE_BID_MULTIPLIER["poor"]
    assert any("no water bonus" in r for r in flooded["reasons"])


def test_the_listing_score_gives_no_water_bonus_where_it_floods():
    base, _ = score_detail({**PLOT, "climate": MILD})
    wet, _ = score_detail({**PLOT, "climate": {**MILD, "water_km": 0.2}})
    flooded, reasons = score_detail({**PLOT, "climate": {**MILD, "water_km": 0.2, "flood_m": 1.5}})
    assert wet > base >= flooded
    assert not any(r.startswith("permanent water 0.2 km away") and "no water bonus" not in r for r in reasons)


def test_extreme_heat_is_poor_even_from_a_town_position():
    # The heat grid is ~4.5 km: a town pin is as good as an exact one for it.
    oven = climate_score({"hot_days": {"rcp45_2071-2100": 30}}, "home")
    town = climate_score({"hot_days": {"rcp45_2071-2100": 30}, "approx": True}, "home")
    assert oven["grade"] == town["grade"] == "poor" and "extreme_heat" in oven["flags"]
    warm = climate_score({"hot_days": {"rcp45_2071-2100": 10}}, "home")
    assert warm["grade"] == "caution" and "too_hot" in warm["flags"]


def test_repeated_burns_at_the_spot_are_poor():
    burnt = climate_score({**MILD, "fire": {"count": 2, "years": [2017, 2022], "burnt_here": True, "km": 2}}, "home")
    assert {"burnt_here", "repeated_burns"} <= set(burnt["flags"]) and burnt["grade"] == "poor"
    assert burnt["bid_multiplier"] < 1


def test_an_approximate_position_never_grades_poor_on_local_risks():
    c = {**MILD, "fire": {"count": 2, "years": [2017, 2022], "burnt_here": True, "km": 2}, "flood_m": 2.0,
         "approx": True}
    got = climate_score(c, "home")
    assert got["confidence"] == "approximate" and got["grade"] == "caution"
    assert got["bid_multiplier"] == 1.0
    assert any("approximate" in r for r in got["reasons"])
    best = climate_score({**MILD, "water_km": 0.2, "approx": True}, "rural_plot")
    assert best["grade"] != "excellent"


def test_no_data_is_unknown_not_a_judgement():
    got = climate_score(None)
    assert got == {"score": None, "grade": "unknown", "reasons": [], "flags": [],
                   "bid_multiplier": 1.0, "confidence": "unknown"}


def test_the_grade_does_not_move_with_the_weight_sliders():
    c = {**MILD, "water_km": 0.2}
    before = climate_score(c, "rural_plot")
    score_detail({**PLOT, "climate": c}, targets={"weights": {"water": 0, "heat": 2}})
    assert climate_score(c, "rural_plot") == before


def test_the_guardrail_lowers_ai_bids_only_at_exact_positions():
    poor = climate_score({"hot_days": {"rcp45_2071-2100": 30}}, "home")
    result = dashboard.apply_climate_guardrail({"recommended_bid": "10.000,00", "max_bid": "12.000,00"}, poor)
    assert result["recommended_bid"] == "6.000,00" and result["max_bid"] == "7.200,00"
    assert result["climate_guardrail"]["applied"] and result["climate_guardrail"]["was"]["max_bid"] == "12.000,00"
    town = climate_score({"hot_days": {"rcp45_2071-2100": 30}, "approx": True}, "home")
    kept = dashboard.apply_climate_guardrail({"recommended_bid": "10.000,00", "max_bid": "12.000,00"}, town)
    assert kept["recommended_bid"] == "10.000,00" and not kept["climate_guardrail"]["applied"]
    unknown = dashboard.apply_climate_guardrail({"recommended_bid": "10.000,00"}, climate_score(None))
    assert unknown["recommended_bid"] == "10.000,00"


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _stored(c):
    return json.dumps({"climate": {"result": c}})


def test_the_guardrail_is_off_by_default_and_saved_from_settings(client):
    import config
    assert config.DEFAULTS["climate"]["bid_guardrail"] is False
    assert client.get("/api/settings").get_json()["climate"] == {"bid_guardrail": False}
    assert client.post("/api/settings", json={"climate": {"bid_guardrail": "yes"}}).status_code == 400
    assert client.post("/api/settings", json={"climate": {"bid_guardrail": True}}).get_json() == {"ok": True}
    assert config.load_config()["climate"]["bid_guardrail"] is True


def test_the_ai_check_applies_the_guardrail_only_when_it_is_on(client, add, monkeypatch):
    import analysis
    import config
    add(external_id="hot", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=_stored({"hot_days": {"rcp45_2071-2100": 30}}))
    monkeypatch.setattr(analysis, "analyze_property",
                        lambda data: {"verdict": "BUY", "recommended_bid": "10.000,00", "max_bid": "12.000,00"})
    off = client.post("/api/analyze-property", json={"id": "eleiloes:hot", "bid": "9.000,00"}).get_json()
    assert off["recommended_bid"] == "10.000,00" and "climate_guardrail" not in off
    config.update_config({"climate": {"bid_guardrail": True}})
    on = client.post("/api/analyze-property", json={"id": "eleiloes:hot", "bid": "9.000,00"}).get_json()
    assert on["recommended_bid"] == "6.000,00" and on["climate_guardrail"]["grade"] == "poor"


def test_listings_filter_by_climate_grade_and_exact_data(client, add):
    add(external_id="cool", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=_stored({**MILD, "water_km": 0.2}))
    # poor (arid by 2080) but not rejected by the score, so it is listed
    add(external_id="hot", title="Moradia T2", tipo="moradia", area_m2=120, price=20000,
        raw_json=_stored({"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": -1}}))
    add(external_id="town", title="Moradia T4", tipo="moradia", area_m2=120, price=20000,
        raw_json=_stored({**MILD, "approx": True}))
    add(external_id="none", title="Moradia T1", tipo="moradia", area_m2=120, price=20000)

    def ids(**q):
        items = client.get("/api/listings", query_string=q).get_json()["items"]
        return {it["id"].split(":")[1] for it in items}

    assert ids() == {"cool", "hot", "town", "none"}
    assert ids(climate="good") == {"cool", "town"}
    assert ids(climate="poor") == {"hot"}
    assert ids(climate="unknown") == {"none"}
    assert ids(climate_exact=1) == {"cool", "hot"}
    grades = {it["id"]: it["climate_grade"] for it in client.get("/api/listings").get_json()["items"]}
    assert grades["eleiloes:hot"] == "poor" and grades["eleiloes:none"] == "unknown"


def test_the_panel_says_what_is_missing(client, add):
    add(external_id="none", title="Moradia T1", tipo="moradia", area_m2=120, price=20000)
    panel = client.get("/api/listing?id=eleiloes:none").get_json()["climate"]
    assert panel["grade"] == "unknown" and panel["missing"]
    add(external_id="hot", title="Moradia T2", tipo="moradia", area_m2=120, price=20000,
        raw_json=_stored({"hot_days": {"rcp45_2071-2100": 30}}))
    panel = client.get("/api/listing?id=eleiloes:hot").get_json()["climate"]
    assert panel["grade"] == "poor" and "missing" not in panel
