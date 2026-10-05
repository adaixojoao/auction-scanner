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
import math
import re
import time
import urllib.parse

from common import LOG, normalize, uf_parish

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "auction-scanner (+https://github.com/adaixojoao/auction-scanner)"
GEOCODE_PER_SCAN = 60
# After place-field repairs (or a long gap between scans) hundreds of board
# listings suddenly become addressable; drain them faster so climate and water
# can run. Still one Nominatim request a second (~3 minutes at the catch-up
# limit).
GEOCODE_CATCHUP = 180
GEOCODE_CATCHUP_WHEN = 120   # pending addressable listings that trigger catch-up
COUNTRY_CODES = {"PT": "pt", "ES": "es", "FR": "fr", "IT": "it", "NL": "nl", "DE": "de", "BE": "be",
                 "HR": "hr", "GR": "gr", "RO": "ro", "PL": "pl", "CY": "cy",
                 "BG": "bg", "SK": "sk", "LV": "lv"}
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
    """{"lat", "lon", "precision"} or None: a position you verified first
    (verify_location), then the sale's own coordinates, else a stored
    OpenStreetMap or cadastre lookup."""
    raw = _raw(item)
    mine = raw.get("verified_geo")
    if isinstance(mine, dict) and mine.get("lat") is not None and mine.get("lon") is not None:
        return mine
    return source_position(raw, item)


def source_position(raw: dict, item: dict) -> dict | None:
    """The position the scanner found by itself, without yours."""
    for lat_key, lon_key in (("lat", "lon"), ("prop_latitude", "prop_longitude"), ("lat", "lng"),
                             ("coordenadasLAT", "coordenadasLON")):
        lat, lon = _num(raw.get(lat_key)), _num(raw.get(lon_key))
        if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lat": lat, "lon": lon, "precision": "sale"}
    geo = raw.get("geo")
    if isinstance(geo, dict) and geo.get("lat") and geo.get("lon") and not stale_lookup(item, geo):
        return geo
    return None


def stale_lookup(item: dict, geo: dict) -> bool:
    """A stored lookup made with another municipality than the listing has now
    (the town was read wrong then, "El" for "El Campo De Peñaranda"): its pin
    is not trusted and the listing is looked up again."""
    if geo.get("precision") in ("cadastre", "sale", "verified"):
        return False
    town, query = municipality(item), geo.get("query")
    return bool(town and query and normalize(town) not in normalize(query))


_CONCELHO = re.compile(r"\bconcelho\s+(?:de|do|da)\s+([A-ZÀ-Ú][^,.;:()]{2,40})", re.I)
_CONSERVATORIA_TOWN = re.compile(r"Conservat[óo]ria\s+(?:do\s+)?(?:Registo\s+Predial\s+)?(?:de|do|da)\s+"
                                 r"([A-ZÀ-Ú][^,.;:()]{2,40}?)\s+(?:sob|com|n\.?º|$)", re.I)
_POSTCODE_TOWN = re.compile(r"\b\d{4}\s*-\s*\d{3}\s+([A-ZÀ-Ú][^,.;:()\d]{2,40})")
_FREGUESIA = re.compile(r"\bfreguesia\s+(?:de|do|da|dos|das)\s+([A-ZÀ-Ú][^,.;:()]{2,50})", re.I)
# Citius land: "localização : Rojanda, Freixedas, Pinhel" — the places, smallest first.
_LOCALIZACAO = re.compile(r"\blocaliza[çc][ãa]o\s*:\s*([^.;:()]{2,120})", re.I)
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
    """Title, description and the sale's own address fields, joined so a
    postcode's town name cannot run into the next field ("408 Vila do Bispo
    Fracção…" when both title and description mention the place)."""
    raw = _raw(item)
    parts = [str(x).strip() for x in (raw.get("morada"), raw.get("descricao_completa"),
                                      item.get("title"), item.get("description")) if x]
    return ". ".join(parts)


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
        for m in ([] if town else _LOCALIZACAO.finditer(text)):
            for part in reversed(m.group(1).split(",")):
                words = part.split()                 # "Pinhel ano de inscrição…": the text runs on
                town = next((t for k in range(1, min(4, len(words)) + 1)
                             if (t := _real_municipality(" ".join(words[:k]), country))), None)
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
    if parish:
        parish = uf_parish(parish)
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


