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
- power (every EU country): high-voltage lines (110 kV and up) within 1 km,
  wind turbines and wind or solar farms within 2 km, from OpenStreetMap,
  which maps the grid well. Buyers pay less next to pylons and turbines;
- zoning: France, the local plan's zone (PLU, IGN Géoportail de
  l'urbanisme), or that the town has none online (national rules, RNU);
  Portugal, whether the point is in the national farmland (RAN) or ecological
  (REN) reserve, where building is restricted (DGT);
- French risks: clay shrink-swell (RGA), radon class and seismic zone of the
  commune, mines and cavities within 500 m (BRGM / Géorisques);
- sold prices: France only, the houses or flats sold in the commune in the
  last two years (DVF, the tax office's register of sales); and every sale
  of the cadastral parcel itself since 2021, once the outline has found it:
  the house's own past price, or the flats sold in its building;
- urban regeneration areas: Portugal, whether the point is in an ARU on
  DGT's national register. Renovating there can qualify for 6% VAT on the
  works and IMT/IMI exemptions (Estatuto dos Benefícios Fiscais, art. 45 and
  71). The register misses some towns (Lisbon's ARU is not on it), so a miss
  says to ask the câmara;
- land values: Germany, the official Bodenrichtwert of the zone (the local
  Gutachterausschuss, from sales), €/m² of land without the building. North
  Rhine-Westphalia so far (BORIS-NRW, open data); each state runs its own
  service and Bavaria's is not open;
- energy ratings: France only, the energy performance certificates (DPE,
  ADEME) issued at the address since July 2021. One whose floor area matches
  the listing's is probably this home's. A G home cannot be let on a new lease
  since 2025, F from 2028 and E from 2034 (loi Climat et résilience);
- heritage: France, the protections the planning servitudes register (IGN
  Géoportail de l'urbanisme, AC1 / AC2 / AC4): a monument historique, its
  protected surroundings (500 m or the perimeter set for it), a listed site, a
  site patrimonial remarquable. Works there need the Architecte des Bâtiments
  de France's agreement. Portugal, DGPC's atlas of classified heritage
  (mainland): a classified building or ensemble and its protection zones
  (ZGP, ZEP), where works need the heritage office's opinion;
- Italian hazards: the basin authorities' landslide (P1-P4, attention areas)
  and flood (P1-P3) zones as ISPRA's IdroGEO joins them for the whole
  country, and whether a mapped landslide (IFFI inventory) is at the point;
- official flood maps (Floods Directive): Spain, the water depth of the 10,
  100 and 500-year river floods and the 100 and 500-year sea floods
  (MITECO, SNCZI via IDEE); Portugal, the depth of the 20, 100 and
  1,000-year floods (APA) and the legal "zonas adjacentes" where building is
  restricted; France, the natural and technological risk prevention plans
  (PPR, PPRT) on the planning servitudes. The maps cover the areas each
  country found at risk, not every stream;
- French polluted ground and dangerous plants: former industrial sites
  within 100 m (CASIAS), sites with known pollution within 300 m (BASOL,
  SIS) and Seveso plants within 3 km (Géorisques);
- protected nature, EU-wide: the Natura 2000 sites (habitats and birds
  directives) and each country's own parks and reserves with their IUCN
  category (EEA, CDDA/NatDA);
- Portugal's coastal programmes (APA, POC): the safeguard strips against
  erosion, overtopping and cliff falls, the critical areas, the 500 m
  land protection zone and the public maritime domain's margin;
- land use, EU-wide (CORINE Land Cover 2018, EEA): what the land at the
  point is (town, farmland, forest, wetland) and the nuisances near it: an
  industrial or commercial area or a road or rail yard within 300 m, a port,
  an airport, a quarry or a dump within 1 km. CORINE maps patches of 25 ha
  and more, so a small workshop or field is not in it;
- bathing water, EU-wide: the nearest official bathing site within 10 km
  and its class under the Bathing Water Directive (Excellent, Good,
  Sufficient, Poor) for the latest season and the ones before (EEA);
- winter sun, EU-wide (PVGIS, JRC): the hours the sun clears the hills and
  mountains on 21 December (the terrain's horizon, not buildings or trees),
  and how much of December's direct sun the clouds let through (SARAH-3
  satellite, 2005 onwards), so roughly how many hours of sun an average
  December day gets;
- PFAS ("forever chemicals"), EU-wide: the highest sum measured in ground or
  surface water within 3 km, against the EU drinking-water limit of 0.1 µg/l
  for 20 PFAS (EEA, WISE monitoring);
- altitude and slope (EU-DEM 25 m, Copernicus): the height, how steep the
  ground is and which way it faces;
- the trip from home (Amsterdam Centraal): by train, coach and ferry, the
  fastest journey found leaving on the next Tuesday morning or evening
  (Transitous, the open journey planner built on the operators' timetables;
  where no stop is close, the last 45 min by taxi); and by plane, through the
  nearest airports Schiphol flies to directly (Eurostat route statistics):
  2 h to the gate, the flight (estimated from the distance), 30 min out of the
  airport and the faster of public transport or a taxi from there. Over 20
  hours either way is flagged as too far.

A street, village or town pin is not the building, so the point checks (noise,
zoning, clay) need a building pin; amenities take a street or village pin too;
earthquakes, the commune's risks and sold prices take any position.

Each answer is shown with its source and never changes a score (AGENTS.md):
a map is an estimate of a place, not a survey of the house.
"""
from __future__ import annotations

import csv
import functools
import io
import json
import math
import os
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
# BORIS-NRW layers: building land (one or two storeys, several, outlying,
# other), then farmland and forest
DE_BORIS = {"NRW": ("https://www.wms.nrw.de/boris/wms_nw_brw", (51.9, 6.1, 52.6, 9.5), (50.3, 5.8, 51.9, 9.5))}
DE_BORIS_LAYERS = {"20": "building land", "17": "building land (flats)", "5": "building land outside the village",
                   "2": "other land", "14": "commercial land", "8": "farmland", "11": "forest"}
DE_LAND_USE = {"W": "residential", "MI": "mixed", "WS": "small-holding", "A": "arable", "GR": "grassland",
               "F": "forest", "G": "commercial", "M": "mixed"}
FR_HERITAGE = {"ac1": "monument", "ac2": "site", "ac4": "spr", "pm1": "ppr", "pm3": "pprt"}
PT_HERITAGE = ("https://geo.patrimoniocultural.gov.pt/arcgis/rest/services/PatrimonioClassificadoEmViasClassificacao/"
               "Atlas_Patrimonio_Servicos/MapServer/identify")
PT_HERITAGE_LAYERS = {"PatrimonioImovel": "inside", "ZEP": "zep", "ZGP": "zgp", "Restricoes": "zep"}
ES_FLOOD = "https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones"
ES_FLOOD_LAYERS = {"NZ.Flood.FluvialT10": ("river", 10), "NZ.Flood.FluvialT100": ("river", 100),
                   "NZ.Flood.FluvialT500": ("river", 500), "NZ.Flood.MarinaT100": ("sea", 100),
                   "NZ.Flood.MarinaT500": ("sea", 500)}
PT_FLOOD = "https://sniambgeoogc.apambiente.pt/getogc/rest/services/SNIAmb/{}/MapServer/identify"
SEVESO_KM = {"Seveso seuil haut": 3.0, "Seveso seuil bas": 1.0}
EEA_SITES = "https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/{}/MapServer/{}/query"
NATURA_TYPES = {"A": "birds", "B": "habitats", "C": "habitats and birds"}
PT_COAST = "https://sniambgeoogc.apambiente.pt/getogc/rest/services/SNIAmb/POC/MapServer/identify"
PT_COAST_LAYERS = {8: "margin", 10: "flooding", 11: "erosion", 12: "cliff", 15: "critical", 16: "zone"}
CORINE = "https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/CLC2018_WM/MapServer/0/query"
CORINE_NEAR = {"121": (300, "an industrial or commercial area"), "122": (300, "a road or rail yard"),
               "123": (1000, "a port"), "124": (1000, "an airport"), "131": (1000, "a quarry or mine"),
               "132": (1000, "a dump"), "133": (300, "a construction site")}
CORINE_AT = {"111": "dense town centre", "112": "town or village", "123": "port", "124": "airport", "12": "industrial, commercial or transport land",
             "13": "quarry, dump or building site", "14": "urban green or sports area", "2": "farmland",
             "31": "forest", "32": "scrub or grassland", "33": "bare or burnt land", "4": "wetland", "5": "water"}
BATHING = "https://water.discomap.eea.europa.eu/arcgis/rest/services/BathingWater"
BATHING_KM = 10
PVGIS = "https://re.jrc.ec.europa.eu/api/v5_3/"
SUN_HOURS_MIN = 6                 # the winter sun a buyer can count on
PFAS = "https://water.discomap.eea.europa.eu/arcgis/rest/services/PFAS/PFAS_all/MapServer/0/query"
PFAS_KM = 3
PFAS_LIMIT = 0.1                  # µg/l, sum of 20 PFAS (Directive 2020/2184, from 2026)
DEM = "https://api.opentopodata.org/v1/eudem25m"
DEM_STEP_M = 30
TRANSITOUS = "https://api.transitous.org/api/v1/plan"
HOME = ("Amsterdam", 52.3791, 4.9003)            # Amsterdam Centraal
TRAVEL_MAX_H = 20
TRAVEL_DEPARTURES = ("06:00", "17:00")          # UTC: a morning start and an evening one (night trains)
TAXI_S = 2700
FLIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flights_from_ams.csv")
TO_GATE_H, FROM_PLANE_H = 2.0, 0.5            # train to Schiphol and check-in; leaving the airport
FLIGHT_KMH, FLIGHT_EXTRA_H = 750, 0.5          # cruise and taxi/climb/descent, for the flight's length
AIRPORT_KM = 400
TRAVEL_MODES = {"HIGHSPEED_RAIL": "high-speed train", "LONG_DISTANCE": "train", "NIGHT_RAIL": "night train",
                "REGIONAL_FAST_RAIL": "regional train", "REGIONAL_RAIL": "regional train", "RAIL": "train",
                "COACH": "coach", "BUS": "bus", "FERRY": "ferry", "SUBWAY": "metro", "METRO": "metro",
                "TRAM": "tram", "SUBURBAN": "suburban train"}
IT_IDROGEO = "https://idrogeo.isprambiente.it/geoserver/ows"
IT_LANDSLIDE = {1: "moderate (P1)", 2: "medium (P2)", 3: "high (P3)", 4: "very high (P4)", 5: "an attention area"}
IT_FLOOD = {"scenariop1": (1, "low (P1, rare floods)"), "scenariop2": (2, "medium (P2, every 100–200 years)"),
            "scenariop3": (3, "high (P3, every 20–50 years)")}
PT_ARU = ("https://servicos.dgterritorio.pt/SDISNITWMSARU/WMService.aspx", "ARU")
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
LINE_RADIUS_M, PLANT_RADIUS_M = 1000, 2000
HIGH_VOLTAGE = 110000


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


def power_query(lat: float, lon: float) -> str:
    return (f'[out:json][timeout:60];(way["power"="line"](around:{LINE_RADIUS_M},{lat},{lon});'
            f'node["power"="generator"]["generator:source"="wind"](around:{PLANT_RADIUS_M},{lat},{lon});'
            f'nwr["power"="plant"]["plant:source"~"wind|solar"](around:{PLANT_RADIUS_M},{lat},{lon}););out tags geom;')


def _segment_m(lat, lon, a, b) -> float:
    """Metres from the point to the segment a-b (flat projection: fine within a few km)."""
    k = math.cos(math.radians(lat)) * 111320
    ax, ay, bx, by = (a["lon"] - lon) * k, (a["lat"] - lat) * 110540, (b["lon"] - lon) * k, (b["lat"] - lat) * 110540
    dx, dy = bx - ax, by - ay
    t = 0.0 if not (dx or dy) else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def _volts(tag) -> int:
    return max((int(v) for v in str(tag or "").replace(",", ";").split(";") if v.strip().isdigit()), default=0)


def _centre(el: dict) -> tuple[float, float] | None:
    if el.get("lat") is not None:
        return el["lat"], el["lon"]
    pts = el.get("geometry") or [p for m in el.get("members") or [] for p in (m.get("geometry") or [])]
    if not pts:
        b = el.get("bounds")
        return ((b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2) if b else None
    return sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts)


def power_nearby(elements: list[dict], lat: float, lon: float) -> dict:
    """{"line": {"kv", "m"} or None, "turbines": n, "turbine_m", "farms": [{"source", "m"}]}."""
    line, turbines, farms = None, [], []
    for el in elements:
        tags = el.get("tags") or {}
        if tags.get("power") == "line" and el.get("geometry"):
            volts = _volts(tags.get("voltage"))
            if volts and volts < HIGH_VOLTAGE:
                continue
            pts = el["geometry"]
            d = min(_segment_m(lat, lon, a, b) for a, b in zip(pts, pts[1:])) if len(pts) > 1 else None
            if d is not None and (line is None or d < line["m"]):
                line = {"kv": volts // 1000 or None, "m": round(d)}
        elif tags.get("power") == "generator":
            c = _centre(el)
            if c:
                turbines.append(round(_distance_m(lat, lon, *c)))
        elif tags.get("power") == "plant":
            c = _centre(el)
            if c:
                farms.append({"source": tags.get("plant:source"), "m": round(_distance_m(lat, lon, *c))})
    return {"line": line, "turbines": len(turbines), "turbine_m": min(turbines) if turbines else None,
            "farms": sorted(farms, key=lambda f: f["m"])[:3]}


def power(session, lat: float, lon: float, item: dict) -> dict:
    from geo import OVERPASS, USER_AGENT
    resp = session.post(OVERPASS, data={"data": power_query(lat, lon)}, headers={"User-Agent": USER_AGENT},
                        timeout=90)
    resp.raise_for_status()
    return power_nearby(resp.json().get("elements") or [], lat, lon)


def _in(box, lat, lon) -> bool:
    return box[0] <= lat <= box[2] and box[1] <= lon <= box[3]


def de_land(session, lat: float, lon: float, item: dict) -> dict:
    """{"zones": [{"layer", "eur_m2", "date", "use", "place"}]} for the zones at
    the point, or {"zones": [], "state": None} where no open service covers it."""
    for state, (url, *boxes) in DE_BORIS.items():
        if not any(_in(b, lat, lon) for b in boxes):
            continue
        d = 0.0005
        layers = ",".join(DE_BORIS_LAYERS)
        resp = session.get(url, params={
            "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layers,
            "QUERY_LAYERS": layers, "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d}",
            "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "INFO_FORMAT": "application/geo+json",
            "FEATURE_COUNT": 10}, timeout=40)
        resp.raise_for_status()
        zones = boris_zones(resp.json())
        if zones:
            return {"state": state, "zones": zones}
    return {"state": None, "zones": []}


def boris_zones(geojson: dict) -> list[dict]:
    order = {"brw_ein_zweigeschossig": "20", "brw_mehrgeschossige_bauweise": "17", "brw_aussenbereich": "5",
             "brw_sonstige_flaechen": "2", "brw_gewerbe_industrie_sondergebiete": "14", "brw_landwirtschaft": "8",
             "brw_forstwirtschaft": "11"}
    out = []
    for f in geojson.get("features") or []:
        layer = order.get(f.get("layerName"))
        p = f.get("properties") or {}
        value = _float(str(p.get("BRW") or "").replace(".", "").replace(",", "."))
        if not layer or value is None:
            continue
        out.append({"layer": layer, "eur_m2": value, "date": (p.get("STAG") or "")[:4],
                    "use": (p.get("NUTA") or "").strip(), "place": (p.get("ORTST") or p.get("GENA") or "").strip()})
    out.sort(key=lambda z: list(order.values()).index(z["layer"]))
    return out


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


def pt_aru(session, lat: float, lon: float, item: dict) -> dict:
    """{"name", "notice"} of the ARU at the point, or {"name": None} outside every ARU on the register."""
    d = 0.0005
    url, layer = PT_ARU
    resp = session.get(url, params={
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
        "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d}",
        "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "INFO_FORMAT": "text/xml", "FEATURE_COUNT": 1}, timeout=40)
    resp.raise_for_status()
    return aru_attributes(resp.content.decode("utf-8", "replace"))


def aru_attributes(xml: str) -> dict:
    import html
    import re
    found = {k: html.unescape(v).strip() for k, v in re.findall(r'<Attribute Name="([^"]+)">([^<]*)<', xml)}
    if not found:
        return {"name": None}
    name = next((v for k, v in found.items() if k.startswith("Designa")), None)
    return {"name": name, "notice": found.get("DR"),
            "town": found.get("Concelho")}


def pt_zoning(session, lat: float, lon: float, item: dict) -> dict:
    return {key: _pt_reserve(session, url, layer, lat, lon) for key, (url, layer, _) in PT_RESERVES.items()}



def _unmangle(text: str | None) -> str:
    """Some names come UTF-8 encoded twice ("HÃ´tel"): undone where it was."""
    text = (text or "").strip()
    if "Ã" in text or "Â" in text:
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def fr_heritage(session, lat: float, lon: float, item: dict) -> dict:
    d = 0.00005
    resp = session.get(FR_PLU, params={
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "wfs_sup:assiette_sup_s",
        "OUTPUTFORMAT": "application/json", "COUNT": 1000, "propertyName": "suptype,typeass,nomsuplitt",
        "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d},urn:ogc:def:crs:EPSG::4326"}, timeout=60)
    resp.raise_for_status()
    return fr_protections(json.loads(resp.content.decode("utf-8", "replace")).get("features") or [])


def fr_protections(features: list[dict]) -> dict:
    """{"monument": [names the point is on], "near": [monuments whose surroundings it is in],
    "site": [listed sites], "spr": [sites patrimoniaux remarquables]}."""
    out: dict[str, list[str]] = {"monument": [], "near": [], "site": [], "spr": [], "ppr": [], "pprt": []}
    for f in features:
        p = f.get("properties") or {}
        kind = FR_HERITAGE.get((p.get("suptype") or "").lower())
        if not kind:
            continue
        name = _unmangle(p.get("nomsuplitt")) or ("unnamed plan" if kind in ("ppr", "pprt") else "")
        if kind == "monument":
            kind = "monument" if "monument" in (p.get("typeass") or "").lower() else "near"
        if name not in out[kind]:
            out[kind].append(name)
    out["near"] = [n for n in out["near"] if n not in out["monument"]]
    return out


def pt_heritage(session, lat: float, lon: float, item: dict) -> dict:
    d = 0.002
    resp = session.get(PT_HERITAGE, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "sr": 4326, "layers": "all",
        "tolerance": 1, "mapExtent": f"{lon - d},{lat - d},{lon + d},{lat + d}", "imageDisplay": "400,400,96",
        "returnGeometry": "false", "f": "json"}, timeout=60)
    resp.raise_for_status()
    return pt_protections(json.loads(resp.content.decode("utf-8", "replace")).get("results") or [])


def pt_protections(results: list[dict]) -> dict:
    """{"inside": [[name, category, status]], "zep": [names], "zgp": [names]}: the classified
    buildings or ensembles the point is in, and whose protection zones it is in."""
    out: dict[str, list] = {"inside": [], "zep": [], "zgp": []}
    for r in results:
        key = PT_HERITAGE_LAYERS.get(r.get("layerName"))
        a = r.get("attributes") or {}
        name = (a.get("Designacao") or "").strip()
        if not key or not name:
            continue
        if key == "inside":
            if name not in [e[0] for e in out["inside"]]:
                out["inside"].append([name, (a.get("Categoria") or "").strip(), (a.get("Situacao_A") or "").strip()])
        elif name not in out[key]:
            out[key].append(name)
    inside = {e[0] for e in out["inside"]}
    out["zep"] = [n for n in out["zep"] if n not in inside]
    out["zgp"] = [n for n in out["zgp"] if n not in inside and n not in out["zep"]]
    return out


def _idrogeo(session, layer: str, lat: float, lon: float) -> list[dict]:
    d = 0.0005
    resp = session.get(IT_IDROGEO, params={
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
        "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d}",
        "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "INFO_FORMAT": "application/json", "FEATURE_COUNT": 10},
        timeout=60)
    resp.raise_for_status()
    return [f.get("properties") or {} for f in resp.json().get("features") or []]


def it_hazard(session, lat: float, lon: float, item: dict) -> dict:
    return it_hazards(_idrogeo(session, "idrogeo:pericolosita_frane", lat, lon),
                      _idrogeo(session, "idrogeo:pericolosita_alluvioni", lat, lon),
                      _idrogeo(session, "idrogeo:frane", lat, lon))


def it_hazards(landslide: list[dict], flood: list[dict], mapped: list[dict]) -> dict:
    """{"landslide": the highest class 1-4 (5: attention area), "flood": 1-3,
    "mapped": landslides on the inventory}."""
    classes = [int(p["cod_per_it"]) for p in landslide if str(p.get("cod_per_it") or "").isdigit()]
    real = [c for c in classes if c <= 4]
    flood_classes = [IT_FLOOD[k][0] for p in flood for k in p if k in IT_FLOOD and p[k]]
    return {"landslide": max(real) if real else (5 if 5 in classes else None),
            "flood": max(flood_classes) if flood_classes else None, "mapped": len(mapped)}


def es_flood(session, lat: float, lon: float, item: dict) -> dict:
    """{"river": {years: depth m}, "sea": {...}}: where each mapped flood reaches the point."""
    d = 0.0005
    out: dict[str, dict] = {"river": {}, "sea": {}}
    for layer, (kind, years) in ES_FLOOD_LAYERS.items():
        resp = session.get(ES_FLOOD, params={
            "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer,
            "QUERY_LAYERS": layer, "STYLES": "", "CRS": "EPSG:4326",
            "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d}", "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "INFO_FORMAT": "text/plain"}, timeout=60)
        resp.raise_for_status()
        depth = flood_depth_text(resp.text)
        if depth:
            out[kind][str(years)] = depth
    return out


def flood_depth_text(text: str) -> float | None:
    """The water depth in a GetFeatureInfo answer ("GRAY_INDEX = 1.25"). The
    map's other values (0, 999, -3, -9999, 3.4E38) mean dry or not studied."""
    import re
    m = re.search(r"GRAY_INDEX\s*=\s*(-?[\d.]+(?:E-?\d+)?)", text)
    try:
        v = float(m.group(1)) if m else 0.0
    except ValueError:
        return None
    return round(v, 2) if 0 < v < 100 else None


def _arcgis_identify(session, url: str, lat: float, lon: float, layers: str) -> list[dict]:
    d = 0.002
    resp = session.get(url, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "sr": 4326, "layers": layers,
        "tolerance": 1, "mapExtent": f"{lon - d},{lat - d},{lon + d},{lat + d}", "imageDisplay": "400,400,96",
        "returnGeometry": "false", "f": "json"}, timeout=60)
    resp.raise_for_status()
    data = json.loads(resp.content.decode("utf-8", "replace"))
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return data.get("results") or []


def pt_flood(session, lat: float, lon: float, item: dict) -> dict:
    depths = _arcgis_identify(session, PT_FLOOD.format("Diretiva200760CE_2c"), lat, lon, "all:2")
    adjacent = _arcgis_identify(session, PT_FLOOD.format("ZonasAdjacentes_PubDR"), lat, lon, "all:0")
    return pt_floods(depths, adjacent)


def pt_floods(depths: list[dict], adjacent: list[dict]) -> dict:
    """{"river": {years: depth m}, "place": the mapped area, "adjacent": in a legal zona adjacente}."""
    out: dict = {"river": {}, "place": None, "adjacent": bool(adjacent)}
    for r in depths:
        a = r.get("attributes") or {}
        years = str(a.get("pretorno") or "").lstrip("T0")
        try:
            depth = float(str(a.get("Profundidade") or "").replace(",", "."))
        except ValueError:
            continue
        if years.isdigit() and depth > 0:
            out["river"][years] = max(round(depth, 2), out["river"].get(years, 0))
            out["place"] = out["place"] or a.get("Local")
    return out


def _flood_note(f: dict | None, source: str) -> str | None:
    if not f:
        return None
    bits = []
    for kind in ("river", "sea"):
        reach = f.get(kind) or {}
        if reach:
            first = min(reach, key=int)
            more = "; ".join(f"{reach[y]:.1f} m in a {int(y):,}-year one" for y in sorted(reach, key=int)[1:])
            bits.append(f"in the {int(first):,}-year {kind} flood zone, water up to {reach[first]:.1f} m"
                        + (f" ({more})" if more else ""))
    if f.get("adjacent"):
        bits.append("in a legal flood zone (zona adjacente): building there is restricted")
    if not bits:
        return f"not in a mapped flood zone ({source}; the maps cover the areas found at risk, not every stream)"
    return "; ".join(bits) + f" ({source})"


def fr_soil(session, lat: float, lon: float, item: dict) -> dict:
    latlon = f"{lon},{lat}"
    former = _georisques(session, "ssp/casias", {"latlon": latlon, "rayon": 100}).get("data") or []
    known = []
    for path in ("ssp/instructions", "ssp/conclusions_sis"):
        known += _georisques(session, path, {"latlon": latlon, "rayon": 300}).get("data") or []
    plants = []
    for page in (1, 2, 3):
        got = _georisques(session, "installations_classees", {"latlon": latlon, "rayon": 3000,
                                                               "page": page, "page_size": 100})
        plants += got.get("data") or []
        if page >= (got.get("total_pages") or 1):
            break
    return soil_found(former, known, plants, lat, lon)


def soil_found(former: list[dict], known: list[dict], plants: list[dict], lat: float, lon: float) -> dict:
    """{"former": [activities], "known": [names], "seveso": [{"name", "level", "km"}]}."""
    seveso = []
    for p in plants:
        level = p.get("statutSeveso")
        if level not in SEVESO_KM or p.get("latitude") is None:
            continue
        km = _distance_m(lat, lon, float(p["latitude"]), float(p["longitude"])) / 1000
        if km <= SEVESO_KM[level]:
            seveso.append({"name": p.get("raisonSociale") or "", "level": "high" if "haut" in level else "low",
                           "km": round(km, 1)})
    names = [x.get("nom_etablissement") or x.get("nom_site") or x.get("nom") or "" for x in known]
    return {"former": [(x.get("nom_etablissement") or "").strip() for x in former],
            "known": [n for n in names if n], "known_n": len(known),
            "seveso": sorted(seveso, key=lambda x: x["km"])}


def _soil_note(s: dict | None) -> list[str]:
    if not s:
        return []
    out = []
    if s.get("known_n"):
        out.append("a site with known soil pollution within 300 m"
                   + (f" ({_names(s['known'])})" if s.get("known") else "") + " (Géorisques, BASOL/SIS)")
    if s.get("former"):
        n = len(s["former"])
        out.append(f"{n} former industrial or service site{'s' if n > 1 else ''} within 100 m "
                   f"({_names([x for x in s['former'] if x] or ['unnamed'])}): the ground may be polluted "
                   "(Géorisques, CASIAS)")
    plants = s.get("seveso") or []
    if plants:
        near = plants[0]
        more = (f"; {len(plants)} Seveso plants within {max(SEVESO_KM.values()):.0f} km"
                if len(plants) > 1 else "")
        out.append(f"Seveso {near['level']}-threshold plant {near['km']:.1f} km away ({near['name']}){more}: "
                   "an accident plan and possibly a PPRT apply (Géorisques)")
    return out


def _eea_sites(session, service: str, layer: int, lat: float, lon: float) -> list[dict]:
    resp = session.get(EEA_SITES.format(service, layer), params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "false", "f": "json"},
        timeout=60)
    resp.raise_for_status()
    data = json.loads(resp.content.decode("utf-8", "replace"))
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return [f.get("attributes") or {} for f in data.get("features") or []]


def nature(session, lat: float, lon: float, item: dict) -> dict:
    natura = _eea_sites(session, "Natura2000_Dyna_WM", 4, lat, lon) + _eea_sites(session, "Natura2000_Dyna_WM", 8,
                                                                                 lat, lon)
    return nature_found(natura, _eea_sites(session, "NatDAv24_Dyna_WM", 4, lat, lon))


def nature_found(natura: list[dict], national: list[dict]) -> dict:
    """{"natura": [[code, name, "habitats"|"birds"|...]], "national": [[name, IUCN label]]}."""
    out: dict[str, list] = {"natura": [], "national": []}
    for a in natura:
        code = a.get("SITECODE") or ""
        if code and code not in [n[0] for n in out["natura"]]:
            out["natura"].append([code, (a.get("SITENAME") or "").strip(), NATURA_TYPES.get(a.get("SITETYPE"), "")])
    codes = {n[0] for n in out["natura"]}
    for a in national:
        name = (a.get("siteName") or "").strip()
        if not name or a.get("nationalId") in codes:    # the Natura site itself, listed again
            continue
        iucn = a.get("iucnDescri") or ""
        iucn = "" if iucn.startswith("Other") else iucn
        if [name, iucn] not in out["national"]:
            out["national"].append([name, iucn])
    return out


def _nature_note(n: dict | None) -> list[str]:
    if not n:
        return []
    out = []
    if n["natura"]:
        sites = [f"{name or code} ({code}, {kind})" if kind else f"{name or code} ({code})"
                 for code, name, kind in n["natura"]]
        out.append(f"inside Natura 2000 site {_names(sites)}: works or a change of use that could harm it need "
                   "an environmental assessment and may be refused (EEA; Habitats Directive art. 6)")
    if n["national"]:
        parks = [f"{name} — {iucn}" if iucn else name for name, iucn in n["national"]]
        out.append(f"inside a protected area under national law ({_names(parks)}): building is restricted "
                   "or banned by its own rules (EEA, CDDA)")
    return out


def pt_coast(session, lat: float, lon: float, item: dict) -> dict:
    layers = "all:0," + ",".join(str(k) for k in PT_COAST_LAYERS)
    return pt_coast_found(_arcgis_identify(session, PT_COAST, lat, lon, layers))


def pt_coast_found(results: list[dict]) -> dict:
    """{"poc": the programme, kind: level or True} for the strips the point is in."""
    out: dict = {"poc": None}
    for r in results:
        a = r.get("attributes") or {}
        out["poc"] = out["poc"] or (a.get("poc") or None)
        kind = PT_COAST_LAYERS.get(r.get("layerId"))
        if kind:
            level = (a.get("legenda_4") or "").replace("Nível", "level").strip()
            out[kind] = level if level and level != "Null" else out.get(kind, True)
    return out


_PT_COAST_WORDS = {"erosion": "the coastal erosion safeguard strip", "flooding": "the overtopping and coastal "
                   "flooding safeguard strip", "cliff": "a cliff safeguard strip", "critical": "a critical area",
                   "margin": "the margin of the public maritime domain (DPM)",
                   "zone": "the 500 m land protection zone"}


def _pt_coast_note(c: dict | None) -> str | None:
    if not c or not c.get("poc"):
        return None
    inside = [_PT_COAST_WORDS[k] + (f" ({c[k]})" if isinstance(c[k], str) else "")
              for k in ("erosion", "flooding", "cliff", "critical", "margin", "zone") if c.get(k)]
    if not inside:
        return f"inside the {c['poc']} area but outside its safeguard strips (APA, POC)"
    strips = any(c.get(k) for k in ("erosion", "flooding", "cliff", "critical", "margin"))
    rule = (": new building and enlarging are banned or restricted there" if strips
            else ": new building is limited and must follow the programme")
    return f"in {', '.join(inside)}{rule} (APA, {c['poc']})"


def _corine(session, lat: float, lon: float, metres: int) -> set[str]:
    resp = session.get(CORINE, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": metres, "units": "esriSRUnit_Meter",
        "outFields": "Code_18", "returnGeometry": "false", "f": "json"}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return {str((f.get("attributes") or {}).get("Code_18") or "") for f in data.get("features") or []}


def land_use(session, lat: float, lon: float, item: dict) -> dict:
    return land_use_found(_corine(session, lat, lon, 1), _corine(session, lat, lon, 300),
                          _corine(session, lat, lon, 1000))


def land_use_found(at: set[str], near: set[str], far: set[str]) -> dict:
    """{"at": CORINE code at the point, "near": [codes of nuisances within their distance]}."""
    point = sorted(at)[0] if at else None
    found = [c for c, (m, _) in CORINE_NEAR.items() if c in (near if m <= 300 else far) and c != point]
    return {"at": point, "near": found}


def _land_use_note(u: dict | None) -> str | None:
    if not u or not (u.get("at") or u.get("near")):
        return None
    code = u.get("at") or ""
    what = next((CORINE_AT[k] for k in sorted(CORINE_AT, key=len, reverse=True) if code.startswith(k)), None)
    bits = [f"land here: {what}"] if what else []
    near = [f"{CORINE_NEAR[c][1]} within " + (f"{CORINE_NEAR[c][0]} m" if CORINE_NEAR[c][0] < 1000 else "1 km")
            for c in u.get("near") or []]
    if near:
        bits.append(", ".join(near))
    return "; ".join(bits) + " (CORINE Land Cover 2018, EEA)" if bits else None


_bathing_service: list[str] = []


def _bathing_url(session) -> str:
    """The newest season's map (one service per year; the undated one lags)."""
    if not _bathing_service:
        resp = session.get(BATHING, params={"f": "json"}, timeout=60)
        resp.raise_for_status()
        years = [x["name"] for x in resp.json().get("services") or [] if x["name"][-4:].isdigit()]
        _bathing_service.append(f"{BATHING}/{max(years, key=lambda n: n[-4:]).split('/')[-1]}/MapServer/3/query")
    return _bathing_service[0]


def bathing(session, lat: float, lon: float, item: dict) -> dict:
    url = _bathing_url(session)
    resp = session.get(url, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": BATHING_KM * 1000, "units": "esriSRUnit_Meter",
        "outFields": "*", "returnGeometry": "false", "f": "json"}, timeout=60)
    resp.raise_for_status()
    data = json.loads(resp.content.decode("utf-8", "replace"))
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return bathing_found([f.get("attributes") or {} for f in data.get("features") or []], lat, lon,
                         int(url.split("_WM_")[1][:4]))


