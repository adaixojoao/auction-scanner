"""Germany: zvg-portal.de, justiz-auktion.de, zwangsversteigerung.de."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, parse_price, safe_url, stable_id
from db import upsert_listing
from sources import register
from sources._cards import listing_id_from_url


@register("zvg", "DE")
def scrape_zvg(db, max_price: float = 50000, **_):
    """zvg-portal.de — German court forced auctions (Zwangsversteigerungen)."""
    session = make_session()
    resp = session.get("https://www.zvg-portal.de/index.php?button=Suchen&all=1")
    resp.raise_for_status()

    rows = BeautifulSoup(resp.text, "html.parser").select("table.table-bordered tr")[1:]
    LOG.info(f"  ZVG: {len(rows)} rows found")

    total_scraped = 0
    for row in rows:
        cells = row.select("td")
        if len(cells) < 5:
            continue
        aktenzeichen = cells[0].get_text(strip=True)
        objekt = cells[1].get_text(strip=True)
        ort = cells[2].get_text(strip=True)
        termin = cells[3].get_text(strip=True)
        verkehrswert = cells[4].get_text(strip=True)

        price = parse_price(verkehrswert)
        if price and price > max_price:
            continue
        link_tag = row.select_one("a[href]")
        eid = re.sub(r"[^A-Za-z0-9]", "", aktenzeichen)[:50] or stable_id(objekt, ort)

        upsert_listing(db, make_listing(
            "zvg", eid, "DE",
            title=f"{objekt} — {ort}"[:200],
            description=f"Aktenzeichen: {aktenzeichen}. {objekt}. Verkehrswert: {verkehrswert}",
            tipo="imovel", price=price, district=ort,
            url=link_tag["href"] if link_tag else None, base_url="https://www.zvg-portal.de/",
            date_end=parse_date_dmy(termin),
        ))
        total_scraped += 1
    return total_scraped


@register("justiz_auktion", "DE")
def scrape_justiz_auktion(db, max_price: float = 50000, **_):
    """justiz-auktion.de — seized/surplus assets from German courts and authorities."""
    session = make_session()
    base = "https://www.justiz-auktion.de/"
    total_scraped = 0

    for page in range(1, 15):
        try:
            resp = session.get(f"{base}categories.php", params={"parent": 1, "page": page})
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.article-item, div.lot-item, tr.lot") or soup.select("table.auctions tr")[1:]
        if not items:
            break

        for item_el in items:
            link = item_el.select_one("a[href]")
            if not link:
                continue
            title = link.get_text(strip=True)[:200]
            href = link["href"]
            eid_m = re.search(r'id[=_](\d+)', href)
            eid = eid_m.group(1) if eid_m else stable_id(href, title)
            price = find_price(item_el.get_text(" "))
            if price and price > max_price:
                continue
            upsert_listing(db, make_listing(
                "justiz_auktion", eid, "DE", id_prefix="justiz",
                title=title, description=item_el.get_text(" ", strip=True)[:500],
                tipo="imovel", price=price, url=href, base_url=base,
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  Justiz-Auktion page {page}: {total_scraped} total")
        time.sleep(1)
    return total_scraped


@register("zvg_de", "DE")
def scrape_zvg_de(db, max_price: float = 100000, **_):
    """zwangsversteigerung.de — private aggregator of German forced auctions."""
    session = make_session()
    base = "https://www.zwangsversteigerung.de"
    resp = session.get(f"{base}/immobilien/", params={"verkehrswert_max": int(max_price)})
    resp.raise_for_status()
    total = 0
    seen = set()
    for card in BeautifulSoup(resp.text, "html.parser").select(
            "div.object, article, div[class*='object'], tr.result"):
        link = card.select_one("a[href]")
        url = safe_url(link.get("href"), base) if link else None
        if not url:
            continue
        eid = listing_id_from_url(url)
        if eid in seen:
            continue
        seen.add(eid)
        text = card.get_text(" ", strip=True)
        price = find_price(text)
        if price and price > max_price:
            continue
        loc_m = re.search(r"\b\d{5}\s+(\w[\w\s-]+)", text)
        upsert_listing(db, make_listing(
            "zvg_de", eid, "DE",
            title=text[:120], description="Zwangsversteigerung.de aggregator",
            tipo="imovel", price=price, district=loc_m.group(1).strip()[:60] if loc_m else None,
            url=url, date_end=parse_date_dmy(text),
        ))
        total += 1
    return total