def in_its_town(hit: dict, item: dict, towns: dict | None) -> bool:
    """A street hit must be in the listing's own municipality: "C. Larga, El"
    found the Platja Llarga 600 km away. Near the town's pin, or the town's
    name in the hit's address, is enough."""
    town = municipality(item)
    if not town:
        return False
    pin = (towns or {}).get(town_key(item.get("country") or "PT", town))
    if pin:
        return distance_km(float(hit["lat"]), float(hit["lon"]), pin["lat"], pin["lon"]) <= MAX_TOWN_KM
    name = hit.get("display_name")
    return not name or normalize(town) in normalize(name)     # no address to check: take it


def geocode(session, item: dict, towns: dict | None = None) -> dict | None:
    """Look the listing up on OpenStreetMap; the first query that finds it,
    in the listing's own municipality, wins."""
    country = COUNTRY_CODES.get(item.get("country") or "PT")
    for q in queries(item):
        params = {"format": "jsonv2", "limit": 1, "q": q}
        if country:
            params["countrycodes"] = country
        resp = session.get(NOMINATIM, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
        time.sleep(1.1)                          # Nominatim: at most one request a second
        resp.raise_for_status()
        hits = resp.json()
        if hits and ("," not in q or in_its_town(hits[0], item, towns)):
            hit = hits[0]
            # Found by the town's name alone: that is the town's pin, not the house.
            precision = "municipality" if "," not in q else _PLACE_OF.get(hit.get("addresstype"), "parish")
            return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "precision": precision, "query": q}
    return None


def _should_geocode(item: dict, raw: dict) -> bool:
    """Whether this listing still needs an OpenStreetMap lookup.

    A listing is tried once per set of address queries. When the first try found
    nothing because the municipality field was a street (Citius), and a later
    scrape or parser fix made the town readable, the queries change and we ask
    again. A stored pin whose query named another town is also redone
    (`stale_lookup`)."""
    if position(item):
        return False
    q = queries(item)
    if not q:
        return False
    if not raw.get("geo_checked"):
        return True
    stored = raw.get("geo")
    if isinstance(stored, dict) and stale_lookup(item, stored):
        return True
    # Checked with nothing (or older code that did not keep what it asked):
    # retry when the address we would ask about is new.
    if not stored and raw.get("geo_tried") != q:
        return True
    return False


def geocode_budget(items: list[dict], limit: int | None = None) -> int:
    """How many OpenStreetMap lookups this scan should attempt.

    Steady state stays at GEOCODE_PER_SCAN. When a place-field repair or a gap
    between scans leaves a long queue, use GEOCODE_CATCHUP so pins (and then
    climate / water) catch up in a few scans instead of dozens."""
    if limit is not None:
        return limit
    pending = 0
    for item in items:
        if _should_geocode(item, _raw(item)):
            pending += 1
            if pending > GEOCODE_CATCHUP_WHEN:
                return GEOCODE_CATCHUP
    return GEOCODE_PER_SCAN


def geocode_pending(db, session, items: list[dict], limit: int | None = None,
                    towns: dict | None = None) -> int:
    """Look up the listings (best first) that have no position yet, once each
    set of address queries."""
    limit = geocode_budget(items, limit)
    done = 0
    for item in items:
        if done >= limit:
            break
        raw = _raw(item)
        if not _should_geocode(item, raw):
            continue
        try:
            geo = geocode(session, item, towns)
        except Exception as e:  # noqa: BLE001 — offline or refused: try next scan
            LOG.info(f"OpenStreetMap lookup failed ({type(e).__name__}); trying next scan")
            break
        raw["geo_checked"] = True
        raw["geo_tried"] = queries(item)
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

TOWNS_PER_SCAN = 60         # new municipalities looked up per scan (1 request/s, about a minute)
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
        country = (item.get("country") or "PT").upper()
        for name in (municipality(item), stated_town(item)):
            if name and town_key(country, name) not in known:
                wanted.setdefault(town_key(country, name), (country, name))
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


