"""cadastre.py — the exact position of a plot, from the country's land cadastre.

Most sales place a plot only at its village or town, which is too vague to say
whether a river runs along it. The public cadastres know where it is:

- Spain (Catastro): a sale that gives the "referencia catastral" (BOE auctions
  often do) → the parcel's centre, from the Catastro's coordinate service.
- Portugal (DGT, INSPIRE cadastral parcels): the sales give the old tax article,
  not the new parcel number, so the parcel is found by its area. Near the
  plot's town, the parcels whose area matches the one written in the sale
  (to within AREA_TOLERANCE, 0.05%); only a single match is kept. Round areas
  ("83.000 m2") match too many parcels and are not tried. The cadastre covers
  most of the Alentejo, the Algarve and Beira Baixa; elsewhere nothing is found.

A position found this way is stored as raw["geo"] with precision "cadastre",
which counts as exact (geo.EXACT_ENOUGH): the map then checks the water next to
it. Every listing is asked once (raw["cadastre_checked"]).
"""
from __future__ import annotations

import json
import re
import time

from common import LOG

CATASTRO = ("https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/COVCCoordenadas.svc/json/"
            "Consulta_CPMRC")
DGT_WFS = "https://snicws.dgterritorio.gov.pt/geoserver/inspire/ows"
AREA_TOLERANCE = 0.0005         # 0.05%: close enough for the same parcel, tight enough to be the only one
TOWN_BOX_DEG = 0.2              # how far around the town the parcel is looked for (~20 km)
PER_SCAN = 30
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) auction-scanner"}

# 14 characters identify the parcel: 7 digits + 7 (urban, "3589701UK6938N") or
# 5 digits + a letter + 8 digits (rural: province, municipality, polígono, parcela).
_RC = re.compile(r"(?i)referencia\s+catastral\s*(?:n[ºo.]*)?\s*[:\-]?\s*"
                 r"(\d{7}[A-Z]{2}\d{4}[A-Z]|\d{5}[A-Z]\d{8})(?:[A-Z0-9]{4,6})?\b")


def referencia_catastral(text: str) -> str | None:
    """The 14-character parcel reference written in a Spanish sale, if any."""
    m = _RC.search(text or "")
    return m.group(1).upper() if m else None


_RC_ALONE = re.compile(r"(?i)^\s*(\d{7}[A-Z]{2}\d{4}[A-Z]|\d{5}[A-Z]\d{8})[A-Z0-9]{0,6}\s*$")


def reference_given(text: str) -> str | None:
    """A Spanish cadastral reference pasted on its own, or written in a text."""
    m = _RC_ALONE.match(text or "")
    return m.group(1).upper() if m else referencia_catastral(text)


