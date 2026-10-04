"""Belgium: biddit.be notary auctions."""
from __future__ import annotations

import html
import json
import re
import time

from common import LOG, make_listing, make_session, to_number
from db import upsert_listing
from sources import register


def biddit_listing(item: dict) -> dict | None:
    eid = str(item.get("id", item.get("propertyId", "")) or "")
    if not eid:
        return None
    price = (to_number(item.get("minimumBid")) or to_number(item.get("startingPrice"))
             or to_number(item.get("price")) or 0)
    addr = item.get("address") or {}
    return make_listing(
        "biddit", eid, "BE",
        title=str(item.get("title") or item.get("description") or f"Biddit {eid}")[:200],
        description=item.get("description") or item.get("shortDescription"),
        tipo=item.get("type", "imovel"),
        area_m2=item.get("livingArea") or item.get("area"),
        price=price,
        current_bid=item.get("currentBid") or item.get("highestBid"),
        min_price=item.get("minimumBid") or price,
        district=addr.get("city") or addr.get("municipality") or item.get("city"),
        concelho=addr.get("postalCode"),
        url=item.get("url") or f"https://www.biddit.be/catalog/detail/{eid}",
        base_url="https://www.biddit.be",
        image_url=item.get("imageUrl") or item.get("mainImage"),
        date_end=item.get("endDate"),
        raw_json=json.dumps(item, ensure_ascii=False)[:2000],
    )


# Not in the default scan (Sept 2026): Biddit: its search API rejects anything but a browser (F5 firewall); check biddit.be by hand.
# Bot walls are not worked around; it stays runnable by name in case the site opens up.
@register("biddit", "BE", default=False)
def scrape_biddit(db, max_price: float = 50000, **_):
    """biddit.be — Belgian online notary auctions."""
    session = make_session(headers={"Accept": "application/json"})
    total_scraped = 0

    for page in range(0, 20):
        try:
            resp = session.get("https://www.biddit.be/api/v1/properties", params={
                "page": page, "size": 50, "sort": "endDate,asc",
                "status": "OPEN", "maxPrice": int(max_price),
            })
            if resp.status_code in (404, 400):
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if page == 0:
                raise
            break

        items = data if isinstance(data, list) else data.get("content", data.get("properties", []))
        if not items:
            break
        for item in items:
            listing = biddit_listing(item)
            if not listing or (listing["price"] or 0) > max_price:
                continue
            upsert_listing(db, listing)
            total_scraped += 1

        db.commit()
        LOG.info(f"  Biddit page {page}: {len(items)} items (total: {total_scraped})")
        if len(items) < 50:
            break
        time.sleep(1)
    return total_scraped


# ─── Immoweb ─────────────────────────────────────────────────────────
# Belgium's main portal. The search page embeds its results as JSON in a
# ":results" attribute, with map positions and surfaces. Most cheap houses there
# are life annuities (a monthly payment, not a price) or already under option:
# only plain sales and notary public sales are kept.

IMMOWEB = "https://www.immoweb.be"
IMMOWEB_SEARCHES = {"maison": "maison", "terrain-a-batir": "terrain"}
IMMOWEB_MAX_PAGES = 15
IMMOWEB_KINDS = {"HOUSE": "Maison", "VILLA": "Villa", "BUNGALOW": "Bungalow", "CHALET": "Chalet",
                 "FARMHOUSE": "Ferme", "COUNTRY_COTTAGE": "Fermette", "TOWN_HOUSE": "Maison de ville",
                 "MANSION": "Maison de maître", "MIXED_USE_BUILDING": "Immeuble mixte", "LAND": "Terrain",
                 "BUILDING_LAND": "Terrain à bâtir"}
IMMOWEB_SALE_TYPES = {"residential_sale", "first_session_with_reserve_price", "public_sale"}


def immoweb_results(page_html: str) -> list[dict]:
    m = re.search(r":results='([^']*)'", page_html)
    if not m:
        return []
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return []


def parse_immoweb(ad: dict, tipo: str) -> dict | None:
    price = ad.get("price") or {}
    flags = ad.get("flags") or {}
    if (price.get("type") not in IMMOWEB_SALE_TYPES or not price.get("mainValue")
            or flags.get("main") == "under_option" or "life_annuity" in (flags.get("secondary") or [])):
        return None
    prop = ad.get("property") or {}
    loc = prop.get("location") or {}
    town = loc.get("locality")
    raw = {}
    if loc.get("latitude") and loc.get("longitude"):
        raw["geo"] = {"lat": loc["latitude"], "lon": loc["longitude"],
                      "precision": "village" if loc.get("approximated") else "street"}
    public = price.get("type") != "residential_sale"
    land, built = prop.get("landSurface"), prop.get("netHabitableSurface")
    pitch = prop.get("salesPitch")
    if isinstance(pitch, dict):                  # one text per language
        pitch = pitch.get("fr") or pitch.get("nl") or pitch.get("en") or next(iter(pitch.values()), None)
    description = " · ".join(str(x) for x in (
        prop.get("title"), pitch,
        f"Surface habitable {built} m²" if built else None, f"Terrain {land} m²" if land else None,
        "Vente publique (notaire)" if public else None,
        f"PEB {ad['transaction']['certificate']}" if (ad.get("transaction") or {}).get("certificate") else None) if x)
    kind = IMMOWEB_KINDS.get(prop.get("subtype") or prop.get("type") or "",
                             (prop.get("subtype") or "").replace("_", " ").capitalize())
    return make_listing(
        "immoweb", ad["id"], "BE", title=f"{kind or 'Maison'} à {town} ({loc.get('postalCode') or ''})",
        description=description, tipo=tipo, area_m2=(land if tipo == "terrain" else built) or land or built,
        price=float(price["mainValue"]), min_price=float(price["mainValue"]),
        district=loc.get("province"), concelho=town,
        url=f"{IMMOWEB}/fr/annonce/{ad['id']}",
        image_url=next((m.get("mediumUrl") or m.get("smallUrl") for m in (ad.get("media") or {}).get("pictures") or []
                        if m.get("mediumUrl") or m.get("smallUrl")), None),
        raw_json=json.dumps(raw) if raw else None,
    )


@register("immoweb", "BE", description="Immoweb — houses and building land in Belgium (no life annuities)")
def scrape_immoweb(db, max_price: float = 50000, **_):
    """Immoweb — houses and building land in Belgium, plain and notary public sales only."""
    session = make_session(timeout=40)
    total = 0
    for slug, tipo in IMMOWEB_SEARCHES.items():
        for page in range(1, IMMOWEB_MAX_PAGES + 1):
            try:
                resp = session.get(f"{IMMOWEB}/fr/recherche/{slug}/a-vendre",
                                   params={"countries": "BE", "maxPrice": int(max_price), "orderBy": "cheapest",
                                           "page": page})
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one search failing is not the source failing
                if total == 0 and page == 1 and slug == next(iter(IMMOWEB_SEARCHES)):
                    raise
                LOG.info(f"Immoweb {slug} p{page}: {type(e).__name__}")
                break
            ads = immoweb_results(resp.text)
            for ad in ads:
                row = parse_immoweb(ad, tipo)
                if row and row["price"] <= max_price:
                    upsert_listing(db, row)
                    total += 1
            db.commit()
            if len(ads) < 30:
                break
            time.sleep(2)
    LOG.info(f"Immoweb: {total} listings")
    return total
