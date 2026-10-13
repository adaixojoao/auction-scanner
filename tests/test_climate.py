"""Heat in 50-70 years, water, fire and flood where a listing is (climate.py, scoring)."""
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import climate
from scoring import score_detail

HOME = dict(source="eleiloes", country="PT", title="Moradia T3", tipo="moradia", area_m2=120, price=20000)
PLOT = dict(source="eleiloes", country="PT", title="Prédio rústico", tipo="terreno", area_m2=40000, price=9000)


def with_climate(item, **c):
    return {**item, "climate": {"water_km": 0.5, **c}}      # somewhere to swim: not what these test


def plot_score(item):
    """A plot is ranked on the Investment land goal, not as somewhere to live."""
    return score_detail(item, mode="land")


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


def test_more_than_7_days_above_35_by_2090_are_not_wanted():
    # the day count beats the monthly mean: a place can average 30 °C and still roast
    mild = {"ssp245_2081-2100": 30.0}
    cool, reasons = score_detail(with_climate(HOME, heat=mild, hot_days={"today": 1, "rcp45_2071-2100": 4,
                                                                         "rcp85_2071-2100": 9}))
    hot, hot_reasons = score_detail(with_climate(HOME, heat=mild, hot_days={"rcp45_2071-2100": 10}))
    oven, oven_reasons = score_detail(with_climate(HOME, heat=mild, hot_days={"rcp45_2071-2100": 30}))
    assert cool > hot > oven
    assert "4 days a year above 35 °C by 2071-2100, 9 worst case; 1 today" in reasons
    assert hot <= 60 and any(r.startswith("too hot in 50-70 years") for r in hot_reasons)
    assert any(r.startswith("rejected: too hot") for r in oven_reasons)


def test_permanent_water_fire_flood_and_stress():
    base, _ = plot_score(PLOT)
    wet, reasons = plot_score(with_climate(PLOT, water_km=0.3))
    assert wet - base >= 13 and "permanent water 0.3 km away" in reasons
    approx, _ = plot_score(with_climate(PLOT, water_km=0.3, approx=True))
    assert base < approx < wet
    burnt, reasons = plot_score(with_climate(PLOT, fire={"count": 2, "years": [2017, 2022], "burnt_here": True,
                                                         "km": 2}))
    assert burnt < base and "burnt since 2016 (2017, 2022) — EFFIS" in reasons
    dry, reasons = plot_score(with_climate(PLOT, stress={"stress_2080": 4}))
    assert dry < base and "water stress extremely high by 2080 (WRI Aqueduct)" in reasons
    flooded, reasons = score_detail(with_climate(HOME, flood_m=0.8))
    assert flooded < score_detail(with_climate(HOME))[0] and "in the 100-year flood zone (0.8 m) — JRC" in reasons


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


def test_future_fire_danger(tmp_path, monkeypatch):
    monkeypatch.setattr(climate, "data_dir", lambda cfg=None: str(tmp_path))
    climate._fire_danger.cache_clear()
    (tmp_path / "layers").mkdir()
    lat, lon = np.meshgrid(np.array([40.0, 40.1], "float32"), np.array([-7.3, -7.2], "float32"), indexing="ij")
    np.savez_compressed(tmp_path / "layers" / "fire_danger.npz", lat=lat, lon=lon,
                        gt30_historical_1981_2005=np.full((2, 2), 27.0, "float32"),
                        gt30_rcp45_2079_2098=np.array([[47.4, 90], [90, 90]], "float32"))
    got = climate.fire_danger(40.01, -7.29)
    assert got == {"high_days_now": 27.0, "high_days_2090": 47.4}
    assert climate.fire_danger(45.0, 10.0) is None                      # off the grid
    climate._fire_danger.cache_clear()
    calm, _ = plot_score(with_climate(PLOT, fire_danger={"high_days_2090": 10}))
    risky, reasons = plot_score(with_climate(PLOT, fire_danger={"high_days_2090": 90, "high_days_now": 60}))
    assert calm - risky >= 15 and "90 days a year of high fire danger by 2079-2098 (60 today) — Copernicus" in reasons


