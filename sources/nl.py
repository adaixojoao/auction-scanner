"""Netherlands: openbareverkoop.nl, veilingnotaris.nl, veilingbiljet.nl."""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from common import find_price, make_listing, make_session, parse_date_dmy, parse_price, safe_url
from db import upsert_listing
from sources import register
from sources._cards import listing_id_from_url


def netherlands_listing(obj: dict, base: str = "https://www.openbareverkoop.nl") -> dict | None:
    eid = str(obj.get("id", "") or "")
    if not eid:
        return None
    # NL auctions don't publish the property price upfront;
    # "veilingkosten" is the auction FEE, not the property value.
    price = None
    for field in ("inzet", "afslag"):
        val = obj.get(field, "")
        if val:
            price = parse_price(str(val).replace("€", ""))
            break
    title = obj.get("kavelNaam", "")
    wtype = obj.get("woningtype", "")
    if wtype:
        title = f"{title} ({wtype})"
    vtype = obj.get("veilingwijze", "")
    return make_listing(
        "netherlands", eid, "NL",
        title=title,
        description=f"Executieveiling ({vtype})" if vtype else "Executieveiling",
        tipo="vastgoed", price=price,
        url=obj.get("url", ""), image_url=obj.get("image", ""), base_url=base,
        raw_json=json.dumps(obj, ensure_ascii=False, default=str),
    )


@register("netherlands", "NL")
def scrape_netherlands(db, max_price: float = 50000, **_):
    """openbareverkoop.nl — Dutch public (notarial) property auctions (JSON)."""
    session = make_session(timeout=30)
    base = "https://www.openbareverkoop.nl"
    session.get(f"{base}/kavels?view=resultaten")
    resp = session.post(f"{base}/kavels/searchresults",
                        data={"text": "", "view": "", "periode": "alles", "woningtype": ""})
    resp.raise_for_status()
    data = resp.json()

    total_scraped = 0
    for zitting in data.get("results", []):
        for opr in zitting.get("objectenPerRegio", []):
            for obj in opr.get("objects", []):
                listing = netherlands_listing(obj, base)
                if listing:
                    upsert_listing(db, listing)
                    total_scraped += 1
    return total_scraped


@register("veilingnotaris", "NL")
def scrape_veilingnotaris(db, max_price: float = 50000, **_):
    """veilingnotaris.nl — Dutch execution auctions."""
    session = make_session(timeout=15)
    base = "https://veilingnotaris.nl"
    resp = session.get(f"{base}/veilingen/")
    resp.raise_for_status()

    total_scraped = 0
    seen = set()
    for a in BeautifulSoup(resp.text, "html.parser").select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"/veilingen/(\d+)/([^/]+)/", href)
        if not m or m.group(1) in seen:
            continue
        eid, slug = m.group(1), m.group(2)
        seen.add(eid)
        text = a.get_text(" ", strip=True)
        address = slug.replace("_", " ").replace("-", " ").title()
        wtype = ""
        for t in ["Appartement", "Woonhuis", "Tussenwoning", "Hoekwoning", "Bovenwoning",
                  "Twee-onder-een-kap", "Vrijstaand", "Bedrijfspand", "Winkel", "Kantoor"]:
            if t.lower() in text.lower():
                wtype = t
                break
        upsert_listing(db, make_listing(
            "veilingnotaris", eid, "NL",
            title=f"{address} ({wtype})" if wtype else address,
            tipo="vastgoed", url=href, base_url=base,
        ))
        total_scraped += 1
    return total_scraped


@register("veilingbiljet", "NL")
def scrape_veilingbiljet(db, max_price: float = 100000, **_):
    """veilingbiljet.nl — Dutch foreclosure auctions."""
    session = make_session()
    base = "https://www.veilingbiljet.nl"
    resp = session.get(f"{base}/objecten/")
    resp.raise_for_status()
    total = 0
    seen = set()
    for card in BeautifulSoup(resp.text, "html.parser").select(
            "div.object, article, div[class*='object'], li[class*='object']"):
        link = card.select_one("a[href]")
        url = safe_url(link.get("href"), base) if link else None
        if not url:
            continue
        eid = listing_id_from_url(url)
        if eid in seen:
            continue
        seen.add(eid)
        title_el = card.select_one("h2,h3,.title,.object-title")
        price = find_price(card.get_text(" "))
        if price and price > max_price:
            continue
        loc_el = card.select_one(".location,.city,.plaats")
        date_el = card.select_one(".date,.veilingdatum")
        upsert_listing(db, make_listing(
            "veilingbiljet", eid, "NL",
            title=title_el.get_text(strip=True)[:200] if title_el else f"Veilingbiljet #{eid}",
            description="Veilingbiljet.nl executieveiling", tipo="vastgoed",
            price=price, concelho=loc_el.get_text(strip=True) if loc_el else None,
            url=url, date_end=parse_date_dmy(date_el.get_text()) if date_el else None,
        ))
        total += 1
    return total
