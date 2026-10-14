"""cadastre.py — the exact position of a plot, from the country's land cadastre.

Most sales place a plot only at its village or town, which is too vague to say
whether a river runs along it. The public cadastres know where it is:

- Spain (Catastro): the "referencia catastral", from the detail page's own
  field or from the text → the parcel's centre, from the Catastro's coordinate
  service.
- France (IGN cadastre): a notice that names one parcel — "section AB n° 123",
  or the 14-character id — in a commune that can be named for certain. The
  parcel's centre and its official plot area are stored. Several parcels, or
  two communes, are not given one pin. A listing at 85 or more in any goal is
  also asked of the national building database (BDNB); the year and the use
  are kept only when that parcel has a single building, and they are not a score.
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

from common import LOG, normalize

CATASTRO = ("https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/COVCCoordenadas.svc/json/"
            "Consulta_CPMRC")
DGT_WFS = "https://snicws.dgterritorio.gov.pt/geoserver/inspire/ows"
IGN_PARCEL = "https://apicarto.ign.fr/api/cadastre/parcelle"
FR_COMMUNES = "https://geo.api.gouv.fr/communes"
BDNB = "https://api.bdnb.io/v1/bdnb/donnees"
FR_PARCELS_MAX = 4          # more than this is a list, not one lot's parcels
AREA_TOLERANCE = 0.0005         # 0.05%: close enough for the same parcel, tight enough to be the only one
TOWN_BOX_DEG = 0.2              # how far around the town the parcel is looked for (~20 km)
PER_SCAN = 60                   # Spanish sales with a stored reference come first
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


def parcel_reference(item: dict) -> str | None:
    """The 14-character parcel id: the detail page's own field, else the text.

    The goods tab stores "referencia catastral" on its own. The description
    often never repeats it, and a lookup that only reads the prose misses it."""
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    given = reference_given(str(raw.get("referencia_catastral") or ""))
    if given:
        return given
    return referencia_catastral(f"{item.get('title') or ''} {item.get('description') or ''}")


# "section AB n° 123", "section A numéro 654", "section ZH nos 12, 13 et 14".
# A one-letter section is what the notice writes; the cadastre stores it as 0A.
_FR_SECTION = re.compile(
    r"section\s+([0-9]?[A-Z][A-Z0-9]?)\s+"
    r"(?:num[eé]ros?\s+|nos\s+|n[°ºo.]{0,3}\s*)"
    r"(\d{1,4}(?!\d)(?:\s*(?:,|et)\s*\d{1,4}(?!\d))*)",
    re.I)
# 14 characters: commune, prefix, section, number. The section contains a letter,
# so a phone number or a price does not match.
_FR_IDU = re.compile(r"\b(\d{5})(\d{3})((?:[0-9][A-Z]|[A-Z][0-9A-Z]))(\d{4})\b", re.I)
_FR_CUE = re.compile(r"cadastr|parcelle|feuille", re.I)
_FR_COMMUNE = re.compile(
    r"commune\s+(?:de\s+|d['’])\s*([^\n,;(]{2,40}?)(?=\s*(?:,|\(|section|cadastr|parcelle|$))",
    re.I)
_FR_DEPT = re.compile(r"\bd[eé]partement\s+(?:n[°o]\s*)?(\d{2,3}|2[ab])\b", re.I)


def _fr_numbers(text: str) -> list[str]:
    return [n.zfill(4) for n in re.findall(r"\d{1,4}", text or "")]


def french_parcels(text: str) -> list[dict]:
    """Parcels named in a French notice: [{insee, section, numero}].

    insee is set when the 14-character id is written, and None when the notice
    only gives the section and the number. More than one number is more than
    one parcel. The same parcel named twice is kept once."""
    text = text or ""
    found: list[dict] = []
    seen = set()
    idu_pairs = set()

    def add(insee, section, numero, from_idu=False):
        section = section.upper().zfill(2)
        numero = numero.zfill(4)
        if not re.fullmatch(r"[0-9A-Z]{2}", section):
            return
        if not from_idu and (section, numero) in idu_pairs:
            return
        key = (insee, section, numero)
        if key in seen:
            return
        seen.add(key)
        if from_idu:
            idu_pairs.add((section, numero))
        found.append({"insee": insee, "section": section, "numero": numero})

    for m in _FR_IDU.finditer(text):
        before = text[max(0, m.start() - 80):m.start()]
        if _FR_CUE.search(before):
            add(m.group(1), m.group(3), m.group(4), from_idu=True)
    for m in _FR_SECTION.finditer(text):
        for numero in _fr_numbers(m.group(2)):
            add(None, m.group(1), numero)
    return found


def _french_communes(text: str) -> list[str]:
    found = []
    for m in _FR_COMMUNE.finditer(text or ""):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .")
        if name and normalize(name) not in {normalize(n) for n in found}:
            found.append(name)
    return found


def french_commune_code(session, name: str, department: str | None) -> str | None:
    """The INSEE code when exactly one commune has this name (in the department,
    when the notice gives one). None when several do, or none does."""
    params = {"nom": name, "fields": "nom,code", "limit": 10, "boost": "population"}
    if department:
        params["codeDepartement"] = department
    resp = session.get(FR_COMMUNES, params=params, headers=BROWSER, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return None
    exact = [r for r in rows if normalize(r.get("nom") or "") == normalize(name) and r.get("code")]
    if len(exact) != 1:
        return None
    return str(exact[0]["code"])


def _ring_centroid(ring: list) -> tuple[float, float, float] | None:
    """(lat, lon, absolute area weight) of one outer ring. None when it is not a ring."""
    if not ring or len(ring) < 4:
        return None
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        return None
    cross_sum = lon = lat = 0.0
    pts = list(ring) + [ring[0]]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        cross = x1 * y2 - x2 * y1
        cross_sum += cross
        lon += (x1 + x2) * cross
        lat += (y1 + y2) * cross
    if abs(cross_sum) < 1e-14:
        return None
    return lat / (3 * cross_sum), lon / (3 * cross_sum), abs(cross_sum)


def _polygon_centroid(geometry: dict) -> tuple[float, float] | None:
    """The centre of the largest ring. A point outside France is refused."""
    gtype = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        return None
    best = None
    weight = 0.0
    for poly in polys:
        if not poly:
            continue
        got = _ring_centroid(poly[0])
        if got and got[2] > weight:
            best, weight = got, got[2]
    if not best:
        return None
    lat, lon = best[0], best[1]
    if not (-50 <= lat <= 52 and -65 <= lon <= 60):
        return None
    return lat, lon


def ign_parcel(session, insee: str, section: str, numero: str) -> dict | None:
    """One French parcel, or None when the cadastre has none or more than one."""
    resp = session.get(IGN_PARCEL, params={
        "code_insee": insee, "section": section.upper().zfill(2), "numero": numero.zfill(4),
    }, headers=BROWSER, timeout=30)
    resp.raise_for_status()
    feats = (resp.json() or {}).get("features") or []
    if len(feats) != 1:
        return None
    prop = feats[0].get("properties") or {}
    centre = _polygon_centroid(feats[0].get("geometry") or {})
    if not centre:
        return None
    try:
        area = float(prop.get("contenance"))
    except (TypeError, ValueError):
        area = 0.0
    return {
        "insee": str(prop.get("code_insee") or insee),
        "section": str(prop.get("section") or section).upper(),
        "numero": str(prop.get("numero") or numero).zfill(4),
        "idu": prop.get("idu") or None,
        "commune": prop.get("nom_com") or None,
        "plot_m2": area or None,
        "lat": centre[0], "lon": centre[1],
    }


def bdnb_building(session, idu: str) -> dict:
    """What the national building database says about the one building on a parcel.

    Several buildings, or none, are reported as a count. A year taken from one
    of several buildings would be the wrong building."""
    rel = session.get(f"{BDNB}/rel_batiment_groupe_parcelle", params={
        "parcelle_id": f"eq.{idu}", "select": "batiment_groupe_id",
    }, headers=BROWSER, timeout=30)
    rel.raise_for_status()
    body = rel.json()
    rows = body if isinstance(body, list) else []
    ids = sorted({r.get("batiment_groupe_id") for r in rows if r.get("batiment_groupe_id")})
    if len(ids) != 1:
        return {"buildings": len(ids)}
    bat = session.get(f"{BDNB}/batiment_groupe_ffo_bat", params={
        "batiment_groupe_id": f"eq.{ids[0]}",
        "select": "annee_construction,usage_niveau_1_txt",
    }, headers=BROWSER, timeout=30)
    bat.raise_for_status()
    body = bat.json()
    got = body if isinstance(body, list) else []
    if len(got) != 1:
        return {"buildings": len(got)}
    out = {"buildings": 1}
    year = got[0].get("annee_construction")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None
    if year and 1000 <= year <= 2100:
        out["year"] = year
    use = str(got[0].get("usage_niveau_1_txt") or "").strip()
    if use:
        out["use"] = use
    return out


def _high_score(item: dict) -> bool:
    from photos import KEEP_SCORE
    rank = item.get("best_rank")
    if rank is None:
        rank = item.get("rank") or 0
    try:
        return float(rank) >= KEEP_SCORE
    except (TypeError, ValueError):
        return False


def _french_department(item: dict, text: str) -> str | None:
    import geo
    raw = geo._raw(item)
    dept = str(raw.get("department") or "").strip()
    if re.fullmatch(r"\d{2,3}|2[ab]", dept, re.I):
        return dept.upper()
    m = _FR_DEPT.search(text or "")
    return m.group(1).upper() if m else None


def france_locate(session, item: dict, raw: dict) -> str | None:
    """Place one French parcel, and read its building when the score is already high.

    "placed" when the pin moved to the parcel, "checked" when the notice was
    read and no single pin was justified, None when there is nothing to do yet
    (no parcel written, or the commune is not known for certain)."""
    record = raw.get("fr_cadastre") if isinstance(raw.get("fr_cadastre"), dict) else {}
    if raw.get("cadastre_checked"):
        if not (record.get("idu") and "bdnb" not in record and _high_score(item) and not record.get("parcels")):
            return None
        record = dict(record)
        record["bdnb"] = bdnb_building(session, record["idu"])
        raw["fr_cadastre"] = record
        return "checked"

    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    refs = french_parcels(text)
    if not refs:
        return None
    if len(refs) > FR_PARCELS_MAX:
        raw["cadastre_checked"] = 1
        raw["fr_cadastre"] = {"note": "several parcels — not placed as one pin"}
        return "checked"

    communes = _french_communes(text)
    if len(communes) > 1:
        raw["cadastre_checked"] = 1
        raw["fr_cadastre"] = {"note": "several communes — not placed as one pin"}
        return "checked"
    name = communes[0] if communes else (item.get("concelho") or "").strip()
    if any(not ref.get("insee") for ref in refs):
        if not name:
            return None
        department = _french_department(item, text)
        if not department and raw.get("fr_commune_miss") == name:
            return None
        code = french_commune_code(session, name, department)
        if not code:
            if department:
                raw["cadastre_checked"] = 1
                raw["fr_cadastre"] = {"note": "the commune could not be named for certain"}
                return "checked"
            # Asked once. A department stored later is a new fact, so it is asked again.
            if raw.get("fr_commune_miss") == name:
                return None
            raw["fr_commune_miss"] = name
            return "checked"
        for ref in refs:
            ref["insee"] = ref.get("insee") or code
    if len({ref["insee"] for ref in refs}) != 1:
        raw["cadastre_checked"] = 1
        raw["fr_cadastre"] = {"note": "several communes — not placed as one pin"}
        return "checked"

    parcels = []
    for ref in refs:
        hit = ign_parcel(session, ref["insee"], ref["section"], ref["numero"])
        if not hit:
            raw["cadastre_checked"] = 1
            raw["fr_cadastre"] = {"note": "the cadastre did not return that one parcel"}
            return "checked"
        parcels.append(hit)
    raw["cadastre_checked"] = 1
    if len(parcels) != 1:
        raw["fr_cadastre"] = {"parcels": [
            {"section": p["section"], "numero": p["numero"], "plot_m2": p["plot_m2"], "commune": p["commune"]}
            for p in parcels
        ], "note": "several parcels — not placed as one pin"}
        return "checked"

    hit = parcels[0]
    stored = {k: hit[k] for k in ("insee", "section", "numero", "idu", "commune", "plot_m2", "lat", "lon")}
    stored = {k: v for k, v in stored.items() if v not in (None, "")}
    if stored.get("idu") and _high_score(item):
        stored["bdnb"] = bdnb_building(session, stored["idu"])
    raw["fr_cadastre"] = stored
    precision = (raw.get("geo") or {}).get("precision") if isinstance(raw.get("geo"), dict) else None
    if precision in ("sale", "verified"):
        return "checked"
    area = stored.get("plot_m2")
    label = f"{stored.get('section', '')} {str(int(stored['numero']))}" if str(stored.get("numero") or "").isdigit() \
        else f"{stored.get('section', '')} {stored.get('numero', '')}"
    where = stored.get("commune") or name
    bits = [b for b in (where, label.strip()) if b]
    if area:
        bits.append(f"{area:,.0f} m²".replace(",", " "))
    raw["geo"] = {"lat": stored["lat"], "lon": stored["lon"], "precision": "cadastre",
                  "query": "Cadastre " + ", ".join(bits)}
    raw["climate"] = None
    raw["water_check"] = None
    return "placed"


def _unplaced_spanish(db, limit: int) -> list[dict]:
    """Spanish sales whose cadastral reference is stored and not yet looked up."""
    rows = db.execute(
        """SELECT * FROM listings WHERE country = 'ES'
             AND raw_json LIKE '%"referencia_catastral"%'
             AND raw_json NOT LIKE '%"cadastre_checked"%'
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def locate_pending(db, session, items: list[dict], towns: dict, limit: int = PER_SCAN) -> int:
    """Find the exact position of the best plots, and of Spanish sales whose
    cadastral reference is known, once each.

    The reference stored on the detail page is used even when the description
    does not repeat it. Those sales are asked first: without this pin the beach
    and the climate are never this building's. A French notice that names a
    parcel is placed the same way, ahead of a vaguer pin."""
    import geo
    from scoring import property_kind
    queued, seen = [], set()
    for item in _unplaced_spanish(db, limit) + list(items):
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        queued.append(item)
    done = found = 0
    for item in queued:
        if done >= limit:
            break
        raw = geo._raw(item)
        country = (item.get("country") or "PT").upper()
        if country == "FR":
            try:
                outcome = france_locate(session, item, raw)
            except Exception as e:  # noqa: BLE001 — a cadastre down must not stop the scan
                LOG.info(f"Cadastre lookup for {item['id']} failed ({type(e).__name__}); next scan")
                break
            if outcome is None:
                continue
            done += 1
            if outcome == "placed":
                found += 1
            db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
                       (json.dumps(raw, ensure_ascii=False), item["id"]))
            db.commit()
            time.sleep(1)
            continue
        if raw.get("cadastre_checked") or (geo.position(item) or {}).get("precision") in geo.EXACT_ENOUGH:
            continue
        pos = None
        try:
            if country == "ES":
                rc = parcel_reference(item)
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
            # The old reading, if any, was not this parcel.
            raw["climate"] = None
            raw["water_check"] = None
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        time.sleep(1)
    if done:
        LOG.info(f"Cadastre: {found} of {done} plots placed exactly")
    return found
