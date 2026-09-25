"""Italy: astegiudiziarie.it, PVP Giustizia, Gobid Real, Astalegale."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_area, find_price, make_listing, make_session, parse_date_dmy, parse_price, stable_id
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


@register("pvp_giustizia", "IT")
def scrape_italy_pvp(db, max_price: float = 50000, **_):
    """pvp.giustizia.it — Portale Vendite Pubbliche (official judicial sales portal)."""
    session = make_session()
    base = "https://pvp.giustizia.it"
    total_scraped = 0

    for page in range(1, 20):
        try:
            resp = session.get(f"{base}/pvp/it/risultati_ricerca.page", params={
                "tipoBene": "Immobile", "pag": page, "num": 50,
                "ord": "dataVendita", "dir": "asc", "prezzoMax": int(max_price),
            })
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.risultato, div.card-risultato, div.bene-item") \
            or soup.select("table.risultati tr")[1:]
        if not items:
            break

        for item_el in items:
            link = item_el.select_one("a[href]")
            if not link:
                continue
            title = link.get_text(strip=True)[:200]
            href = link["href"]
            eid_m = re.search(r'[/=](\d+)', href)
            eid = eid_m.group(1) if eid_m else stable_id(href, title)
            text = item_el.get_text(" ", strip=True)
            price = find_price(text)
            if price and price > max_price:
                continue
            loc_m = re.search(r"(?:Ubicazione|Comune|Provincia)[:\s]+([^\n,]+)", text, re.I)
            upsert_listing(db, make_listing(
                "pvp_giustizia", eid, "IT", id_prefix="pvp",
                title=title or f"Italian judicial sale {eid}", description=text[:500],
                tipo="immobile", price=price, min_price=price,
                district=loc_m.group(1).strip() if loc_m else None,
                url=href, base_url=base, date_end=parse_date_dmy(text),
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  PVP page {page}: {total_scraped} total")
        if len(items) < 50:
            break
        time.sleep(1)
    return total_scraped


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