def bathing_found(sites: list[dict], lat: float, lon: float, season: int) -> dict:
    """{"season", "nearest": {"name", "kind", "km", "class", "past": [classes before]}}."""
    best = None
    for a in sites:
        if a.get("latitude") is None or not a.get("qualityStatus"):
            continue
        km = _distance_m(lat, lon, float(a["latitude"]), float(a["longitude"])) / 1000
        if best is None or km < best["km"]:
            past = [a.get(f"qualityStatus_minus{i}") for i in range(1, 6)]
            best = {"name": (a.get("bathingWaterName") or "").strip().title(),
                    "kind": (a.get("bwWaterCategory") or "").lower(), "km": round(km, 1),
                    "class": a["qualityStatus"], "past": [p for p in past if p and p != "Not classified"]}
    return {"season": season, "nearest": best}


def _bathing_note(b: dict | None) -> str | None:
    if not b or not b.get("nearest"):
        return None
    n = b["nearest"]
    past = n.get("past") or []
    if past and all(p == n["class"] for p in past):
        history = f", {n['class']} every season since {b['season'] - len(past)}"
    elif past:
        history = f" (before: {', '.join(past[:3])})"
    else:
        history = ""
    kind = {"coastal": "sea, ", "transitional": "estuary, "}.get(n.get("kind"), "inland, " if n.get("kind") else "")
    rated = ("not classified yet" if n["class"] == "Not classified" else n["class"]) + f" in {b['season']}"
    return (f"nearest official bathing water: {n['name']} ({kind}{n['km']:.1f} km), {rated}{history} "
            "(EEA, Bathing Water Directive)")


