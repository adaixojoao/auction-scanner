"""geo.py — where a listing is on the map, for Street View and the satellite view.

Coordinates come from the sale when it has them (e-leilões, Imobancos,
Leilosoc, the Dutch notaries). Otherwise the address is looked up on
OpenStreetMap (Nominatim): the street when it is known, else the village,
parish or municipality. Nominatim asks for at most one request a second and a
User-Agent that names the application, so only listings someone would see are
looked up (GEOCODE_PER_SCAN a scan), once each, and the result is kept.

Each position says how exact it is ("street", "village", "parish",
"municipality", "sale"): at village level Street View shows a street near the
property, not the building.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

from common import LOG

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "auction-scanner (+https://github.com/adaixojoao/auction-scanner)"
GEOCODE_PER_SCAN = 60
COUNTRY_CODES = {"PT": "pt", "ES": "es", "FR": "fr", "IT": "it", "NL": "nl", "DE": "de", "BE": "be",
                 "HR": "hr", "GR": "gr", "RO": "ro", "PL": "pl", "CY": "cy"}
_STREET = re.compile(r"\b((?:Rua|Travessa|Avenida|Av\.|Largo|Estrada|Caminho|Praceta|Beco|Canada|Calçada|Alameda|"
                     r"Praça|Quinta|Bairro|Urbaniza[çc][ãa]o)\s+[^,.;:()]{2,60})", re.I)
_LUGAR = re.compile(r"\b(?:lugar|sitio|sítio)\s+(?:de|do|da|dos|das)\s+([^,.;:()]{2,40})", re.I)
_PLACE_OF = {"road": "street", "house_number": "street", "building": "street", "residential": "street",
             "hamlet": "village", "village": "village", "isolated_dwelling": "village", "locality": "village",
             "neighbourhood": "village", "suburb": "parish", "quarter": "parish", "town": "parish",
             "city": "municipality", "municipality": "municipality", "county": "municipality"}


def _raw(item: dict) -> dict:
    try:
        raw = json.loads(item.get("raw_json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v or None


def position(item: dict) -> dict | None:
    """{"lat", "lon", "precision"} or None: the sale's own coordinates first,
    else a stored OpenStreetMap lookup."""
    raw = _raw(item)
    for lat_key, lon_key in (("lat", "lon"), ("prop_latitude", "prop_longitude"), ("lat", "lng"),
                             ("coordenadasLAT", "coordenadasLON")):
        lat, lon = _num(raw.get(lat_key)), _num(raw.get(lon_key))
        if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lat": lat, "lon": lon, "precision": "sale"}
    geo = raw.get("geo")
    if isinstance(geo, dict) and geo.get("lat") and geo.get("lon"):
        return geo
    return None


_CONCELHO = re.compile(r"\bconcelho\s+(?:de|do|da)\s+([A-ZÀ-Ú][^,.;:()]{2,40})", re.I)
_CONSERVATORIA_TOWN = re.compile(r"Conservat[óo]ria\s+(?:do\s+)?(?:Registo\s+Predial\s+)?(?:de|do|da)\s+"
                                 r"([A-ZÀ-Ú][^,.;:()]{2,40}?)\s+(?:sob|com|n\.?º|$)", re.I)
_POSTCODE_TOWN = re.compile(r"\b\d{4}\s*-\s*\d{3}\s+([A-ZÀ-Ú][^,.;:()\d]{2,40})")
_FREGUESIA = re.compile(r"\bfreguesia\s+(?:de|do|da|dos|das)\s+([A-ZÀ-Ú][^,.;:()]{2,50})", re.I)
_NOT_A_PLACE = re.compile(r"^\s*(?:lugar|rua|travessa|largo|avenida|estrada|caminho|sitio|sítio)\b", re.I)


def _real_municipality(name: str | None, country: str) -> str | None:
    """The name, if it is a municipality (Portugal: one of INE's 308); court
    texts sometimes put a street in that field ("largo de São Vicente")."""
    if not name or _NOT_A_PLACE.match(name):
        return None
    if country != "PT":
        return name.strip()
    import prices
    table = prices.pt_table()
    if not table:                                  # no price file: trust the field
        return name.strip()
    return name.strip() if prices.place_key(name) in table else None


def _address_text(item: dict) -> str:
    raw = _raw(item)
    return " ".join(str(x or "") for x in (raw.get("morada"), raw.get("descricao_completa"),
                                           item.get("title"), item.get("description")))


def municipality(item: dict) -> str | None:
    """The municipality this listing is in: the field when it holds a real one,
    else the court text ("concelho de …", the conservatória, the postcode)."""
    country = item.get("country") or "PT"
    town = _real_municipality(item.get("concelho"), country)
    if not town and country == "PT":
        text = _address_text(item)
        for pattern in (_CONCELHO, _CONSERVATORIA_TOWN, _POSTCODE_TOWN):
            for m in pattern.finditer(text):
                town = _real_municipality(m.group(1), country)
                if town:
                    break
            if town:
                break
    if not town and country != "PT":
        town = item.get("district")
    return re.sub(r"\s+", " ", town).strip(" ,") if town else None


def queries(item: dict) -> list[str]:
    """Address lookups from the most exact to the least, always with the
    municipality: without it a village name can match the wrong place in the
    country (there are many "Lage"), and a wrong pin is worse than none."""
    text = _address_text(item)
    town = municipality(item)
    if not town:
        return []
    parish = item.get("freguesia")
    if not parish or _NOT_A_PLACE.match(parish):
        m = _FREGUESIA.search(text)
        parish = m.group(1).strip() if m else None
    # The address comes before the boundaries ("confrontações: norte - caminho público …").
    address = re.split(r"confront", text, maxsplit=1, flags=re.I)[0]
    street = _STREET.search(address)
    lugar = _LUGAR.search(address)
    parts = [
        [street.group(1) if street else None, parish, town],
        [lugar.group(1) if lugar else None, parish, town],
        [parish, town],
        [town],
    ]
    out = []
    for p in parts:
        cleaned = [re.sub(r"\s+", " ", str(x)).strip(" ,") for x in p if x]
        if len(cleaned) >= 2 or cleaned == [town]:
            q = ", ".join(dict.fromkeys(cleaned))
            if q not in out:
                out.append(q)
    return out


def geocode(session, item: dict) -> dict | None:
    """Look the listing up on OpenStreetMap; the first query that finds it wins."""
    country = COUNTRY_CODES.get(item.get("country") or "PT")
    for q in queries(item):
        params = {"format": "jsonv2", "limit": 1, "q": q}
        if country:
            params["countrycodes"] = country
        resp = session.get(NOMINATIM, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
        time.sleep(1.1)                          # Nominatim: at most one request a second
        resp.raise_for_status()
        hits = resp.json()
        if hits:
            hit = hits[0]
            # Found by the town's name alone: that is the town's pin, not the house.
            precision = "municipality" if "," not in q else _PLACE_OF.get(hit.get("addresstype"), "parish")
            return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "precision": precision, "query": q}
    return None


def geocode_pending(db, session, items: list[dict], limit: int = GEOCODE_PER_SCAN) -> int:
    """Look up the listings (best first) that have no position yet, once each."""
    done = 0
    for item in items:
        if done >= limit:
            break
        raw = _raw(item)
        if position(item) or raw.get("geo_checked"):
            continue
        try:
            geo = geocode(session, item)
        except Exception as e:  # noqa: BLE001 — offline or refused: try next scan
            LOG.info(f"OpenStreetMap lookup failed ({type(e).__name__}); trying next scan")
            break
        raw["geo_checked"] = True
        if geo:
            raw["geo"] = geo
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
    if done:
        LOG.info(f"OpenStreetMap: located {done} listings")
    return done


def street_view_url(pos: dict) -> str:
    """Google Maps opened in Street View at that point (no key needed)."""
    return ("https://www.google.com/maps/@?" + urllib.parse.urlencode(
        {"api": 1, "map_action": "pano", "viewpoint": f"{pos['lat']:.6f},{pos['lon']:.6f}"}))


def satellite_url(pos: dict) -> str:
    return ("https://www.google.com/maps/@?" + urllib.parse.urlencode(
        {"api": 1, "map_action": "map", "center": f"{pos['lat']:.6f},{pos['lon']:.6f}", "zoom": 18,
         "basemap": "satellite"}))


def street_view_embed_url(pos: dict, key: str) -> str | None:
    """Street View inside the app: needs the owner's (free) Google Maps Embed API key."""
    if not key:
        return None
    return ("https://www.google.com/maps/embed/v1/streetview?" + urllib.parse.urlencode(
        {"key": key, "location": f"{pos['lat']:.6f},{pos['lon']:.6f}", "fov": 80}))


# ─── How far the property is from its town ───────────────────────────
# "Good location" guessed from words in the description only works when the
# description says something. The distance from the property to the middle of
# its municipality's town is a fact, and it is the same fact in every country.

TOWNS_PER_SCAN = 20         # new municipalities looked up per scan (1 request/s)
MAX_TOWN_KM = 40            # farther than any Portuguese municipality is wide:
                            # the lookup found the wrong town, so say nothing
TOO_VAGUE = {"municipality"}   # that pin *is* the town: the distance would be 0 by construction


def town_key(country: str, name: str) -> str:
    import prices
    return f"{(country or 'PT').upper()}:{prices.place_key(name)}"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def town_index(db) -> dict[str, dict]:
    """{key: {"name", "lat", "lon"}} for the towns already found."""
    rows = db.execute("SELECT key, name, lat, lon FROM places WHERE lat IS NOT NULL").fetchall()
    return {r[0]: {"name": r[1], "lat": r[2], "lon": r[3]} for r in rows}


def _geocode_town(session, country: str, name: str) -> tuple[float, float] | None:
    params = {"format": "jsonv2", "limit": 1, "q": name, "featuretype": "settlement"}
    code = COUNTRY_CODES.get((country or "PT").upper())
    if code:
        params["countrycodes"] = code
    resp = session.get(NOMINATIM, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    time.sleep(1.1)                          # Nominatim: at most one request a second
    resp.raise_for_status()
    hits = resp.json()
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


def locate_towns(db, session, items: list[dict], limit: int = TOWNS_PER_SCAN) -> int:
    """Find the middle of each municipality seen, once each, and keep it.
    A municipality that cannot be found is stored without coordinates so it is
    not looked up again every scan."""
    from common import utcnow_iso
    known = {r[0] for r in db.execute("SELECT key FROM places")}
    wanted: dict[str, tuple[str, str]] = {}
    for item in items:
        name = municipality(item)
        if not name:
            continue
        country = (item.get("country") or "PT").upper()
        key = town_key(country, name)
        if key not in known:
            wanted.setdefault(key, (country, name))
    done = 0
    for key, (country, name) in list(wanted.items())[:limit]:
        try:
            found = _geocode_town(session, country, name)
        except Exception as e:  # noqa: BLE001 — offline or refused: try next scan
            LOG.info(f"OpenStreetMap town lookup failed ({type(e).__name__}); trying next scan")
            break
        db.execute("INSERT OR REPLACE INTO places (key, country, name, lat, lon, checked_at) "
                   "VALUES (?,?,?,?,?,?)",
                   (key, country, name, found[0] if found else None,
                    found[1] if found else None, utcnow_iso()))
        db.commit()
        done += 1
    if done:
        LOG.info(f"OpenStreetMap: located {done} towns")
    return done


def distance_to_town(item: dict, towns: dict[str, dict]) -> dict | None:
    """{"km", "town", "approx", "text"} — how far this property is from the middle of
    its town. None when either position is unknown, when the property is only
    placed at municipality level (that pin *is* the town), or when the distance
    is too big to believe."""
    pos = position(item)
    if not pos or pos.get("precision") in TOO_VAGUE or _town_only(pos):
        return None
    name = municipality(item)
    if not name:
        return None
    town = towns.get(town_key(item.get("country") or "PT", name))
    if not town:
        return None
    km = distance_km(pos["lat"], pos["lon"], town["lat"], town["lon"])
    if km > MAX_TOWN_KM:
        return None
    approx = pos["precision"] == "parish"
    return {"km": round(km, 1), "town": town["name"], "approx": approx,
            "text": f"{'about ' if approx else ''}{km_text(km)} from {town['name']}"}


# ─── The beach ──────────────────────────────────────────────────────
# The sea beaches of each country (data/beaches.csv, from OpenStreetMap by
# scripts/update_beaches.py), and how far a property is from the nearest one.
# A town-level pin gives an approximate distance: good enough for "about 3 km".

import csv as _csv
import functools as _functools
import os as _os

BEACH_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "beaches.csv")
MAX_BEACH_KM = 40
_CELL = 0.5          # degrees: the grid the beaches are filed under


@_functools.lru_cache(maxsize=2)
def _beach_grid(path: str, mtime: float) -> dict[tuple[int, int], list[tuple[float, float, str]]]:
    grid: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in _csv.DictReader(f):
                try:
                    lat, lon = float(row["lat"]), float(row["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                grid.setdefault((int(lat // _CELL), int(lon // _CELL)), []).append((lat, lon, row.get("name") or ""))
    except OSError:
        return {}
    return grid


def beaches(path: str | None = None) -> dict:
    path = path or BEACH_FILE
    try:
        return _beach_grid(path, _os.path.getmtime(path))
    except OSError:
        return {}


def nearest_beach(item: dict, path: str | None = None) -> dict | None:
    """{"km", "name", "approx", "text"} for the sea beach nearest the property,
    None without a position, without the beach file or farther than MAX_BEACH_KM."""
    pos = position(item)
    grid = beaches(path)
    if not pos or not grid:
        return None
    lat, lon = pos["lat"], pos["lon"]
    ci, cj = int(lat // _CELL), int(lon // _CELL)
    best = None
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for blat, blon, name in grid.get((ci + di, cj + dj), ()):
                km = distance_km(lat, lon, blat, blon)
                if best is None or km < best[0]:
                    best = (km, name)
    if best is None or best[0] > MAX_BEACH_KM:
        return None
    km, name = best
    approx = pos.get("precision") not in EXACT_ENOUGH
    where = f" ({name})" if name else ""
    return {"km": round(km, 1), "name": name, "approx": approx,
            "text": f"{'about ' if approx else ''}{km_text(km)} from the beach{where}"}


def _town_only(pos: dict) -> bool:
    """A stored lookup made with the town's name alone ("Salvaterra de Magos"):
    older ones were labelled "parish", which gave a house anywhere in the
    municipality 0.0 km and the best location score."""
    return pos.get("precision") != "sale" and bool(pos.get("query")) and "," not in pos["query"]


def km_text(km: float) -> str:
    return f"{km:.1f} km" if km < 10 else f"{km:.0f} km"


# ─── Water next to the property (OpenStreetMap, Overpass) ───────────
# "Next to water" was only known when the text said so. With a position that
# is the property itself (the sale's coordinates or its street), the map says
# whether a river, stream, canal, lake or reservoir is within WATER_RADIUS_M.
# A village or parish pin is not the plot, so it is not asked.

OVERPASS = "https://overpass-api.de/api/interpreter"
WATER_RADIUS_M = 300
WATER_PER_SCAN = 40
EXACT_ENOUGH = {"sale", "street"}
_WATER_KIND = {"river": "river", "stream": "stream", "canal": "canal", "reservoir": "reservoir",
               "lake": "lake", "pond": "pond", "water": "water"}


def water_query(pos: dict, radius: int = WATER_RADIUS_M) -> str:
    at = f"around:{radius},{pos['lat']:.6f},{pos['lon']:.6f}"
    return (f'[out:json][timeout:25];(way({at})["waterway"~"^(river|stream|canal)$"];'
            f'way({at})["natural"="water"];relation({at})["natural"="water"];);out tags 10;')


def water_near(session, pos: dict, radius: int = WATER_RADIUS_M) -> list[dict]:
    """[{"name", "kind"}] of the water within `radius` metres, named ones first."""
    resp = session.post(OVERPASS, data={"data": water_query(pos, radius)},
                        headers={"User-Agent": USER_AGENT}, timeout=40)
    resp.raise_for_status()
    found = []
    for el in resp.json().get("elements", []):
        tags = el.get("tags") or {}
        kind = _WATER_KIND.get(tags.get("waterway") or tags.get("water") or "water", "water")
        entry = {"name": tags.get("name") or "", "kind": kind}
        if entry not in found:
            found.append(entry)
    found.sort(key=lambda w: (not w["name"], w["kind"] not in ("river", "reservoir", "lake")))
    return found[:5]


def check_water_pending(db, session, items: list[dict], limit: int = WATER_PER_SCAN) -> int:
    """Ask the map about water next to the best properties with an exact position, once each."""
    done = 0
    for item in items:
        if done >= limit:
            break
        raw = _raw(item)
        pos = position(item)
        if "water_check" in raw or not pos or pos.get("precision") not in EXACT_ENOUGH:
            continue
        try:
            found = water_near(session, pos)
        except Exception as e:  # noqa: BLE001 — offline or busy: next scan
            LOG.info(f"Water lookup failed ({type(e).__name__}); trying next scan")
            break
        raw["water_check"] = {"radius_m": WATER_RADIUS_M, "found": found}
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
        time.sleep(1.1)
    if done:
        LOG.info(f"OpenStreetMap: water checked for {done} listings")
    return done
