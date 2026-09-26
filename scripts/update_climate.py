"""
Build the climate-risk layers the scanner reads (climate.py) from the public
datasets downloaded into the climate data folder (config: climate.data_dir,
default Desktop/auction-climate-data). Run it once after downloading, and
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

EUROPE = (-25.0, 27.0, 45.0, 72.0)      # west, south, east, north
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
                arr, profile = crop(f)
                stack.append(arr)
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
    if "firefuture" in parts:
        build_fire_future(data_dir, out_dir)
    with open(os.path.join(out_dir, "built.json"), "w", encoding="utf-8") as f:
        json.dump({"parts": parts, "built": time.strftime("%Y-%m-%d %H:%M")}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
