"""Cyprus: Department of Lands and Surveys forced sales."""
from __future__ import annotations

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, stable_id
from db import upsert_listing
from sources import register


# Not in the default scan (Sept 2026): Cyprus DLS: mof.gov.cy sends an incomplete certificate chain; check it by hand.
# Bot walls are not worked around; it stays runnable by name in case the site opens up.
@register("cyprus", "CY", default=False)
def scrape_cyprus(db, max_price: float = 50000, **_):
    """mof.gov.cy DLS — Cyprus forced sales of foreclosed properties."""
    session = make_session()
    base = "https://www.mof.gov.cy"
    resp = session.get(f"{base}/mof/dls/dls.nsf/All/AllSales")
    resp.raise_for_status()

    rows = BeautifulSoup(resp.text, "html.parser").select("table tr")[1:]
    LOG.info(f"  Cyprus: {len(rows)} rows found")

    total_scraped = 0
    for row in rows:
        cells = row.select("td")
        if len(cells) < 3:
            continue
        link = row.select_one("a[href]")
        title = (link.get_text(strip=True) if link else cells[0].get_text(strip=True))[:200]
        text = row.get_text(" ", strip=True)
        price = find_price(text)
        if price and price > max_price:
            continue
        # Was str(hash(...)): a new ID every run, so every row was "new" every run.
        eid = stable_id(title, text[:100])
        upsert_listing(db, make_listing(
            "cyprus", eid, "CY",
            title=title or f"Cyprus sale {eid}", description=text[:500],
            tipo="imovel", price=price,
            url=link["href"] if link else None, base_url=base,
        ))
        total_scraped += 1
    return total_scraped