def test_no_climate_libraries_means_no_climate_not_a_failed_scan(layers, db, add, monkeypatch):
    import json
    monkeypatch.setattr(climate, "_libraries", lambda: False)
    add("citius", "c1", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=json.dumps({"lat": 40.495, "lon": -7.405}))
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    assert not climate.available()
    assert climate.assess_pending(db, items, {}) == 0
    assert climate.for_item(items[0]) is None


def test_exact_seaside_homes_are_read_before_a_missing_grade_rejects_them(layers, db, add, monkeypatch):
    """Live and invest must not be the first time a seaside house is judged.

    A home the score filter left out of the queue is still read when its pin is
    the building and the sea is within 1.5 km. A town pin is not, and neither
    is a plot. One already read does not use up the budget. A missing grade
    stays unknown: it does not pass, and it is not called poor."""
    import json

    import geo
    from scoring import score_detail
    add("citius", "inland", title="Moradia no interior", tipo="moradia", area_m2=90, price=20000,
        raw_json=json.dumps({"lat": 40.50, "lon": -7.50}))
    add("citius", "sea", title="Moradia pela praia", tipo="moradia", area_m2=90, price=20000,
        raw_json=json.dumps({"lat": 40.60, "lon": -7.60}))
    add("citius", "town", title="Moradia na vila", tipo="moradia", area_m2=90, price=20000,
        raw_json=json.dumps({"geo": {"lat": 40.70, "lon": -7.70, "precision": "municipality"}}))
    add("citius", "plot", title="Terreno rustico", tipo="terreno", area_m2=20000, price=9000,
        raw_json=json.dumps({"lat": 40.80, "lon": -7.80}))

    def fake_beach(item, path=None, towns=None):
        title = item.get("title") or ""
        if "praia" in title or "Terreno" in title:
            return {"km": 0.4, "approx": item.get("title", "").startswith("Moradia na"), "name": "Praia"}
        if "vila" in title:
            return {"km": 0.4, "approx": True, "name": "Praia"}
        return {"km": 12.0, "approx": False, "name": "Praia"}

    monkeypatch.setattr(geo, "nearest_beach", fake_beach)
    inland = dict(db.execute("SELECT * FROM listings WHERE id = ?", ("citius:inland",)).fetchone())
    assert climate.assess_pending(db, [inland], {}, limit=1, seaside_first=True) == 1
    sea = dict(db.execute("SELECT * FROM listings WHERE id = ?", ("citius:sea",)).fetchone())
    town = dict(db.execute("SELECT * FROM listings WHERE id = ?", ("citius:town",)).fetchone())
    plot = dict(db.execute("SELECT * FROM listings WHERE id = ?", ("citius:plot",)).fetchone())
    inland = dict(db.execute("SELECT * FROM listings WHERE id = ?", ("citius:inland",)).fetchone())
    assert climate.stored(sea) is not None
    assert climate.stored(inland) is None
    assert climate.stored(town) is None
    assert climate.stored(plot) is None
    # Already read: the one slot goes to the inland house, not to the sea again.
    assert climate.assess_pending(db, [inland], {}, limit=1, seaside_first=True) == 1
    inland = dict(db.execute("SELECT * FROM listings WHERE id = ?", ("citius:inland",)).fetchone())
    assert climate.stored(inland) is not None
    unread = {"title": "Moradia T3", "tipo": "moradia", "area_m2": 90, "price": 20000,
              "description": "moradia em bom estado", "source": "citius", "country": "PT",
              "beach": {"km": 0.4, "approx": False},
              "raw_json": json.dumps({"lat": 40.60, "lon": -7.60})}
    assert score_detail(unread, mode="both")[0] == 0
    assert score_detail(unread, mode="both")[1][0].startswith("climate unknown")


def test_the_scan_stores_the_climate_and_the_list_only_reads_it(layers, db, add):
    import json

    from db import load_listings
    add("citius", "c1", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=json.dumps({"lat": 40.495, "lon": -7.405}))
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    assert climate.assess_pending(db, items, {}) == 1
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    assert climate.assess_pending(db, items, {}) == 0                  # up to date: not read again
    listed = load_listings(db, filters={}, apply_min_score=False)[0]
    assert listed["climate"]["heat"] == {"ssp245_2081-2100": 33.3}
    assert any("33.3 °C summer max by 2081-2100" in r for r in listed["reasons"])


