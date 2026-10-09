"""United Kingdom: Clive Emson property auctions."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, make_listing, make_session, safe_url
from costs import ECB_PER_EUR, ECB_RATE_DATE, euros_from
from db import upsert_listing
from sources import register

CLIVE_PAGES = (
    "https://www.cliveemson.co.uk/properties/",
    "https://www.cliveemson.co.uk/properties/land-auctions/",
)
# A guide of £1 is how this auction house opens a lot with no real guide.
MIN_GUIDE_GBP = 1000
_ACRE_M2 = 4046.86
_LOT = re.compile(r"/properties/(\d+)/(\d+)/")
_POUNDS = re.compile(r"£\s*([\d,]+)")
_ACRES = re.compile(r"(\d+(?:[.,]\d+)?)\s*acres?\b", re.I)
_LANDISH = re.compile(r"\b(?:land|woodland|acre|plot|paddock|site)\b", re.I)
_GONE = re.compile(r"\b(?:sold|withdrawn)\b", re.I)


def parse_clive_emson(html: str) -> list[dict]:
    """Open lots with a real guide price. The price is euros at the ECB rate."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = _LOT.search(a["href"])
        if not m:
            continue
        eid = f"{m.group(1)}-{m.group(2)}"
        if eid in seen:
            continue
        text = a.get_text(" ", strip=True)
        if _GONE.search(text):
            continue
        pounds = _POUNDS.search(text)
        if not pounds:
            continue
        gbp = float(pounds.group(1).replace(",", ""))
        if gbp < MIN_GUIDE_GBP:
            continue
        eur = euros_from(gbp, "GBP")
        if not eur:
            continue
        heading = a.find(class_="LotHeading")
        place = a.find(class_="LotLocation")
        title = heading.get_text(" ", strip=True) if heading else ""
        where = place.get_text(" ", strip=True) if place else ""
        town, _, county = where.partition(" - ")
        acres = _ACRES.search(title)
        area = round(float(acres.group(1).replace(",", ".")) * _ACRE_M2) if acres else None
        seen.add(eid)
        guide = f"£{gbp:,.0f}"
        out.append({
            "external_id": eid,
            "title": title or f"Lot {m.group(2)}",
            "description": (
                f"Guide price {guide} plus fees, about €{eur:,.0f} at the ECB rate of "
                f"{ECB_RATE_DATE} (£{ECB_PER_EUR['GBP']} per euro). "
                "A guide is not the price the lot will fetch."
            ),
            "tipo": "terreno" if _LANDISH.search(title) else "house",
            "area_m2": area,
            "price": eur,
            "district": county.strip() or None,
            "concelho": town.strip() or None,
            "url": safe_url(a["href"], "https://www.cliveemson.co.uk") or "",
        })
    return [row for row in out if row["url"]]


@register("cliveemson", "GB", description="Clive Emson — property auctions in England, at guide prices")
def scrape_cliveemson(db, max_price: float = 50000, **_):
    """Clive Emson — property auctions in England. The guide is stored in euros."""
    session = make_session(timeout=25)
    total = 0
    seen: set[str] = set()
    first = True
    for url in CLIVE_PAGES:
        try:
            resp = session.get(url)
            resp.raise_for_status()
        except Exception:
            if first:
                raise
            LOG.info(f"cliveemson: skipped a later page ({url})")
            continue
        first = False
        kept = 0
        for lot in parse_clive_emson(resp.text):
            if lot["external_id"] in seen or lot["price"] > max_price:
                continue
            seen.add(lot["external_id"])
            upsert_listing(db, make_listing(
                "cliveemson", lot["external_id"], "GB",
                title=lot["title"], description=lot["description"], tipo=lot["tipo"],
                area_m2=lot["area_m2"], price=lot["price"], district=lot["district"],
                concelho=lot["concelho"], url=lot["url"],
            ))
            kept += 1
        if kept:
            total += kept
            db.commit()
            time.sleep(0.8)
    LOG.info(f"cliveemson: {total} listings")
    return total
