"""outline.py — the building and the land it stands on, from open maps.

A court or tax notice rarely says how big the house is or how much land comes
with it, and when it gives one figure it often does not say which. Where the
position is the building itself, the maps can tell the two apart:

- the land: the cadastral parcel the pin falls in, with its official area.
  Portugal: DGT's INSPIRE cadastre (the municipalities with a geometric
  cadastre; elsewhere there is no parcel and none is drawn). France: the IGN
  cadastre;
- the buildings: OpenStreetMap's outlines. With a parcel, every building whose
  centre is inside it: the largest is the house, the others are annexes.
  Without one, only the building the pin falls in.

What comes out is the ground each building covers (its footprint), the land
less those footprints (the yard or garden), and the floor area only where the
map states the number of levels. A flat's building is the whole block, so a
flat gets no house or garden figure. A building bigger than its parcel is a
terrace or block the map draws as one, not this house.

It is all an estimate from a map and says so. It is shown beside the notice's
own figures and never changes a score (AGENTS.md). A street, village or town
pin is not the building, and an outline there would be a neighbour's, so those
are not looked at.
"""
from __future__ import annotations

import json
import math
import time

from common import LOG, has_term, utcnow_iso

VERSION = 1
PER_SCAN = 30
# Positions that are the building: the sale's own pin, a cadastral parcel, one
# you verified, or a portal's map pin (which still has to fall in a building
# or a parcel before anything is said).
BUILDING_PINS = {"sale", "cadastre", "verified", "advertised"}
PIN_RADIUS_M = 40            # without a parcel: the buildings looked at around the pin
PARCEL_MARGIN_DEG = 0.00005  # ~5 m: the box the parcel at the pin is asked for in
BLOCK_SLACK = 1.05           # a building this much bigger than its parcel spans several
MATCH = 0.12                 # a notice figure within 12% of a map figure is that figure

DGT_WFS = "https://snicws.dgterritorio.gov.pt/geoserver/inspire/ows"
IGN_PARCEL = "https://apicarto.ign.fr/api/cadastre/parcelle"
FLAT_TERMS = ["apartamento*", "fração", "fracção", "andar", "appartement*", "apartment*",
              "flat", "piso", "duplex", "estúdio", "studio"]
HOUSE_TERMS = ["moradia*", "vivenda*", "casa", "maison*", "chalet", "villa", "quinta", "monte"]


# ─── Geometry: plain Python, small areas ────────────────────────────
# Rings are lists of (lon, lat). Over a few hundred metres a flat projection
# around the ring's own latitude is within a fraction of a percent of the
# true area, and needs no geo library on the PC.

def _metres(points, lat0: float) -> list[tuple[float, float]]:
    k = math.cos(math.radians(lat0))
    return [(lon * 111_320.0 * k, lat * 110_574.0) for lon, lat in points]


def ring_area_m2(ring) -> float:
    """The area of one ring, in m²."""
    if len(ring) < 3:
        return 0.0
    pts = _metres(ring, sum(p[1] for p in ring) / len(ring))
    s = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    return abs(s) / 2.0


def polygon_area_m2(polygon) -> float:
    """[outer, hole, hole…] → the outer ring less its holes, in m²."""
    if not polygon:
        return 0.0
    return max(0.0, ring_area_m2(polygon[0]) - sum(ring_area_m2(h) for h in polygon[1:]))


