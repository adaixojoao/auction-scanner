"""Site check: what a specific piece of forest land is really like, from open data.

For a shortlisted listing, given its cadastral parcels (France) or a position and
a radius, this reports:
  - slope and altitude: Copernicus DEM GLO-30 (30 m, read tile by tile from the
    public AWS bucket, only the window needed);
  - forest type (France): IGN BD Forêt v2 — closed/open forest, broadleaf/conifer;
  - timber extraction (France): IGN forest accessibility map — how far a
    forwarder must haul to a road;
  - other countries: OpenStreetMap tracks and roads near the land;
  - protection: Natura 2000 (EEA, all EU), ZNIEFF (France);
  - a satellite view link.

    python site_check.py --lat 45.30 --lon 4.00 --radius 250
    python site_check.py --parcels 43246:0A:0123,43246:0A:0124

Slope classes follow forestry practice: under 30% wheeled machines work;
30-60% needs tracked or winch work; over 60% only cable yarding.
"""
from __future__ import annotations

import argparse
import math
import sys

import requests

from geo import OVERPASS, USER_AGENT, satellite_url

DEM_TILE = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
            "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
IGN_WFS = "https://data.geopf.fr/wfs/ows"
IGN_PARCEL = "https://apicarto.ign.fr/api/cadastre/parcelle"
NATURA = ("https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/Natura2000Sites/"
          "MapServer/2/query")
SLOPE_CLASSES = [(30, "wheeled machines"), (60, "tracked or winch only"), (1000, "cable yarding only")]
DEFAULT_RADIUS_M = 250


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# ─── The land ───────────────────────────────────────────────────────
def parcels_shape(session, refs: list[str]):
    """The union of French cadastral parcels "insee:section:numero" (WGS84)."""
    from shapely.geometry import shape
    from shapely.ops import unary_union
    shapes = []
    for ref in refs:
        insee, section, numero = ref.split(":")
        r = session.get(IGN_PARCEL, params={"code_insee": insee, "section": section.zfill(2),
                                            "numero": numero.zfill(4)}, timeout=60)
        r.raise_for_status()
        shapes += [shape(f["geometry"]) for f in r.json().get("features", [])]
    if not shapes:
        raise ValueError("no parcel found for " + ", ".join(refs))
    return unary_union(shapes)


def circle(lat: float, lon: float, radius_m: float):
    """A circle of radius_m around a point, as a WGS84 polygon."""
    from pyproj import Transformer
    from shapely.geometry import Point
    from shapely.ops import transform
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True).transform
    to_deg = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True).transform
    return transform(to_deg, transform(to_m, Point(lon, lat)).buffer(radius_m))


def area_ha(shape_wgs84) -> float:
    from pyproj import Transformer
    from shapely.ops import transform
    return transform(Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True).transform,
                     shape_wgs84).area / 10000


# ─── Slope ──────────────────────────────────────────────────────────
def _dem_url(lat: int, lon: int) -> str:
    return DEM_TILE.format(ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon))


def slope_stats(shape_wgs84) -> dict | None:
    """{"min_m", "max_m", "mean_pct", "p90_pct", "shares": {class: share}} over the land."""
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.merge import merge
    minx, miny, maxx, maxy = shape_wgs84.bounds
    bounds = (minx - 0.001, miny - 0.001, maxx + 0.001, maxy + 0.001)
    tiles = [_dem_url(la, lo) for la in range(math.floor(bounds[1]), math.floor(bounds[3]) + 1)
             for lo in range(math.floor(bounds[0]), math.floor(bounds[2]) + 1)]
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES"):
        sources = [rasterio.open("/vsicurl/" + t) for t in tiles]
        try:
            mosaic, transform = merge(sources, bounds=bounds)    # reads only the window it needs
        finally:
            for src in sources:
                src.close()
    z = mosaic[0].astype("float64")
    dy = abs(transform.e) * 111320.0
    dx = transform.a * 111320.0 * math.cos(math.radians((miny + maxy) / 2))
    gy, gx = np.gradient(z, dy, dx)
    inside = ~geometry_mask([shape_wgs84], out_shape=z.shape, transform=transform, all_touched=True)
    slope, elev = (np.hypot(gx, gy) * 100)[inside], z[inside]
    if not slope.size:
        return None
    shares, low = {}, 0
    for limit, label in SLOPE_CLASSES:
        shares[label] = float(((slope >= low) & (slope < limit)).mean())
        low = limit
    return {"min_m": float(elev.min()), "max_m": float(elev.max()), "mean_pct": float(slope.mean()),
            "p90_pct": float(np.percentile(slope, 90)), "shares": shares}


# ─── France: forest type and extraction ─────────────────────────────
def _wfs(session, type_name: str, shape_wgs84) -> list[dict]:
    minx, miny, maxx, maxy = shape_wgs84.bounds
    r = session.get(IGN_WFS, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_name,
        "bbox": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326",
        "outputFormat": "application/json", "srsName": "EPSG:4326", "count": 500}, timeout=90)
    r.raise_for_status()
    return r.json().get("features", [])


def _shares(features: list[dict], shape_wgs84, key) -> dict[str, float]:
    """The share of the land covered by each value of `key` (features clipped to the land)."""
    from shapely.geometry import shape
    total = area_ha(shape_wgs84)
    out: dict[str, float] = {}
    for f in features:
        part = shape(f["geometry"]).intersection(shape_wgs84)
        if not part.is_empty:
            label = key(f["properties"])
            out[label] = out.get(label, 0) + area_ha(part) / total
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def forest_types(session, shape_wgs84) -> dict[str, float]:
    return _shares(_wfs(session, "LANDCOVER.FORESTINVENTORY.V2:formation_vegetale", shape_wgs84), shape_wgs84,
                   lambda p: p.get("tfv") or "not forest")