TITLE_TOWN_CONFLICT_KM = 40
# Words that are also somewhere's name: never read as a town in a title.
GENERIC_PLACE_WORDS = {"barrio", "centro", "campo", "aldea", "pueblo", "lugar", "monte", "villa", "ciudad",
                       "playa", "sierra", "valle", "puerto", "ribera", "quinta", "herdade", "bairro", "terreno",
                       "finca", "parcela", "casal"}
# "esta em Horcajo Medianero", "situada en Lugo", "nalazi se u Splitu": where the
# seller says the property is, which may not be where the portal filed it.
_STATED = re.compile(r"\b(?:est[aá] (?:em|en)|situad[ao] (?:em|en)|ubicad[ao] en|localizad[ao] em|"
                     r"se (?:encuentra|encontra) en|située? à|located in|nalazi se u)\s+"
                     r"((?:[A-ZÁÉÍÓÚÑÇ][\w'-]+)(?:\s+(?:de |del |da |do |la )?[A-ZÁÉÍÓÚÑÇ][\w'-]+){0,3})")


def stated_town(item: dict) -> str | None:
    """The town the description says the property is in, if it says so."""
    text = item.get("description") or ""
    m = _STATED.search(re.sub(r"\b(est[aá] e[mn]) ([a-zà-ÿ]+(?: [a-zà-ÿ]{4,}){0,2})\s*(?=[.,;!]|$)",
                                  lambda x: f"{x.group(1)} {x.group(2).title()}", text))
    return m.group(1).strip() if m and len(m.group(1)) >= 4 else None
_TOWN_PATTERNS: dict = {}


def _town_pattern(towns: dict[str, dict], country: str):
    """One whole-word regex of the known town names of a country (cached per index)."""
    key = (id(towns), len(towns), country)
    if key not in _TOWN_PATTERNS:
        import prices
        names = {}
        for k, t in towns.items():
            if (k.startswith(f"{country}:") and len(t.get("name") or "") >= 5
                    and prices.place_key(t["name"]).split(" (")[0] not in GENERIC_PLACE_WORDS):
                names[prices.place_key(t["name"])] = t
        alts = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
        _TOWN_PATTERNS.clear()
        _TOWN_PATTERNS[key] = (re.compile(rf"\b(?:{alts})\b") if alts else None, names)
    return _TOWN_PATTERNS[key]


def title_town_conflict(item: dict, towns: dict[str, dict] | None) -> dict | None:
    """{"town", "km"} when the title names a known town far from where the listing
    is placed (a portal that files a Covilhã house under the agency's town)."""
    pos = _place(item, towns) if towns else None
    if not pos:
        return None
    import prices
    pattern, names = _town_pattern(towns, (item.get("country") or "PT").upper())
    own = prices.place_key(municipality(item) or "")
    for m in (pattern.finditer(normalize(item.get("title") or "")) if pattern else ()):
        town = names[m.group(0)]
        if m.group(0) == own:
            continue
        km = distance_km(pos["lat"], pos["lon"], town["lat"], town["lon"])
        if km > TITLE_TOWN_CONFLICT_KM:
            return {"town": town["name"], "km": km}
    stated = stated_town(item)
    town = towns.get(town_key(item.get("country") or "PT", stated)) if stated else None
    if town and prices.place_key(stated) != own:
        km = distance_km(pos["lat"], pos["lon"], town["lat"], town["lon"])
        if km > TITLE_TOWN_CONFLICT_KM:
            return {"town": town["name"], "km": km, "where": "description"}
    return None


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


TRANSPORT_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "transport.csv")
# Airports with scheduled flights (an IATA code) and the stations long-distance
# trains stop at (scripts/update_transport.py): how far to fly or ride away.
MAX_HUB_KM = {"airport": 120, "station": 40}
_HUB_WORDS = {"airport": "the airport", "station": "the station"}