def write_gpkg(path, layers, index=True):
    """A GeoPackage written by hand, {layer: [(geometry, {column: value})]} in
    EPSG:4326: every other geometry carries an envelope, as GDAL may write it,
    and each layer its R-tree (rtree_<layer>_geom) unless `index` is False."""
    import sqlite3
    import struct

    import shapely
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT, srs_id INTEGER PRIMARY KEY, organization TEXT,
                                           organization_coordsys_id INTEGER, definition TEXT, description TEXT);
        INSERT INTO gpkg_spatial_ref_sys VALUES ('WGS 84', 4326, 'EPSG', 4326, 'undefined', NULL);
        CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT,
                                            srs_id INTEGER, z INTEGER, m INTEGER);
    """)
    for layer, rows in layers.items():
        columns = sorted({c for _, attrs in rows for c in attrs})
        names, marks = "".join(f', "{c}"' for c in columns), ", ?" * len(columns)
        db.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, geom BLOB{names})')
        db.execute("INSERT INTO gpkg_contents VALUES (?, 'features', 4326)", (layer,))
        db.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', 'GEOMETRY', 4326, 0, 0)", (layer,))
        if index:
            db.execute(f'CREATE VIRTUAL TABLE "rtree_{layer}_geom" USING rtree(id, minx, maxx, miny, maxy)')
        for i, (geom, attrs) in enumerate(rows):
            minx, miny, maxx, maxy = geom.bounds
            if i % 2:
                header = b"GP" + bytes([0, 0b001]) + struct.pack("<i", 4326)
            else:
                header = b"GP" + bytes([0, 0b011]) + struct.pack("<i4d", 4326, minx, maxx, miny, maxy)
            fid = db.execute(f'INSERT INTO "{layer}" (geom{names}) VALUES (?{marks})',
                             (header + shapely.to_wkb(geom), *(attrs.get(c) for c in columns))).lastrowid
            if index:
                db.execute(f'INSERT INTO "rtree_{layer}_geom" VALUES (?, ?, ?, ?, ?)', (fid, minx, maxx, miny, maxy))
    db.commit()
    db.close()


def _write_maps(folder, index):
    from shapely.geometry import box
    write_gpkg(folder / "burnt_areas.gpkg", {"burnt_areas": [
        (box(-7.41, 40.49, -7.40, 40.50), {"year": 2017}),       # where the house is
        (box(-7.39, 40.49, -7.38, 40.50), {"year": 2022}),       # 1.3 km east
        (box(-7.00, 40.00, -6.99, 40.01), {"year": 2019})]}, index=index)     # far away
    basin = box(-8, 40, -7, 41)
    write_gpkg(folder / "aqueduct_europe.gpkg", {
        "baseline_annual": [(basin, {"bws_cat": 3.0, "drr_cat": 2.0, "name_0": "Portugal"})],
        "baseline_monthly": [(basin, {"bws_01_cat": 4.0})],
        "future_annual": [(basin, {"bau50_ws_x_c": 4.0, "bau80_ws_x_c": 4.0})]}, index=index)
    for cache in (climate._fires, climate._basins, climate._gpkg_index):
        cache.cache_clear()


@pytest.fixture
def vector_layers(layers, monkeypatch):
    """The burnt areas and the Aqueduct basins, with pyogrio and fiona unable to
    load, as when Windows Smart App Control refuses their GDAL DLLs."""
    monkeypatch.setitem(sys.modules, "pyogrio", None)
    monkeypatch.setitem(sys.modules, "fiona", None)
    _write_maps(layers / "layers", index=True)
    yield layers
    for cache in (climate._fires, climate._basins, climate._gpkg_index):
        cache.cache_clear()


@pytest.fixture
def unindexed_layers(layers):
    """The same maps without their R-trees: the whole map is loaded."""
    _write_maps(layers / "layers", index=False)
    yield layers
    for cache in (climate._fires, climate._basins, climate._gpkg_index):
        cache.cache_clear()


def test_fire_and_water_layers_are_read_without_gdal(vector_layers):
    assert climate.fires_near(40.495, -7.405) == {"count": 2, "years": [2017, 2022], "burnt_here": True, "km": 2.0}
    assert climate.water_stress(40.495, -7.405) == {"stress_now": 3, "drought": 2,
                                                    "stress_2050": 4, "stress_2080": 4}
    got = climate.assess(40.495, -7.405)
    assert got["fire"]["burnt_here"] and got["stress"]["stress_2080"] == 4
    assert not climate.maps_loaded()                       # through the R-trees: no whole map in memory
    gdf = climate._read_gpkg(str(vector_layers / "layers" / "aqueduct_europe.gpkg"), layer="baseline_annual")
    assert list(gdf.columns) == ["bws_cat", "drr_cat", "name_0", "geometry"]       # no fid, as read_file
    assert gdf.crs.to_epsg() == 4326


def test_without_an_index_the_whole_map_gives_the_same_answers(unindexed_layers):
    assert climate.fires_near(40.495, -7.405) == {"count": 2, "years": [2017, 2022], "burnt_here": True, "km": 2.0}
    assert climate.water_stress(40.495, -7.405) == {"stress_now": 3, "drought": 2,
                                                    "stress_2050": 4, "stress_2080": 4}
    assert climate.maps_loaded()


def test_the_index_finds_what_the_whole_map_finds(layers):
    """300 burnt areas and four basins side by side, 200 places: the areas
    near the 2 km edge too, read through the R-tree or from the whole map."""
    import random

    from shapely.geometry import box
    rnd = random.Random(7)
    areas = []
    for _ in range(300):
        x, y = rnd.uniform(-7.6, -7.2), rnd.uniform(40.3, 40.7)
        areas.append((box(x, y, x + rnd.uniform(0.001, 0.03), y + rnd.uniform(0.001, 0.03)),
                      {"year": rnd.randint(2016, 2025)}))
    strips = [box(-8 + i * 0.25, 40, -8 + (i + 1) * 0.25, 41) for i in range(4)]
    places = [(rnd.uniform(40.3, 40.7), rnd.uniform(-7.65, -7.15)) for _ in range(200)]

    def answers(index):
        folder = layers / "layers"
        write_gpkg(folder / "burnt_areas.gpkg", {"burnt_areas": areas}, index=index)
        write_gpkg(folder / "aqueduct_europe.gpkg", {
            "baseline_annual": [(s, {"bws_cat": float(i), "drr_cat": 1.0}) for i, s in enumerate(strips)],
            "future_annual": [(s, {"bau50_ws_x_c": float(i), "bau80_ws_x_c": 4.0}) for i, s in enumerate(strips)]},
            index=index)
        for cache in (climate._fires, climate._basins, climate._gpkg_index):
            cache.cache_clear()
        return [(climate.fires_near(lat, lon), climate.water_stress(lat, lon)) for lat, lon in places]

    indexed = answers(True)
    assert indexed == answers(False)
    assert sum(1 for fire, _ in indexed if fire["count"]) > 50          # the places do meet burnt areas
    for cache in (climate._fires, climate._basins, climate._gpkg_index):
        cache.cache_clear()


def test_a_layer_that_cannot_be_read_stores_nothing(layers, db, add, monkeypatch):
    """A reading without its fire layer looks safer than the place is: it is
    not stored, the panel says why, and the next scan reads it again."""
    import json

    import listing_info
    from db import load_listings
    add("citius", "c1", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=json.dumps({"lat": 40.495, "lon": -7.405}))
    add("citius", "c2", title="Moradia T2", tipo="moradia", area_m2=80, price=15000,
        raw_json=json.dumps({"lat": 40.30, "lon": -7.30}))
    calls = []

    def blocked(lat, lon):
        calls.append((lat, lon))
        raise ImportError("DLL load failed while importing _io: An Application Control policy has blocked this file.")

    def rows():
        return [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]

    monkeypatch.setattr(climate, "fires_near", blocked)
    assert climate.assess_pending(db, rows(), {}) == 0
    assert len(calls) == 1                       # the library will not load: no other place is tried
    assert all("climate" not in json.loads(it["raw_json"]) for it in rows())
    assert climate.for_item(rows()[0]) is None
    assert "fire: ImportError" in climate.problem()
    listed = load_listings(db, filters={}, apply_min_score=False)[0]
    assert listed["climate"] is None
    assert "could not be read" in listing_info.climate_panel(listed)["missing"]

    def odd(lat, lon):
        calls.append((lat, lon))
        raise ValueError("a burnt area without a year")

    calls.clear()
    monkeypatch.setattr(climate, "fires_near", odd)
    assert climate.assess_pending(db, rows(), {}) == 0
    assert len(calls) == 2                       # one odd place does not stop the others
    assert all("climate" not in json.loads(it["raw_json"]) for it in rows())

    monkeypatch.setattr(climate, "fires_near",
                        lambda lat, lon: {"count": 0, "years": [], "burnt_here": False, "km": 2.0})
    assert climate.assess_pending(db, rows(), {}) == 2
    assert all(climate.stored(it)["fire"]["count"] == 0 for it in rows())
    assert climate.problem() is None


def test_a_layer_not_on_this_pc_is_skipped_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "rasterio", None)          # would not even load
    climate._raster.cache_clear()
    try:
        assert climate._raster(str(tmp_path / "not-here.tif")) is None
    finally:
        climate._raster.cache_clear()


def test_a_reading_belongs_to_its_own_spot_not_its_100_m_cell(vector_layers, db, add):
    """The burnt area's east edge is at -7.400. A pin 17 m inside it and one
    17 m outside are in the same ~100 m cell, but not in the same fire."""
    import json
    inside, outside = -7.4002, -7.3998
    assert climate._place_key({"lat": 40.495, "lon": inside}) == climate._place_key({"lat": 40.495, "lon": outside})
    assert climate.assess(40.495, inside)["fire"]["burnt_here"] is True
    assert climate.assess(40.495, outside)["fire"]["burnt_here"] is False       # not the neighbour's reading

    add("citius", "c1", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=json.dumps({"lat": 40.495, "lon": inside}))

    def row():
        return dict(db.execute("SELECT * FROM listings WHERE id = 'citius:c1'").fetchone())

    assert climate.assess_pending(db, [row()], {}) == 1
    assert climate.stored(row())["fire"]["burnt_here"] is True
    at = json.loads(row()["raw_json"])["climate"]["at"]
    raw = json.loads(row()["raw_json"])
    raw["lon"] = outside                                   # a better pin, 34 m east
    db.execute("UPDATE listings SET raw_json = ? WHERE id = 'citius:c1'", (json.dumps(raw),))
    db.commit()
    assert climate.assess_pending(db, [row()], {}) == 1                  # moved: read again
    assert climate.stored(row())["fire"]["burnt_here"] is False
    assert json.loads(row()["raw_json"])["climate"]["at"] == at          # site_check keeps its position
    assert climate.assess_pending(db, [row()], {}) == 0                  # up to date


def test_a_reading_stored_before_spots_is_read_once_more(layers, db, add):
    import json
    add("citius", "c1", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=json.dumps({"lat": 40.495, "lon": -7.405, "climate": {
            "at": "40.495,-7.405", "v": climate.VERSION, "layers": climate._layers_stamp(),
            "result": {"heat": {"ssp245_2081-2100": 30.0}}}}))
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    assert climate.assess_pending(db, items, {}) == 1
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    assert climate.stored(items[0])["heat"] == {"ssp245_2081-2100": 33.3}
    assert climate.assess_pending(db, items, {}) == 0


def test_the_maps_a_batch_loaded_are_let_go_after_it(unindexed_layers, db, add):
    """Without their R-trees the burnt areas and the basins are loaded whole
    (~500 MB): kept for the life of the app they filled a 6 GB PC. A batch
    that loaded them lets them go."""
    import json
    add("citius", "c1", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
        raw_json=json.dumps({"lat": 40.495, "lon": -7.405}))
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    assert not climate.maps_loaded()
    assert climate.assess_pending(db, items, {}) == 1
    assert climate.stored([dict(r) for r in db.execute("SELECT * FROM listings")][0])["fire"]["burnt_here"]
    assert not climate.maps_loaded()
    climate._fires()                                       # loaded by someone else: theirs to let go
    climate._CACHE.clear()
    add("citius", "c2", title="Moradia T2", tipo="moradia", area_m2=80, price=15000,
        raw_json=json.dumps({"lat": 40.30, "lon": -7.30}))
    assert climate.assess_pending(db, [dict(r) for r in db.execute("SELECT * FROM listings")], {}) == 1
    assert climate.maps_loaded()
