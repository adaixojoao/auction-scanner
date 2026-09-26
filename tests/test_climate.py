"""Heat in 50-70 years, water, fire and flood where a listing is (climate.py, scoring)."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import climate
from scoring import score_detail

HOME = dict(source="eleiloes", country="PT", title="Moradia T3", tipo="moradia", area_m2=120, price=20000)
PLOT = dict(source="eleiloes", country="PT", title="Prédio rústico", tipo="terreno", area_m2=40000, price=9000)


def with_climate(item, **c):
    return {**item, "climate": c}


def test_summers_hotter_than_35_by_2090_are_not_wanted():
    cool, reasons = score_detail(with_climate(HOME, heat={"ssp245_2081-2100": 31.0, "ssp585_2081-2100": 33.5,
                                                          "today": 29.0}))
    hot, hot_reasons = score_detail(with_climate(HOME, heat={"ssp245_2081-2100": 35.8}))
    oven, oven_reasons = score_detail(with_climate(HOME, heat={"ssp245_2081-2100": 37.5}))
    plain, _ = score_detail(HOME)
    assert cool > plain > hot > oven
    assert "31.0 °C summer max by 2081-2100, 33.5 °C worst case; 29.0 °C today" in reasons
    assert hot <= 60 and any(r.startswith("too hot in 50-70 years") for r in hot_reasons)
    assert any(r.startswith("rejected: too hot") for r in oven_reasons) and oven <= 30


def test_permanent_water_fire_flood_and_stress():
    base, _ = score_detail(PLOT)
    wet, reasons = score_detail(with_climate(PLOT, water_km=0.3))
    assert wet - base >= 13 and "permanent water 0.3 km away" in reasons
    approx, _ = score_detail(with_climate(PLOT, water_km=0.3, approx=True))
    assert base < approx < wet
    burnt, reasons = score_detail(with_climate(PLOT, fire={"count": 2, "years": [2017, 2022], "burnt_here": True,
                                                           "km": 2}))
    assert burnt < base and "burnt since 2016 (2017, 2022) — EFFIS" in reasons
    dry, reasons = score_detail(with_climate(PLOT, stress={"stress_2080": 4}))
    assert dry < base and "water stress extremely high by 2080 (WRI Aqueduct)" in reasons
    flooded, reasons = score_detail(with_climate(HOME, flood_m=0.8))
    assert flooded < score_detail(HOME)[0] and "in the 100-year flood zone (0.8 m) — JRC" in reasons


@pytest.fixture
def layers(tmp_path, monkeypatch):
    monkeypatch.setattr(climate, "data_dir", lambda cfg=None: str(tmp_path))
    climate._raster.cache_clear()
    climate._CACHE.clear()
    (tmp_path / "layers").mkdir()
    (tmp_path / "jrc_flood").mkdir()
    t = from_origin(-8.0, 41.0, 0.01, 0.01)          # 100 x 100 cells over (-8, 40)..(-7, 41)

    def write(path, arr):
        with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
                           crs="EPSG:4326", transform=t, nodata=-9999) as dst:
            dst.write(arr.astype("float32"), 1)
    write(tmp_path / "layers" / "heat_ssp245_2081-2100.tif", np.full((100, 100), 33.26))
    water = np.zeros((100, 100))
    water[50, 60] = 1                                  # one wet cell at (40.495, -7.395)
    write(tmp_path / "jrc_flood" / "Europe_permanent_water_bodies.tif", water)
    flood = np.zeros((100, 100))
    flood[10, 10] = 1.5
    write(tmp_path / "jrc_flood" / "Europe_RP100_filled_depth.tif", flood)
    yield tmp_path
    climate._raster.cache_clear()


def test_the_layers_are_read_at_the_position(layers):
    got = climate.assess(40.495, -7.405)                 # half a cell west of the wet cell
    assert got["heat"] == {"ssp245_2081-2100": 33.3}
    assert 0 < got["water_km"] < 1.0
    assert got["flood_m"] == 0.0
    assert climate.flood_depth(40.895, -7.895) == 1.5
    assert climate.permanent_water_km(40.1, -7.9) is None
    item = {"country": "PT", "raw_json": '{"lat": 40.495, "lon": -7.40}'}
    assert climate.for_item(item)["approx"] is False