def _sun(lat: float, lon: float, minute: float) -> tuple[float, float]:
    """The sun's elevation and azimuth (degrees; azimuth from the south, west
    positive, as PVGIS counts) on 21 December, `minute` after midnight UTC."""
    decl = math.radians(-23.44)
    eq_time = 1.7                                   # minutes, near the solstice
    hour = math.radians((minute + eq_time + 4 * lon) / 4 - 180)
    phi = math.radians(lat)
    sin_el = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour)
    el = math.asin(max(-1.0, min(1.0, sin_el)))
    az = math.atan2(math.sin(hour), math.cos(hour) * math.sin(phi) - math.tan(decl) * math.cos(phi))
    return math.degrees(el), math.degrees(az)


def _horizon_at(horizon: list[tuple[float, float]], az: float) -> float:
    """The terrain's height (degrees) toward azimuth `az`, between profile points."""
    for (a1, h1), (a2, h2) in zip(horizon, horizon[1:]):
        if a1 <= az <= a2:
            return h1 + (h2 - h1) * (az - a1) / (a2 - a1) if a2 > a1 else h1
    return horizon[0][1] if horizon else 0.0


def winter_sun_hours(lat: float, lon: float, horizon: list[tuple[float, float]]) -> dict:
    """{"day": hours of daylight, "sun": hours the sun is above the terrain, "clear_dni": kWh/m²
    of direct sun a clear 21 December would bring}, minute by minute."""
    day = sun = 0
    dni = 0.0
    for minute in range(0, 1440, 2):
        el, az = _sun(lat, lon, minute)
        if el <= -0.833:
            continue
        day += 2
        if el > max(_horizon_at(horizon, az), 0.0):
            sun += 2
            air_mass = 1 / (math.sin(math.radians(el)) + 0.50572 * (el + 6.07995) ** -1.6364)
            dni += 1.353 * 0.7 ** (air_mass ** 0.678) * 2 / 60     # kW/m² × hours (Meinel)
    return {"day": round(day / 60, 1), "sun": round(sun / 60, 1), "clear_dni": round(dni, 3)}