def catastro_position(session, rc: str) -> dict | None:
    resp = session.get(CATASTRO, params={"RefCat": rc, "SRS": "EPSG:4326"}, headers=BROWSER, timeout=30)
    resp.raise_for_status()
    coords = ((resp.json().get("Consulta_CPMRCResult") or {}).get("coordenadas") or {}).get("coord") or []
    if not coords:
        return None
    geo = coords[0].get("geo") or {}
    try:
        lat, lon = float(geo["ycen"]), float(geo["xcen"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"lat": lat, "lon": lon, "precision": "cadastre", "query": f"Catastro {rc}",
            "label": coords[0].get("ldt")}


def is_measured(area: float | None) -> bool:
    """An area written to the square metre (46 905, 82 750), not rounded (83 000)."""
    return bool(area) and area >= 5000 and area % 100 != 0


def dgt_candidates(session, lat: float, lon: float, area: float) -> list[dict]:
    lo, hi = area * (1 - AREA_TOLERANCE), area * (1 + AREA_TOLERANCE)
    box = (lon - TOWN_BOX_DEG, lat - TOWN_BOX_DEG, lon + TOWN_BOX_DEG, lat + TOWN_BOX_DEG)
    resp = session.get(DGT_WFS, params={
        "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "inspire:cadastralparcel",
        "maxFeatures": 5, "outputFormat": "application/json", "srsName": "EPSG:4326",
        "cql_filter": f"areavalue BETWEEN {lo:.0f} AND {hi:.0f} AND "
                      f"BBOX(geometry,{box[0]:.4f},{box[1]:.4f},{box[2]:.4f},{box[3]:.4f},'EPSG:4326')",
    }, headers=BROWSER, timeout=90)
    resp.raise_for_status()
    out = []
    for f in resp.json().get("features") or []:
        p = f.get("properties") or {}
        point = (p.get("referencepoint") or {}).get("coordinates") or []
        if len(point) == 2:
            out.append({"lat": point[1], "lon": point[0], "area": p.get("areavalue"), "label": p.get("label"),
                        "unit": p.get("administrativeunit")})
    return out


def dgt_municipality_code(session, lat: float, lon: float) -> str | None:
    """The municipality code (DICO, 4 digits) of the parcels around a town's centre."""
    resp = session.get(DGT_WFS, params={
        "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "inspire:cadastralparcel",
        "maxFeatures": 1, "outputFormat": "application/json", "propertyName": "administrativeunit",
        "cql_filter": f"BBOX(geometry,{lon - 0.02:.4f},{lat - 0.02:.4f},{lon + 0.02:.4f},{lat + 0.02:.4f},'EPSG:4326')",
    }, headers=BROWSER, timeout=60)
    resp.raise_for_status()
    feats = resp.json().get("features") or []
    unit = ((feats[0].get("properties") or {}).get("administrativeunit") or "") if feats else ""
    return unit[:4] or None


def dgt_position(session, town: dict, area: float) -> dict | None:
    found = dgt_candidates(session, town["lat"], town["lon"], area)
    if len(found) > 1:                            # keep the ones in the town's own municipality
        dico = dgt_municipality_code(session, town["lat"], town["lon"])
        if dico:
            found = [f for f in found if str(f.get("unit") or "").startswith(dico)]
    if len(found) != 1:
        return None                               # none, or several: not sure which one
    hit = found[0]
    return {"lat": hit["lat"], "lon": hit["lon"], "precision": "cadastre",
            "query": f"DGT cadastre {hit['label']} ({hit['area']:,.0f} m²)".replace(",", " ")}


def locate_pending(db, session, items: list[dict], towns: dict, limit: int = PER_SCAN) -> int:
    """Find the exact position of the best plots (and Spanish sales with a
    cadastral reference) that have none, once each."""
    import geo
    from scoring import property_kind
    done = found = 0
    for item in items:
        if done >= limit:
            break
        raw = geo._raw(item)
        if raw.get("cadastre_checked") or (geo.position(item) or {}).get("precision") in geo.EXACT_ENOUGH:
            continue
        country = (item.get("country") or "PT").upper()
        pos = None
        try:
            if country == "ES":
                rc = referencia_catastral(f"{item.get('title') or ''} {item.get('description') or ''}")
                if not rc:
                    continue
                pos = catastro_position(session, rc)
            elif country == "PT" and property_kind(item) == "rural_plot" and is_measured(item.get("area_m2")):
                name = geo.municipality(item)
                town = towns.get(geo.town_key("PT", name)) if name else None
                if not town:
                    continue                      # its town is not located yet: next scan
                pos = dgt_position(session, town, item["area_m2"])
            else:
                continue
        except Exception as e:  # noqa: BLE001 — a cadastre down must not stop the scan
            LOG.info(f"Cadastre lookup for {item['id']} failed ({type(e).__name__}); next scan")
            break
        done += 1
        raw["cadastre_checked"] = 1
        if pos:
            found += 1
            raw["geo"], raw["geo_checked"] = pos, True
            raw.pop("water_check", None)          # ask the map again, now at the plot itself
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        time.sleep(1)
    if done:
        LOG.info(f"Cadastre: {found} of {done} plots placed exactly")
    return found
