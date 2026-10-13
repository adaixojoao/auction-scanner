"""climate.py — will this place still be liveable, wet and safe in 50-70 years?

The owner buys land and houses to keep. Four risks are read at a listing's
position from public datasets (scripts/update_climate.py builds the layers in
the climate data folder, config climate.data_dir):

- heat:  the mean daily maximum of the warmest month (WorldClim BIO5), median
         of 13 CMIP6 models, for 2061-2080 and 2081-2100 under SSP2-4.5 (and
         SSP5-8.5 for the worst case). The owner's rule: summers no hotter than
         35 °C in 50-70 years.
- water: permanent water within 1 km (JRC permanent water bodies) and the
         sub-basin's water stress now and in 2050/2080 (WRI Aqueduct 4.0).
- fire:  EFFIS burnt areas since 2016 within 2 km, and the days a year of high
         fire danger in 2079-2098 (Copernicus fire danger indicators, RCP4.5).
- flood: water depth of the 100-year river flood at the spot (JRC).

`assess(item)` returns a dict of what is known (layers not on this PC are
skipped); load_listings stores it in item["climate"] and scoring.py turns it
into points. Results are kept per position to about a metre: a house 40 m
away can be on the other side of a river basin's edge or of the 2 km fire limit.

A layer that is on this PC but cannot be read (a library Windows would not
load, a damaged file) raises `Unreadable`, and nothing is stored: the listing
stays "not read yet" and a later scan reads it again. A reading without its
fire or water layer looks safer than the place is.
"""
from __future__ import annotations

import functools
import json
import math
import os
import sqlite3
from contextlib import closing

SCENARIOS = ("ssp245", "ssp585")
PERIODS = ("2061-2080", "2081-2100")
MAIN = ("ssp245", "2081-2100")               # the figure the score uses
FIRE_KM = 2.0
WATER_KM = 1.5   # the owner swims within 1.5 km (scoring.SWIM_MAX_KM)


def data_dir(cfg: dict | None = None) -> str:
    if cfg is None:
        try:
            from config import load_config
            cfg = load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    return ((cfg or {}).get("climate") or {}).get("data_dir") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auction-climate-data")


def _layers() -> str:
    return os.path.join(data_dir(), "layers")


@functools.lru_cache(maxsize=256)   # Forestry reads ~30 tree maps per place
def _raster(path: str):
    if not os.path.exists(path):
        return None                  # not on this PC: skipped, not a failure
    import rasterio
    return rasterio.open(path)


# ─── GeoPackages, read without GDAL ─────────────────────────────────
# A GeoPackage is an SQLite file. pyogrio and fiona bring their own GDAL DLLs,
# which Windows Smart App Control refuses to load (no known publisher), and the
# fire and water layers could not be read. sqlite3 and shapely read them.

_GPKG_ENVELOPE = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}   # bytes after the header, by its envelope code


def _gpkg(path: str):
    from pathlib import Path
    return sqlite3.connect(Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True)


