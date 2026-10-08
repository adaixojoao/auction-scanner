"""
Build the climate-risk layers the scanner reads (climate.py) from the public
datasets downloaded into the climate data folder (config: climate.data_dir,
default the auction-climate-data folder next to the app). Run it once after downloading, and
again when a dataset is refreshed:

    python scripts/update_climate.py              # everything
    python scripts/update_climate.py heat fire    # just these parts

Parts:
- heat:  WorldClim 2.1 CMIP6 bioclimatic variables (2.5'), all 13 models:
         the ensemble median of BIO5 (mean daily maximum of the warmest month)
         for SSP2-4.5 and SSP5-8.5, 2061-2080 and 2081-2100, and today's
         (1970-2000), cropped to Europe → layers/heat_<ssp>_<period>.tif.
- fire:  EFFIS burnt-area polygons 2016 to last year for the scanned
         countries, from the EFFIS web feature service → layers/burnt_areas.gpkg.
- water: WRI Aqueduct 4.0 sub-basins (baseline and 2050/2080 water stress,
         drought risk) cropped to Europe → layers/aqueduct_europe.gpkg.
- firefuture: Copernicus "Fire danger indicators for Europe 1970-2098"
         (EURO-CORDEX, multi-model mean): days a year with high and very high
         fire danger and the seasonal FWI, 2041-2060 and 2079-2098, RCP4.5 and
         RCP8.5, plus 1981-2000. Needs the owner's free Copernicus account: the
         token in %USERPROFILE%\\.cdsapirc ("url: …" and "key: …" lines) and the
         dataset's licence accepted on its page. The token is only sent to CDS.
The JRC flood depth (100-year) and permanent water rasters are read as they
are downloaded (jrc_flood/).
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import climate  # noqa: E402

EUROPE = (-32.0, 27.0, 45.0, 72.0)      # west, south, east, north (the Azores reach -31.3°)
EFFIS_WFS = "https://maps.effis.emergency.copernicus.eu/effis"
FIRE_BOXES = {"PT": (-31.5, 32.4, -6.1, 42.2), "ES": (-18.2, 27.6, 4.4, 43.8), "FR": (-5.2, 41.3, 9.6, 51.2),
              "IT": (6.6, 35.4, 18.6, 47.1), "HR": (13.4, 42.3, 19.5, 46.6), "GR": (19.3, 34.8, 29.7, 41.8),
              "CY": (32.2, 34.5, 34.7, 35.8)}
FIRST_FIRE_YEAR = 2016


def build_heat(data_dir: str, out_dir: str) -> None:
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds

    def crop(path, band=5):
        with rasterio.open(path) as src:
            win = from_bounds(*EUROPE, transform=src.transform).round_offsets().round_lengths()
            arr = src.read(band, window=win).astype("float32")
            nodata = src.nodata
            profile = src.profile.copy()
            profile.update(height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                           transform=src.window_transform(win), compress="deflate", nodata=np.nan)
        if nodata is not None:
            arr[arr == nodata] = np.nan
        arr[arr < -100] = np.nan
        return arr, profile

    folder = os.path.join(data_dir, "worldclim")
    for ssp in climate.SCENARIOS:
        for period in climate.PERIODS:
            files = sorted(glob.glob(os.path.join(folder, f"wc2.1_2.5m_bioc_*_{ssp}_{period}.tif")))
            if not files:
                print(f"heat {ssp} {period}: no files")
                continue
            stack, profile = [], None
            for f in files:
                try:
                    arr, profile = crop(f)
                except Exception as e:  # noqa: BLE001 — a broken download: skip it, say so
                    print(f"heat {ssp} {period}: skipped {os.path.basename(f)} ({type(e).__name__}); download it again")
                    continue
                stack.append(arr)
            if not stack:
                continue
            median = np.nanmedian(np.stack(stack), axis=0).astype("float32")
            out = os.path.join(out_dir, f"heat_{ssp}_{period}.tif")
            with rasterio.open(out, "w", **profile) as dst:
                dst.write(median, 1)
            print(f"heat {ssp} {period}: median of {len(files)} models → {out}")
    base_zip = os.path.join(folder, "wc2.1_2.5m_bio.zip")
    if os.path.exists(base_zip):
        with zipfile.ZipFile(base_zip) as z:
            name = next(n for n in z.namelist() if n.endswith("bio_5.tif"))
            z.extract(name, folder)
        arr, profile = crop(os.path.join(folder, name), band=1)
        with rasterio.open(os.path.join(out_dir, "heat_today.tif"), "w", **profile) as dst:
            dst.write(arr, 1)
        print("heat today (1970-2000) → heat_today.tif")


def build_fire(out_dir: str) -> None:
    import datetime

    import geopandas as gpd
    import pandas as pd
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (auction-scanner climate layers)"
    frames = []
    for year in range(FIRST_FIRE_YEAR, datetime.date.today().year):
        for country, (w, s, e, n) in FIRE_BOXES.items():
            for attempt in range(4):
                try:
                    r = session.get(EFFIS_WFS, params={
                        "SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
                        "TYPENAME": f"ms:modis.ba.poly.{year}", "BBOX": f"{s},{w},{n},{e},EPSG:4326",
                        "OUTPUTFORMAT": "geojson"}, timeout=600)
                    r.raise_for_status()
                    feats = r.json().get("features") or []
                    break
                except Exception as ex:  # noqa: BLE001 — busy server: wait and retry
                    print(f"  fire {year} {country}: {type(ex).__name__}, retrying")
                    time.sleep(30 * (attempt + 1))
            else:
                print(f"  fire {year} {country}: skipped")
                continue
            if feats:
                gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
                gdf["year"] = year
                frames.append(gdf[["id", "year", "FIREDATE", "COUNTRY", "AREA_HA", "geometry"]])
            print(f"fire {year} {country}: {len(feats)} burnt areas")
    if frames:
        allf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
        allf = allf.drop_duplicates(subset=["id", "year"])
        out = os.path.join(out_dir, "burnt_areas.gpkg")
        allf.to_file(out, driver="GPKG")
        print(f"{len(allf)} burnt areas → {out}")


def build_water(data_dir: str, out_dir: str) -> None:
    import geopandas as gpd
    zpath = os.path.join(data_dir, "aqueduct", "aqueduct-4-0-water-risk-data.zip")
    if not os.path.exists(zpath):
        print("water: Aqueduct zip not downloaded")
        return
    target = os.path.join(data_dir, "aqueduct", "unzipped")
    if not os.path.isdir(target):
        with zipfile.ZipFile(zpath) as z:
            z.extractall(target)
    gdbs = glob.glob(os.path.join(target, "**", "*.gdb"), recursive=True)
    if not gdbs:
        print("water: no geodatabase in the Aqueduct zip")
        return
    import fiona
    layers = fiona.listlayers(gdbs[0])
    print("Aqueduct layers:", layers)
    out = os.path.join(out_dir, "aqueduct_europe.gpkg")
    for layer in layers:
        gdf = gpd.read_file(gdbs[0], layer=layer, bbox=EUROPE)
        keep = [c for c in gdf.columns if c == "geometry" or c.lower().startswith(("pfaf", "bws", "bwd", "drr", "iav", "bau", "opt", "pes",
                                                                                      "sev", "rfr", "w_awr", "name"))]
        gdf[keep].to_file(out, layer=layer, driver="GPKG")
        print(f"water: {layer}: {len(gdf)} sub-basins in Europe")


CDS_DATASET = "sis-tourism-fire-danger-indicators"
FIRE_FUTURE = [  # (experiment, period): the 20-25 year blocks the dataset offers
    ("historical", "1981_2005"), ("rcp4_5", "2041_2060"), ("rcp4_5", "2079_2098"),
    ("rcp8_5", "2041_2060"), ("rcp8_5", "2079_2098")]
# The seasonal FWI is a "seasonal indicator"; the days of (very) high danger are
# "annual indicators" (checked with the CDS constraints endpoint, Sept 2026).
FIRE_REQUESTS = {
    "fwi": {"time_aggregation": "seasonal_indicators", "variable": ["seasonal_fire_weather_index"]},
    "days": {"time_aggregation": "annual_indicators",
             "variable": ["number_of_days_with_high_fire_danger", "number_of_days_with_very_high_fire_danger"]},
}


def _cds_credentials() -> tuple[str, str]:
    path = os.path.join(os.path.expanduser("~"), ".cdsapirc")
    url = key = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            name, _, value = line.partition(":")
            if name.strip() == "url":
                url = value.strip()
            elif name.strip() == "key":
                key = value.strip()
    if not url or not key:
        raise SystemExit(f"{path} needs a 'url:' and a 'key:' line")
    return url.rstrip("/"), key


def cds_retrieve(request: dict, target: str) -> None:
    """Ask the Copernicus Climate Data Store for one request and save the result."""
    import requests
    url, key = _cds_credentials()
    headers = {"PRIVATE-TOKEN": key}
    r = requests.post(f"{url}/retrieve/v1/processes/{CDS_DATASET}/execution", json={"inputs": request},
                      headers=headers, timeout=120)
    if r.status_code in (401, 403):
        raise SystemExit(f"Copernicus refused the request ({r.status_code}): check the token in .cdsapirc and "
                         f"that the licence is accepted on the dataset page. {r.text[:200]}")
    r.raise_for_status()
    job = r.json()["jobID"]
    while True:
        status = requests.get(f"{url}/retrieve/v1/jobs/{job}", headers=headers, timeout=60).json().get("status")
        if status == "successful":
            break
        if status in ("failed", "rejected", "dismissed"):
            detail = requests.get(f"{url}/retrieve/v1/jobs/{job}/results", headers=headers, timeout=60).text
            raise RuntimeError(f"CDS job {status}: {detail[:300]}")
        time.sleep(15)
    href = requests.get(f"{url}/retrieve/v1/jobs/{job}/results", headers=headers,
                        timeout=60).json()["asset"]["value"]["href"]
    with requests.get(href, stream=True, timeout=600) as dl:
        dl.raise_for_status()
        with open(target, "wb") as f:
            for chunk in dl.iter_content(1 << 20):
                f.write(chunk)


def build_fire_future(data_dir: str, out_dir: str) -> None:
    folder = os.path.join(data_dir, "copernicus_fire")
    os.makedirs(folder, exist_ok=True)
    for experiment, period in FIRE_FUTURE:
        for what, base in FIRE_REQUESTS.items():
            target = os.path.join(folder, f"fire_{what}_{experiment}_{period}.zip")
            if os.path.exists(target) and os.path.getsize(target) > 0:
                print(f"firefuture {what} {experiment} {period}: already downloaded")
                continue
            request = {**base, "product_type": "multi_model_mean_case", "experiment": experiment,
                       "period": period, "version": "v1_0"}   # the multi-model products are v1_0 only
            print(f"firefuture {what} {experiment} {period}: asking Copernicus …", flush=True)
            cds_retrieve(request, target)
            print(f"firefuture {what} {experiment} {period}: {os.path.getsize(target) / 1e6:.1f} MB", flush=True)
    build_fire_danger_layer(folder, out_dir)


def build_fire_danger_layer(folder: str, out_dir: str) -> None:
    """The EURO-CORDEX grids (rotated pole, 0.11°), averaged over their years,
    with each cell's latitude and longitude → layers/fire_danger.npz, keys like
    "gt30_rcp45_2079_2098" (days a year with FWI > 30, high danger), "gt45_…"
    (very high) and "jjas_…" (mean FWI June-September)."""
    import re as _re
    import numpy as np
    import xarray as xr
    nc = os.path.join(folder, "nc")
    for z in glob.glob(os.path.join(folder, "*.zip")):
        with zipfile.ZipFile(z) as f:
            f.extractall(nc)
    out, grid = {}, None
    for path in sorted(glob.glob(os.path.join(nc, "*.nc"))):
        m = _re.search(r"_(historical|rcp45|rcp85)_fwi-(nods-gt-30|nods-gt-45|mean-jjas)_(\d{4})\d{4}_(\d{4})", path)
        if not m:
            continue
        exp, what, y0, y1 = m.groups()
        short = {"nods-gt-30": "gt30", "nods-gt-45": "gt45", "mean-jjas": "jjas"}[what]
        key = f"{short}_{exp}_{y0}_{y1}"
        with xr.open_dataset(path) as ds:
            var = next(v for v in ds.data_vars if ds[v].ndim == 3)
            out[key] = ds[var].mean(dim="time").values.astype("float32")
            if grid is None:
                grid = (ds["lat"].values.astype("float32"), ds["lon"].values.astype("float32"))
        print(f"fire danger: {key}")
    if grid is not None:
        np.savez_compressed(os.path.join(out_dir, "fire_danger.npz"), lat=grid[0], lon=grid[1], **out)
        print(f"fire danger: {len(out)} layers → fire_danger.npz")


HOT_DAYS_FILES = {"today": ("historical", 1976, 2005), "rcp45_2071-2100": ("rcp_4_5", 2071, 2100),
                  "rcp85_2071-2100": ("rcp_8_5", 2071, 2100)}


def hot_days_request(experiment: str) -> dict:
    """Copernicus climate atlas: monthly days above 35 °C, EURO-CORDEX, bias-adjusted."""
    return {"origin": "cordex_eur_11", "experiment": experiment, "domain": "euro_cordex",
            "period": "1970-2005" if experiment == "historical" else "2006-2100",
            "variable": "monthly_extreme_hot_days", "bias_adjustment": "isimip_method"}


def build_hot_days(data_dir: str, out_dir: str) -> None:
    """Days a year above 35 °C: each model's yearly total averaged over the
    period, then the median of the models, cropped to Europe."""
    import zipfile
    import numpy as np
    import rasterio
    import xarray as xr
    from rasterio.transform import from_origin
    global CDS_DATASET
    folder = os.path.join(data_dir, "hot_days")
    os.makedirs(folder, exist_ok=True)
    west, south, east, north = EUROPE
    for name, (experiment, y0, y1) in HOT_DAYS_FILES.items():
        target = os.path.join(folder, f"{experiment}_isimip_method.zip")
        if not os.path.exists(target):
            CDS_DATASET, keep = "multi-origin-c3s-atlas", CDS_DATASET
            try:
                cds_retrieve(hot_days_request(experiment), target)
            finally:
                CDS_DATASET = keep
        with zipfile.ZipFile(target) as z:
            nc = next(n for n in z.namelist() if n.endswith(".nc"))
            path = os.path.join(folder, "nc", experiment, nc)
            if not os.path.exists(path):
                z.extract(nc, os.path.join(folder, "nc", experiment))
        ds = xr.open_dataset(path, mask_and_scale=True)
        var = next(v for v in ds.data_vars if v.startswith("tx35"))
        da = ds[var].sel(time=slice(f"{y0}-01-01", f"{y1}-12-31"), lat=slice(south, north), lon=slice(west, east))
        per_model = []
        for m in range(da.sizes["member"]):
            one = da.isel(member=m).values.astype("float32")
            one[(one < 0) | (one > 31)] = np.nan
            if np.isnan(one).all():
                continue
            per_model.append(np.nansum(one, axis=0) / (y1 - y0 + 1) * np.where(np.isnan(one).all(axis=0), np.nan, 1))
        grid = np.nanmedian(np.stack(per_model), axis=0).astype("float32")
        lats, lons = da.lat.values, da.lon.values
        res = float(lons[1] - lons[0])
        with rasterio.open(os.path.join(out_dir, f"hot35_{name}.tif"), "w", driver="GTiff", height=grid.shape[0],
                           width=grid.shape[1], count=1, dtype="float32", crs="EPSG:4326", nodata=np.nan,
                           transform=from_origin(lons[0] - res / 2, lats[-1] + res / 2, res, res),
                           compress="deflate") as dst:
            dst.write(grid[::-1], 1)
        print(f"hot35_{name}: {len(per_model)} models")


AMOC_ZIP = "https://zenodo.org/records/14586440"   # van Westen & Baatsen 2025, CC-BY 4.0
AMOC_RUNS = {"on": "CESM_0600_RCP45", "off": "CESM_1500_RCP45"}   # AMOC 15 Sv vs collapsed (6 Sv), both ~2 °C warmer


COLD_VAR = "monthly_minimum_of_daily_minimum_temperature"
COLD_BASE = (1976, 2005)


def _cold10(path: str, y0: int, y1: int) -> tuple:
    """(grid, lats, lons): each member's 10th percentile of the yearly coldest
    night over y0-y1 (the 1-in-10-year cold), median over members, °C."""
    import numpy as np
    import xarray as xr
    west, south, east, north = EUROPE
    ds = xr.open_dataset(path, mask_and_scale=True)
    var = next(v for v in ds.data_vars if v.lower().startswith("tnn"))
    lat = "lat" if "lat" in ds.dims else "latitude"
    lon = "lon" if "lon" in ds.dims else "longitude"
    da = ds[var].sel(time=slice(f"{y0}-01-01", f"{y1}-12-31"), **{lat: slice(south, north), lon: slice(west, east)})
    members = range(da.sizes["member"]) if "member" in da.dims else [None]
    per_model = []
    for m in members:
        one = da if m is None else da.isel(member=m)
        yearly = one.groupby("time.year").min("time").values.astype("float32")
        if np.isnan(yearly).all():
            continue
        if np.nanmax(yearly) > 100:          # Kelvin
            yearly -= 273.15
        per_model.append(np.nanpercentile(yearly, 10, axis=0))
    grid = np.nanmedian(np.stack(per_model), axis=0).astype("float32")
    return grid, da[lat].values, da[lon].values


def _cold_download(folder: str, name: str, request: dict) -> str:
    import zipfile
    global CDS_DATASET
    target = os.path.join(folder, f"{name}.zip")
    if not os.path.exists(target):
        CDS_DATASET, keep = "multi-origin-c3s-atlas", CDS_DATASET
        try:
            cds_retrieve(request, target)
        finally:
            CDS_DATASET = keep
    with zipfile.ZipFile(target) as z:
        nc = next(n for n in z.namelist() if n.endswith(".nc"))
        path = os.path.join(folder, "nc", name, nc)
        if not os.path.exists(path):
            z.extract(nc, os.path.join(folder, "nc", name))
    return path


def _write_grid(path: str, grid, lats, lons) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    if lats[0] > lats[-1]:
        lats, grid = lats[::-1], grid[::-1]
    res = float(lons[1] - lons[0])
    with rasterio.open(path, "w", driver="GTiff", height=grid.shape[0], width=grid.shape[1], count=1,
                       dtype="float32", crs="EPSG:4326", nodata=np.nan, compress="deflate",
                       transform=from_origin(lons[0] - res / 2, lats[-1] + res / 2, res, res)) as dst:
        dst.write(grid[::-1], 1)


def build_cold(data_dir: str, out_dir: str) -> None:
    """The local 1-in-10-year coldest night, which knows valleys from mountains:
    cold10_today.tif  measured, E-OBS 1976-2005 (~11 km, from weather stations);
    cold10_change.tif how much milder by 2071-2100 under RCP4.5, EURO-CORDEX
                      12 km (the atlas has this variable only without bias
                      adjustment, so only the models' change is used)."""
    folder = os.path.join(data_dir, "cold")
    os.makedirs(folder, exist_ok=True)
    y0, y1 = COLD_BASE
    obs = _cold_download(folder, "e_obs", {"origin": "e_obs", "domain": "europe", "period": "1950-2024",
                                           "variable": COLD_VAR})
    grid, lats, lons = _cold10(obs, y0, y1)
    _write_grid(os.path.join(out_dir, "cold10_today.tif"), grid, lats, lons)
    print("cold10_today: E-OBS")
    base = {"origin": "cordex_eur_11", "domain": "euro_cordex", "variable": COLD_VAR,
            "bias_adjustment": "no_bias_adjustment"}
    hist = _cold_download(folder, "cordex_historical", {**base, "experiment": "historical", "period": "1970-2005"})
    fut = _cold_download(folder, "cordex_rcp45", {**base, "experiment": "rcp_4_5", "period": "2006-2100"})
    then, lats, lons = _cold10(hist, y0, y1)
    later, _, _ = _cold10(fut, 2071, 2100)
    _write_grid(os.path.join(out_dir, "cold10_change.tif"), later - then, lats, lons)
    print("cold10_change: EURO-CORDEX RCP4.5 2071-2100 minus 1976-2005")


def build_amoc(data_dir: str, out_dir: str) -> None:
    """amoc_cold10_{on,off}.tif: the 1-in-10-year coldest day (°C) in a 2 °C
    warmer world with the AMOC as now and collapsed (CESM, 2° grid, GRL 2025).
    One coarse model: only the difference off - on is used, added to the local
    12 km cold of build_cold() (the delta method)."""
    import zipfile

    import numpy as np
    import rasterio
    import xarray as xr
    from rasterio.transform import from_origin
    from scipy.interpolate import RegularGridInterpolator

    archive = os.path.join(data_dir, "amoc", "amoc.zip")
    if not os.path.exists(archive):
        raise SystemExit(f"Download the archive from {AMOC_ZIP} to {archive} first")
    res, lats, lons = 0.25, np.arange(27, 72.01, 0.25), np.arange(-32, 45.01, 0.25)
    with zipfile.ZipFile(archive) as z:
        for name, run in AMOC_RUNS.items():
            member = next(n for n in z.namelist()
                          if n.endswith(f"Data/{run}/Atmosphere/TEMP_2m_extremes_GEV_fit_minima.nc"))
            target = os.path.join(data_dir, "amoc", f"{run}_minima.nc")
            with open(target, "wb") as f:
                f.write(z.read(member))
            d = xr.open_dataset(target)
            # The 10-year return level of the yearly minimum, as the paper's ReturnValue().
            p = 0.1
            level = d["loc"] - (d["scale"] / d["shape"]) * (1 - (-np.log(1 - p)) ** (-d["shape"]))
            level = level.assign_coords(lon=((level.lon + 180) % 360) - 180).sortby("lon")
            interp = RegularGridInterpolator((level.lat.values, level.lon.values), level.values.astype("float64"))
            yy, xx = np.meshgrid(lats, lons, indexing="ij")
            grid = interp(np.stack([yy, xx], axis=-1)).astype("float32")
            with rasterio.open(os.path.join(out_dir, f"amoc_cold10_{name}.tif"), "w", driver="GTiff",
                               height=grid.shape[0], width=grid.shape[1], count=1, dtype="float32",
                               crs="EPSG:4326", nodata=np.nan, compress="deflate",
                               transform=from_origin(lons[0] - res / 2, lats[-1] + res / 2, res, res)) as dst:
                dst.write(grid[::-1], 1)
            print(f"amoc_cold10_{name}: {run}")


AMOC_HYDRO_ZIP = "https://zenodo.org/records/16905376"   # van Westen et al. 2025, HESS, CC-BY 4.0


def build_amoc_dry(data_dir: str, out_dir: str) -> None:
    """amoc_dry_change.tif: how the April-September water balance (rain minus
    potential evaporation, mm) changes if the AMOC collapses, in a 2 °C warmer
    world (CESM, 2° grid, HESS 2025). Negative = drier. Only the change is
    used: the model's own rain is too low in Galicia."""
    import zipfile

    import numpy as np
    import rasterio
    import xarray as xr
    from rasterio.transform import from_origin
    from scipy.interpolate import RegularGridInterpolator

    archive = os.path.join(data_dir, "amoc_hydro", "AMOC-Hydroclimate.zip")
    if not os.path.exists(archive):
        raise SystemExit(f"Download AMOC-Hydroclimate.zip from {AMOC_HYDRO_ZIP} to {archive} first")
    balance = {}
    with zipfile.ZipFile(archive) as z:
        for name, run in AMOC_RUNS.items():
            target = os.path.join(data_dir, "amoc_hydro", f"{run}_month_4-9.nc")
            with open(target, "wb") as f:
                f.write(z.read(f"Data/{run}/Atmosphere/PREC_POT_EVAP_fields_month_4-9.nc"))
            d = xr.open_dataset(target).mean("time")
            balance[name] = (d["PREC"] - d["POT_EVAP"]) * 183      # mm/day over April-September
    change = (balance["off"] - balance["on"]).where(lambda v: abs(v) < 5000)   # ocean cells hold fill values
    res, lats, lons = 0.25, np.arange(27, 72.01, 0.25), np.arange(-20, 45.01, 0.25)
    interp = RegularGridInterpolator((change.lat.values, change.lon.values), change.values.astype("float64"))
    yy, xx = np.meshgrid(lats, lons, indexing="ij")
    grid = interp(np.stack([yy, xx], axis=-1)).astype("float32")
    with rasterio.open(os.path.join(out_dir, "amoc_dry_change.tif"), "w", driver="GTiff",
                       height=grid.shape[0], width=grid.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", nodata=np.nan, compress="deflate",
                       transform=from_origin(lons[0] - res / 2, lats[-1] + res / 2, res, res)) as dst:
        dst.write(grid[::-1], 1)
    print("amoc_dry_change: CESM RCP4.5 AMOC off minus on, April-September P - PET")


def main(argv=None) -> int:
    parts = (argv if argv is not None else sys.argv[1:]) or ["heat", "fire", "water"]
    data_dir = climate.data_dir()
    out_dir = os.path.join(data_dir, "layers")
    os.makedirs(out_dir, exist_ok=True)
    if "heat" in parts:
        build_heat(data_dir, out_dir)
    if "fire" in parts:
        build_fire(out_dir)
    if "water" in parts:
        build_water(data_dir, out_dir)
    if "hotdays" in parts:
        build_hot_days(data_dir, out_dir)
    if "amoc" in parts:
        build_cold(data_dir, out_dir)
        build_amoc(data_dir, out_dir)
        build_amoc_dry(data_dir, out_dir)
    if "firefuture" in parts:
        build_fire_future(data_dir, out_dir)
    with open(os.path.join(out_dir, "built.json"), "w", encoding="utf-8") as f:
        json.dump({"parts": parts, "built": time.strftime("%Y-%m-%d %H:%M")}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
