"""Belgium: biddit.be notary auctions."""
from __future__ import annotations

import json
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
