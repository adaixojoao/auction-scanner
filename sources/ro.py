"""Romania: Storia asking prices, and ANAF tax seizures (off by default)."""
from __future__ import annotations

import json
import re
import time

from common import LOG, make_listing, make_session, to_number
from db import upsert_listing
from sources import register


# Not in the default scan (Sept 2026): ANAF: the sales list needs a browser and the old REST address answers 403; check anaf.ro by hand.
# Bot walls are not worked around; it stays runnable by name in case the site opens up.
@register("anaf", "RO", default=False)
def scrape_anaf(db, max_price: float = 50000, **_):
    """anaf.ro — Romanian tax-authority forced sales. Prices are in RON, not EUR."""
    session = make_session()
    url = "https://www.anaf.ro/BunuriSechestrate/rest/bunuri"
    total_scraped = 0

    for page in range(1, 20):
        try:
            resp = session.get(url, params={"tipBun": "I", "pagina": page, "nrBunuriPagina": 50})
            if resp.status_code in (404, 500):
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if page == 1:
                raise
            break

        items = data if isinstance(data, list) else data.get("bunuri", data.get("items", []))
        if not items:
            break
        for item in items:
            eid = str(item.get("id", item.get("idBun", "")) or "")
            if not eid:
                continue
            price = to_number(item.get("pretVanzare")) or to_number(item.get("pretEvaluare")) or 0
            if price > max_price:
                continue
            upsert_listing(db, make_listing(
                "anaf", eid, "RO",
                title=str(item.get("denumire") or item.get("descriere") or f"ANAF sale {eid}")[:200],
                description=item.get("descriere") or item.get("observatii"),
                tipo="imovel",
                area_m2=item.get("suprafata"),
                price=price, min_price=item.get("pretMinim") or price,
                district=item.get("judet") or item.get("localitate"),
                concelho=item.get("localitate"),
                url=item.get("url") or f"https://www.anaf.ro/BunuriSechestrate/detalii.html?id={eid}",
                base_url="https://www.anaf.ro",
                date_end=item.get("dataLicitatie") or item.get("dataLimita"),
                raw_json=json.dumps(item, ensure_ascii=False)[:2000],
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  ANAF page {page}: {len(items)} items (total: {total_scraped})")
        if len(items) < 50:
            break
        time.sleep(1)
    return total_scraped


# ─── Storia ──────────────────────────────────────────────────────────
# storia.ro search pages embed the ads as JSON. Houses, flats and plots,
# asking prices in euros. The price filter is the site's own (priceMax).

STORIA = "https://www.storia.ro"
STORIA_KINDS = (("casa", "house"), ("apartament", "house"), ("teren", "terreno"))
STORIA_MAX_PAGES = 4


def _storia_items(page_html: str) -> list:
    found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html or "", re.S)
    if not found:
        return []
    try:
        data = json.loads(found.group(1))
    except json.JSONDecodeError:
        return []
    ads = (((data.get("props") or {}).get("pageProps") or {}).get("data") or {}).get("searchAds") or {}
    items = ads.get("items") if isinstance(ads, dict) else None
    return items if isinstance(items, list) else []


def _storia_place(ad: dict) -> dict[str, str | None]:
    locs = ((ad.get("location") or {}).get("reverseGeocoding") or {}).get("locations") or []
    by = {}
    for loc in locs:
        if isinstance(loc, dict) and loc.get("locationLevel") and loc.get("name"):
            by[str(loc["locationLevel"])] = str(loc["name"])
    return by


def parse_storia_ad(ad: dict, tipo: str) -> dict | None:
    """One Storia ad, or None when it has no euro asking price."""
    if not isinstance(ad, dict) or ad.get("hidePrice"):
        return None
    price = ad.get("totalPrice") or {}
    if not isinstance(price, dict) or str(price.get("currency") or "").upper() != "EUR":
        return None
    amount = to_number(price.get("value"))
    eid = str(ad.get("id") or "").strip()
    slug = str(ad.get("slug") or "").strip()
    if not amount or not eid or not slug:
        return None
    where = _storia_place(ad)
    area = ad.get("terrainAreaInSquareMeters") if tipo == "terreno" else None
    area = to_number(area or ad.get("areaInSquareMeters"))
    # €16 for 2,370 m² is not an asking price (the per-m² figure is about zero).
    if area and amount / area < 0.05:
        return None
    images = ad.get("images") or []
    image = None
    if images and isinstance(images[0], dict):
        image = images[0].get("medium") or images[0].get("small")
    return make_listing(
        "storia", eid, "RO", title=str(ad.get("title") or f"Storia {eid}")[:200],
        description=ad.get("title"), tipo=tipo, area_m2=area, price=amount, min_price=amount,
        district=where.get("county"), concelho=where.get("commune") or where.get("city"),
        freguesia=where.get("village") or where.get("district"),
        url=f"{STORIA}/ro/ad/{slug}", image_url=image if isinstance(image, str) else None,
    )


@register("storia", "RO", description="Storia — houses, flats and plots for sale all over Romania")
def scrape_storia(db, max_price: float = 50000, **_):
    """Storia — houses, flats and plots for sale all over Romania (asking prices, euros)."""
    session = make_session(timeout=30)
    total = 0
    seen: set[str] = set()
    for kind, tipo in STORIA_KINDS:
        for page in range(1, STORIA_MAX_PAGES + 1):
            url = f"{STORIA}/ro/rezultate/vanzare/{kind}/toata-romania"
            try:
                resp = session.get(url, params={"priceMax": int(max_price), "page": page})
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one category failing is not the source failing
                if total == 0 and page == 1 and kind == STORIA_KINDS[0][0]:
                    raise
                LOG.info(f"Storia {kind} p{page}: {type(e).__name__}")
                break
            items = _storia_items(resp.text)
            if not items:
                break
            kept = 0
            for ad in items:
                row = parse_storia_ad(ad, tipo)
                if not row or row["price"] > max_price or row["external_id"] in seen:
                    continue
                seen.add(row["external_id"])
                upsert_listing(db, row)
                kept += 1
            total += kept
            db.commit()
            if len(items) < 20:
                break
            time.sleep(0.7)
    LOG.info(f"Storia: {total} listings")
    return total
