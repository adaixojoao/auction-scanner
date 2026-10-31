"""surroundings.py — what official maps say about the place a listing stands.

For the best listings with a position, once per pin, it asks each source what
it says at that point and keeps the answer in raw_json["surroundings"]:

- noise (every EU country): the EEA's strategic noise maps (Environmental
  Noise Directive, 2026 release), Lden from roads, railways, airports and
  industry. The maps cover towns over 100,000 people and main roads, railways
  and airports; elsewhere nothing is mapped, which is said as such;
- earthquakes (every EU country): the European Seismic Hazard Model 2020
  (EFEHR), median peak ground acceleration on rock with a 475-year return
  period (10% in 50 years), the figure building codes use;
- amenities (every EU country): the nearest supermarket, pharmacy, doctor,
  school, bus or tram stop and train station within 2 km, from OpenStreetMap;
- zoning: France, the local plan's zone (PLU, IGN Géoportail de
  l'urbanisme), or that the town has none online (national rules, RNU);
  Portugal, whether the point is in the national farmland (RAN) or ecological
  (REN) reserve, where building is restricted (DGT);
- French risks: clay shrink-swell (RGA), radon class and seismic zone of the
  commune, mines and cavities within 500 m (BRGM / Géorisques);
- sold prices: France only, the houses or flats sold in the commune in the
  last two years (DVF, the tax office's register of sales);
- energy ratings: France only, the energy performance certificates (DPE,
  ADEME) issued at the address since July 2021. One whose floor area matches
  the listing's is probably this home's. A G home cannot be let on a new lease
  since 2025, F from 2028 and E from 2034 (loi Climat et résilience).

A street, village or town pin is not the building, so the point checks (noise,
zoning, clay) need a building pin; amenities take a street or village pin too;
earthquakes, the commune's risks and sold prices take any position.

Each answer is shown with its source and never changes a score (AGENTS.md):
a map is an estimate of a place, not a survey of the house.
"""
from __future__ import annotations

import csv
import io
import json
import math
import statistics
import time

from common import LOG, utcnow_iso

VERSION = 1
PER_SCAN = 30
TRIES = 3                       # a source that fails this often for a pin is not asked again
BUILDING_PINS = {"sale", "cadastre", "verified", "advertised"}
NEAR_PINS = BUILDING_PINS | {"street", "village"}

NOISE = "https://noise.discomap.eea.europa.eu/arcgis/rest/services/noiseStoryMap/NoiseContours_{}_lden/ImageServer/identify"
NOISE_SOURCES = {"road": "road traffic", "rail": "railway", "air": "aircraft", "ind": "industry"}
NOISE_BANDS = {1: "55–60", 2: "60–65", 3: "65–70", 4: "70–75", 5: "over 75"}
# WHO Environmental Noise Guidelines (2018), Lden; none set for industry
WHO_LDEN = {"road": 53, "rail": 54, "air": 45}
QUAKE = "https://efehr-services.ethz.ch/hazard/api/v1/maps"
FR_COMMUNE = "https://geo.api.gouv.fr/communes"
FR_PLU = "https://data.geopf.fr/wfs/ows"
GEORISQUES = "https://www.georisques.gouv.fr/api/v1/"
DPE = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines"
DPE_RADIUS_M = 30
LET_BAN = {"G": "cannot be let on a new lease since 2025", "F": "cannot be let on a new lease from 2028",
           "E": "cannot be let on a new lease from 2034"}
DVF = "https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{dep}/{insee}.csv"
PT_RESERVES = {
    "RAN": ("https://servicos.dgterritorio.pt/SDISNITWMSSRUP_RAN_PT1/WMService.aspx", "Reserva_Agricola_Nacional",
            "national farmland reserve (RAN)"),
    "REN": ("https://servicos.dgterritorio.pt/SDISNITWMSSRUP_REN_PT1/WMService.aspx", "REN_em_Vigor",
            "national ecological reserve (REN)"),
}
AMENITIES = {               # name: Overpass filters
    "supermarket": ('["shop"~"^(supermarket|convenience)$"]',),
    "pharmacy": ('["amenity"="pharmacy"]',),
    "doctor": ('["amenity"~"^(doctors|clinic|hospital)$"]', '["healthcare"~"^(doctor|centre|clinic)$"]'),
    "school": ('["amenity"~"^(school|kindergarten)$"]',),
    "bus or tram stop": ('["highway"="bus_stop"]', '["public_transport"="platform"]["bus"="yes"]',
                         '["railway"="tram_stop"]'),
    "train station": ('["railway"~"^(station|halt)$"]',),
}
AMENITY_RADIUS_M = 2000


