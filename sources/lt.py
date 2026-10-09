"""Lithuania: Aruodas, the country's main property portal."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, safe_url
from db import upsert_listing
from sources import register

# Houses, garden houses and plots. The price ceiling is the site's own
# (FPriceMax); anything still above the scan budget is left out here.
# "10.31 a" is ares (1 are = 100 m²). A starred-ad tooltip quotes 0.99 € a day,
# so the price is the list-item-price span, not the first euro figure on the card.

ARUODAS = "https://www.aruodas.lt"
ARUODAS_SECTIONS = (
    ("/gyvenamieji-namai/", "house"),
    ("/sodo-namai/", "house"),
    ("/sklypai/", "terreno"),
)
ARUODAS_MAX_PAGES = 4

_AREA = re.compile(r"([\d]+(?:[.,]\d+)?)\s*(m²|m2|ha|a)\b", re.I)
_SKIP_IMG = ("nophoto", "sprite", ".svg", "1x1")


def _area_m2(text: str) -> float | None:
    found = _AREA.search(text or "")
    if not found:
        return None
    number = float(found.group(1).replace(",", "."))
    unit = found.group(2).lower()
    if unit == "a":
        return number * 100
    if unit == "ha":
        return number * 10000
    return number


def _image(card) -> str | None:
    img = card.find("img")
    if img is None:
        return None
    src = img.get("data-src") or img.get("src") or ""
    if not src.startswith("http") or any(mark in src for mark in _SKIP_IMG):
        return None
    return src


def parse_aruodas(page_html: str, tipo: str) -> list[dict]:
    """Cards on one Aruodas results page."""
    if not page_html:
        return []
    soup = BeautifulSoup(page_html, "html.parser")
    out = []
    for card in soup.select("div.list-row-v2"):
        eid = (card.get("data-uid") or "").strip()
        link = card.select_one("h3 a[href]") or card.select_one("a[href]")
        price_el = card.select_one("span.list-item-price-v2")
        if not eid or link is None or price_el is None:
            continue
        url = safe_url(link.get("href"), ARUODAS)
        if not url or re.search(r"nuomai|/rent", url, re.I):
            continue
        price = find_price(price_el.get_text(" ", strip=True))
        if not price:
            continue
        title = link.get_text(" ", strip=True)
        parts = [p.strip() for p in title.split(",") if p.strip()]
        area_el = card.select_one("[data-param-label='Plotas'] .list-detail-value-v2")
        area = _area_m2(area_el.get_text(" ", strip=True) if area_el else "")
        out.append({
            "id": eid, "title": title[:200], "price": price, "area_m2": area,
            "district": parts[0] if parts else None,
            "concelho": parts[1] if len(parts) > 1 else None,
            "url": url.split("?")[0], "image": _image(card), "tipo": tipo,
        })
    return out


@register("aruodas", "LT", description="Aruodas — houses, garden houses and plots all over Lithuania")
def scrape_aruodas(db, max_price: float = 50000, **_):
    """Aruodas — houses, garden houses and plots all over Lithuania."""
    session = make_session(timeout=30)
    total = 0
    seen: set[str] = set()
    for index, (path, tipo) in enumerate(ARUODAS_SECTIONS):
        for page in range(1, ARUODAS_MAX_PAGES + 1):
            url = ARUODAS + (path if page == 1 else path.rstrip("/") + f"/puslapis/{page}/")
            try:
                resp = session.get(url, params={"FPriceMax": int(max_price)})
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one section failing is not the source failing
                if total == 0 and page == 1 and index == 0:
                    raise
                LOG.info(f"Aruodas {path} p{page}: {type(e).__name__}")
                break
            cards = parse_aruodas(resp.text, tipo)
            if not cards:
                break
            kept = 0
            for card in cards:
                if card["id"] in seen or card["price"] > max_price:
                    continue
                seen.add(card["id"])
                upsert_listing(db, make_listing(
                    "aruodas", card["id"], "LT", title=card["title"], description=card["title"],
                    tipo=card["tipo"], area_m2=card["area_m2"], price=card["price"],
                    min_price=card["price"], district=card["district"], concelho=card["concelho"],
                    url=card["url"], image_url=card["image"]))
                kept += 1
            total += kept
            db.commit()
            if len(cards) < 15:
                break
            time.sleep(0.8)
    LOG.info(f"Aruodas: {total} listings")
    return total
