"""Heavy industry next to a home hides it; lighter pollution nearby lowers its score (geo.pollution)."""
import json

import pytest

import geo
from scoring import air_grade, air_hidden, pollution_penalty, score_detail


@pytest.fixture
def polluters(tmp_path, monkeypatch):
    path = tmp_path / "polluters.csv"
    path.write_text("country,kind,lat,lon,name\n"
                    "PT,heavy,38.6600,-8.8300,Refinaria (refinery)\n"
                    "PT,light,40.2000,-8.4000,Aterro (landfill)\n", encoding="utf-8")
    monkeypatch.setattr(geo, "POLLUTION_FILE", str(path))
    return str(path)


def at(lat, lon, precision=None):
    raw = {"lat": lat, "lon": lon}
    if precision:
        raw = {"geo": {"lat": lat, "lon": lon, "precision": precision}}
    return {"country": "PT", "raw_json": json.dumps(raw)}


def test_heavy_industry_next_to_the_house_is_heavy(polluters):
    found = geo.pollution(at(38.6700, -8.8300))                 # about 1 km
    assert found["level"] == "heavy" and "heavy industry (Refinaria (refinery))" in found["text"]


def test_heavy_industry_a_few_km_away_or_a_landfill_close_by_is_light(polluters):
    assert geo.pollution(at(38.7100, -8.8300))["level"] == "light"   # about 5.5 km
    assert geo.pollution(at(40.2100, -8.4000))["level"] == "light"   # landfill 1 km
    assert geo.pollution(at(40.2500, -8.4000)) is None               # landfill 5.5 km
    assert geo.pollution({"country": "PT"}) is None                  # no position


def test_the_penalty_is_bigger_for_heavy_and_named():
    light = {"level": "light", "text": "about 2.0 km from a pollution source (Aterro)"}
    assert pollution_penalty({"pollution": light}) == (-10, ["some pollution nearby: " + light["text"]])
    assert pollution_penalty({"pollution": {"level": "heavy", "text": "x"}})[0] == -30
    assert pollution_penalty({}) == (0, [])


def test_a_polluted_home_scores_lower():
    home = {"source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia", "area_m2": 120,
            "price": 25000}
    clean, _ = score_detail(dict(home))
    dirty, reasons = score_detail({**home, "pollution": {"level": "light", "text": "1 km from a landfill"}})
    assert dirty == max(0.0, clean - 10) and any("pollution nearby" in r for r in reasons)


def test_heavy_industry_hides_the_home_unless_turned_off(db, add, polluters):
    from db import load_listings
    add(external_id="dirty", title="Moradia T3", tipo="moradia", price=20000, area_m2=100,
        raw_json={"lat": 38.6700, "lon": -8.8300})
    add(external_id="clean", title="Moradia T3", tipo="moradia", price=20000, area_m2=100,
        raw_json={"lat": 41.5, "lon": -8.0})
    shown = {it["id"]: it for it in load_listings(db, filters={}, include_hidden=True)}
    assert shown["eleiloes:dirty"]["hidden_reason"].startswith("heavy pollution:")
    assert not (shown["eleiloes:clean"].get("hidden_reason") or "").startswith("heavy pollution")
    kept = {it["id"]: it for it in load_listings(db, filters={"avoid_heavy_pollution": False},
                                                 include_hidden=True)}
    assert not (kept["eleiloes:dirty"].get("hidden_reason") or "").startswith("heavy pollution")
    assert any("heavy pollution nearby" in r for r in kept["eleiloes:dirty"]["reasons"]) \
        or kept["eleiloes:dirty"]["score"] == 0


def test_air_is_graded_against_the_who_and_eu_limits():
    assert air_grade({"air": {"pm25": 4.2, "no2": 8}}) == ("clean", "PM2.5 4.2 µg/m³, NO₂ 8 µg/m³")
    assert air_grade({"air": {"pm25": 8, "no2": 15}})[0] == "fair"
    assert air_grade({"air": {"pm25": 11, "no2": 9}})[0] == "poor"
    assert air_grade({"air": {"pm25": 9, "no2": 31}})[0] == "bad"
    assert air_grade({"heat": {}}) == (None, "")
    assert air_grade(None) == (None, "")


def test_bad_air_is_hidden_only_at_a_pin_that_is_the_house():
    assert air_hidden({"climate": {"air": {"pm25": 16, "no2": 20}}})
    assert not air_hidden({"climate": {"air": {"pm25": 16, "no2": 20}, "approx": True}})
    assert not air_hidden({"climate": {"air": {"pm25": 12, "no2": 20}}})


def test_clean_air_earns_points_and_bad_air_costs_them():
    clean_pts, why = pollution_penalty({"climate": {"air": {"pm25": 4, "no2": 6}}})
    assert clean_pts == 5 and why == ["clean air (PM2.5 4 µg/m³, NO₂ 6 µg/m³)"]
    bad_pts, why = pollution_penalty({"climate": {"air": {"pm25": 18, "no2": 35}, "approx": True},
                                      "pollution": {"level": "light", "text": "1 km from a landfill"}})
    assert bad_pts == -30 and why[1].startswith("badly polluted air (about PM2.5 18")


def test_the_air_layers_are_read_at_the_position(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    import climate
    layers = tmp_path / "layers"
    layers.mkdir()
    for name, value in (("pm25", 7.25), ("no2", 18.0)):
        with rasterio.open(layers / f"air_{name}.tif", "w", driver="GTiff", height=10, width=10, count=1,
                           dtype="float32", crs="EPSG:4326", nodata=np.nan,
                           transform=from_origin(-9.0, 39.0, 0.1, 0.1)) as dst:
            dst.write(np.full((10, 10), value, dtype="float32"), 1)
    monkeypatch.setattr(climate, "_layers", lambda: str(layers))
    assert climate.air_quality(38.5, -8.5) == {"pm25": 7.2, "no2": 18.0}
    assert climate.air_quality(50.0, 2.0) == {}                     # off the map