# ─── The sources (each raises on a failed request; None: nothing there) ──
def noise(session, lat: float, lon: float, item: dict) -> dict:
    """{"road": band or None, ...}: the Lden band (1-5) each source reaches at the point."""
    out = {}
    point = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    for key in NOISE_SOURCES:
        resp = session.get(NOISE.format(key), params={"geometry": point, "geometryType": "esriGeometryPoint",
                                                      "returnCatalogItems": "false", "f": "json"}, timeout=40)
        resp.raise_for_status()
        value = resp.json().get("value")
        out[key] = int(value) if str(value).isdigit() else None
    return out


def quake(session, lat: float, lon: float, item: dict) -> dict | None:
    """{"pga": g}: ESHM20's median 475-year peak ground acceleration on rock."""
    resp = session.get(QUAKE, params={"lat": round(lat, 3), "lon": round(lon, 3), "modelid": 81, "imt": "PGA",
                                      "poe": 0.002103, "timespan": 1, "soiltype": "rock_vs30_800ms-1",
                                      "aggregationtype": "ordinal", "aggregationlevel": 0.5}, timeout=40)
    resp.raise_for_status()
    points = resp.json().get("points") or []
    return {"pga": round(float(points[0]["value"]), 3)} if points else None


def amenities_query(lat: float, lon: float, radius: int = AMENITY_RADIUS_M) -> str:
    parts = []
    for name, filters in AMENITIES.items():
        for f in filters:
            parts.append(f'nwr{f}(around:{radius},{lat},{lon});')
    return f"[out:json][timeout:60];({''.join(parts)});out center tags;"


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def _amenity_kind(tags: dict) -> str | None:
    shop, amenity, care = tags.get("shop"), tags.get("amenity"), tags.get("healthcare")
    if shop in ("supermarket", "convenience"):
        return "supermarket"
    if amenity == "pharmacy":
        return "pharmacy"
    if amenity in ("doctors", "clinic", "hospital") or care in ("doctor", "centre", "clinic"):
        return "doctor"
    if amenity in ("school", "kindergarten"):
        return "school"
    if tags.get("railway") in ("station", "halt"):
        return "train station"
    if (tags.get("highway") == "bus_stop" or tags.get("railway") == "tram_stop"
            or (tags.get("public_transport") == "platform" and tags.get("bus") == "yes")):
        return "bus or tram stop"
    return None


def nearest_amenities(elements: list[dict], lat: float, lon: float) -> dict:
    """{kind: metres to the nearest one, or None within the radius}."""
    out: dict = {name: None for name in AMENITIES}
    for el in elements:
        kind = _amenity_kind(el.get("tags") or {})
        at = el.get("center") or el
        if not kind or at.get("lat") is None:
            continue
        d = round(_distance_m(lat, lon, at["lat"], at["lon"]))
        if out[kind] is None or d < out[kind]:
            out[kind] = d
    return out


def amenities(session, lat: float, lon: float, item: dict) -> dict:
    from geo import OVERPASS, USER_AGENT
    resp = session.post(OVERPASS, data={"data": amenities_query(lat, lon)},
                        headers={"User-Agent": USER_AGENT}, timeout=90)
    resp.raise_for_status()
    return nearest_amenities(resp.json().get("elements") or [], lat, lon)


def _fr_commune(session, lat: float, lon: float, arrondissement: bool = False) -> dict | None:
    """{"insee", "dep", "name"} of the commune at the point; with arrondissement,
    Paris, Lyon and Marseille by arrondissement, as DVF files them."""
    for kind in (("arrondissement-municipal", None) if arrondissement else (None,)):
        params = {"lat": lat, "lon": lon, "fields": "code,nom,codeDepartement"}
        if kind:
            params["type"] = kind
        resp = session.get(FR_COMMUNE, params=params, timeout=30)
        resp.raise_for_status()
        found = resp.json()
        if found:
            dep = found[0].get("codeDepartement") or found[0]["code"][:2]
            return {"insee": found[0]["code"], "dep": dep, "name": found[0].get("nom")}
    return None