def winter_sun(session, lat: float, lon: float, item: dict) -> dict:
    resp = session.get(PVGIS + "printhorizon", params={"lat": lat, "lon": lon, "outputformat": "json"}, timeout=60)
    resp.raise_for_status()
    horizon = [(float(p["A"]), float(p["H_hor"])) for p in resp.json()["outputs"]["horizon_profile"]]
    resp = session.get(PVGIS + "MRcalc", params={"lat": lat, "lon": lon, "mr_dni": 1, "outputformat": "json"},
                       timeout=90)
    resp.raise_for_status()
    data = resp.json()
    december = [m["Hb(n)_m"] for m in data["outputs"]["monthly"] if m["month"] == 12 and "Hb(n)_m" in m]
    years = [m["year"] for m in data["outputs"]["monthly"]]
    return winter_sun_found(winter_sun_hours(lat, lon, horizon), december, min(years), max(years))


def winter_sun_found(hours: dict, december: list[float], first: int, last: int) -> dict:
    """Adds "share": December's measured direct sun over a clear sky's (the clouds),
    and "hours": roughly the sun an average December day gets."""
    out = {**hours, "years": [first, last]}
    if december and hours["clear_dni"] > 0:
        share = min(1.0, sum(december) / len(december) / 31 / hours["clear_dni"])
        out["share"] = round(share, 2)
        out["hours"] = round(hours["sun"] * share, 1)
    elif december:
        out["share"], out["hours"] = 0.0, 0.0
    return out