@_functools.lru_cache(maxsize=8)
def _grid(path: str, mtime: float, kind: str | None) -> dict[tuple[int, int], list[tuple[float, float, str]]]:
    """The points of a CSV (lat, lon, name; with a `kind` column, only that kind)
    filed under a grid of _CELL degrees."""
    grid: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in _csv.DictReader(f):
                if kind and row.get("kind") != kind:
                    continue
                try:
                    lat, lon = float(row["lat"]), float(row["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                grid.setdefault((int(lat // _CELL), int(lon // _CELL)), []).append((lat, lon, row.get("name") or ""))
    except OSError:
        return {}
    return grid


_MTIMES: dict[str, tuple[float, float]] = {}      # path → (checked at, file time)


def _points(path: str, kind: str | None = None) -> dict:
    # The file's date is looked at once a minute, not for every listing
    # (72,000 checks took 3 s of an 18,000-listing load).
    import time as _time
    now = _time.monotonic()
    checked = _MTIMES.get(path)
    try:
        if not checked or now - checked[0] > 60:
            checked = (now, _os.path.getmtime(path))
            _MTIMES[path] = checked
        return _grid(path, checked[1], kind)
    except OSError:
        return {}


def beaches(path: str | None = None) -> dict:
    return _points(path or BEACH_FILE)


def _place(item: dict, towns: dict | None) -> dict | None:
    """The property's own position, else its town's (most listings have only that)."""
    pos = position(item)
    if not pos and towns:
        name = municipality(item)
        town = towns.get(town_key(item.get("country") or "PT", name)) if name else None
        if town:
            pos = {"lat": town["lat"], "lon": town["lon"], "precision": "municipality"}
    return pos


_NEAREST: dict[tuple, tuple[float, str] | None] = {}


def _nearest(pos: dict, grid: dict, max_km: float) -> tuple[float, str] | None:
    # Many listings share a position (their town's): each is searched once.
    # The search compares a flat-earth distance (exact enough to rank points a
    # few km apart) and measures only the winner on the sphere.
    key = (round(pos["lat"], 4), round(pos["lon"], 4), id(grid), max_km)
    if key in _NEAREST:
        return _NEAREST[key]
    lat, lon = pos["lat"], pos["lon"]
    ci, cj = int(lat // _CELL), int(lon // _CELL)
    reach = int(max_km // 45) + 1            # a cell is at least ~45 km wide here
    shrink = math.cos(math.radians(lat)) ** 2
    best, best_d2 = None, None
    for di in range(-reach, reach + 1):
        for dj in range(-reach, reach + 1):
            for plat, plon, name in grid.get((ci + di, cj + dj), ()):
                d2 = (plat - lat) ** 2 + (plon - lon) ** 2 * shrink
                if best_d2 is None or d2 < best_d2:
                    best, best_d2 = (plat, plon, name), d2
    found = None
    if best:
        km = distance_km(lat, lon, best[0], best[1])
        found = (km, best[2]) if km <= max_km else None
    if len(_NEAREST) > 200000:
        _NEAREST.clear()
    _NEAREST[key] = found
    return found


def _found(pos: dict, best: tuple[float, str], what: str) -> dict:
    km, name = best
    approx = pos.get("precision") not in EXACT_ENOUGH
    where = f" ({name})" if name else ""
    return {"km": round(km, 1), "name": name, "approx": approx,
            "text": f"{'about ' if approx else ''}{km_text(km)} from {what}{where}"}


def nearest_beach(item: dict, path: str | None = None, towns: dict | None = None) -> dict | None:
    """{"km", "name", "approx", "text"} for the sea beach nearest the property,
    None without a position, without the beach file or farther than MAX_BEACH_KM.
    Without a position of its own the property is placed at its town (`towns`,
    from town_index)."""
    pos, grid = _place(item, towns), beaches(path)
    best = _nearest(pos, grid, MAX_BEACH_KM) if pos and grid else None
    return _found(pos, best, "the beach") if best else None


def distance_to_place(item: dict, lat: float, lon: float, name: str, towns: dict | None = None,
                      max_km: float = 150) -> dict | None:
    """{"km", "approx", "text"}: how far the property is from a given place
    (Guarda, for land), from its own position or its town's."""
    pos = _place(item, towns)
    if not pos:
        return None
    km = distance_km(pos["lat"], pos["lon"], lat, lon)
    if km > max_km:
        return None
    approx = pos.get("precision") not in EXACT_ENOUGH
    return {"km": round(km, 1), "approx": approx, "text": f"{'about ' if approx else ''}{km_text(km)} from {name}"}


def nearest_hub(item: dict, kind: str, path: str | None = None, towns: dict | None = None) -> dict | None:
    """The nearest airport ("airport") or long-distance train station ("station"),
    like nearest_beach."""
    pos, grid = _place(item, towns), _points(path or TRANSPORT_FILE, kind)
    best = _nearest(pos, grid, MAX_HUB_KM[kind]) if pos and grid else None
    return _found(pos, best, _HUB_WORDS[kind]) if best else None


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
EXACT_ENOUGH = {"sale", "street", "cadastre", "verified"}
# A hamlet or village pin is not the plot, but water within a kilometre of it
# is still worth knowing (and says "approx.").
NEAR_ENOUGH = {"village"}
WATER_APPROX_RADIUS_M = 1000
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
        if "water_check" in raw or not pos or pos.get("precision") not in EXACT_ENOUGH | NEAR_ENOUGH:
            continue
        radius = WATER_RADIUS_M if pos["precision"] in EXACT_ENOUGH else WATER_APPROX_RADIUS_M
        try:
            found = water_near(session, pos, radius)
        except Exception as e:  # noqa: BLE001 — offline or busy: next scan
            LOG.info(f"Water lookup failed ({type(e).__name__}); trying next scan")
            break
        raw["water_check"] = {"radius_m": radius, "found": found, "approx": radius != WATER_RADIUS_M}
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
        time.sleep(1.1)
    if done:
        LOG.info(f"OpenStreetMap: water checked for {done} listings")
    return done


# ─── How far to trust the position, and a position you verify ───────
# Every check above follows position(). A position you verify (coordinates,
# an address, a Spanish cadastral reference) comes first; the scanner's own
# stays in raw_json beside it, and every change is kept in location_checks.

LEVEL_OF = {"verified": "exact", "sale": "exact", "cadastre": "exact", "street": "street",
            "village": "area", "parish": "area", "municipality": "municipality"}
CONFIDENCE = {
    "exact": ("Exact location",
              "Street View, the satellite view, water next to it, flood and fire at the spot and the "
              "distance to town are about this property."),
    "street": ("Street-level estimate",
               "The street, not the building: Street View and distances are close; flood and water at "
               "the spot may be a neighbour's."),
    "area": ("Village or parish estimate",
             "Somewhere in the village or parish: distances are \"about\"; heat, water stress and fire "
             "danger hold for the area, flood and fires at the spot do not."),
    "municipality": ("Municipality estimate",
                     "Only the town is known: heat and water stress hold for the area; flood, fires, "
                     "water nearby and the distance to town say nothing about this property."),
    "unknown": ("Location unknown", "No position and no municipality: no map, climate or distance checks."),
}
# The least exact first: a verification that would make the position less exact asks first.
PRECISION_RANK = ["municipality", "parish", "village", "street", "cadastre", "sale", "verified"]
VERIFY_METHODS = ("coordinates", "address", "cadastre")
_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*[,; ]\s*(-?\d{1,3}\.\d+)")


def _provenance(pos: dict | None, item: dict) -> str:
    if not pos:
        town = municipality(item)
        return (f"only the municipality is known ({town}): the checks use its town centre" if town
                else "the listing names no municipality")
    day = (pos.get("at") or "")[:10]
    if pos.get("method") == "coordinates":
        return f"coordinates you entered on {day}"
    if pos.get("method") == "address":
        return f"the address you entered (\u201c{pos.get('input', '')}\u201d), found on OpenStreetMap on {day}"
    if pos.get("method") == "cadastre":
        return f"the cadastral reference you entered ({pos.get('input', '')}) on {day}"
    if pos.get("precision") == "sale":
        return "coordinates given by the sale"
    if pos.get("precision") == "cadastre":
        return f"the land cadastre ({pos.get('query', '')})"
    return f"OpenStreetMap, looked up as \u201c{pos.get('query', '')}\u201d"


def location_confidence(item: dict) -> dict:
    """{"level": exact / street / area / municipality / unknown, "label",
    "reliable" (what the checks are good for at that level), "source" (where
    the position came from), "scanner" (the scanner's own, when yours replaced it)}."""
    pos = position(item)
    level = (LEVEL_OF.get(pos.get("precision"), "area") if pos
             else "municipality" if municipality(item) else "unknown")
    label, reliable = CONFIDENCE[level]
    out = {"level": level, "label": label, "reliable": reliable, "precision": (pos or {}).get("precision"),
           "source": _provenance(pos, item), "verified": bool(pos and pos.get("method")), "scanner": None}
    if out["verified"]:
        raw = _raw(item)
        own = source_position(raw, item)
        out["scanner"] = ({"lat": own["lat"], "lon": own["lon"], "source": _provenance(own, item)}
                          if own else None)
    return out


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """"38.7223, -9.1393", or a Google Maps link with "@38.7223,-9.1393" in it."""
    m = _COORDS.search(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon) else None


def lookup_address(session, text: str, country: str | None) -> dict | None:
    """An address you typed, on OpenStreetMap: {"lat", "lon", "precision"} or None."""
    params = {"format": "jsonv2", "limit": 1, "q": text}
    code = COUNTRY_CODES.get((country or "PT").upper())
    if code:
        params["countrycodes"] = code
    resp = session.get(NOMINATIM, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    time.sleep(1.1)                          # Nominatim: at most one request a second
    resp.raise_for_status()
    hits = resp.json()
    if not hits:
        return None
    return {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
            "precision": _PLACE_OF.get(hits[0].get("addresstype"), "parish")}


def verify_doubts(item: dict, pos: dict, towns: dict | None) -> list[str]:
    """Why a new position might be wrong: less exact than the one it replaces,
    or farther from the listing's town than any municipality is wide."""
    doubts = []
    now = position(item)
    rank = {p: i for i, p in enumerate(PRECISION_RANK)}
    if now and rank.get(pos["precision"], 0) < rank.get(now.get("precision"), 0):
        doubts.append(f"It is less exact ({pos['precision']}) than the position the listing has now "
                      f"({now.get('precision')}).")
    name = municipality(item)
    town = (towns or {}).get(town_key(item.get("country") or "PT", name)) if name else None
    if town:
        km = distance_km(pos["lat"], pos["lon"], town["lat"], town["lon"])
        if km > MAX_TOWN_KM:
            doubts.append(f"It is {km:.0f} km from {town['name']}, the listing's town.")
    return doubts


def _save_raw(db, item: dict, raw: dict) -> None:
    item["raw_json"] = json.dumps(raw, ensure_ascii=False)
    db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (item["raw_json"], item["id"]))


def _log_check(db, item: dict, action: str, pos: dict | None, method: str | None, text: str | None) -> None:
    from common import utcnow_iso
    db.execute("INSERT INTO location_checks (listing_id, action, method, input, lat, lon, precision, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (item["id"], action, method, text, (pos or {}).get("lat"), (pos or {}).get("lon"),
                (pos or {}).get("precision"), utcnow_iso()))


def verify_location(db, item: dict, pos: dict, method: str, text: str) -> dict:
    """Keep `pos` as the listing's position, from now on and across rescrapes
    (db.LEARNED_RAW_KEYS), and log it. The scanner's own position is kept."""
    from common import utcnow_iso
    raw = _raw(item)
    mine = {"lat": round(pos["lat"], 6), "lon": round(pos["lon"], 6), "precision": pos["precision"],
            "method": method, "input": (text or "").strip()[:200], "at": utcnow_iso()}
    raw["verified_geo"] = mine
    raw.pop("water_check", None)             # ask the map about water again, at the new spot
    _save_raw(db, item, raw)
    _log_check(db, item, "verified", mine, method, mine["input"])
    db.commit()
    return mine


def clear_location(db, item: dict) -> bool:
    """Go back to the scanner's own position; the history keeps what you had set."""
    raw = _raw(item)
    old = raw.pop("verified_geo", None)
    if not old:
        return False
    raw.pop("water_check", None)
    _save_raw(db, item, raw)
    _log_check(db, item, "cleared", old, old.get("method"), old.get("input"))
    db.commit()
    return True


def location_history(db, listing_id: str) -> list[dict]:
    rows = db.execute("SELECT action, method, input, lat, lon, precision, created_at FROM location_checks "
                      "WHERE listing_id = ? ORDER BY created_at DESC, id DESC", (listing_id,)).fetchall()
    return [dict(r) for r in rows]