def extraction(session, shape_wgs84) -> dict[str, float]:
    return _shares(_wfs(session, "IGNF_ACCESSIBILITE-PHYSIQUE-FORETS-:acces_porteur", shape_wgs84), shape_wgs84,
                   lambda p: p.get("cat") or "unknown")


def znieff(session, shape_wgs84) -> list[str]:
    names = []
    for kind in ("znieff1", "znieff2"):
        for f in _wfs(session, f"patrinat_{kind}:{kind}", shape_wgs84):
            from shapely.geometry import shape
            if not shape(f["geometry"]).intersects(shape_wgs84):
                continue
            names.append(f"ZNIEFF {kind[-1]}: {f['properties'].get('nom') or f['properties'].get('nom_site') or '?'}")
    return names


# ─── Everywhere: protection and access ──────────────────────────────
def natura2000(session, shape_wgs84) -> list[str]:
    import json
    from shapely.geometry import mapping
    rings = [list(map(list, ring)) for ring in mapping(shape_wgs84.convex_hull)["coordinates"]]
    r = session.post(NATURA, data={
        "geometry": json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPolygon", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "SITECODE,SITENAME", "returnGeometry": "false",
        "f": "json"}, timeout=60)
    r.raise_for_status()
    return [f"Natura 2000 {a['attributes'].get('SITECODE')}: {a['attributes'].get('SITENAME')}"
            for a in r.json().get("features", [])]


def tracks_near(session, lat: float, lon: float, radius_m: int) -> dict[str, int]:
    """OpenStreetMap ways a vehicle can use within radius_m: {kind: count}."""
    q = (f'[out:json][timeout:30];way(around:{radius_m},{lat},{lon})'
         f'["highway"~"^(track|unclassified|tertiary|secondary|service|residential)$"];out tags;')
    import time
    for attempt in range(3):
        r = session.post(OVERPASS, data={"data": q}, timeout=60)
        if r.status_code not in (429, 504):
            break
        time.sleep(10 * (attempt + 1))                 # the public server is busy: wait and ask again
    r.raise_for_status()
    out: dict[str, int] = {}
    for el in r.json().get("elements", []):
        tags = el.get("tags") or {}
        kind = tags["highway"] + (f" (grade {tags['tracktype'][-1]})" if tags.get("tracktype") else "")
        out[kind] = out.get(kind, 0) + 1
    return out


# ─── The report ─────────────────────────────────────────────────────
def check(lat: float | None = None, lon: float | None = None, radius_m: float = DEFAULT_RADIUS_M,
          parcels: list[str] | None = None, country: str = "FR") -> dict:
    session = _session()
    land = parcels_shape(session, parcels) if parcels else circle(lat, lon, radius_m)
    c = land.centroid
    lat, lon = c.y, c.x
    out = {"lat": lat, "lon": lon, "hectares": area_ha(land), "exact": bool(parcels),
           "satellite": satellite_url({"lat": lat, "lon": lon})}
    steps = [("slope", lambda: slope_stats(land)), ("natura2000", lambda: natura2000(session, land))]
    if country.upper() == "FR":
        steps += [("forest", lambda: forest_types(session, land)), ("extraction", lambda: extraction(session, land)),
                  ("znieff", lambda: znieff(session, land))]
    steps.append(("tracks", lambda: tracks_near(session, lat, lon, int(max(radius_m, 300)))))
    for name, step in steps:
        try:
            out[name] = step()
        except Exception as e:  # noqa: BLE001 — one service down must not hide the others
            out[name] = None
            out.setdefault("errors", []).append(f"{name}: {type(e).__name__}")
    return out


def describe(r: dict) -> list[str]:
    lines = [f"{r['hectares']:.1f} ha {'(the parcels)' if r['exact'] else '(a circle around the position)'} "
             f"at {r['lat']:.5f}, {r['lon']:.5f} — {r['satellite']}"]
    s = r.get("slope")
    if s:
        lines.append(f"altitude {s['min_m']:.0f}-{s['max_m']:.0f} m; slope mean {s['mean_pct']:.0f}%, "
                     f"steepest tenth over {s['p90_pct']:.0f}%")
        lines.append("  " + ", ".join(f"{share:.0%} {label}" for label, share in s["shares"].items()))
    if r.get("forest") is not None:
        lines.append("forest (IGN BD Forêt): " + (", ".join(f"{v:.0%} {k}" for k, v in r["forest"].items())
                                                  or "no forest mapped"))
    if r.get("extraction") is not None:
        lines.append("timber extraction (IGN): " + (", ".join(f"{v:.0%} {k}" for k, v in r["extraction"].items())
                                                    or "not mapped"))
    protected = (r.get("natura2000") or []) + (r.get("znieff") or [])
    lines.append("protection: " + ("; ".join(protected) if protected else "none found (Natura 2000"
                                   + (", ZNIEFF" if r.get("znieff") is not None else "") + ")"))
    if r.get("tracks") is not None:
        lines.append("tracks and roads nearby (OSM): " + (", ".join(f"{n} {k}" for k, n in r["tracks"].items())
                                                          or "none mapped"))
    for e in r.get("errors", []):
        lines.append(f"not checked: {e}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS_M, help="metres around the position")
    ap.add_argument("--parcels", help="French cadastral parcels insee:section:numero, comma-separated")
    ap.add_argument("--country", default="FR")
    a = ap.parse_args()
    if not a.parcels and (a.lat is None or a.lon is None):
        ap.error("give --parcels, or --lat and --lon")
    r = check(a.lat, a.lon, a.radius, a.parcels.split(",") if a.parcels else None, a.country)
    print("\n".join(describe(r)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
