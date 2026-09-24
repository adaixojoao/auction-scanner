"""Pan-European aggregators."""
from __future__ import annotations

import json
import time

from common import LOG, make_listing, make_session, stable_id, to_number
from db import upsert_listing
from sources import register

COURTBID_COUNTRIES = ["PT", "ES", "FR", "DE", "IT", "NL", "HR", "GR", "BE", "RO", "PL", "CY"]


@register("courtbid", "EU", default=False)
def scrape_courtbid(db, max_price: float = 100000, config: dict | None = None, **_):
    """CourtBid via Apify — 17-country distressed property feed. Needs
    "apify_token" in config.json; costs Apify credits, so it only runs with
    --source courtbid."""
    token = (config or {}).get("apify_token", "")
    if not token:
        raise RuntimeError("no apify_token in config.json")

    session = make_session(timeout=30, headers={"Authorization": f"Bearer {token}"})
    run_resp = session.post(
        "https://api.apify.com/v2/acts/studio-amba~distressed-property-feed/runs",
        json={"maxPrice": int(max_price), "propertyType": "residential",
              "countries": COURTBID_COUNTRIES})
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]

    for _attempt in range(30):
        time.sleep(10)
        try:
            status = session.get(f"https://api.apify.com/v2/actor-runs/{run_id}", timeout=15).json()["data"]["status"]
        except Exception:
            continue
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {status}")

    items_resp = session.get(f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items",
                             params={"format": "json", "limit": 5000}, timeout=60)
    items_resp.raise_for_status()

    total = 0
    for item in items_resp.json():
        price = to_number(item.get("reservePrice")) or to_number(item.get("price")) or 0
        if price and price > max_price:
            continue
        country = (item.get("country") or "EU").upper()[:2]
        eid = str(item.get("id") or item.get("lotId")
                  or stable_id(json.dumps(item, sort_keys=True, default=str)))
        upsert_listing(db, make_listing(
            "courtbid", eid, country,
            title=str(item.get("title") or item.get("description") or f"CourtBid #{eid}")[:200],
            description=item.get("description") or item.get("details"),
            tipo=item.get("propertyType") or "imovel",
            area_m2=item.get("area") or item.get("surfaceArea"),
            price=price, current_bid=item.get("currentBid"),
            min_price=item.get("minimumBid") or price,
            district=item.get("region") or item.get("province"),
            concelho=item.get("city") or item.get("municipality"),
            url=item.get("url") or item.get("sourceUrl"),
            image_url=item.get("imageUrl"),
            date_end=item.get("auctionDate") or item.get("endDate"),
            raw_json=json.dumps(item, ensure_ascii=False)[:2000],
        ))
        total += 1
    LOG.info(f"CourtBid (Apify): {total} listings across EU")
    return total