def in_ring(lon: float, lat: float, ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def in_polygons(lon: float, lat: float, polygons) -> bool:
    """Inside any polygon's outer ring and none of its holes."""
    return any(p and in_ring(lon, lat, p[0]) and not any(in_ring(lon, lat, h) for h in p[1:])
               for p in polygons)


def centre(ring) -> tuple[float, float]:
    """The ring's centre of area (lon, lat); its average point when it has no area.

    Worked out from the first corner: products of whole degrees would drown a
    house-sized ring in rounding."""
    ox, oy = ring[0]
    pts = [(x - ox, y - oy) for x, y in ring]
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < 1e-24:
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    return ox + cx / (3 * a), oy + cy / (3 * a)


def _polygons(geometry: dict) -> list[list[list[tuple[float, float]]]]:
    """A GeoJSON Polygon or MultiPolygon as [[outer, holes…], …] of (lon, lat)."""
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    parts = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    out = []
    for poly in parts:
        rings = [[(float(p[0]), float(p[1])) for p in ring] for ring in poly if len(ring) >= 4]
        if rings:
            out.append(rings)
    return out


# ─── The land: the cadastral parcel at the pin ──────────────────────

def _parcel_at(features, lat: float, lon: float, source: str, ref_keys, area_keys) -> dict | None:
    """The one parcel among `features` that the pin falls in. Some services
    answer latitude first: a parcel that holds the pin only once its
    coordinates are swapped is read swapped."""
    for swap in (False, True):
        hits = []
        for f in features or []:
            polys = _polygons(f.get("geometry") or {})
            if swap:
                polys = [[[(y, x) for x, y in ring] for ring in poly] for poly in polys]
            if polys and in_polygons(lon, lat, polys):
                hits.append((f.get("properties") or {}, polys))
        if len(hits) == 1:
            props, polys = hits[0]
            official = next((props.get(k) for k in area_keys if props.get(k) not in (None, "")), None)
            try:
                official = float(official) if official is not None else None
            except (TypeError, ValueError):
                official = None
            return {"m2": round(sum(polygon_area_m2(p) for p in polys)),
                    "official_m2": round(official) if official else None,
                    "ref": next((str(props[k]) for k in ref_keys if props.get(k)), None),
                    "source": source, "polygons": polys}
        if len(hits) > 1:
            return None                 # overlapping parcels: not sure which one
    return None


def dgt_parcel(session, lat: float, lon: float) -> dict | None:
    """Portugal: the DGT cadastral parcel the pin is in, or None (no geometric
    cadastre there, or the pin is on a road)."""
    m = PARCEL_MARGIN_DEG
    resp = session.get(DGT_WFS, params={
        "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "inspire:cadastralparcel",
        "maxFeatures": 10, "outputFormat": "application/json", "srsName": "EPSG:4326",
        "cql_filter": f"BBOX(geometry,{lon - m:.6f},{lat - m:.6f},{lon + m:.6f},{lat + m:.6f},'EPSG:4326')",
    }, timeout=60)
    resp.raise_for_status()
    return _parcel_at(resp.json().get("features"), lat, lon, "DGT cadastre",
                      ("label", "nationalcadastralreference"), ("areavalue",))


def ign_parcel_at(session, lat: float, lon: float) -> dict | None:
    """France: the IGN cadastral parcel the pin is in, or None."""
    resp = session.get(IGN_PARCEL, params={
        "geom": json.dumps({"type": "Point", "coordinates": [lon, lat]}),
    }, timeout=30)
    resp.raise_for_status()
    return _parcel_at((resp.json() or {}).get("features"), lat, lon, "IGN cadastre",
                      ("idu",), ("contenance",))


PARCEL_SERVICES = {"PT": dgt_parcel, "FR": ign_parcel_at}


# ─── The buildings: OpenStreetMap outlines ──────────────────────────

def buildings_query(lat: float, lon: float, box: tuple[float, float, float, float] | None) -> str:
    where = (f"({box[1]:.6f},{box[0]:.6f},{box[3]:.6f},{box[2]:.6f})" if box
             else f"(around:{PIN_RADIUS_M},{lat:.6f},{lon:.6f})")
    return f'[out:json][timeout:25];(way["building"]{where};relation["building"]{where};);out geom tags;'


def parse_buildings(payload: dict) -> list[dict]:
    """[{"polygons", "m2", "levels", "kind"}] from an Overpass answer.

    A way is one outline; a relation's outer members are its outlines and its
    inner members holes in them."""
    out = []
    for el in (payload or {}).get("elements") or []:
        tags = el.get("tags") or {}
        if el.get("type") == "way":
            rings = [[(float(p["lon"]), float(p["lat"])) for p in el.get("geometry") or []]]
            polys = [rings] if len(rings[0]) >= 4 else []
        elif el.get("type") == "relation":
            members = el.get("members") or []
            ring_of = lambda m: [(float(p["lon"]), float(p["lat"])) for p in m.get("geometry") or []]  # noqa: E731
            outers = [r for r in (ring_of(m) for m in members if m.get("role") == "outer") if len(r) >= 4]
            inners = [r for r in (ring_of(m) for m in members if m.get("role") == "inner") if len(r) >= 4]
            polys = [[o] + [i for i in inners if in_ring(*centre(i), o)] for o in outers]
        else:
            continue
        if not polys:
            continue
        try:
            levels = int(float(str(tags.get("building:levels") or "").replace(",", ".")))
        except ValueError:
            levels = None
        out.append({"polygons": polys, "m2": round(sum(polygon_area_m2(p) for p in polys)),
                    "levels": levels if levels and 0 < levels < 60 else None,
                    "kind": tags.get("building") or "yes"})
    return out


def osm_buildings(session, lat: float, lon: float, box=None) -> list[dict]:
    from geo import OVERPASS, USER_AGENT
    resp = session.post(OVERPASS, data={"data": buildings_query(lat, lon, box)},
                        headers={"User-Agent": USER_AGENT}, timeout=40)
    resp.raise_for_status()
    return parse_buildings(resp.json())


def _box(polygons) -> tuple[float, float, float, float]:
    pts = [p for poly in polygons for p in poly[0]]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


# ─── Putting the two together ───────────────────────────────────────

def is_flat(item: dict) -> bool:
    text = f"{item.get('tipo') or ''} {item.get('title') or ''}"
    return has_term(text, FLAT_TERMS, negations=False) and not has_term(text, HOUSE_TERMS, negations=False)


def _shape(polygons) -> list[list[float]]:
    """The first outer ring, as [[lat, lon], …] to 6 decimals (about 10 cm), for drawing."""
    return [[round(lat, 6), round(lon, 6)] for lon, lat in polygons[0][0]] if polygons else []


def combine(lat: float, lon: float, parcel: dict | None, buildings: list[dict], flat: bool) -> dict:
    """What the map says about the house and its land (raw_json "outline")."""
    out: dict = {"v": VERSION, "pin": [round(lat, 6), round(lon, 6)], "flat": flat}
    if parcel:
        out["plot"] = {k: parcel[k] for k in ("m2", "official_m2", "ref", "source")}
        out["shapes"] = {"plot": _shape(parcel["polygons"])}
        mine = [b for b in buildings if in_polygons(*centre(b["polygons"][0][0]), parcel["polygons"])]
    else:
        mine = [b for b in buildings if in_polygons(lon, lat, b["polygons"])]
    mine.sort(key=lambda b: -b["m2"])
    shapes = out.setdefault("shapes", {})
    shapes["buildings"] = [_shape(b["polygons"]) for b in mine]
    plot_m2 = (parcel or {}).get("official_m2") or (parcel or {}).get("m2")
    if not mine:
        out["note"] = ("no building drawn on OpenStreetMap on this parcel" if parcel
                       else "no building drawn on OpenStreetMap at the pin")
        return out
    house = mine[0]
    if flat:                            # the parcel is the block's land, not this home's
        out.pop("plot", None)
        shapes.pop("plot", None)
        out["block"] = {"m2": house["m2"], "levels": house["levels"]}
        out["note"] = "a flat: the outline is the whole block, not this home"
        return out
    if plot_m2 and house["m2"] > plot_m2 * BLOCK_SLACK:
        out["block"] = {"m2": house["m2"], "levels": house["levels"]}
        out["note"] = "the map draws a terrace or block as one building, bigger than this parcel"
        return out
    out["house"] = {"m2": house["m2"], "levels": house["levels"],
                    "floor_m2": house["m2"] * house["levels"] if house["levels"] else None}
    if len(mine) > 1:
        out["annexes"] = {"count": len(mine) - 1, "m2": sum(b["m2"] for b in mine[1:])}
    out["built_m2"] = sum(b["m2"] for b in mine)
    if plot_m2:
        out["open_m2"] = max(0, round(plot_m2 - out["built_m2"]))
    return out


def notes(item: dict, outline: dict | None) -> list[str]:
    """What the notice's own area turns out to be, next to the map. Hedged: a
    match is a hint which figure the notice gave, not a measurement."""
    if not outline:
        return []
    said = float(item.get("area_m2") or 0)
    plot = (outline.get("plot") or {})
    plot_m2 = plot.get("official_m2") or plot.get("m2")
    house = outline.get("house") or {}
    floor = house.get("floor_m2") or house.get("m2")
    if not said:
        return []
    near = lambda a, b: bool(a and b) and abs(a - b) <= MATCH * max(a, b)  # noqa: E731
    m2 = lambda v: f"{v:,.0f} m²".replace(",", " ")  # noqa: E731
    if near(said, plot_m2) and not near(said, floor):
        return [f"The notice's {m2(said)} matches the plot on the cadastre, not the house."]
    if near(said, floor):
        what = "the house's floors on the map" if house.get("floor_m2") else "the ground the house covers on the map"
        return [f"The notice's {m2(said)} matches {what}."]
    if plot_m2 and said > plot_m2 * (1 + MATCH):
        return [f"The notice's {m2(said)} is more than the parcel at the pin ({m2(plot_m2)}): "
                "more land may be sold with it, or the pin is on another parcel — check."]
    return []


def summary(outline: dict | None) -> list[dict]:
    """Labelled lines for the detail panel."""
    if not outline:
        return []
    rows = []
    fmt = lambda v: f"{v:,.0f} m²".replace(",", " ")  # noqa: E731
    plot = outline.get("plot")
    if plot:
        size = plot.get("official_m2") or plot.get("m2")
        ref = f", parcel {plot['ref']}" if plot.get("ref") else ""
        rows.append({"label": "Plot", "value": f"{fmt(size)} ({plot['source']}{ref})"})
    house = outline.get("house")
    if house:
        levels = f", {house['levels']} level{'s' if house['levels'] > 1 else ''} → about " \
                 f"{fmt(house['floor_m2'])} of floor" if house.get("levels") else ", levels not on the map"
        rows.append({"label": "House (ground it covers)", "value": f"about {fmt(house['m2'])}{levels}"})
    if outline.get("annexes"):
        a = outline["annexes"]
        rows.append({"label": "Other buildings on the plot",
                     "value": f"{a['count']}, about {fmt(a['m2'])} together"})
    if outline.get("open_m2") is not None:
        rows.append({"label": "Yard or garden", "value": f"about {fmt(outline['open_m2'])} (the plot less the buildings)"})
    if outline.get("block"):
        rows.append({"label": "Building on the map", "value": f"about {fmt(outline['block']['m2'])} — the whole block"})
    if outline.get("note"):
        rows.append({"label": "Map", "value": outline["note"]})
    return rows


# ─── Once a scan ────────────────────────────────────────────────────

def _pin_key(pos: dict) -> list[float]:
    return [round(float(pos["lat"]), 6), round(float(pos["lon"]), 6)]


def wanted(item: dict) -> bool:
    """A home whose pin is the building and whose outline is not looked up yet
    for this pin (a pin you verify later is looked up again)."""
    import geo
    from scoring import property_kind
    pos = geo.position(item)
    if not pos or pos.get("precision") not in BUILDING_PINS or property_kind(item) != "home":
        return False
    done = geo._raw(item).get("outline")
    return not (isinstance(done, dict) and done.get("v") == VERSION and done.get("pin") == _pin_key(pos))


def look_up(session, item: dict) -> dict:
    import geo
    pos = geo.position(item)
    lat, lon = float(pos["lat"]), float(pos["lon"])
    service = PARCEL_SERVICES.get((item.get("country") or "PT").upper())
    parcel = service(session, lat, lon) if service else None
    buildings = osm_buildings(session, lat, lon, _box(parcel["polygons"]) if parcel else None)
    result = combine(lat, lon, parcel, buildings, is_flat(item))
    result["at"] = utcnow_iso()
    return result


def check_pending(db, session, items: list[dict], limit: int = PER_SCAN) -> int:
    """Outline the best homes with a building pin, once each (a failed lookup
    is tried again next scan)."""
    import geo
    done = 0
    for item in items:
        if done >= limit:
            break
        if not wanted(item):
            continue
        try:
            found = look_up(session, item)
        except Exception as e:  # noqa: BLE001 — offline or busy: next scan
            LOG.info(f"Outline lookup failed ({type(e).__name__}); trying next scan")
            break
        raw = geo._raw(item)
        raw["outline"] = found
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
        time.sleep(1.1)                 # OpenStreetMap asks for one request a second
    if done:
        LOG.info(f"Outlines: building and plot looked up for {done} homes")
    return done