def _gpkg_layers(path: str) -> list[str]:
    with closing(_gpkg(path)) as db:
        return [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents WHERE data_type = 'features'")]


def _gpkg_wkb(blob) -> bytes | None:
    """The WKB in a GeoPackage geometry: after the 8-byte "GP" header and its envelope."""
    if blob is None:
        return None
    b = bytes(blob)
    if b[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry")
    return b[8 + _GPKG_ENVELOPE[(b[3] >> 1) & 0b111]:]


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _read_gpkg(path: str, layer: str | None = None):
    """A layer as geopandas.read_file gives it: the attribute columns (not the
    fid), then `geometry`, in the layer's CRS. Without `layer`, the first one."""
    import geopandas as gpd
    import pandas as pd
    import shapely
    layer = layer or _gpkg_layers(path)[0]
    with closing(_gpkg(path)) as db:
        geom, srs_id = db.execute("SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name = ?",
                                  (layer,)).fetchone()
        org, code, wkt = db.execute("SELECT organization, organization_coordsys_id, definition "
                                    "FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,)).fetchone()
        columns = [c[1] for c in db.execute(f"PRAGMA table_info({_quoted(layer)})") if c[1] != geom and not c[5]]
        picked = ", ".join(_quoted(c) for c in [*columns, geom])
        rows = db.execute(f"SELECT {picked} FROM {_quoted(layer)}").fetchall()
    crs = f"EPSG:{code}" if str(org).upper() == "EPSG" else (wkt if wkt and wkt != "undefined" else None)
    return gpd.GeoDataFrame(pd.DataFrame([r[:-1] for r in rows], columns=columns),
                            geometry=shapely.from_wkb([_gpkg_wkb(r[-1]) for r in rows]), crs=crs)


# ─── Near a place, through the R-tree ───────────────────────────────
# GDAL writes an R-tree (rtree_<layer>_<geom>) for every GeoPackage layer.
# Through it a reading takes the few polygons near the place, not the whole
# map (~500 MB in memory). The coordinates are reprojected by geopandas' own
# routine and transformer, so the answers are those of the whole map. Each
# reading opens the file and closes it again: a map stays free to be rebuilt.


@functools.lru_cache(maxsize=16)
def _gpkg_index(path: str, mtime: float) -> dict | None:
    """{layer: (geometry column, fid column, attribute columns)} when every
    feature layer is in EPSG:4326 and has its R-tree; None otherwise."""
    with closing(_gpkg(path)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        layers = {}
        for layer, geom, srs_id in db.execute(
                "SELECT g.table_name, g.column_name, g.srs_id FROM gpkg_geometry_columns g "
                "JOIN gpkg_contents c ON c.table_name = g.table_name WHERE c.data_type = 'features'").fetchall():
            org, code = db.execute("SELECT organization, organization_coordsys_id FROM gpkg_spatial_ref_sys "
                                   "WHERE srs_id = ?", (srs_id,)).fetchone()
            if f"rtree_{layer}_{geom}" not in tables or str(org).upper() != "EPSG" or int(code) != 4326:
                return None
            info = list(db.execute(f"PRAGMA table_info({_quoted(layer)})"))
            layers[layer] = (geom, next(c[1] for c in info if c[5]),
                             [c[1] for c in info if c[1] != geom and not c[5]])
    return layers


def _index_of(path: str) -> dict | None:
    return _gpkg_index(path, os.path.getmtime(path)) if os.path.exists(path) else None


def _near_rows(con, layer: str, index: dict, box: tuple) -> tuple[list[dict], object]:
    """(attributes, geometries) of the features whose envelope meets `box`
    (minx, miny, maxx, maxy in degrees), in fid order."""
    import shapely
    geom, fid, columns = index[layer]
    picked = ", ".join(_quoted(c) for c in [*columns, geom])
    rows = con.execute(
        f"SELECT {picked} FROM {_quoted(layer)} WHERE {_quoted(fid)} IN (SELECT id FROM "
        f"{_quoted(f'rtree_{layer}_{geom}')} WHERE maxx >= ? AND minx <= ? AND maxy >= ? AND miny <= ?) "
        f"ORDER BY {_quoted(fid)}", (box[0], box[2], box[1], box[3])).fetchall()
    return [dict(zip(columns, r[:-1])) for r in rows], shapely.from_wkb([_gpkg_wkb(r[-1]) for r in rows])


@functools.lru_cache(maxsize=4)
def _transformer(src: str, dst: str):
    """geopandas' own cached transformer (what GeoDataFrame.to_crs uses)."""
    from geopandas.array import TransformerFromCRS
    from pyproj import CRS
    return TransformerFromCRS(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def _box_4326(zone, margin: float = 0.02) -> tuple:
    """A lon/lat box around a shape in EPSG:3035, with a margin (~2 km): the
    straight edges in metres bow a little in degrees."""
    xs, ys = zone.exterior.coords.xy
    lons, lats = _transformer("EPSG:3035", "EPSG:4326").transform(list(xs), list(ys))
    return min(lons) - margin, min(lats) - margin, max(lons) + margin, max(lats) + margin


def _sample(path: str, lat: float, lon: float) -> float | None:
    src = _raster(path)
    if src is None:
        return None
    try:
        row, col = src.index(lon, lat)
        if not (0 <= row < src.height and 0 <= col < src.width):
            return None
        import rasterio.windows
        val = src.read(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0]
    except Exception:  # noqa: BLE001 — outside the grid
        return None
    val = float(val)
    if math.isnan(val) or (src.nodata is not None and val == src.nodata):
        return None
    return val


def heat(lat: float, lon: float) -> dict:
    out = {}
    today = _sample(os.path.join(_layers(), "heat_today.tif"), lat, lon)
    if today is not None:
        out["today"] = round(today, 1)
    for ssp in SCENARIOS:
        for period in PERIODS:
            v = _sample(os.path.join(_layers(), f"heat_{ssp}_{period}.tif"), lat, lon)
            if v is not None:
                out[f"{ssp}_{period}"] = round(v, 1)
    return out


HOT_DAYS_PERIODS = ("today", "rcp45_2071-2100", "rcp85_2071-2100")


def hot_days(lat: float, lon: float) -> dict:
    """Days a year above 35 °C (Copernicus climate atlas, EURO-CORDEX 12 km,
    bias-adjusted ensemble median): today = 1976-2005."""
    out = {}
    for period in HOT_DAYS_PERIODS:
        v = _sample(os.path.join(_layers(), f"hot35_{period}.tif"), lat, lon)
        if v is not None:
            out[period] = round(v, 1)
    return out


def flood_depth(lat: float, lon: float) -> float | None:
    v = _sample(os.path.join(data_dir(), "jrc_flood", "Europe_RP100_filled_depth.tif"), lat, lon)
    return round(v, 2) if v is not None and v > 0 else (0.0 if v is not None else None)


def permanent_water_km(lat: float, lon: float, max_km: float = WATER_KM) -> float | None:
    """Distance to the nearest permanent water pixel within max_km, or None."""
    src = _raster(os.path.join(data_dir(), "jrc_flood", "Europe_permanent_water_bodies.tif"))
    if src is None:
        return None
    import numpy as np
    import rasterio.windows
    from rasterio.warp import transform as warp
    xs, ys = warp("EPSG:4326", src.crs, [lon], [lat]) if src.crs and src.crs.to_epsg() != 4326 else ([lon], [lat])
    row, col = src.index(xs[0], ys[0])
    px_m = abs(src.transform.a) if src.crs and src.crs.is_projected else abs(src.transform.a) * 111_000 * math.cos(
        math.radians(lat))
    r = max(1, int(max_km * 1000 / max(px_m, 1)))
    win = rasterio.windows.Window(col - r, row - r, 2 * r + 1, 2 * r + 1)
    try:
        block = src.read(1, window=win, boundless=True, fill_value=0)
    except Exception:  # noqa: BLE001
        return None
    wet = np.argwhere(block > 0)
    if not len(wet):
        return None
    d = np.sqrt(((wet - r) ** 2).sum(axis=1)).min() * px_m / 1000
    return round(float(d), 2) if d <= max_km else None


@functools.lru_cache(maxsize=1)
def _fires():
    path = os.path.join(_layers(), "burnt_areas.gpkg")
    if not os.path.exists(path):
        return None
    gdf = _read_gpkg(path).to_crs(3035)              # metres, equal-area Europe
    gdf.sindex
    return gdf


def fires_near(lat: float, lon: float, km: float = FIRE_KM) -> dict | None:
    """Burnt areas meeting the `km` zone around the place (EPSG:3035, metres).
    Through the map's R-tree only the areas near the place are read; the
    whole map (~270 MB in memory) is loaded only when the file has no index."""
    import shapely
    from shapely.geometry import Point
    path = os.path.join(_layers(), "burnt_areas.gpkg")
    x, y = _transformer("EPSG:4326", "EPSG:3035").transform(lon, lat)
    here = Point(x, y)
    zone = here.buffer(km * 1000)
    index = _index_of(path)
    if index:
        from geopandas.array import transform
        with closing(_gpkg(path)) as con:
            attrs, shapes = _near_rows(con, next(iter(index)), index, _box_4326(zone))
        if len(shapes):
            shapes = transform(shapes, _transformer("EPSG:4326", "EPSG:3035").transform)
        meets = shapely.intersects(shapes, zone)
        years = [a["year"] for a, m in zip(attrs, meets) if m]
        return {"count": len(years), "years": sorted({int(y) for y in years}),
                "burnt_here": bool(shapely.intersects(shapes[meets], here).any()) if years else False, "km": km}
    gdf = _fires()
    if gdf is None:
        return None
    hits = gdf.iloc[list(gdf.sindex.query(zone, predicate="intersects"))]
    inside = hits[hits.intersects(here)]
    return {"count": int(len(hits)), "years": sorted({int(y) for y in hits["year"]}),
            "burnt_here": bool(len(inside)), "km": km}


@functools.lru_cache(maxsize=1)
def _basins():
    path = os.path.join(_layers(), "aqueduct_europe.gpkg")
    if not os.path.exists(path):
        return None
    layers = {}
    for name in _gpkg_layers(path):
        gdf = _read_gpkg(path, layer=name)
        gdf.sindex
        layers[name] = gdf
    return layers


def _category(row: dict, prefix: str):
    for col in row:
        c = col.lower()
        if c.startswith(prefix) and c.endswith("_cat"):
            try:
                return int(row[col])
            except (TypeError, ValueError):
                return None
    return None


def _basins_at(lat: float, lon: float):
    """(layer, the sub-basin's attributes) for each Aqueduct layer with a
    basin at the place. Through the map's R-tree only the basins there are
    read; the whole map (~250 MB in memory) is loaded only without an index."""
    import shapely
    from shapely.geometry import Point
    pt = Point(lon, lat)
    path = os.path.join(_layers(), "aqueduct_europe.gpkg")
    index = _index_of(path)
    if index:
        found = []
        with closing(_gpkg(path)) as con:
            for name in index:
                if "annual" not in name.lower():
                    continue                 # monthly: water_stress reads only the annual layers
                attrs, shapes = _near_rows(con, name, index, (lon, lat, lon, lat))
                first = next((a for a, m in zip(attrs, shapely.intersects(shapes, pt)) if m), None)
                if first is not None:
                    found.append((name, first))
        yield from found
        return
    for name, gdf in (_basins() or {}).items():
        hits = gdf.iloc[list(gdf.sindex.query(pt, predicate="intersects"))]
        if len(hits):
            yield name, hits.iloc[0].to_dict()


def water_stress(lat: float, lon: float) -> dict | None:
    """Aqueduct categories (0 low … 4 extremely high; -1 arid & low water use)."""
    out = {}
    for name, row in _basins_at(lat, lon):
        low = name.lower()
        if "baseline" in low and "annual" in low:
            out["stress_now"] = _category(row, "bws")
            out["drought"] = _category(row, "drr")
        elif "future" in low and "annual" in low:
            for col in row:
                c = col.lower()
                # e.g. bau50_ws_x_c / bau80_ws_x_c (business as usual, 2050 / 2080)
                if c in ("bau50_ws_x_c", "bau80_ws_x_c"):
                    try:
                        out["stress_2050" if "50" in c else "stress_2080"] = int(row[col])
                    except (TypeError, ValueError):
                        pass
    return out or None


@functools.lru_cache(maxsize=1)
def _fire_danger():
    path = os.path.join(_layers(), "fire_danger.npz")
    if not os.path.exists(path):
        return None
    import numpy as np
    from scipy.spatial import cKDTree
    data = dict(np.load(path))
    lat, lon = data.pop("lat"), data.pop("lon")
    tree = cKDTree(np.column_stack([lat.ravel(), lon.ravel()]))
    return tree, {k: v.ravel() for k, v in data.items()}


_MAPS = (_fires, _basins, _fire_danger)


def maps_loaded() -> bool:
    """A map is in memory (a cached "not on this PC" is not)."""
    return any(load.cache_info().currsize and load() is not None for load in _MAPS)


def release_maps() -> None:
    """Let go of the burnt areas, the basins and the fire danger grid: about
    500 MB that otherwise stayed in memory for as long as the app ran. The
    next reading loads them again (~20 s); a reading already under way keeps
    the copy it has."""
    for load in _MAPS:
        load.cache_clear()


def fire_danger(lat: float, lon: float) -> dict | None:
    """Days a year with high (FWI > 30) and very high (> 45) fire danger, now
    (1981-2005) and in 2079-2098 (RCP4.5, and RCP8.5 as the worst case):
    Copernicus / EURO-CORDEX multi-model mean, cells of ~12 km."""
    found = _fire_danger()
    if not found:
        return None
    tree, layers = found
    dist, i = tree.query([lat, lon])
    if dist > 0.2:                                   # outside the EURO-CORDEX grid (or at sea)
        return None
    import math
    pick = lambda key: (round(float(layers[key][i]), 1)  # noqa: E731
                        if key in layers and not math.isnan(float(layers[key][i])) else None)
    out = {"high_days_now": pick("gt30_historical_1981_2005"),
           "high_days_2090": pick("gt30_rcp45_2079_2098"), "very_high_days_2090": pick("gt45_rcp45_2079_2098"),
           "high_days_2090_worst": pick("gt30_rcp85_2079_2098"), "fwi_summer_2090": pick("jjas_rcp45_2079_2098")}
    return {k: v for k, v in out.items() if v is not None} or None


_CACHE: dict[str, dict] = {}


def amoc_cold(lat: float, lon: float) -> dict:
    """The 1-in-10-year coldest day (°C) with the AMOC as now ("on") and
    collapsed ("off"), both in a 2 °C warmer world (van Westen & Baatsen 2025,
    one model on a ~200 km grid: a stress test, not a forecast)."""
    model = {}
    for name in ("on", "off"):
        v = _sample(os.path.join(_layers(), f"amoc_cold10_{name}.tif"), lat, lon)
        if v is not None:
            model[name] = v
    if len(model) < 2:
        return {}
    # Delta method: the model's change, added to the local 12 km cold (which
    # knows the altitude). Without the local layer, the coarse model's own values.
    today = _sample(os.path.join(_layers(), "cold10_today.tif"), lat, lon)
    change = _sample(os.path.join(_layers(), "cold10_change.tif"), lat, lon)
    local = today if today is not None and change is not None else None
    on = today + change if local is not None else model["on"]
    return {"on": round(on, 1), "off": round(on + model["off"] - model["on"], 1),
            "local": local is not None}


class Unreadable(RuntimeError):
    """A layer is on this PC but could not be read. `everywhere` when a library
    or file would not even load (Windows refused a DLL, say): no other position
    can be read then either."""

    def __init__(self, layer: str, error: Exception):
        super().__init__(f"{layer}: {type(error).__name__}: {error}")
        self.everywhere = isinstance(error, (ImportError, OSError))


_PROBLEM: list[str] = []     # why the last reading in this process failed; a full reading clears it


def problem() -> str | None:
    """Why the climate could not be read just now, for the listing's panel."""
    return _PROBLEM[-1] if _PROBLEM else None


def _read(layer: str, fn, lat: float, lon: float):
    try:
        return fn(lat, lon)
    except Exception as e:  # noqa: BLE001 — any failure: the reading is incomplete
        err = Unreadable(layer, e)
        _PROBLEM[:] = [str(err)]
        raise err from e


def assess(lat: float, lon: float) -> dict:
    """Every layer on this PC at the position. Raises Unreadable rather than
    return a reading with a layer missing."""
    key = _spot({"lat": lat, "lon": lon})
    if key in _CACHE:
        return _CACHE[key]
    out: dict = {}
    h = _read("heat", heat, lat, lon)
    if h:
        out["heat"] = h
    d = _read("hot days", hot_days, lat, lon)
    if d:
        out["hot_days"] = d
    a = _read("AMOC cold", amoc_cold, lat, lon)
    if a:
        out["amoc_cold10"] = a
    dry = _read("AMOC dry", lambda la, lo: _sample(os.path.join(_layers(), "amoc_dry_change.tif"), la, lo),
                lat, lon)
    if dry is not None:
        out["amoc_dry_mm"] = round(dry)
    for name, fn in (("flood_m", flood_depth), ("water_km", permanent_water_km), ("fire", fires_near),
                     ("stress", water_stress), ("fire_danger", fire_danger)):
        v = _read(name, fn, lat, lon)
        if v is not None:
            out[name] = v
    _PROBLEM.clear()
    _CACHE[key] = out
    return out


@functools.lru_cache(maxsize=1)
def _libraries() -> bool:
    """requirements-climate.txt is optional: without it the scan must not fail."""
    import importlib.util
    return all(importlib.util.find_spec(name) is not None for name in ("numpy", "rasterio"))


def available() -> bool:
    if not (os.path.isdir(_layers()) or os.path.isdir(os.path.join(data_dir(), "jrc_flood"))):
        return False
    return _libraries()


def for_item(item: dict, towns: dict | None = None) -> dict | None:
    """The assessment at the listing's own position, else its town's (approximate)."""
    if not available():
        return None
    import geo
    pos = geo._place(item, towns)
    if not pos:
        return None
    try:
        out = assess(pos["lat"], pos["lon"])
    except Unreadable:
        return None                  # unknown, never a reading with a layer missing
    if not out:
        return None
    return {**out, "approx": pos.get("precision") not in geo.EXACT_ENOUGH}


def as_json(result: dict | None) -> str:
    return json.dumps(result or {}, ensure_ascii=False)


# ─── Stored per listing ─────────────────────────────────────────────
# Reading the layers takes ~0.1 s a place: fine once, far too slow for every
# page load. The scan stores the result in raw["climate"] with the position
# it was read at; load_listings only reads it back. A listing that moved, even
# a few metres (a better position found later), or a rebuilt layer set is read
# again.

VERSION = 3   # 3: water searched to 1.5 km


def _place_key(pos: dict) -> str:
    """Where a reading was taken, to ~100 m ("at"): site_check and forestry use it as the position."""
    return f"{pos['lat']:.3f},{pos['lon']:.3f}"


def _spot(pos: dict) -> str:
    """The same to ~1 m ("spot"): whether the stored reading is this position's."""
    return f"{pos['lat']:.5f},{pos['lon']:.5f}"


def _layers_stamp() -> str:
    try:
        with open(os.path.join(_layers(), "built.json"), encoding="utf-8") as f:
            return json.load(f).get("built", "")
    except (OSError, ValueError):
        return ""


def stored(item: dict) -> dict | None:
    """The climate stored for this listing's current pin.

    A reading taken somewhere else — the pin has moved, or it was stored before
    readings named their spot — is not this pin's, so the score does not use it."""
    import geo
    kept = geo._raw(item).get("climate") or {}
    result = kept.get("result")
    if not isinstance(result, dict) or not result:
        return None
    pos = geo.position(item)
    if pos and kept.get("spot") != _spot(pos):
        return None
    return result


def remember(item: dict, result: dict) -> None:
    """Keep a climate just read on the listing, in the same shape as a scan.

    The next load uses it and does not read the layers again. Nothing is written
    here: the caller saves `raw_json` when `_climate_write` is set."""
    import geo
    pos = geo._place(item, None) or {}
    raw = dict(geo._raw(item))
    placed = pos.get("lat") is not None and pos.get("lon") is not None
    raw["climate"] = {"at": _place_key(pos) if placed else "", "spot": _spot(pos) if placed else "",
                      "v": VERSION, "layers": _layers_stamp(), "result": result}
    item["raw_json"] = json.dumps(raw, ensure_ascii=False)
    item["_climate_write"] = True


def _exact_seaside_home(item: dict, towns: dict) -> bool:
    """A home whose pin is the building, with the sea within WATER_KM of that pin.

    A town pin does not count, and neither does a river or a lake: the distance
    is the sea beach, and only when it is this building's."""
    import geo
    from scoring import property_kind
    pos = geo.position(item)
    if not pos or pos.get("precision") not in geo.EXACT_ENOUGH:
        return False
    if property_kind(item) != "home":
        return False
    beach = item.get("beach")
    if not isinstance(beach, dict):
        beach = geo.nearest_beach(item, towns=towns) or {}
    km = beach.get("km")
    return km is not None and not beach.get("approx") and km <= WATER_KM


def _still_for_sale(item: dict, today: str) -> bool:
    if item.get("duplicate_of"):
        return False
    end = str(item.get("date_end") or "")[:10]
    return not end or end >= today


def _seaside_ahead(db, items: list[dict], towns: dict) -> list[dict]:
    """Exact seaside homes first, including ones a score filter left out of `items`.

    Live and invest rejects a climate that was never read. That rejection has to
    follow a reading. Poor and caution still fail; a missing lookup does not
    pass and is not graded as poor.

    A row on disk over common.check_max_price is not read, and from
    common.light_checks_from neither is one already over the cash before any
    repair (db.heavy_checks_spared): Live and invest could never show it. One
    in `items` can be shown elsewhere, and is read."""
    import db as dbmod
    from common import price_to_pay, utcnow
    today = utcnow().date().isoformat()
    statuses, offers = dbmod.listing_statuses(db), dbmod.latest_offers(db)
    cash = dbmod._cash_on_hand()
    cap, light_from = dbmod._work_limits()
    seaside: list[dict] = []
    seen: set = set()
    # The row on disk, not the copy taken before this scan's geocoding: that
    # copy can still say the pin is the town after the street was found.
    for row in db.execute("SELECT * FROM listings"):
        item = dict(row)
        blob = item.get("raw_json") or ""
        if "lat" not in blob and "geo" not in blob:
            continue
        item["status"] = statuses.get(item["id"])
        mine = item["status"] == "shortlisted" or item["id"] in offers
        if cap and not mine and price_to_pay(dict(item)) > cap:
            continue
        if not _still_for_sale(item, today) or not _exact_seaside_home(item, towns):
            continue
        if dbmod.heavy_checks_spared(item, cash, light_from):
            continue
        seaside.append(item)
        seen.add(item["id"])
    for it in items:
        if it.get("id") not in seen and _exact_seaside_home(it, towns):
            seaside.append(it)
            seen.add(it["id"])
    return seaside + [it for it in items if it.get("id") not in seen]


def assess_pending(db, items: list[dict], towns: dict, limit: int = 3000, *,
                   seaside_first: bool = False) -> int:
    """Read the climate layers for the listings whose stored result is missing
    or out of date (moved, or the layers were rebuilt).

    `seaside_first` reads exact seaside homes before the rest of `items`, so a
    scan's budget of `limit` is not spent inland while Live and invest still
    has no grade for the houses by the sea. A single listing (a position just
    verified) leaves this off.

    A reading with a layer that could not be read is not stored: the listing
    stays unread and the next scan tries again."""
    if not available():
        return 0
    import geo
    from common import LOG
    if seaside_first:
        items = _seaside_ahead(db, items, towns)
    loaded_before = maps_loaded()
    stamp, done = _layers_stamp(), 0
    for item in items:
        if done >= limit:
            break
        # Geocoding in this same scan writes the new pin to the database and
        # leaves this copy where it was. Compare the pin that is stored now.
        fresh = db.execute("SELECT raw_json FROM listings WHERE id = ?", (item.get("id"),)).fetchone()
        if fresh and fresh[0]:
            item = {**dict(item), "raw_json": fresh[0]}
        pos = geo._place(item, towns)
        if not pos:
            continue
        raw = geo._raw(item)
        kept = raw.get("climate") or {}
        if (kept.get("at") == _place_key(pos) and kept.get("spot") == _spot(pos)
                and kept.get("v") == VERSION and kept.get("layers") == stamp):
            continue
        try:
            result = assess(pos["lat"], pos["lon"]) or {}
        except Unreadable as e:
            LOG.warning(f"Climate not stored for {item.get('id')}: {e}. A later scan reads it again.")
            if e.everywhere:
                break
            continue
        result = {**result, "approx": pos.get("precision") not in geo.EXACT_ENOUGH} if result else None
        raw["climate"] = {"at": _place_key(pos), "spot": _spot(pos), "v": VERSION, "layers": stamp,
                          "result": result}
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        done += 1
        if done % 200 == 0:
            db.commit()
    db.commit()
    if not loaded_before:
        release_maps()               # loaded for this batch: not kept for the life of the app
    return done
