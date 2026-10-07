"""Austria: willhaben.at and wohnnet.at."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("willhaben", "AT", description="willhaben — houses and plots for sale in Austria")
def scrape_willhaben(db, max_price: float = 50000, **_):
    """willhaben — houses and plots for sale in Austria."""
    return scrape_named(db, "willhaben", max_price)


@register("wohnnet", "AT", description="Wohnnet — homes for sale in Austria")
def scrape_wohnnet(db, max_price: float = 50000, **_):
    """Wohnnet — homes for sale in Austria."""
    return scrape_named(db, "wohnnet", max_price)
