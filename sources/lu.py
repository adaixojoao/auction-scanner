"""Luxembourg: atHome.lu and Wortimmo."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("athome", "LU", description="atHome — homes and land for sale in Luxembourg and nearby")
def scrape_athome(db, max_price: float = 50000, **_):
    """atHome — homes and land for sale in Luxembourg and nearby."""
    return scrape_named(db, "athome", max_price)


@register("wortimmo", "LU", description="Wortimmo — plots for sale in Luxembourg and the neighbouring border")
def scrape_wortimmo(db, max_price: float = 50000, **_):
    """Wortimmo — plots for sale in Luxembourg and just over the German border."""
    return scrape_named(db, "wortimmo", max_price)
