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


def map_url(item: dict) -> str | None:
    raw = raw_of(item)
    where = _coords(item, raw)
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
