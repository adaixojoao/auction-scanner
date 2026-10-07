"""Finland: Etuovi."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("etuovi", "FI", description="Etuovi — homes and plots for sale in Finland")
def scrape_etuovi(db, max_price: float = 50000, **_):
    """Etuovi — homes and plots for sale in Finland."""
    return scrape_named(db, "etuovi", max_price)
