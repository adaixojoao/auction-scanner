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


def queries(item: dict) -> list[str]:
    """Address lookups from the most exact to the least, always with the
    municipality: without it a village name can match the wrong place in the
    country (there are many "Lage"), and a wrong pin is worse than none."""
    raw = _raw(item)
    country = item.get("country") or "PT"
    text = " ".join(str(x or "") for x in (raw.get("morada"), raw.get("descricao_completa"),
                                           item.get("title"), item.get("description")))
    town = _real_municipality(item.get("concelho"), country)
    if not town and country == "PT":
        for pattern in (_CONCELHO, _CONSERVATORIA_TOWN, _POSTCODE_TOWN):
            for m in pattern.finditer(text):
                town = _real_municipality(m.group(1), country)
                if town:
                    break
            if town:
                break
    if not town and country != "PT":
        town = item.get("district")
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
            return {"lat": float(hit["lat"]), "lon": float(hit["lon"]),
                    "precision": _PLACE_OF.get(hit.get("addresstype"), "parish"), "query": q}
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
