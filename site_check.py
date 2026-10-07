"""Site check: what a specific piece of forest land is really like, from open data.

For a shortlisted listing, given its cadastral parcels (France) or a position and
a radius, this reports:
  - slope and altitude: Copernicus DEM GLO-30 (30 m, read tile by tile from the
    public AWS bucket, only the window needed);
  - forest type: France, IGN BD Forêt v2; Portugal, DGT's COS 2025 land cover
    (montados of cork and holm oak included); elsewhere Copernicus forest type
    and tree cover (2018);
  - timber extraction: France, IGN's forest accessibility map; elsewhere an
    estimate from the slope and the nearest OpenStreetMap track;
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
import re
import sys

import requests

from geo import OVERPASS, USER_AGENT, satellite_url

DEM_TILE = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
            "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
IGN_WFS = "https://data.geopf.fr/wfs/ows"
IGN_PARCEL = "https://apicarto.ign.fr/api/cadastre/parcelle"
PT_COS = "https://geo2.dgterritorio.gov.pt/geoserver/COS-S2/wms"   # DGT land cover, COS 2025 (WMS only)
PT_COS_LAYER = "cos2025v1-s2"
EEA_HRL = "https://image.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic/{}/ImageServer/getSamples"
SAMPLE_POINTS = 49          # about a 7 × 7 grid over the land
MONTADO = ("sobreiro", "azinheira")
EUCALYPTUS = ("eucalipto", "eucalyptus")
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


# ─── Portugal: land cover, montados included ────────────────────────
def grid_points(shape_wgs84, n: int = SAMPLE_POINTS) -> list[tuple[float, float]]:
    """About n (lon, lat) points spread evenly inside the land."""
    from shapely.geometry import Point
    minx, miny, maxx, maxy = shape_wgs84.bounds
    side = max(2, round(math.sqrt(n * (maxx - minx) * (maxy - miny) / max(shape_wgs84.area, 1e-12))))
    pts = [(minx + (i + 0.5) * (maxx - minx) / side, miny + (j + 0.5) * (maxy - miny) / side)
           for i in range(side) for j in range(side)]
    inside = [p for p in pts if shape_wgs84.contains(Point(p))]
    return inside or [(shape_wgs84.centroid.x, shape_wgs84.centroid.y)]


def pt_land_cover(session, shape_wgs84) -> dict[str, float]:
    """Share of the land in each COS 2025 class (finest level), from a grid of points."""
    import time
    counts: dict[str, int] = {}
    points = grid_points(shape_wgs84)
    d = 0.0005
    for lon, lat in points:
        r = session.get(PT_COS, params={
            "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": PT_COS_LAYER,
            "query_layers": PT_COS_LAYER, "crs": "EPSG:4326", "bbox": f"{lat - d},{lon - d},{lat + d},{lon + d}",
            "width": 11, "height": 11, "i": 5, "j": 5, "info_format": "application/json", "feature_count": 1},
            timeout=60)
        r.raise_for_status()
        feats = r.json().get("features") or []
        props = feats[0]["properties"] if feats else {}
        label = next((v for k, v in props.items() if k.endswith("_n4_l")), None) or "not mapped"
        counts[label] = counts.get(label, 0) + 1
        time.sleep(0.2)
    return {k: v / len(points) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}


def montado_share(cover: dict[str, float]) -> float:
    """The share of cork-oak and holm-oak montado or forest in a COS breakdown."""
    return sum(v for k, v in cover.items() if any(w in k.lower() for w in MONTADO))


def eucalyptus_share(cover: dict[str, float]) -> float:
    """The share of eucalyptus forest in a COS breakdown."""
    return sum(v for k, v in cover.items() if any(w in k.lower() for w in EUCALYPTUS))


# ─── Everywhere: EU forest type and tree cover ─────────────────────
def eu_forest(session, shape_wgs84) -> dict | None:
    """{"broadleaf", "conifer", "no forest": share, "tree_cover_pct"} from the Copernicus
    high-resolution layers (2018, 10-20 m), sampled over a grid of points."""
    import json
    pts = [list(p) for p in grid_points(shape_wgs84)]
    geometry = json.dumps({"points": pts, "spatialReference": {"wkid": 4326}})
    values = {}
    for name in ("HRL_ForestType_2018", "HRL_TreeCoverDensity_2018"):
        r = session.get(EEA_HRL.format(name), params={"geometry": geometry, "geometryType": "esriGeometryMultipoint",
                                                      "returnFirstValueOnly": "true", "f": "json"}, timeout=60)
        r.raise_for_status()
        values[name] = [int(float(s["value"])) for s in r.json().get("samples", []) if s.get("value") not in (None, "")]
    types = [v for v in values["HRL_ForestType_2018"] if v in (0, 1, 2)]
    cover = [v for v in values["HRL_TreeCoverDensity_2018"] if 0 <= v <= 100]
    if not types:
        return None
    return {"broadleaf": types.count(1) / len(types), "conifer": types.count(2) / len(types),
            "no forest": types.count(0) / len(types), "tree_cover_pct": sum(cover) / len(cover) if cover else None}


def access_estimate(slope: dict | None, track_m: float | None) -> str | None:
    """Where there is no official access map: from the slope and the nearest track."""
    if slope is None or track_m is None:
        return None
    machine = max(slope["shares"], key=slope["shares"].get)
    reach = "a track reaches the land" if track_m <= 50 else f"nearest track {track_m:,.0f} m away"
    if track_m > 500:
        reach += " — long haul"
    return f"mostly {machine} ground, {reach} (estimate from slope and OpenStreetMap)"


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


# Nationally designated areas (CDDA). Natura 2000 does not include every national
# park; Sierra Nevada is one the forestry score has to see.
CDDA = ("https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/CDDA_Dyna_WM/"
        "MapServer/0/query")
_NATIONAL_PARK = re.compile(r"national park|parque nacional|parc national|parco nazionale", re.I)


def national_parks(session, shape_wgs84) -> list[str]:
    """National parks intersecting the land. An empty list is 'none named', not
    proof the land is unprotected — the service can miss a site."""
    import json
    from shapely.geometry import mapping
    rings = [list(map(list, ring)) for ring in mapping(shape_wgs84.convex_hull)["coordinates"]]
    r = session.post(CDDA, data={
        "geometry": json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPolygon", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "siteName,iucncat,designation", "returnGeometry": "false", "f": "json"}, timeout=60)
    r.raise_for_status()
    names = []
    for feat in r.json().get("features") or []:
        attrs = {str(k).lower(): v for k, v in (feat.get("attributes") or {}).items()}
        blob = " ".join(str(v or "") for v in attrs.values())
        if not _NATIONAL_PARK.search(blob):
            continue
        label = attrs.get("sitename") or attrs.get("site_name") or "national park"
        names.append(f"National park: {label}")
    return names


def tracks_near(session, lat: float, lon: float, radius_m: int, land=None) -> dict:
    """OpenStreetMap ways a vehicle can use within radius_m: {kind: count}, plus
    "_nearest_m": the distance from the land to the nearest of them."""
    q = (f'[out:json][timeout:30];way(around:{radius_m},{lat},{lon})'
         f'["highway"~"^(track|unclassified|tertiary|secondary|service|residential)$"];out tags geom;')
    import time
    for attempt in range(3):
        r = session.post(OVERPASS, data={"data": q}, timeout=60)
        if r.status_code not in (429, 504):
            break
        time.sleep(10 * (attempt + 1))                 # the public server is busy: wait and ask again
    r.raise_for_status()
    from pyproj import Transformer
    from shapely.geometry import LineString
    from shapely.ops import transform
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True).transform
    out: dict = {}
    nearest = None
    for el in r.json().get("elements", []):
        tags = el.get("tags") or {}
        kind = tags["highway"] + (f" (grade {tags['tracktype'][-1]})" if tags.get("tracktype") else "")
        out[kind] = out.get(kind, 0) + 1
        pts = [(g["lon"], g["lat"]) for g in el.get("geometry") or []]
        if land is not None and len(pts) >= 2:
            d = transform(to_m, LineString(pts)).distance(transform(to_m, land))
            nearest = d if nearest is None else min(nearest, d)
    out["_nearest_m"] = nearest
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
    steps = [("slope", lambda: slope_stats(land)), ("natura2000", lambda: natura2000(session, land)),
             ("national_parks", lambda: national_parks(session, land))]
    if country.upper() == "FR":
        steps += [("forest", lambda: forest_types(session, land)), ("extraction", lambda: extraction(session, land)),
                  ("znieff", lambda: znieff(session, land))]
    else:
        steps.append(("eu_forest", lambda: eu_forest(session, land)))
    if country.upper() == "PT":
        steps.append(("pt_cover", lambda: pt_land_cover(session, land)))
    steps.append(("tracks", lambda: tracks_near(session, lat, lon, int(max(radius_m, 300)) + 1000, land)))
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
    e = r.get("eu_forest")
    if e:
        lines.append(f"forest (Copernicus 2018): {e['conifer']:.0%} conifer, {e['broadleaf']:.0%} broadleaf, "
                     f"{e['no forest']:.0%} no forest"
                     + (f"; tree cover {e['tree_cover_pct']:.0f}%" if e.get("tree_cover_pct") is not None else ""))
    cover = r.get("pt_cover")
    if cover:
        lines.append("land cover (DGT COS 2025): " + ", ".join(f"{v:.0%} {k}" for k, v in list(cover.items())[:5]))
        lines.append(f"montado (cork or holm oak): {montado_share(cover):.0%} of the land")
        gum = eucalyptus_share(cover)
        if gum:
            lines.append(f"eucalyptus: {gum:.0%} of the land")
    if r.get("extraction") is None and r.get("slope") and r.get("tracks") is not None:
        est = access_estimate(r["slope"], r["tracks"].get("_nearest_m"))
        if est:
            lines.append("timber extraction: " + est)
    if r.get("extraction") is not None:
        lines.append("timber extraction (IGN): " + (", ".join(f"{v:.0%} {k}" for k, v in r["extraction"].items())
                                                    or "not mapped"))
    protected = ((r.get("natura2000") or []) + (r.get("znieff") or [])
                 + (r.get("national_parks") or []))
    lines.append("protection: " + ("; ".join(protected) if protected else "none found (Natura 2000, national parks"
                                   + (", ZNIEFF" if r.get("znieff") is not None else "") + ")"))
    if r.get("tracks") is not None:
        ways = {k: n for k, n in r["tracks"].items() if not k.startswith("_")}
        lines.append("tracks and roads nearby (OSM): " + (", ".join(f"{n} {k}" for k, n in ways.items())
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


# ─── After a scan: the Forestry shortlist ───────────────────────────
# The best Forestry listings get a site check once, stored in raw["site_check"]
# with the position it was made at. Without parcels it describes a circle of the
# listing's own area around its position — approximate, and marked so.
SHORTLIST = 20
CHECK_COUNTRIES = {"PT", "ES", "FR"}
VERSION = 1


def _radius_for(area_m2: float | None) -> float:
    return min(800.0, max(100.0, math.sqrt((area_m2 or 0) / math.pi)))


def summary(r: dict) -> dict:
    """What the score needs from a check, kept small for raw_json."""
    s = r.get("slope") or {}
    shares = s.get("shares") or {}
    extraction = r.get("extraction") or {}
    out = {"v": VERSION, "exact": r.get("exact", False), "hectares": round(r.get("hectares") or 0, 1),
           "cable_share": round(shares.get("cable yarding only", 0), 2),
           "winch_share": round(shares.get("tracked or winch only", 0), 2),
           "slope_mean": round(s["mean_pct"]) if s else None,
           "min_m": round(s["min_m"]) if s.get("min_m") is not None else None,
           "max_m": round(s["max_m"]) if s.get("max_m") is not None else None,
           "inaccessible": round(sum(v for k, v in extraction.items()
                                     if k.lower().startswith(("inaccessible", "zone non exploitable"))), 2)
           if extraction else None,
           "track_m": (r.get("tracks") or {}).get("_nearest_m"),
           "protected": ((r.get("natura2000") or []) + (r.get("znieff") or [])
                         + (r.get("national_parks") or [])),
           "lines": describe(r)}
    if r.get("pt_cover"):
        out["montado"] = round(montado_share(r["pt_cover"]), 2)
        out["eucalyptus"] = round(eucalyptus_share(r["pt_cover"]), 2)
    e = r.get("eu_forest")
    if e:
        out["forest_share"] = round(1 - e["no forest"], 2)
    if r.get("forest") is not None:
        out["forest_share"] = round(sum(v for k, v in r["forest"].items() if k.lower().startswith("forêt")), 2)
    return out


def check_pending(db, items: list[dict], limit: int = SHORTLIST) -> int:
    """Site-check up to `limit` listings that have none yet (already ranked).

    Walks past rows that are already checked or have no position, so a shortlist
    of 20 already-checked plots does not block the rest of the land tab.
    """
    import json
    import geo
    from common import LOG
    done = 0
    for item in items:
        if done >= limit:
            break
        if (item.get("country") or "").upper() not in CHECK_COUNTRIES:
            continue
        raw = geo._raw(item)
        at = (raw.get("climate") or {}).get("at")
        kept = raw.get("site_check") or {}
        if not at or (kept.get("at") == at and kept.get("v") == VERSION):
            continue
        lat, lon = (float(x) for x in at.split(","))
        try:
            result = check(lat, lon, _radius_for(item.get("area_m2")), country=item["country"])
        except Exception as e:  # noqa: BLE001 — a service down: try at the next scan
            LOG.info(f"Site check {item['id']}: {type(e).__name__}")
            continue
        raw["site_check"] = {**summary(result), "at": at}
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
    if done:
        LOG.info(f"Site check: {done} listings")
    return done
