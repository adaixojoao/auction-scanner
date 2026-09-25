"""listing_info.py — everything the scanner knows about one listing, for the
detail panel on Listings (and the Telegram alert).

- facts():       labelled details from the listing and its source's raw data
                 (case, court, agente, deposit, occupancy, visits, bank, rooms…);
- related():     the same sale on other sites (a Citius case that is also an
                 e-leilões auction), the Spanish land registry, a map;
- how_to_find(): steps to reach the exact sale when its link cannot, as on
                 Citius, whose results only exist behind a search form.

Scraped values are untrusted: links go through common.safe_url, and the page
escapes every value (AS.esc).
"""
from __future__ import annotations

import json
import re
import urllib.parse

from common import safe_url

CITIUS_SEARCH = "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx"


def raw_of(item: dict) -> dict:
    try:
        raw = json.loads(item.get("raw_json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


def _money(v) -> str | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return f"€{v:,.0f}" if v else None


def _first(raw: dict, *keys):
    for k in keys:
        v = raw.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _energy(value) -> str | None:
    """"UNPARSED- isento" → "Isento"; "detail-item" and blanks → nothing."""
    text = re.sub(r"^\s*unparsed-?\s*", "", str(value or ""), flags=re.I).strip()
    if not text or text.lower().startswith("detail"):
        return None
    return text[:1].upper() + text[1:]


def case_number(item: dict) -> str | None:
    """The court case ("3841/03.3TBBRR"), without the court that Citius appends."""
    raw = raw_of(item)
    proc = _first(raw, "processo", "expediente", "caseNumber")
    return str(proc).split(",")[0].strip() if proc else None


# (label, how to get it from (item, raw)). Only the ones with a value are shown.
_FACTS = [
    ("Case", lambda it, r: case_number(it)),
    ("Court", lambda it, r: _first(r, "tribunal", "autoridad", "court")),
    ("Sale type", lambda it, r: _first(r, "modalidade", "tipo_subasta", "veilingwijze")),
    ("Status", lambda it, r: _first(r, "estado")),
    ("Base value", lambda it, r: _money(it.get("price"))),
    ("Minimum accepted", lambda it, r: _money(it.get("min_price")) if it.get("min_price") != it.get("price") else None),
    ("Opening value", lambda it, r: _money(r.get("valor_abertura"))),
    ("Current bid", lambda it, r: _money(it.get("current_bid"))),
    ("Deposit", lambda it, r: _money(r.get("deposito"))),
    ("Mise à prix", lambda it, r: _money(r.get("mise_a_prix"))),
    ("Ends", lambda it, r: (it.get("date_end") or "").replace("T", " ")[:16] or None),
    ("Hearing", lambda it, r: (r.get("audience") or "").replace("T", " ")[:16] or None),
    ("Area", lambda it, r: f"{it['area_m2']:,.0f} m²".replace(",", " ") if it.get("area_m2") else None),
    ("Bedrooms", lambda it, r: _first(r, "prop_bedrooms", "field_nr_quartos")),
    ("Bathrooms", lambda it, r: _first(r, "prop_wc", "field_nr_wcs")),
    ("Energy rating", lambda it, r: _energy(_first(r, "prop_energy_rating", "field_cls"))),
    ("Address", lambda it, r: _first(r, "morada", "field_morada_completa")),
    ("Distance to town", lambda it, r: (it.get("town_distance") or {}).get("text")),
    ("Place", lambda it, r: ", ".join(p for p in (it.get("freguesia"), it.get("concelho"), it.get("district")) if p) or None),
    ("Occupancy", lambda it, r: _first(r, "situacion_posesoria", "occupation")),
    ("Visits", lambda it, r: _first(r, "visitable", "visite")),
    ("Charges", lambda it, r: _first(r, "cargas")),
    ("Main home of the debtor", lambda it, r: _first(r, "vivienda_habitual")),
    ("Land registry ref.", lambda it, r: _first(r, "referencia_catastral")),
    ("Seller", lambda it, r: _first(r, "site_name", "organisatie")),
    ("Their reference", lambda it, r: _first(r, "prop_reference", "field_ul_referencia", "referencia")),
    ("Agente de execução", lambda it, r: _first(r, "agente_nome")),
    ("Agente e-mail", lambda it, r: _first(r, "agente_email")),
    ("Agente phone", lambda it, r: _first(r, "agente_contacto")),
    ("Court e-mail", lambda it, r: _first(r, "autoridad_email")),
    ("Court phone", lambda it, r: _first(r, "autoridad_telefono")),
    ("Court address", lambda it, r: _first(r, "autoridad_direccion")),
    ("Lawyer", lambda it, r: _first(r, "avocat_nom")),
    ("Lawyer e-mail", lambda it, r: _first(r, "avocat_email")),
    ("Lawyer phone", lambda it, r: _first(r, "avocat_tel")),
    ("Lots in this sale", lambda it, r: r.get("lots") if (r.get("lots") or 0) > 1 else None),
]


def facts(item: dict) -> list[dict]:
    raw = raw_of(item)
    out = []
    for label, get in _FACTS:
        try:
            value = get(item, raw)
        except (TypeError, ValueError, KeyError):
            value = None
        if value not in (None, "", [], {}):
            out.append({"label": label, "value": str(value)[:500]})
    return out


def _coords(item: dict, raw: dict):
    lat = _first(raw, "lat", "prop_latitude", "coordenadasLAT")
    lon = _first(raw, "lon", "lng", "prop_longitude", "coordenadasLON")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180 else None


def street_view(item: dict, google_key: str = "") -> dict | None:
    """Street View and the satellite view where the listing is (geo.py)."""
    import geo
    pos = geo.position(item)
    if not pos:
        return None
    note = {"sale": "position from the sale", "street": "the street, from OpenStreetMap",
            "village": "approximate: the village (Street View shows a nearby street)",
            "parish": "approximate: the parish centre", "municipality": "approximate: the town centre"}
    return {"url": geo.street_view_url(pos), "satellite": geo.satellite_url(pos),
            "embed": geo.street_view_embed_url(pos, google_key) if pos.get("precision") in ("sale", "street") else None,
            "precision": note.get(pos.get("precision"), pos.get("precision")),
            "lat": pos["lat"], "lon": pos["lon"]}


def map_url(item: dict) -> str | None:
    raw = raw_of(item)
    where = _coords(item, raw)
    if not where:
        import geo
        pos = geo.position(item)
        where = (pos["lat"], pos["lon"]) if pos else None
    if where:
        query = f"{where[0]:.6f},{where[1]:.6f}"
    else:
        parts = [_first(raw, "morada", "field_morada_completa"), item.get("freguesia"),
                 item.get("concelho"), item.get("district")]
        parts = [str(p) for p in parts if p]
        if not parts:
            return None
        query = ", ".join(parts)
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)


def catastro_url(item: dict) -> str | None:
    """Spain's land registry page for the property (its referencia catastral)."""
    ref = re.sub(r"[^0-9A-Z]", "", str(raw_of(item).get("referencia_catastral") or "").upper())
    if len(ref) not in (14, 20):
        return None
    return ("https://www1.sedecatastro.gob.es/CYCBienInmueble/OVCListaBienes.aspx?"
            + urllib.parse.urlencode({"rc1": ref[:7], "rc2": ref[7:14]}))


def related(db, item: dict) -> list[dict]:
    """Links to the same sale elsewhere, the land registry and a map."""
    out = []
    proc = case_number(item)
    if proc and item.get("source") in ("citius", "eleiloes"):
        other = "eleiloes" if item["source"] == "citius" else "citius"
        like = f'%"processo": "{proc}%'
        for row in db.execute("SELECT id, url, title FROM listings WHERE source = ? AND raw_json LIKE ?",
                              (other, like)).fetchall()[:3]:
            url = safe_url(row["url"])
            if url and url != CITIUS_SEARCH:
                out.append({"label": f"Same case on {'e-leilões' if other == 'eleiloes' else 'Citius'}: "
                                     f"{(row['title'] or '')[:60]}", "url": url, "id": row["id"]})
    er = item.get("earlier_round")               # set by db.load_listings (rounds.py)
    if er:
        row = db.execute("SELECT url FROM listings WHERE id = ?", (er["id"],)).fetchone()
        was = f" at €{er['price']:,.0f}" if er.get("price") else ""
        out.append({"label": f"Earlier round, ended {er['ended']}{was} without a sale",
                    "url": safe_url(row["url"]) if row else None, "id": er["id"]})
    cat = catastro_url(item)
    if cat:
        out.append({"label": "Land registry (Catastro)", "url": cat})
    raw = raw_of(item)
    original = safe_url(raw.get("original_url"))
    if original and original != item.get("url"):
        out.append({"label": f"Seller's page ({raw.get('site_name') or 'original'})", "url": original})
    public = safe_url(raw.get("publicUrl"))
    if public and public != item.get("url"):
        out.append({"label": "Court notice", "url": public})
    m = map_url(item)
    if m:
        out.append({"label": "Map", "url": m})
    return out


def same_case_lots(db, item: dict) -> list[dict]:
    """Other lots sold in the same court case (a house and the plot next to it,
    several plots of one owner): worth buying together."""
    proc = case_number(item)
    if not proc or not item.get("id"):
        return []
    like = f'%"processo": "{proc}%'
    out = []
    for row in db.execute("""SELECT id, title, price, area_m2, url, source FROM listings
                             WHERE source = ? AND id != ? AND raw_json LIKE ?
                             ORDER BY price""", (item.get("source"), item["id"], like)).fetchall()[:12]:
        out.append({"id": row["id"], "title": (row["title"] or "")[:140], "price": row["price"],
                    "area": row["area_m2"], "url": safe_url(row["url"])})
    return out


PREDIAL_ONLINE = "https://www.predialonline.pt/PredialOnline/"
_TAX_VALUE = re.compile(r"valor\s+(?:patrimonial(?:\s+tribut[áa]vel)?|tribut[áa]vel)\s*(?:de|:)?\s*€?\s*([\d][\d .]*,\d{2}|[\d][\d .]*)", re.I)

_CONSERVATORIA = re.compile(
    r"Conservat[óo]ria\s+(?:do\s+)?(?:Registo\s+Predial\s+)?(?:de|do|da)\s+(.+?)\s+sob\s+o\s+n[.ºo°]*\s*([\d/]+)", re.I)
_DESCRITO_SOB = re.compile(r"descrit[oa]\s+sob\s+o\s+n[.ºo°]*\s*([\d/]+)\s+da\s+Conservat[óo]ria\s+(?:do\s+Registo\s+Predial\s+)?(?:de|do|da)\s+([^,.;]+)", re.I)
_ARTIGO = re.compile(r"matriz(?:\s+predial)?\s+(urbana|r[úu]stica)?\s*(?:da\s+freguesia\s+de\s+[^,]+?\s+)?sob\s+o\s+art(?:igo)?\.?\s*n?[.ºo°]*\s*(\d+)", re.I)


def _land_register_from_text(text: str) -> list[dict]:
    """"descrito na Conservatória do Registo Predial de Penafiel sob o nº 01469/11062003 e
    inscrito na matriz predial urbana sob o artigo nº 158" → the identifiers."""
    found = []
    m = _CONSERVATORIA.search(text)
    if m:
        found.append({"conservatoria": m.group(1).strip(" ,"), "descricao": m.group(2)})
    else:
        m = _DESCRITO_SOB.search(text)
        if m:
            found.append({"conservatoria": m.group(2).strip(" ,"), "descricao": m.group(1)})
    art = _ARTIGO.search(text)
    if art:
        entry = found[0] if found else {}
        entry["artigos"] = [{"numero": art.group(2), "tipo": (art.group(1) or "").lower() or None}]
        if not found:
            found.append(entry)
    return found


def official_records(item: dict) -> dict | None:
    """Where the government's own record of this property is, with the numbers
    to find it, and what that record says when it can be read for free."""
    raw = raw_of(item)
    country = item.get("country") or "PT"
    if country == "ES":
        ref = raw.get("referencia_catastral")
        cat = raw.get("catastro")
        if not (ref or cat):
            return None
        facts = []
        if cat:
            for label, key, unit in (("Use", "use", ""), ("Built area", "built_m2", " m²"),
                                     ("Plot", "plot_m2", " m²"), ("Year built", "year", "")):
                value = cat.get(key)
                if not value:
                    continue
                if key == "year":
                    value = str(int(value))
                elif isinstance(value, (int, float)):
                    value = f"{value:,.0f}".replace(",", " ") + unit
                facts.append({"label": label, "value": value})
            if cat.get("address"):
                facts.append({"label": "Address", "value": cat["address"]})
            for u in cat.get("units") or []:
                facts.append({"label": "Building", "value": f"{u.get('use')} ({u.get('m2') or '?'} m²)"})
        check = None
        if cat and cat.get("built_m2") and item.get("area_m2"):
            diff = abs(cat["built_m2"] - item["area_m2"]) / max(cat["built_m2"], item["area_m2"])
            check = (f"The sale says {item['area_m2']:,.0f} m²; Catastro says {cat['built_m2']:,.0f} m² built"
                     + (" — they differ, ask the court why." if diff > 0.2 else " — they match."))
        return {"country": "ES", "registry": "Catastro (Spain's land registry)", "facts": facts, "check": check,
                "copy": [{"label": "Referencia catastral", "value": ref}] if ref else [],
                "links": [{"label": "Open in Catastro", "url": catastro_url(item)}] if catastro_url(item) else [],
                "steps": [] if cat else [{"text": "Catastro has not been read for this sale yet (it is read "
                                                  "during scans; open the link to see it now)."}]}
    if country != "PT":
        return None

    entries = raw.get("registo_predial") or _land_register_from_text(
        " ".join(str(x or "") for x in (raw.get("descricao_completa"), item.get("description"))))
    copy = []
    for e in entries[:3]:
        where = e.get("conservatoria") or e.get("concelho")
        if e.get("descricao"):
            copy.append({"label": "Description no. (número da descrição)", "value": e["descricao"]})
        if e.get("fracao"):
            copy.append({"label": "Fraction (fração)", "value": e["fracao"]})
        if e.get("freguesia"):
            copy.append({"label": "Parish (freguesia)", "value": e["freguesia"]})
        if where:
            copy.append({"label": "Conservatória / concelho", "value": where})
        for a in e.get("artigos") or []:
            copy.append({"label": f"Tax article ({a.get('tipo') or 'artigo matricial'})", "value": a["numero"]})
    if raw.get("art_matricial") and not any(c["label"].startswith("Tax article") for c in copy):
        copy.append({"label": "Tax article (artigo matricial)", "value": str(raw["art_matricial"])})
    if raw.get("registo") and not any(c["label"].startswith("Description") for c in copy):
        copy.append({"label": "Description no. (número da descrição)", "value": str(raw["registo"])})
    facts = []
    vpt = _TAX_VALUE.search(" ".join(str(x or "") for x in (raw.get("descricao_completa"), item.get("description"))))
    if vpt:
        facts.append({"label": "Tax value (valor patrimonial tributário)", "value": f"€{vpt.group(1).strip()}"})
    try:
        from scoring import local_price
        local = local_price(item)
    except Exception:  # noqa: BLE001
        local = None
    if local:
        facts.append({"label": "Local median price", "value": f"€{local[0]:,.0f}/m² ({local[1]})"})
    if not copy and not facts:
        return None
    parish = item.get("freguesia") or next((e.get("freguesia") for e in entries if e.get("freguesia")), None)
    return {
        "country": "PT", "registry": "Registo Predial (land register) — certidão permanente",
        "copy": copy, "facts": facts, "check": None,
        "links": [{"label": "Predial Online", "url": PREDIAL_ONLINE}],
        "steps": [
            {"text": "Open Predial Online and sign in with your Cartão de Cidadão or Chave Móvel Digital."},
            {"text": "Choose “Criar nova certidão permanente” and enter the description number"
                     + (f", the parish ({parish})" if parish else ", the parish") + " and the fraction if there is one."},
            {"text": "Pay €15 (you pay it yourself); the access code comes by e-mail and the certificate "
                     "is valid for 6 months. “Consultar” with the code shows the owners, the area and "
                     "composition, and every charge on the property (mortgages, penhoras)."},
            {"text": "If the parish names differ between documents, use the one from the same document "
                     "as the description number."},
            {"text": "The tax record (caderneta predial) is visible only to its owner: ask the agente or the "
                     "court for it in the information request, quoting the tax article."},
        ],
    }


def how_to_find(item: dict) -> dict | None:
    """Steps to reach the exact sale when its link opens only a search page."""
    if item.get("source") != "citius":
        return None
    raw = raw_of(item)
    proc = case_number(item)
    court = raw.get("tribunal")
    modalidade = raw.get("modalidade")
    steps = [
        {"text": "Open the Citius sales search (the link on this listing).", "url": CITIUS_SEARCH},
        {"text": "Tribunal: choose", "copy": court} if court else
        {"text": "Tribunal: choose the court of the case (it is not in the scanned data)."},
        {"text": "Tipo de Bem: Imóvel · Estado da Venda: Em venda"
                 + (f" · Modalidade da Venda: {modalidade}" if modalidade else "")},
        {"text": "Tick “Ignorar Datas”, then click Pesquisar."},
        {"text": "In the results press Ctrl+F and search for the case number:", "copy": proc} if proc else
        {"text": "In the results press Ctrl+F and search for a few words of the title."},
        {"text": "The sale shows the agente de execução (or the court) to contact for visits and "
                 "documents; the Offers page writes the letter."},
    ]
    return {"site": "Citius", "why": "Citius has no page per sale: its results exist only after a search.",
            "steps": steps}
