"""Poland: licytacje.komornik.pl bailiff auctions."""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, safe_url
from db import upsert_listing
from sources import register

PL_BASE = "https://licytacje.komornik.pl"
_LICYT_RE = re.compile(r"/licytacje/(\d+)/([^/?#]*)", re.I)


def parse_poland_list(html: str) -> list[dict]:
    """SSR Vue page: each sale is an <a href="/licytacje/{id}/slug">."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="/licytacje/"]'):
        href = a.get("href") or ""
        m = _LICYT_RE.search(href)
        if not m or m.group(1) in seen:
            continue
        eid = m.group(1)
        seen.add(eid)
        slug = (m.group(2) or "").replace("-", " ").strip()
        text = a.get_text(" ", strip=True)
        # Prefer the slug for a readable title; the link text is noisy chips.
        title = (slug[:1].upper() + slug[1:]) if slug else (text[:200] or f"Polish auction {eid}")
        title = re.sub(r"\s+", " ", title)[:200]
        url = safe_url(href, PL_BASE + "/") or urljoin(PL_BASE + "/", href)
        out.append({
            "eid": eid,
            "title": title,
            "description": text[:500] or None,
            "price": find_price(text),   # PLN has no €; usually None
            "url": url,
            "date_end": parse_date_dmy(text),
        })
    return out


@register("poland", "PL")
def scrape_poland(db, max_price: float = 50000, **_):
    """licytacje.komornik.pl — Polish bailiff auctions (Nuxt SSR).

    Prices are in PLN; only amounts marked € are read, so most rows arrive
    without a price. Pagination is `?page=N` on /nieruchomosci.
    """
    session = make_session()
    total_scraped = 0
    seen: set[str] = set()

    for page in range(1, 20):
        try:
            resp = session.get(f"{PL_BASE}/nieruchomosci", params={"page": page})
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            break

        rows = parse_poland_list(resp.text)
        if not rows:
            break
        new = 0
        for row in rows:
            if row["eid"] in seen:
                continue
            seen.add(row["eid"])
            if row["price"] and row["price"] > max_price:
                continue
            upsert_listing(db, make_listing(
                "poland", row["eid"], "PL",
                title=row["title"], description=row["description"],
                tipo="imovel", price=row["price"], min_price=row["price"],
                url=row["url"], date_end=row["date_end"],
            ))
            total_scraped += 1
            new += 1

        db.commit()
        LOG.info(f"  Poland page {page}: {total_scraped} total (+{new})")
        if new == 0:
            break
        time.sleep(1)
    return total_scraped
