"""Luxembourg: atHome.lu."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("athome", "LU", description="atHome — homes and land for sale in Luxembourg and nearby")
def scrape_athome(db, max_price: float = 50000, **_):
    """atHome — homes and land for sale in Luxembourg and nearby."""
    return scrape_named(db, "athome", max_price)