def _winter_sun_note(w: dict | None) -> str | None:
    if not w or not w.get("day"):
        return None
    hidden = w["day"] - w["sun"]
    hills = f", the hills hide it {hidden:.1f} h" if hidden >= 0.5 else ""
    line = f"on 21 December the sun is up {w['day']:.1f} h{hills}: {w['sun']:.1f} h of direct sun on a clear day"
    if "share" in w and w["sun"] > 0:
        line += (f"; December's clouds let through {w['share'] * 100:.0f}% of the direct sun, so about "
                 f"{w['hours']:.1f} h on an average day")
    if w["sun"] < SUN_HOURS_MIN:
        line += f" — under {SUN_HOURS_MIN} h even on clear winter days"
    years = w.get("years") or []
    when = f", {years[0]}–{years[1]}" if len(years) == 2 else ""
    return line + f" (PVGIS, JRC: terrain horizon and SARAH-3 satellite{when}; buildings and trees not counted)"


def pfas(session, lat: float, lon: float, item: dict) -> dict:
    resp = session.get(PFAS, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": PFAS_KM * 1000, "units": "esriSRUnit_Meter",
        "outFields": "monitoringSiteIdentifier,lat,lon,parameterWaterBodyCategory,phenomenonTimeSamplingDate,Year,"
                     "observedPropertyDeterminandCode,"
                     "resultObservedValue,resultUom,resultQualityObservedValueBelowLOQ",
        "returnGeometry": "false", "f": "json"}, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return pfas_found([f.get("attributes") or {} for f in data.get("features") or []], lat, lon)


