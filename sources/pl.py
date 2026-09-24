"""Poland: licytacje.komornik.pl bailiff auctions."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, stable_id
from db import upsert_listing
from sources import register


@register("poland", "PL")
def scrape_poland(db, max_price: float = 50000, **_):
    """licytacje.komornik.pl — Polish bailiff auctions. Prices there are in PLN;
    only amounts marked € are read, so most rows arrive without a price."""
    session = make_session()
    base = "https://licytacje.komornik.pl"
    total_scraped = 0

    for page in range(1, 20):
        try:
            resp = session.get(f"{base}/nieruchomosci", params={"page": page})
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.listing-item, div.card, article.auction") or soup.select("table.auctions tr")[1:]
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
            loc_m = re.search(r"(?:Lokalizacja|Miasto|Adres)[:\s]+([^\n,]+)", text)
            upsert_listing(db, make_listing(
                "poland", eid, "PL",
                title=title or f"Polish auction {eid}", description=text[:500],
                tipo="imovel", price=price, min_price=price,
                district=loc_m.group(1).strip() if loc_m else None,
                url=href, base_url=base, date_end=parse_date_dmy(text),
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  Poland page {page}: {total_scraped} total")
        if len(items) < 20:
            break
        time.sleep(1)
    return total_scraped
