"""Greece: eauction.gr judicial e-auctions."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, stable_id
from db import upsert_listing
from sources import register


@register("greece", "GR")
def scrape_greece(db, max_price: float = 50000, **_):
    """eauction.gr — Greek court-mandated electronic auctions."""
    session = make_session()
    base = "https://www.eauction.gr"
    resp = session.get(f"{base}/Auction/SearchResult", params={
        "AssetCategory": "REAL_ESTATE", "AuctionStatus": "ACTIVE",
        "PageSize": 100, "PageNumber": 1,
    })
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("div.auction-result-item, div.card, tr.auction-row") or soup.select("table tr")[1:]
    LOG.info(f"  Greece: {len(items)} items found")

    total_scraped = 0
    for item_el in items:
        link = item_el.select_one("a[href*='Auction']") or item_el.select_one("a[href]")
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
        loc_m = re.search(r"(?:Τοποθεσία|Location|Περιοχή)[:\s]+([^\n,]+)", text)
        upsert_listing(db, make_listing(
            "greece", eid, "GR",
            title=title or f"Greek auction {eid}", description=text[:500],
            tipo="imovel", price=price, min_price=price,
            district=loc_m.group(1).strip() if loc_m else None,
            url=href, base_url=base, date_end=parse_date_dmy(text),
        ))
        total_scraped += 1
    return total_scraped