def pfas_found(rows: list[dict], lat: float, lon: float) -> dict:
    """{"sites": how many measured, "worst": {"sum" µg/l, "km", "water", "year"} — the highest
    sum of PFAS found above the lab's limit in one sample}."""
    samples: dict[tuple, dict] = {}
    seen: dict[tuple, float] = {}                 # (sample, compound): the value counted
    sites = set()
    for r in rows:
        site = r.get("monitoringSiteIdentifier")
        if not site or r.get("lat") is None:
            continue
        sites.add(site)
        key = (site, r.get("phenomenonTimeSamplingDate") or r.get("Year"))
        got = samples.setdefault(key, {"sum": 0.0, "lat": float(r["lat"]), "lon": float(r["lon"]),
                                       "water": "groundwater" if r.get("parameterWaterBodyCategory") == "GW"
                                       else "river or lake water", "year": str(r.get("Year") or "")})
        value = r.get("resultObservedValue")
        if value is None or r.get("resultQualityObservedValueBelowLOQ") not in (0, "0", False):
            continue
        unit = (r.get("resultUom") or "ug/L").lower()
        value = float(value) / (1000 if unit.startswith("ng") else 1)
        compound = (key, r.get("observedPropertyDeterminandCode"))
        if value > seen.get(compound, 0.0):        # a compound reported twice for one sample counts once
            got["sum"] += value - seen.get(compound, 0.0)
            seen[compound] = value
    worst = max(samples.values(), key=lambda x: x["sum"], default=None)
    if worst:
        worst = {"sum": round(worst["sum"], 3), "water": worst["water"], "year": worst["year"],
                 "km": round(_distance_m(lat, lon, worst["lat"], worst["lon"]) / 1000, 1)}
    return {"sites": len(sites), "worst": worst}


def _pfas_note(f: dict | None) -> str | None:
    if not f or not f.get("sites"):
        return None
    w = f.get("worst") or {}
    n = f["sites"]
    where = f"{n} water monitoring site{'s' if n > 1 else ''} within {PFAS_KM} km"
    if not w or w["sum"] < PFAS_LIMIT:
        return (f"PFAS measured at {where}, all under the EU drinking-water limit ({PFAS_LIMIT} µg/l for 20 "
                "PFAS) (EEA, WISE)")
    return (f"PFAS in {w['water']} {w['km']:.1f} km away: {w['sum']:.2f} µg/l (the PFAS measured, summed) in "
            f"{w['year']}, "
            f"{w['sum'] / PFAS_LIMIT:.0f}× the EU drinking-water limit ({PFAS_LIMIT} µg/l for 20 PFAS) — it matters "
            f"for a well, a borehole or a vegetable garden; tap water is treated and tested apart ({where}; EEA, "
            "WISE)")


def terrain(session, lat: float, lon: float, item: dict) -> dict:
    dlat = DEM_STEP_M / 111320
    dlon = DEM_STEP_M / (111320 * math.cos(math.radians(lat)))
    points = [(lat, lon), (lat + dlat, lon), (lat - dlat, lon), (lat, lon + dlon), (lat, lon - dlon)]
    resp = session.get(DEM, params={"locations": "|".join(f"{a:.6f},{b:.6f}" for a, b in points)}, timeout=60)
    resp.raise_for_status()
    return terrain_found([r.get("elevation") for r in resp.json().get("results") or []])


