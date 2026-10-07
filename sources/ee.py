"""Estonia: kv.ee."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("kvee", "EE", description="KV.ee — homes for sale in Estonia")
def scrape_kvee(db, max_price: float = 50000, **_):
    """KV.ee — homes for sale in Estonia."""
    return scrape_named(db, "kvee", max_price)
