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

`assess(item)` returns a dict of what is known (missing layers are skipped);
load_listings stores it in item["climate"] and scoring.py turns it into points.
Results are cached per position, rounded to ~100 m.
"""
from __future__ import annotations

import functools
import json
import math
import os

SCENARIOS = ("ssp245", "ssp585")
PERIODS = ("2061-2080", "2081-2100")
MAIN = ("ssp245", "2081-2100")               # the figure the score uses
FIRE_KM = 2.0
WATER_KM = 1.0


def data_dir(cfg: dict | None = None) -> str:
    if cfg is None:
        try:
            from config import load_config
            cfg = load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    return ((cfg or {}).get("climate") or {}).get("data_dir") or os.path.join(
        os.path.expanduser("~"), "Desktop", "auction-climate-data")


def _layers() -> str:
    return os.path.join(data_dir(), "layers")


@functools.lru_cache(maxsize=16)
def _raster(path: str):
    import rasterio
    return rasterio.open(path) if os.path.exists(path) else None


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
    import geopandas as gpd
    gdf = gpd.read_file(path).to_crs(3035)           # metres, equal-area Europe
    gdf.sindex
    return gdf


def fires_near(lat: float, lon: float, km: float = FIRE_KM) -> dict | None:
    gdf = _fires()
    if gdf is None:
        return None
    from pyproj import Transformer
    from shapely.geometry import Point
    x, y = Transformer.from_crs(4326, 3035, always_xy=True).transform(lon, lat)
    zone = Point(x, y).buffer(km * 1000)
    hits = gdf.iloc[list(gdf.sindex.query(zone, predicate="intersects"))]
    inside = hits[hits.intersects(Point(x, y))]
    return {"count": int(len(hits)), "years": sorted({int(y) for y in hits["year"]}),
            "burnt_here": bool(len(inside)), "km": km}


@functools.lru_cache(maxsize=1)
def _basins():
    path = os.path.join(_layers(), "aqueduct_europe.gpkg")
    if not os.path.exists(path):
        return None
    import fiona
    import geopandas as gpd
    layers = {}
    for name in fiona.listlayers(path):
        gdf = gpd.read_file(path, layer=name)
        gdf.sindex
        layers[name] = gdf
    return layers


def _category(row, prefix: str):
    for col in row.index:
        c = col.lower()
        if c.startswith(prefix) and c.endswith("_cat"):
            try:
                return int(row[col])
            except (TypeError, ValueError):
                return None
    return None


def water_stress(lat: float, lon: float) -> dict | None:
    """Aqueduct categories (0 low … 4 extremely high; -1 arid & low water use)."""
    layers = _basins()
    if not layers:
        return None
    from shapely.geometry import Point
    pt = Point(lon, lat)
    out = {}
    for name, gdf in layers.items():
        hits = gdf.iloc[list(gdf.sindex.query(pt, predicate="intersects"))]
        if not len(hits):
            continue
        row = hits.iloc[0]
        low = name.lower()
        if "baseline" in low and "annual" in low:
            out["stress_now"] = _category(row, "bws")
            out["drought"] = _category(row, "drr")
        elif "future" in low and "annual" in low:
            for col in row.index:
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


def assess(lat: float, lon: float) -> dict:
    key = f"{lat:.3f},{lon:.3f}"
    if key in _CACHE:
        return _CACHE[key]
    out: dict = {}
    h = heat(lat, lon)
    if h:
        out["heat"] = h
    for name, fn in (("flood_m", flood_depth), ("water_km", permanent_water_km), ("fire", fires_near),
                     ("stress", water_stress), ("fire_danger", fire_danger)):
        try:
            v = fn(lat, lon)
        except Exception:  # noqa: BLE001 — one layer broken must not hide the others
            v = None
        if v is not None:
            out[name] = v
    _CACHE[key] = out
    return out


def available() -> bool:
    return os.path.isdir(_layers()) or os.path.isdir(os.path.join(data_dir(), "jrc_flood"))


def for_item(item: dict, towns: dict | None = None) -> dict | None:
    """The assessment at the listing's own position, else its town's (approximate)."""
    if not available():
        return None
    import geo
    pos = geo._place(item, towns)
    if not pos:
        return None
    out = assess(pos["lat"], pos["lon"])
    if not out:
        return None
    return {**out, "approx": pos.get("precision") not in geo.EXACT_ENOUGH}


def as_json(result: dict | None) -> str:
    return json.dumps(result or {}, ensure_ascii=False)


# ─── Stored per listing ─────────────────────────────────────────────
# Reading the layers takes ~0.1 s a place: fine once, far too slow for every
# page load. The scan stores the result in raw["climate"] with the position
# it was read at; load_listings only reads it back. A listing that moved
# (a better position found later) or a rebuilt layer set is read again.

VERSION = 1


def _place_key(pos: dict) -> str:
    return f"{pos['lat']:.3f},{pos['lon']:.3f}"


def _layers_stamp() -> str:
    try:
        with open(os.path.join(_layers(), "built.json"), encoding="utf-8") as f:
            return json.load(f).get("built", "")
    except (OSError, ValueError):
        return ""


def stored(item: dict) -> dict | None:
    """The climate stored for a listing by the scan, if any."""
    import geo
    return (geo._raw(item).get("climate") or {}).get("result")


def assess_pending(db, items: list[dict], towns: dict, limit: int = 3000) -> int:
    """Read the climate layers for the listings whose stored result is missing
    or out of date (moved, or the layers were rebuilt)."""
    if not available():
        return 0
    import geo
    stamp, done = _layers_stamp(), 0
    for item in items:
        if done >= limit:
            break
        pos = geo._place(item, towns)
        if not pos:
            continue
        raw = geo._raw(item)
        kept = raw.get("climate") or {}
        if kept.get("at") == _place_key(pos) and kept.get("v") == VERSION and kept.get("layers") == stamp:
            continue
        result = assess(pos["lat"], pos["lon"]) or {}
        result = {**result, "approx": pos.get("precision") not in geo.EXACT_ENOUGH} if result else None
        raw["climate"] = {"at": _place_key(pos), "v": VERSION, "layers": stamp, "result": result}
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        done += 1
        if done % 200 == 0:
            db.commit()
    db.commit()
    return done
