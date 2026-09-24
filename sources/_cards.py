"""
Generic "listing cards" scraper, shared by the bank-portal and auction-house
sites whose search page is a paginated grid of cards.

Those sites were each a 50-line copy of the same loop; now each is a CardSite
spec. The selectors for most of them are best guesses that have not been
confirmed against the live pages — watch the health page (/health) and fix the
spec, not the loop, when one reports 0 listings.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from common import (LOG, find_price, make_listing, make_session, parse_date_dmy,
                    parse_price, safe_url, stable_id)
from db import upsert_listing


@dataclass
class CardSite:
    source: str
    country: str
    base: str
    path: str
    card_selector: str
    title_selector: str = "h2,h3,.title"
    price_selector: str | None = None        # None: first €-amount in the card text
    location_selector: str | None = None
    location_field: str = "concelho"
    area_selector: str | None = None
    date_selector: str | None = None
    page_param: str | None = "page"          # None: the site is a single page
    params: dict = field(default_factory=dict)  # "{max_price}" is substituted
    max_pages: int = 20
    min_cards: int = 6                       # a shorter page is the last page
    delay: float = 1.0
    description: str | Callable = ""
    tipo: str = "imovel"
    price_is_min_price: bool = False
    id_prefix: str | None = None              # only to keep IDs from before CardSite
    id_pattern: str | None = None             # regex on the URL; group 1 is the site's ID


def listing_id_from_url(url: str) -> str:
    """Last numeric path segment ("/imoveis/lisboa/12345" → "12345"), else a hash.

    The old per-site regex took the *first* number, so "/2024/lisboa/12345" gave
    every listing the ID "2024" and they overwrote each other.
    """
    nums = re.findall(r"(?<![^/])(\d+)(?=/|$)", urlsplit(url).path)
    return nums[-1] if nums else stable_id(url)


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def scrape_cards(db, site: CardSite, max_price: float) -> int:
    session = make_session()
    seen: set[str] = set()
    total = 0
    pages = range(1, site.max_pages + 1) if site.page_param else [None]

    for page in pages:
        params = {k: (int(max_price) if v == "{max_price}" else v) for k, v in site.params.items()}
        if page is not None:
            params[site.page_param] = page
        try:
            resp = session.get(site.base + site.path, params=params)
            resp.raise_for_status()
        except Exception:
            if page in (None, 1):
                raise  # first page failing = site down or moved: let the health log show it
            LOG.warning(f"{site.source}: page {page} failed, keeping earlier pages")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(site.card_selector)
        if not cards:
            break

        for card in cards:
            # Some sites make the whole card one <a>; select_one only looks inside it.
            link = card if card.name == "a" and card.get("href") else card.select_one("a[href]")
            if not link:
                continue
            url = safe_url(link.get("href"), site.base)
            if not url:
                continue
            m = re.search(site.id_pattern, url) if site.id_pattern else None
            eid = m.group(1) if m else listing_id_from_url(url)
            if eid in seen:  # nested card selectors hit the same card twice
                continue
            seen.add(eid)

            if site.price_selector:
                price = parse_price(_text(card.select_one(site.price_selector)))
            else:
                price = find_price(_text(card))
            if price and price > max_price:
                continue

            title = _text(card.select_one(site.title_selector))[:200] or f"{site.source} #{eid}"
            location = _text(card.select_one(site.location_selector)) if site.location_selector else None
            area = None
            if site.area_selector:
                m = re.search(r"(\d+)", _text(card.select_one(site.area_selector)))
                area = float(m.group(1)) if m else None
            date_end = parse_date_dmy(_text(card.select_one(site.date_selector))) if site.date_selector else None
            desc = site.description(card) if callable(site.description) else site.description

            fields = {
                "title": title, "description": desc or None, "tipo": site.tipo,
                "area_m2": area, "price": price, "url": url, "date_end": date_end,
                "min_price": price if site.price_is_min_price else None,
                site.location_field: location,
            }
            upsert_listing(db, make_listing(site.source, eid, site.country,
                                            id_prefix=site.id_prefix, **fields))
            total += 1

        db.commit()
        if page is None or len(cards) < site.min_cards:
            break
        time.sleep(site.delay)
    return total
