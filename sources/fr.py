"""France: licitor.com, encheres-publiques.com."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session
from db import upsert_listing
from sources import register
from sources._cards import CardSite, scrape_cards

LICITOR_BASE = "https://www.licitor.com"


@register("france", "FR")
def scrape_france(db, max_price: float = 50000, **_):
    """licitor.com — French judicial auctions, per tribunal."""
    session = make_session(timeout=15)
    resp = session.get(f"{LICITOR_BASE}/ventes-aux-encheres-immobilieres/france.html")
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tribunal_links = []
    for a in soup.select("a[href*='/ventes-judiciaires-immobilieres/']"):
        href = a.get("href", "")
        if href and ".html" in href:
            tribunal_links.append(href if href.startswith("http") else f"{LICITOR_BASE}{href}")
    LOG.info(f"  France: {len(tribunal_links)} tribunal pages found")

    total_scraped = 0
    for i, trib_url in enumerate(tribunal_links[:60]):  # limit to 60 tribunals
        try:
            resp = session.get(trib_url)
            resp.raise_for_status()
        except Exception as e:
            LOG.debug(f"France tribunal error: {e}")
            continue

        count = 0
        for a in BeautifulSoup(resp.text, "html.parser").select(
                "a[href*='/annonce/'], a[href*='/vente-aux-encheres']"):
            href = a.get("href", "")
            title = a.get_text(strip=True)[:120]
            if not href or not title or len(title) < 5:
                continue
            m_id = re.search(r"/(\d+)\.html", href)
            eid = m_id.group(1) if m_id else href.strip("/").split("/")[-1].replace(".html", "")
            price = find_price(a.parent.get_text(" ", strip=True)) if a.parent else None
            if price and price > max_price:
                continue
            upsert_listing(db, make_listing(
                "france", eid, "FR", title=title, tipo="immobilier", price=price,
                url=href, base_url=LICITOR_BASE,
            ))
            count += 1

        if count:
            total_scraped += count
            db.commit()
            LOG.info(f"  France tribunal {i+1}/{len(tribunal_links)}: {count} lots")
        time.sleep(0.5)
    return total_scraped


ENCHERES_PUBLIQUES = CardSite(
    source="encheres_publiques", country="FR", base="https://www.encheres-publiques.com",
    path="/immobilier",
    card_selector="div.bien, article.property, div[class*='bien'], div[class*='lot']",
    title_selector="h2,h3,.title,.bien-title",
    location_selector=".location,.ville,.commune",
    date_selector=".date,.date-vente",
    params={"prix_max": "{max_price}"}, max_pages=19, delay=0.8,
    description="Enchères Publiques France", tipo="immobilier", id_prefix="encheres_pub",
)


@register("encheres_publiques", "FR")
def scrape_encheres_publiques(db, max_price: float = 100000, **_):
    """encheres-publiques.com — French judicial property auctions."""
    return scrape_cards(db, ENCHERES_PUBLIQUES, max_price)
