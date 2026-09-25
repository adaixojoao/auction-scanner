"""Italy: astegiudiziarie.it, PVP Giustizia, Gobid Real, Astalegale."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_area, find_price, make_listing, make_session, parse_price
from db import upsert_listing
from sources import register
from sources._cards import CardSite, scrape_cards


@register("italy", "IT")
def scrape_italy(db, max_price: float = 50000, **_):
    """astegiudiziarie.it — Italian judicial auctions (front page)."""
    session = make_session(timeout=30, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    })
    base = "https://www.astegiudiziarie.it"
    resp = session.get(f"{base}/immobili")
    resp.raise_for_status()

    total_scraped = 0
    # Detail links look like /vendita-asta-TYPE-LOCATION-...-lNNNNNNN-pNNNNNNN
    for a in BeautifulSoup(resp.text, "html.parser").select("a[href*='/vendita-asta-']"):
        href = a.get("href", "")
        m = re.search(r'-l(\d+)-p(\d+)', href)
        if not m:
            continue
        eid = f"{m.group(1)}-{m.group(2)}"

        title = a.get_text(strip=True)[:120]
        if not title:
            img = a.select_one("img")
            title = img.get("alt", "")[:120] if img else ""
        price = find_price(a.parent.get_text(" ", strip=True)) if a.parent else None

        upsert_listing(db, make_listing(
            "italy", eid, "IT", title=title or f"Immobile {eid}", tipo="immobile",
            price=price, url=href, base_url=base,
        ))
        total_scraped += 1
    return total_scraped


# The portal is an Angular app; its search is a JSON API. filtroAnnunci 1 is
# "sales to come" (about 15,000 lots); the price fields of the form are not
# honoured by the API, so the budget is applied here.
PVP = "https://pvp.giustizia.it"
PVP_API = f"{PVP}/ric-496b258c-986a1b71/ric-ms/ricerca/vendite"
PVP_PAGE = 2000
PVP_KINDS = {"IMMOBILE_RESIDENZIALE", "ALTRA_CATEGORIA"}          # homes, and land among "other"
PVP_OCCUPANCY = {"LIBER": "libero", "OCCUP": "occupato", "OCCST": "occupato senza titolo",
                 "INCOR": "in corso di liberazione"}


def pvp_listing(lot: dict) -> dict | None:
    lid = lot.get("id")
    if not lid:
        return None
    where = lot.get("indirizzo") or {}
    coords = where.get("coordinate") or {}
    desc = re.sub(r"\s+", " ", lot.get("descLotto") or "").strip()
    free = sorted({PVP_OCCUPANCY[d] for d in lot.get("disponibilita") or [] if d in PVP_OCCUPANCY})
    parts = [desc, f"Disponibilità: {', '.join(free)}" if free else "",
             f"{lot.get('tribunale') or ''}, procedura {lot.get('procedura') or ''}".strip(", ")]
    raw = {k: lot.get(k) for k in ("procedura", "tribunale", "numeroLotto", "categoriaLotto", "categoriaBene",
                                   "offertaMinima", "disponibilita") if lot.get(k)}
    if coords.get("latitudine") and coords.get("longitudine"):
        raw.update(lat=coords["latitudine"], lon=coords["longitudine"])
    date = lot.get("dataOraVendita") or lot.get("dataVendita")
    return make_listing(
        "pvp_giustizia", str(lid), "IT", id_prefix="pvp",
        title=f"{(desc[:90] or 'Immobile')} · {where.get('citta') or ''}"[:200],
        description=". ".join(p for p in parts if p)[:3000] or None, tipo="immobile",
        area_m2=find_area(desc), price=lot.get("prezzoBaseAsta"), min_price=lot.get("offertaMinima"),
        district=where.get("provincia"), concelho=where.get("citta"),
        url=f"{PVP}/pvp/it/detail_annuncio.page?idAnnuncio={lid}",
        date_end=f"{date}:00" if date and len(date) == 16 else date, raw_json=raw,
    )


@register("pvp_giustizia", "IT")
def scrape_italy_pvp(db, max_price: float = 50000, **_):
    """pvp.giustizia.it — Portale Vendite Pubbliche (official judicial sales portal)."""
    session = make_session(timeout=60)
    total = 0
    for page in range(0, 30):
        resp = session.post(PVP_API, params={"page": page, "size": PVP_PAGE},
                            json={"tipoLotto": "IMMOBILI", "filtroAnnunci": 1})
        resp.raise_for_status()
        body = (resp.json() or {}).get("body") or {}
        for lot in body.get("content") or []:
            price = lot.get("prezzoBaseAsta") or 0
            if lot.get("categoriaLotto") not in PVP_KINDS or not price or price > max_price:
                continue
            row = pvp_listing(lot)
            if row:
                upsert_listing(db, row)
                total += 1
        db.commit()
        if body.get("last", True) or page + 1 >= (body.get("totalPages") or 0):
            break
        time.sleep(1)
    LOG.info(f"PVP: {total} listings")
    return total


GOBIDREAL = CardSite(
    source="gobidreal", country="IT", base="https://www.gobidreal.it", path="/en/real-estate-auctions/",
    card_selector="div.lot-card, article.auction, div[class*='lot'], div[class*='auction']",
    title_selector="h2,h3,.title,.lot-title",
    location_selector=".location,.city,.comune",
    date_selector=".date,.auction-date,.end-date",
    params={"price_to": "{max_price}"}, max_pages=19,
    description="Gobid Real Italian judicial auction", tipo="immobile",
)
ASTALEGALE = "https://www.astalegale.net"
ASTALEGALE_API = "https://api.astalegale.net/Search"
ASTALEGALE_MAX_PAGES = 300          # 12 a page; about 2,000 homes under €30,000


def _astalegale_date(text: str | None) -> str | None:
    """"16/12/2026 - 10:00" → "2026-12-16T10:00:00"."""
    m = re.match(r"\s*(\d{2})/(\d{2})/(\d{4})(?:\s*-\s*(\d{1,2}):(\d{2}))?", text or "")
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return f"{y}-{mo}-{d}T{int(h or 0):02d}:{mi or '00'}:00"


def astalegale_listing(lot: dict) -> dict | None:
    """One result of api.astalegale.net/Search as a listing."""
    lid = str(lot.get("id") or "").strip()
    # Lots copied from the PVP arrive masked ("XXXXXXXXXX", no price) unless logged in.
    if not lid or re.fullmatch(r"[X\s]*", lot.get("tipologia") or "X"):
        return None
    desc = re.sub(r"\s+", " ", lot.get("descrizione") or "").strip()
    pos = lot.get("posizione") or {}
    raw = {k: lot.get(k) for k in ("proceduraNumeroAnno", "tribunale", "tipoProceduraEsteso", "modalitaVendita",
                                   "codiceLotto", "offertaMinima", "dataAsta") if lot.get(k)}
    if pos.get("lat") and pos.get("lng"):
        raw.update(lat=pos["lat"], lon=pos["lng"])
    return make_listing(
        "astalegale", lid, "IT",
        title=f"{lot.get('tipologia') or 'Immobile'} · {lot.get('titolo') or ''} · {lot.get('comune') or ''}"[:200],
        description=desc[:3000] or None, tipo=(lot.get("tipologia") or "immobile").lower(),
        area_m2=find_area(desc), price=lot.get("prezzoNum") or parse_price(lot.get("prezzo") or ""),
        min_price=parse_price(lot.get("offertaMinima") or ""),
        district=lot.get("provincia"), concelho=lot.get("comune"),
        url=f"{ASTALEGALE}/Aste/Detail/{lot.get('friendlyId') or lid}",
        image_url=lot.get("urlImmaginePrincipale"), date_end=_astalegale_date(lot.get("dataAsta")),
        raw_json=raw,
    )


@register("gobidreal", "IT")
def scrape_gobidreal(db, max_price: float = 100000, **_):
    """gobidreal.it — Italian judicial property auctions."""
    return scrape_cards(db, GOBIDREAL, max_price)


@register("astalegale", "IT")
def scrape_astalegale(db, max_price: float = 100000, **_):
    """astalegale.net — Italian judicial auction aggregator (its search API:
    homes up to the budget, 12 a page)."""
    session = make_session(timeout=30)
    total, seen = 0, set()
    for page in range(1, ASTALEGALE_MAX_PAGES + 1):
        resp = session.post(ASTALEGALE_API, json={"categories": ["residenziali"], "prezzoA": int(max_price),
                                                  "page": page})
        resp.raise_for_status()
        results = (resp.json() or {}).get("results") or {}
        lots = results.get("currentPage") or []
        new = 0
        for lot in lots:
            row = astalegale_listing(lot)
            if not row or row["id"] in seen:
                continue
            seen.add(row["id"])
            upsert_listing(db, row)
            total += 1
            new += 1
        db.commit()
        if not new or page * (results.get("pageSize") or 12) >= (results.get("totalResults") or 0):
            break
        time.sleep(0.3)
    LOG.info(f"Astalegale: {total} listings")
    return total