def fr_zoning(session, lat: float, lon: float, item: dict) -> dict:
    """{"zone": "U"/"AU"/"A"/"N"..., "label"} from the local plan, or {"zone": None}
    when the town has no plan online (national rules apply)."""
    d = 0.00005
    resp = session.get(FR_PLU, params={
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "wfs_du:zone_urba",
        "OUTPUTFORMAT": "application/json", "COUNT": 5,
        "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d},urn:ogc:def:crs:EPSG::4326"}, timeout=40)
    resp.raise_for_status()
    features = resp.json().get("features") or []
    if not features:
        return {"zone": None}
    p = features[0].get("properties") or {}
    return {"zone": p.get("typezone"), "label": p.get("libelle"), "name": p.get("libelong")}


def _pt_reserve(session, url: str, layer: str, lat: float, lon: float) -> bool:
    d = 0.0005
    resp = session.get(url, params={
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
        "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d}",
        "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "INFO_FORMAT": "text/xml", "FEATURE_COUNT": 1}, timeout=40)
    resp.raise_for_status()
    return pt_reserve_hit(resp.text)


def pt_reserve_hit(xml: str) -> bool:
    """A GetFeatureInfo answer that names a feature (an empty one has no attributes)."""
    return "<FIELDS" in xml or "SERVIDAO" in xml.upper() or "TIPOLOGIA" in xml.upper()


def pt_zoning(session, lat: float, lon: float, item: dict) -> dict:
    return {key: _pt_reserve(session, url, layer, lat, lon) for key, (url, layer, _) in PT_RESERVES.items()}


def _georisques(session, path: str, params: dict) -> dict:
    resp = session.get(GEORISQUES + path, params=params, timeout=40)
    resp.raise_for_status()
    return resp.json()


def fr_risks(session, lat: float, lon: float, item: dict, building: bool = True) -> dict | None:
    commune = _fr_commune(session, lat, lon)
    if not commune:
        return None
    insee = commune["insee"]
    radon = (_georisques(session, "radon", {"code_insee": insee}).get("data") or [{}])[0]
    seismic = (_georisques(session, "zonage_sismique", {"code_insee": insee}).get("data") or [{}])[0]
    out = {"commune": commune["name"], "insee": insee, "radon": _int(radon.get("classe_potentiel")),
           "seismic_zone": _int(seismic.get("code_zone"))}
    if building:
        latlon = f"{lon},{lat}"
        out["clay"] = _int(_georisques(session, "rga", {"latlon": latlon}).get("codeExposition"))
        out["cavities"] = _georisques(session, "cavites", {"latlon": latlon, "rayon": 500}).get("results") or 0
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def dvf_sales(rows, kind: str) -> list[dict]:
    """The sales of a single house or flat in DVF rows: {"eur_m2", "area", "lat",
    "lon", "date"}. A sale of several dwellings, or of a dwelling with land
    sold as building plots, has no single price per m² and is left out."""
    by_sale: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("nature_mutation") != "Vente":
            continue
        by_sale.setdefault(r.get("id_mutation") or "", []).append(r)
    out = []
    for parts in by_sale.values():
        homes = [r for r in parts if r.get("type_local") in ("Maison", "Appartement")]
        if len(homes) != 1 or homes[0]["type_local"] != kind:
            continue
        try:
            price, area = float(homes[0]["valeur_fonciere"]), float(homes[0]["surface_reelle_bati"])
        except (TypeError, ValueError, KeyError):
            continue
        if area < 15 or price < 5000:
            continue
        r = homes[0]
        out.append({"eur_m2": price / area, "area": area, "date": r.get("date_mutation"),
                    "lat": _float(r.get("latitude")), "lon": _float(r.get("longitude"))})
    return out


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sold_summary(sales: list[dict], lat: float, lon: float) -> dict | None:
    """The median €/m² within 1 km when there are 8 sales there, else the commune's."""
    near = [s for s in sales if s["lat"] is not None and _distance_m(lat, lon, s["lat"], s["lon"]) <= 1000]
    pick, where = (near, "within 1 km") if len(near) >= 8 else (sales, "commune")
    if len(pick) < 5:
        return None
    rates = sorted(s["eur_m2"] for s in pick)
    q = statistics.quantiles(rates, n=4)
    return {"median": round(statistics.median(rates)), "low": round(q[0]), "high": round(q[2]),
            "n": len(pick), "where": where, "since": min(s["date"] for s in pick if s["date"])}


def fr_sold(session, lat: float, lon: float, item: dict) -> dict | None:
    from outline import is_flat
    commune = _fr_commune(session, lat, lon, arrondissement=True)
    if not commune:
        return None
    kind = "Appartement" if is_flat(item) else "Maison"
    rows: list[dict] = []
    this_year = int(utcnow_iso()[:4])
    for year in (this_year - 1, this_year - 2, this_year):
        resp = session.get(DVF.format(year=year, dep=commune["dep"], insee=commune["insee"]), timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        rows.extend(csv.DictReader(io.StringIO(resp.content.decode("utf-8", "replace"))))
    found = sold_summary(dvf_sales(rows, kind), lat, lon)
    if found:
        found.update(kind="flats" if kind == "Appartement" else "houses", commune=commune["name"])
    return found


def fr_energy(session, lat: float, lon: float, item: dict) -> dict:
    """{"certificates": [{"class", "date", "area", "m", "kind"}]}: the DPE issued
    within DPE_RADIUS_M of the pin, nearest first (no address is kept)."""
    resp = session.get(DPE, params={
        "geo_distance": f"{lon},{lat},{DPE_RADIUS_M}", "size": 100,
        "select": "etiquette_dpe,date_etablissement_dpe,surface_habitable_logement,type_batiment,_geopoint"},
        timeout=40)
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results") or []:
        if not r.get("etiquette_dpe"):
            continue
        out.append({"class": r["etiquette_dpe"], "date": r.get("date_etablissement_dpe"),
                    "area": r.get("surface_habitable_logement"), "m": round(r.get("_geo_distance") or 0),
                    "kind": r.get("type_batiment")})
    return {"certificates": sorted(out, key=lambda c: c["m"])}


def matching_certificate(certificates: list[dict], area: float | None) -> dict | None:
    """The latest certificate within 15 m whose floor area is within 10% of the listing's."""
    if not area:
        return None
    hits = [c for c in certificates if c["m"] <= 15 and c.get("area") and abs(c["area"] - area) <= area * 0.10]
    return max(hits, key=lambda c: c.get("date") or "") if hits else None


# Source: (function, countries or None for all, pins it needs, kinds it serves)
SOURCES = {
    "noise": (noise, None, BUILDING_PINS, {"home"}),
    "quake": (quake, None, None, {"home", "land"}),
    "amenities": (amenities, None, NEAR_PINS, {"home"}),
    "fr_zoning": (fr_zoning, {"FR"}, BUILDING_PINS, {"home", "land"}),
    "pt_zoning": (pt_zoning, {"PT"}, BUILDING_PINS, {"home", "land"}),
    "fr_risks": (fr_risks, {"FR"}, None, {"home", "land"}),
    "fr_sold": (fr_sold, {"FR"}, None, {"home"}),
    "fr_energy": (fr_energy, {"FR"}, BUILDING_PINS, {"home"}),
}


# ─── Which listings, and storing what was found ──────────────────────────
def _pin_key(pos: dict) -> list[float]:
    return [round(float(pos["lat"]), 6), round(float(pos["lon"]), 6)]


def _kind(item: dict) -> str | None:
    from scoring import property_kind
    kind = property_kind(item)
    return "land" if kind in ("urban_plot", "rural_plot") else kind


def applicable(item: dict, pos: dict) -> list[str]:
    """The sources that can say something about this listing at this pin."""
    country = (item.get("country") or "PT").upper()
    kind = _kind(item)
    out = []
    for name, (_, countries, pins, kinds) in SOURCES.items():
        if countries and country not in countries:
            continue
        if pins and pos.get("precision") not in pins:
            continue
        if kind not in kinds:
            continue
        out.append(name)
    return out


def current(item: dict) -> dict | None:
    """What was found for the listing's current pin (an earlier pin's answers
    are about another place)."""
    import geo
    found = geo._raw(item).get("surroundings")
    pos = geo.position(item)
    if not isinstance(found, dict) or not pos or found.get("pin") != _pin_key(pos) or found.get("v") != VERSION:
        return None
    return found


def missing(item: dict) -> list[str]:
    """The sources still to ask for this pin (a source that failed TRIES times is left)."""
    import geo
    pos = geo.position(item)
    if not pos:
        return []
    found = current(item) or {}
    done, failed = found.get("found") or {}, found.get("failed") or {}
    if isinstance(done.get("fr_risks"), dict) and "insee" not in done["fr_risks"]:
        done = {k: v for k, v in done.items() if k != "fr_risks"}     # asked before the code was kept
    return [n for n in applicable(item, pos) if n not in done and failed.get(n, 0) < TRIES]


def look_up(session, item: dict, names: list[str]) -> dict:
    """Ask each source; a failure is counted and asked again next scan."""
    import geo
    pos = geo.position(item)
    lat, lon = float(pos["lat"]), float(pos["lon"])
    stored = current(item) or {"v": VERSION, "pin": _pin_key(pos), "found": {}, "failed": {}}
    for name in names:
        fn = SOURCES[name][0]
        try:
            if name == "fr_risks":
                stored["found"][name] = fn(session, lat, lon, item, building=pos.get("precision") in BUILDING_PINS)
            else:
                stored["found"][name] = fn(session, lat, lon, item)
            stored["failed"].pop(name, None)
        except Exception as e:  # noqa: BLE001 — a busy service: next scan
            stored["failed"][name] = stored["failed"].get(name, 0) + 1
            LOG.info(f"Surroundings: {name} failed ({type(e).__name__})")
    stored["at"] = utcnow_iso()
    return stored


def check_pending(db, session, items: list[dict], limit: int = PER_SCAN) -> int:
    """Look up the surroundings of the best listings, once per pin."""
    import geo
    done = 0
    for item in items:
        if done >= limit:
            break
        names = missing(item)
        if not names:
            continue
        found = look_up(session, item, names)
        raw = geo._raw(item)
        raw["surroundings"] = found
        item["raw_json"] = json.dumps(raw, ensure_ascii=False)
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (item["raw_json"], item["id"]))
        db.commit()
        done += 1
        time.sleep(1.1)                 # OpenStreetMap asks for one request a second
    if done:
        LOG.info(f"Surroundings: looked up for {done} listings")
    return done


# ─── What is said ────────────────────────────────────────────────────────
def _metres(m: int) -> str:
    return f"{m} m" if m < 1000 else f"{m / 1000:.1f} km"


def notes(item: dict) -> list[str]:
    """One line per source that found something, with its source named."""
    found = (current(item) or {}).get("found") or {}
    out = []
    loud = {k: v for k, v in (found.get("noise") or {}).items() if v}
    if loud:
        what = ", ".join(f"{NOISE_SOURCES[k]} {NOISE_BANDS[v]} dB" for k, v in loud.items())
        over = [f"{NOISE_SOURCES[k]} {WHO_LDEN[k]} dB" for k in loud if k in WHO_LDEN]
        level = f" — over the WHO's health limit ({', '.join(over)})" if over else ""
        out.append(f"noise at the house (Lden): {what}{level} (EEA noise maps)")
    elif "noise" in found:
        out.append("no noise of 55 dB or more mapped here (EEA noise maps: big towns, main roads, railways "
                   "and airports, where the country reported them)")
    q = found.get("quake")
    if q and q.get("pga") is not None:
        pga = q["pga"]
        level = ("very high" if pga >= 0.3 else "high" if pga >= 0.15 else "moderate" if pga >= 0.05 else "low")
        if pga >= 0.05:
            out.append(f"earthquake hazard {level}: {pga:.2f} g once in 475 years on rock — check the building "
                       "was built or strengthened to the seismic code (ESHM20, EFEHR)")
    a = found.get("amenities")
    if a:
        have = [f"{k} {_metres(v)}" for k, v in a.items() if v is not None]
        lacking = [k for k, v in a.items() if v is None]
        line = "nearest on foot or by car: " + (", ".join(have) if have else "nothing")
        if lacking:
            line += f"; no {', '.join(lacking)} within 2 km"
        out.append(line + " (OpenStreetMap, may be incomplete)")
    z = found.get("fr_zoning")
    if z:
        if z.get("zone") is None:
            out.append("no local plan online: national planning rules (RNU) apply — building outside the "
                       "village is rarely allowed (Géoportail de l'urbanisme)")
        else:
            meaning = {"U": "built-up zone: building allowed", "AU": "zone to be built",
                       "A": "farm zone: new homes not allowed", "N": "natural zone: building very limited"}
            key = next((k for k in ("AU", "U", "A", "N") if (z["zone"] or "").upper().startswith(k)), None)
            label = f" {z['label']}" if z.get("label") else ""
            out.append(f"local plan zone{label} ({meaning.get(key, z['zone'])}) (PLU, Géoportail de l'urbanisme)")
    pz = found.get("pt_zoning")
    if pz:
        inside = [PT_RESERVES[k][2] for k in ("RAN", "REN") if pz.get(k)]
        if inside:
            out.append(f"in the {' and the '.join(inside)}: building and enlarging are restricted — "
                       "ask the câmara before counting on works (DGT)")
        else:
            out.append("outside the national farmland (RAN) and ecological (REN) reserves (DGT)")
    r = found.get("fr_risks")
    if r:
        bits = []
        if r.get("clay") and r["clay"] >= 2:
            bits.append(f"clay shrink-swell {'high' if r['clay'] >= 3 else 'medium'} (cracks walls in droughts)")
        if r.get("radon") == 3:
            bits.append("radon class 3 (high)")
        if r.get("seismic_zone") and r["seismic_zone"] >= 3:
            bits.append(f"seismic zone {r['seismic_zone']}")
        if r.get("cavities"):
            bits.append(f"{r['cavities']} mine or cavity within 500 m")
        if bits:
            out.append(f"risks at {r.get('commune') or 'the commune'}: {'; '.join(bits)} (Géorisques)")
    s = found.get("fr_sold")
    if s:
        line = (f"{s['kind']} sold {s['where'] if s['where'] != 'commune' else 'in ' + s['commune']}: median "
                f"€{s['median']:,}/m² (half between €{s['low']:,} and €{s['high']:,}; {s['n']} sales since "
                f"{s['since'][:7]})")
        mine = _eur_m2(item)
        if mine:
            line += f"; this one €{mine:,.0f}/m², {(mine / s['median'] - 1) * 100:+.0f}%"
        out.append(line + " (DVF, actual sale prices)")
    e = found.get("fr_energy")
    if e is not None:
        line = _energy_note(e.get("certificates") or [], item)
        if line:
            out.append(line)
    return out


def _energy_note(certs: list[dict], item: dict) -> str | None:
    try:
        area = float(item.get("area_m2") or 0) or None
    except (TypeError, ValueError):
        area = None
    mine = matching_certificate(certs, area)
    if mine:
        ban = f" — {LET_BAN[mine['class']]} without renovating" if mine["class"] in LET_BAN else ""
        return (f"energy rating {mine['class']}: a certificate of {mine['date'][:7]} for {mine['area']:.0f} m² at this "
                f"address, probably this home{ban} (DPE, ADEME)")
    if not certs:
        return None
    classes = sorted(c["class"] for c in certs)
    poor = sum(c in ("F", "G") for c in classes)
    return (f"{len(certs)} energy certificates at or next to this address: {classes[0]} to {classes[-1]}, "
            f"{poor} of them F or G (rentals restricted from 2025–2028); none matches this floor area (DPE, ADEME)")


def _eur_m2(item: dict) -> float | None:
    area = item.get("area_m2")
    price = item.get("current_bid") or item.get("price") or item.get("min_price")
    try:
        area, price = float(area), float(price)
    except (TypeError, ValueError):
        return None
    return price / area if area >= 15 and price > 0 else None