_FACING = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def terrain_found(heights: list) -> dict | None:
    """{"m": height, "slope": percent, "facing": the way the ground falls} from the point and
    the points DEM_STEP_M north, south, east and west of it."""
    if len(heights) != 5 or any(h is None for h in heights):
        return None
    here, north, south, east, west = (float(h) for h in heights)
    dz_east, dz_north = (east - west) / (2 * DEM_STEP_M), (north - south) / (2 * DEM_STEP_M)
    slope = math.hypot(dz_east, dz_north) * 100
    out = {"m": round(here), "slope": round(slope), "facing": None}
    if slope >= 3:
        downhill = math.degrees(math.atan2(-dz_east, -dz_north)) % 360     # from north, clockwise
        out["facing"] = _FACING[int((downhill + 22.5) // 45) % 8]
    return out


def _terrain_note(t: dict | None) -> str | None:
    if not t:
        return None
    s = t["slope"]
    if s < 5:
        ground = "flat ground"
    else:
        steep = ("gentle" if s < 15 else "steep: terraces, retaining walls and access cost more" if s < 30
                 else "very steep: building there is hard and costly")
        ground = f"slope {s}% facing {t['facing']} ({steep})"
    return f"altitude {t['m']:,} m, {ground} (EU-DEM 25 m, Copernicus)"


def _next_tuesday(today=None) -> str:
    import datetime as dt
    today = today or dt.date.today()
    return (today + dt.timedelta(days=(1 - today.weekday()) % 7 or 7)).isoformat()


def _journeys(session, lat: float, lon: float, taxi: bool) -> list[dict]:
    day = _next_tuesday()
    found = []
    for start in TRAVEL_DEPARTURES:
        params = {"fromPlace": f"{HOME[1]},{HOME[2]}", "toPlace": f"{lat},{lon}", "time": f"{day}T{start}:00Z",
                  "maxTravelTime": 2880}
        if taxi:
            params.update({"postTransitModes": "CAR", "maxPostTransitTime": TAXI_S})
        resp = session.get(TRANSITOUS, params=params, timeout=180)
        resp.raise_for_status()
        found += resp.json().get("itineraries") or []
    return found


def travel(session, lat: float, lon: float, item: dict) -> dict:
    found = _journeys(session, lat, lon, taxi=False)
    taxi = not found
    ground = travel_found(found or _journeys(session, lat, lon, taxi=True), taxi)
    flights = []
    for airport in served_airports(lat, lon)[:2]:
        on = _from_airport(session, airport, lat, lon)
        if on is not None:
            flights.append(flight_found(airport, on, lat, lon))
    return {**ground, "flight": min(flights, key=lambda f: f["hours"], default=None)}


@functools.lru_cache(maxsize=2)
def _airports(path: str = FLIGHTS_FILE) -> tuple[dict, ...]:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return tuple({**r, "lat": float(r["lat"]), "lon": float(r["lon"])} for r in csv.DictReader(f))
    except (OSError, KeyError, ValueError):
        return ()


def served_airports(lat: float, lon: float, path: str = FLIGHTS_FILE) -> list[dict]:
    """The airports Schiphol flies to directly within AIRPORT_KM, nearest first."""
    near = [(_distance_m(lat, lon, a["lat"], a["lon"]) / 1000, a) for a in _airports(path)]
    return [{**a, "km": round(km)} for km, a in sorted(near, key=lambda x: x[0]) if km <= AIRPORT_KM]


def _from_airport(session, airport: dict, lat: float, lon: float) -> dict | None:
    """{"hours", "taxi"}: the faster of public transport (taxi for the last 45 min) or a taxi all the way."""
    resp = session.get(TRANSITOUS, params={
        "fromPlace": f"{airport['lat']},{airport['lon']}", "toPlace": f"{lat},{lon}",
        "time": f"{_next_tuesday()}T12:00:00Z", "postTransitModes": "CAR", "maxPostTransitTime": TAXI_S,
        "directModes": "CAR", "maxDirectTime": 4 * 3600}, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    options = [(it["duration"], False) for it in data.get("itineraries") or []]
    options += [(it["duration"], True) for it in data.get("direct") or []]
    if not options:
        return None
    seconds, taxi = min(options)
    return {"hours": round(seconds / 3600, 1), "taxi": taxi}


def flight_found(airport: dict, on: dict, lat: float, lon: float) -> dict:
    km = _distance_m(52.3105, 4.7683, airport["lat"], airport["lon"]) / 1000      # from Schiphol
    flight = FLIGHT_EXTRA_H + km / FLIGHT_KMH
    return {"airport": airport["name"], "iata": airport.get("iata") or airport["icao"],
            "flight_h": round(flight, 1), "on_h": on["hours"], "on_taxi": on["taxi"],
            "hours": round(TO_GATE_H + flight + FROM_PLANE_H + on["hours"], 1)}


def travel_found(itineraries: list[dict], taxi: bool) -> dict:
    """{"hours", "changes", "by": [the main services], "taxi_min"} for the fastest journey, or {"hours": None}."""
    best = min(itineraries, key=lambda it: it.get("duration") or 1e9, default=None)
    if not best:
        return {"hours": None, "taxi": taxi}
    legs = best.get("legs") or []
    rides = sorted((leg for leg in legs if leg.get("mode") in TRAVEL_MODES), key=lambda leg: -(leg.get("duration") or 0))
    by = []
    for leg in rides:
        if TRAVEL_MODES[leg["mode"]] not in by:
            by.append(TRAVEL_MODES[leg["mode"]])
    car = sum(leg.get("duration") or 0 for leg in legs if leg.get("mode") == "CAR")
    return {"hours": round(best["duration"] / 3600, 1), "changes": best.get("transfers", 0), "by": by,
            "taxi_min": round(car / 60) if car else 0, "taxi": taxi}


def _flight_text(f: dict) -> str:
    on = "by taxi" if f["on_taxi"] else "by public transport"
    return (f"about {f['hours']:.1f} h by plane via {f['iata']} ({TO_GATE_H:.0f} h to the gate, "
            f"{f['flight_h']:.1f} h flight, then {f['on_h']:.1f} h {on})")


def _travel_note(t: dict | None) -> str | None:
    if not t:
        return None
    f = t.get("flight")
    best = min([h for h in (t.get("hours"), (f or {}).get("hours")) if h is not None], default=None)
    judged = f" — over {TRAVEL_MAX_H} h even at best, too far" if best is not None and best > TRAVEL_MAX_H else ""
    if t.get("hours") is None:
        ground = "no train, coach or ferry journey found within 48 h"
    else:
        ground = _ground_text(t)
    plane = f"{_flight_text(f)}; " if f else ""
    return (f"from {HOME[0]}: {plane}{ground}{judged} (Transitous, operators' timetables; flights Schiphol serves "
            "directly, Eurostat)")


def _ground_text(t: dict) -> str:
    n = t.get("changes") or 0
    by = f": {', '.join(t['by'][:3])}" if t.get("by") else ""
    taxi = f"; the last {t['taxi_min']} min by taxi" if t.get("taxi_min") else ""
    changes = "direct" if not n else f"{n} change{'s' if n > 1 else ''}"
    return f"{t['hours']:.1f} h by train or coach ({changes}{by}{taxi})"


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


def _parcel_ref(item: dict) -> str | None:
    """The French cadastral parcel the outline found at this pin (IGN idu, 14 characters)."""
    import outline
    found = outline.current(item) or {}
    ref = str(((found.get("plot") or {}).get("ref")) or "")
    return ref if len(ref) == 14 else None


def fr_parcel(session, lat: float, lon: float, item: dict) -> dict:
    """{"ref", "sales": [...]}: every sale of the parcel in DVF (2021 on)."""
    ref = _parcel_ref(item)
    insee = ref[:5]
    dep = insee[:3] if insee.startswith("97") else insee[:2]
    rows: list[dict] = []
    for year in range(2021, int(utcnow_iso()[:4]) + 1):
        resp = session.get(DVF.format(year=year, dep=dep, insee=insee), timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        rows.extend(r for r in csv.DictReader(io.StringIO(resp.content.decode("utf-8", "replace")))
                    if ref in (r.get("id_parcelle"), r.get("ancien_id_parcelle")))
    return {"ref": ref, "sales": parcel_sales(rows)}


def parcel_sales(rows: list[dict]) -> list[dict]:
    """One entry per sale, newest first: {"date", "price", "homes": [types], "built", "land",
    "eur_m2": for the sale of a single house or flat}."""
    by_sale: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("nature_mutation") in ("Vente", "Adjudication", "Vente en l'état futur d'achèvement"):
            by_sale.setdefault(r.get("id_mutation") or "", []).append(r)
    out = []
    for parts in by_sale.values():
        price = _float(parts[0].get("valeur_fonciere"))
        if not price:
            continue
        homes = [r for r in parts if r.get("type_local") in ("Maison", "Appartement")]
        built = sum(_float(r.get("surface_reelle_bati")) or 0 for r in homes)
        land = max((_float(r.get("surface_terrain")) or 0 for r in parts), default=0)
        single = dvf_sales(parts, homes[0]["type_local"]) if len(homes) == 1 else []
        out.append({"date": parts[0].get("date_mutation"), "price": round(price),
                    "auction": parts[0].get("nature_mutation") == "Adjudication",
                    "homes": [r["type_local"] for r in homes], "built": round(built), "land": round(land),
                    "eur_m2": round(single[0]["eur_m2"]) if single else None})
    return sorted(out, key=lambda x: x["date"] or "", reverse=True)


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
    "power": (power, None, BUILDING_PINS | {"street"}, {"home", "land"}),
    "fr_zoning": (fr_zoning, {"FR"}, BUILDING_PINS, {"home", "land"}),
    "pt_zoning": (pt_zoning, {"PT"}, BUILDING_PINS, {"home", "land"}),
    "pt_aru": (pt_aru, {"PT"}, BUILDING_PINS, {"home"}),
    "fr_risks": (fr_risks, {"FR"}, None, {"home", "land"}),
    "fr_sold": (fr_sold, {"FR"}, None, {"home"}),
    "fr_energy": (fr_energy, {"FR"}, BUILDING_PINS, {"home"}),
    "de_land": (de_land, {"DE"}, BUILDING_PINS | {"street"}, {"home", "land"}),
    "fr_heritage": (fr_heritage, {"FR"}, BUILDING_PINS, {"home", "land"}),
    "pt_heritage": (pt_heritage, {"PT"}, BUILDING_PINS, {"home", "land"}),
    "fr_parcel": (fr_parcel, {"FR"}, BUILDING_PINS, {"home", "land"}),
    "it_hazard": (it_hazard, {"IT"}, BUILDING_PINS | {"street"}, {"home", "land"}),
    "es_flood": (es_flood, {"ES"}, BUILDING_PINS | {"street"}, {"home", "land"}),
    "pt_flood": (pt_flood, {"PT"}, BUILDING_PINS | {"street"}, {"home", "land"}),
    "fr_soil": (fr_soil, {"FR"}, BUILDING_PINS | {"street"}, {"home", "land"}),
    "nature": (nature, None, BUILDING_PINS | {"street"}, {"home", "land"}),
    "pt_coast": (pt_coast, {"PT"}, BUILDING_PINS | {"street"}, {"home", "land"}),
    "land_use": (land_use, None, BUILDING_PINS | {"street"}, {"home", "land"}),
    "bathing": (bathing, None, NEAR_PINS, {"home"}),
    "winter_sun": (winter_sun, None, BUILDING_PINS | {"street"}, {"home", "land"}),
    "pfas": (pfas, None, BUILDING_PINS | {"street"}, {"home", "land"}),
    "terrain": (terrain, None, BUILDING_PINS, {"home", "land"}),
    "travel": (travel, None, NEAR_PINS, {"home", "land"}),
}
NEEDS_PARCEL = {"fr_parcel"}          # asked once the outline has found the parcel


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
        if name in NEEDS_PARCEL and not _parcel_ref(item):
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
    if isinstance(done.get("fr_heritage"), dict) and "ppr" not in done["fr_heritage"]:
        done = {k: v for k, v in done.items() if k != "fr_heritage"}  # asked before the plans were kept
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



def _it_hazard_note(h: dict | None) -> str | None:
    if not h:
        return None
    bits = []
    if h.get("landslide"):
        bits.append(f"landslide hazard {IT_LANDSLIDE[h['landslide']]}")
    if h.get("mapped"):
        bits.append("a mapped landslide at the point (IFFI)")
    if h.get("flood"):
        flood = {v[0]: v[1] for v in IT_FLOOD.values()}[h["flood"]]
        bits.append(f"flood hazard {flood}")
    if not bits:
        return "no landslide or flood hazard zone at the point (ISPRA IdroGEO, basin plans)"
    return "; ".join(bits) + " (ISPRA IdroGEO, basin plans)"


_HOMES = {"Maison": "house", "Appartement": "flat"}


def _parcel_note(p: dict | None, item: dict) -> str | None:
    """What the parcel sold for before. A flat's parcel is its whole building."""
    from outline import is_flat
    if not p:
        return None
    sales = p.get("sales") or []
    if not sales:
        return f"no sale of this parcel ({p['ref']}) recorded since 2021 (DVF)"
    if is_flat(item):
        rates = sorted(x["eur_m2"] for x in sales if x.get("eur_m2"))
        line = f"this building's parcel sold {len(sales)} time{'s' if len(sales) > 1 else ''} since 2021"
        if rates:
            line += f", flats at a median €{statistics.median(rates):,.0f}/m² ({len(rates)} single-flat sales)"
        return line + " (DVF)"
    shown = []
    for x in sales[:3]:
        what = [_HOMES.get(h, h) for h in x.get("homes") or []]
        bits = (", ".join(what) + (f" {x['built']} m²" if x.get("built") else "")) if what else "no building"
        if x.get("land"):
            bits += f", land {x['land']:,} m²"
        how = "at auction " if x.get("auction") else ""
        shown.append(f"{x['date']} {how}for €{x['price']:,} ({bits})")
    more = f"; {len(sales) - 3} earlier" if len(sales) > 3 else ""
    return f"this parcel ({p['ref']}) sold " + "; ".join(shown) + more + " (DVF)"


def _names(names: list[str], most: int = 2) -> str:
    shown = ", ".join(names[:most])
    return shown + (f" and {len(names) - most} more" if len(names) > most else "")


def _heritage_notes(found: dict) -> list[str]:
    out = []
    h = found.get("fr_heritage")
    if h:
        abf = "outside works need the Architecte des Bâtiments de France's agreement"
        if h.get("monument"):
            out.append(f"on a monument historique ({_names(h['monument'])}): every work needs the state's "
                       "permission (Géoportail de l'urbanisme, AC1)")
        if h.get("spr"):
            out.append(f"in a site patrimonial remarquable ({_names(h['spr'])}): {abf}, under its own rules "
                       "(Géoportail de l'urbanisme, AC4)")
        near = h.get("near") or []
        if near:
            what = "a monument historique" if len(near) == 1 else f"{len(near)} monuments historiques"
            out.append(f"within the protected surroundings of {what} ({_names(near)}): {abf} "
                       "(Géoportail de l'urbanisme, AC1)")
        if h.get("site"):
            out.append(f"in a listed site ({_names(h['site'])}): works must be declared, and demolition needs "
                       "the ABF's agreement (Géoportail de l'urbanisme, AC2)")
        if h.get("ppr"):
            out.append(f"inside the perimeter of a natural risk prevention plan ({_names(h['ppr'])}): "
                       "building rules or bans apply by zone — read the plan's map (Géoportail de l'urbanisme, PM1)")
        if h.get("pprt"):
            out.append(f"inside the perimeter of a technological risk prevention plan ({_names(h['pprt'])}), "
                       "around a dangerous plant: works may be required or banned (Géoportail de l'urbanisme, PM3)")
        if not any(h.get(k) for k in ("monument", "near", "site", "spr")):
            out.append("no heritage protection registered at the point (Géoportail de l'urbanisme)")
    h = found.get("pt_heritage")
    if h:
        for name, category, status in h.get("inside") or []:
            what = category.split(" - ", 1)[-1] if category else "classified heritage"
            pending = " (being classified)" if status and "vias" in status.lower() else ""
            out.append(f"inside {name}, {what}{pending}: works need the heritage office's approval (DGPC atlas)")
        zones = (h.get("zep") or []) + (h.get("zgp") or [])
        if zones:
            out.append(f"in the protection zone of {_names(zones)}: works need the heritage office's opinion "
                       "(DGPC atlas)")
        if not h.get("inside") and not zones:
            out.append("no classified heritage or protection zone at the point (DGPC atlas, mainland)")
    return out


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
    pw = found.get("power")
    if pw is not None:
        bits = []
        if pw.get("line"):
            kv = f" ({pw['line']['kv']} kV)" if pw["line"].get("kv") else ""
            bits.append(f"high-voltage line{kv} {_metres(pw['line']['m'])} away")
        if pw.get("turbines"):
            bits.append(f"{pw['turbines']} wind turbine{'s' if pw['turbines'] > 1 else ''} within 2 km, nearest "
                        f"{_metres(pw['turbine_m'])}")
        farms = [f for f in pw.get("farms") or [] if not (f["source"] == "wind" and pw.get("turbines"))]
        for f in farms[:1]:
            bits.append(f"{'solar' if f['source'] == 'solar' else 'wind'} farm {_metres(f['m'])} away")
        if bits:
            out.append(f"{'; '.join(bits)} — buyers pay less near pylons and turbines (OpenStreetMap)")
        else:
            out.append("no high-voltage line within 1 km, nor wind turbine or solar farm within 2 km (OpenStreetMap)")
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
    aru = found.get("pt_aru")
    if aru:
        if aru.get("name"):
            notice = f"; {aru['notice'].split(' - ')[0]}" if aru.get("notice") else ""
            out.append(f"in the {aru['name']}{notice}: renovating can qualify for 6% VAT on the works and IMT and "
                       "IMI exemptions — the câmara must approve the works first (DGT ARU register; EBF art. 45)")
        else:
            out.append("not in an urban regeneration area (ARU) on DGT's register — some towns are missing "
                       "there, so ask the câmara")
    out.extend(_heritage_notes(found))
    line = _it_hazard_note(found.get("it_hazard"))
    if line:
        out.append(line)
    for line in (_flood_note(found.get("es_flood"), "MITECO flood maps"),
                 _flood_note(found.get("pt_flood"), "APA flood maps")):
        if line:
            out.append(line)
    out.extend(_soil_note(found.get("fr_soil")))
    out.extend(_nature_note(found.get("nature")))
    for line in (_pt_coast_note(found.get("pt_coast")), _land_use_note(found.get("land_use")),
                 _bathing_note(found.get("bathing")), _winter_sun_note(found.get("winter_sun")),
                 _pfas_note(found.get("pfas")), _terrain_note(found.get("terrain")),
                 _travel_note(found.get("travel"))):
        if line:
            out.append(line)
    line = _parcel_note(found.get("fr_parcel"), item)
    if line:
        out.append(line)
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
    land = found.get("de_land")
    if land and land.get("zones"):
        line = _land_note(land, item)
        if line:
            out.append(line)
    e = found.get("fr_energy")
    if e is not None:
        line = _energy_note(e.get("certificates") or [], item)
        if line:
            out.append(line)
    return out


def _land_note(land: dict, item: dict) -> str | None:
    """The zone that fits the listing: building land for a home; for land, the
    building land when the plot is in one, else farmland or forest."""
    from common import has_term
    from scoring import property_kind
    zones = land["zones"]
    building = [z for z in zones if z["layer"] in ("20", "17", "5", "2", "14")]
    rural = [z for z in zones if z["layer"] in ("8", "11")]
    kind = _kind(item)
    if property_kind(item) not in ("home", "urban_plot") and rural:
        # Farm and forest zones lie under the building ones: pick what the plot is
        text = f"{item.get('title') or ''} {item.get('description') or ''}"
        want = ("F" if has_term(text, ["wald", "forst", "waldfläche", "waldgrundstück"], negations=False)
                else "GR" if has_term(text, ["grünland", "wiese", "weide", "dauergrünland"], negations=False)
                else "A")
        z = next((r for r in rural if r["use"] == want), rural[0])
    else:
        z = (building or rural)[0] if building or kind == "home" else rural[0] if rural else None
    if not z:
        return None
    what = DE_BORIS_LAYERS[z["layer"]]
    use = f", {DE_LAND_USE[z['use']]}" if z["use"] in DE_LAND_USE and z["use"] != "F" else ""
    money = f"€{z['eur_m2']:,.0f}" if z["eur_m2"] >= 10 else f"€{z['eur_m2']:,.2f}"
    place = f", {z['place']}" if z["place"] else ""
    line = f"official land value {money}/m² ({what}{use}{place}, {z['date']})"
    try:
        area = float(item.get("area_m2") or 0)
    except (TypeError, ValueError):
        area = 0
    if kind == "land" and area:
        line += f" — this {area:,.0f} m² about €{area * z['eur_m2']:,.0f}"
    return line + " — land only, without buildings (Bodenrichtwert, BORIS-NRW)"


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
