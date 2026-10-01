"""Germany: zvg-portal.de, justiz-auktion.de, zwangsversteigerung.de."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, parse_price, safe_url
from db import upsert_listing
from sources import register
from sources._cards import listing_id_from_url

ZVG_BASE = "https://www.zvg-portal.de/"
# Bundesland codes from the portal's land_abk select (Sept 2026).
ZVG_LANDS = (
    "bw", "by", "be", "br", "hb", "hh", "he", "mv",
    "ni", "nw", "rp", "sl", "sn", "st", "sh", "th",
)
_ZVG_EMPTY = {
    "order_by": "2", "ger_id": "0",
    "az1": "", "az2": "", "az3": "", "az4": "",
    "art": "", "obj": "", "str": "", "hnr": "",
    "plz": "", "ort": "", "ortsteil": "",
    "vtermin": "", "btermin": "",
}


def _zvg_form(land: str) -> dict:
    return {**_ZVG_EMPTY, "land_abk": land}


def parse_zvg_results(html: str, land: str) -> list[dict]:
    """One sale per showZvg link. Rows for a sale follow its Aktenzeichen row
    in the results table until the next Detailansicht link."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="showZvg"]'):
        href = a.get("href") or ""
        m = re.search(r"zvg_id=(\d+)", href)
        if not m or m.group(1) in seen:
            continue
        zid = m.group(1)
        seen.add(zid)
        rows = {}
        tr = a.find_parent("tr")
        first = True
        while tr is not None:
            if not first and tr.select_one('a[href*="showZvg"]'):
                break
            first = False
            cells = tr.select("td")
            if len(cells) >= 2:
                rows[cells[0].get_text(" ", strip=True)] = cells[1].get_text(" ", strip=True)
            tr = tr.find_next_sibling("tr")
        az = re.sub(r"\s*\(Detailansicht\).*$", "",
                    rows.get("Aktenzeichen") or a.get_text(" ", strip=True), flags=re.I).strip()
        lage = rows.get("Objekt/Lage") or rows.get("Objekt / Lage") or ""
        vw = rows.get("Verkehrswert in €") or rows.get("Verkehrswert") or ""
        termin = (rows.get("Termin") or rows.get("Versteigerungstermin")
                  or rows.get("Termin der Versteigerung") or "")
        ag = rows.get("Amtsgericht") or ""
        title = (lage or az or f"ZVG {zid}")[:200]
        if lage and az:
            title = f"{lage} — {az}"[:200]
        out.append({
            "eid": zid,
            "title": title,
            "description": f"Aktenzeichen: {az}. {ag}. {lage}. Verkehrswert: {vw}".strip(),
            "price": parse_price(vw),
            "district": (ag or land).strip()[:60] or None,
            "url": safe_url(href, ZVG_BASE),
            "date_end": parse_date_dmy(termin),
            "land": land,
        })
    return out


@register("zvg", "DE")
def scrape_zvg(db, max_price: float = 50000, **_):
    """zvg-portal.de — German court forced auctions (Zwangsversteigerungen).

    The old `?button=Suchen&all=1` GET is rejected without a Bundesland. Search
    each land with POST, then ask for all pages of that result (`&all=1`).
    """
    session = make_session()
    total_scraped = 0
    seen: set[str] = set()

    for land in ZVG_LANDS:
        form = _zvg_form(land)
        try:
            session.post(f"{ZVG_BASE}index.php?button=Suchen", data=form).raise_for_status()
            resp = session.post(f"{ZVG_BASE}index.php?button=Suchen&all=1", data=form)
            resp.raise_for_status()
        except Exception:
            if land == ZVG_LANDS[0]:
                raise
            LOG.warning(f"  ZVG: land {land} failed, continuing")
            continue

        rows = parse_zvg_results(resp.text, land)
        LOG.info(f"  ZVG {land}: {len(rows)} sales")
        for row in rows:
            if row["eid"] in seen:
                continue
            seen.add(row["eid"])
            if row["price"] and row["price"] > max_price:
                continue
            upsert_listing(db, make_listing(
                "zvg", row["eid"], "DE",
                title=row["title"], description=row["description"],
                tipo="imovel", price=row["price"], district=row["district"],
                url=row["url"], date_end=row["date_end"],
                raw_json={"land_abk": land},
            ))
            total_scraped += 1
        db.commit()
        time.sleep(0.8)
    return total_scraped


@register("justiz_auktion", "DE", default=False)
def scrape_justiz_auktion(db, max_price: float = 50000, **_):
    """justiz-auktion.de — surplus assets. No property category left (Sept 2026);
    kept runnable by name only."""
    session = make_session()
    base = "https://www.justiz-auktion.de/"
    total_scraped = 0

    for start in range(0, 200, 10):
        try:
            resp = session.get(f"{base}auction_search.php", params={"start": start})
            resp.raise_for_status()
        except Exception:
            if start == 0:
                raise
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("a.link_detail, .auktionstitel a, a[href*='auction_detail']")
        if not items:
            break

        for link in items:
            href = link.get("href") or ""
            title = link.get_text(strip=True)[:200]
            if not href or not title:
                continue
            eid_m = re.search(r"(\d{4,})", href) or re.search(r"(\d{4,})", title)
            eid = eid_m.group(1) if eid_m else None
            if not eid:
                continue
            parent = link.find_parent(["div", "article", "li", "tr"]) or link
            price = find_price(parent.get_text(" "))
            if price and price > max_price:
                continue
            upsert_listing(db, make_listing(
                "justiz_auktion", eid, "DE", id_prefix="justiz",
                title=title, description=parent.get_text(" ", strip=True)[:500],
                tipo="imovel", price=price, url=href, base_url=base,
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  Justiz-Auktion start={start}: {total_scraped} total")
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
