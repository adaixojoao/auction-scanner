"""Ireland: MyHome.ie."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("myhome", "IE", description="MyHome — homes for sale in Ireland")
def scrape_myhome(db, max_price: float = 50000, **_):
    """MyHome — homes for sale in Ireland."""
    return scrape_named(db, "myhome", max_price)
