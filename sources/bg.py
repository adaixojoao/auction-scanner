"""Bulgaria: imot.bg, the country's main property portal."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, land_max_price, make_listing, make_session
from db import upsert_listing
from sources import register

# ─── imot.bg ─────────────────────────────────────────────────────────
# Houses, villas, plots and farmland from owners and agents, all over Bulgaria.
# The search form's price filter is not in the page addresses, so every page of
# each city and province is read (40 a page, newest first) and the price is
# checked here. Pages are windows-1251. The seller's phone number at the end of
# each text is cut off: no personal data is stored.

IMOT = "https://www.imot.bg"
IMOT_KINDS = {"kashta": "house", "vila": "villa", "partsel": "terreno", "zemedelska-zemya": "terreno"}
IMOT_AREAS = [f"{kind}-{place}" for place in (
    "blagoevgrad", "burgas", "dobrich", "gabrovo", "haskovo", "kardzhali", "kyustendil", "lovech", "montana",
    "pazardzhik", "pernik", "pleven", "plovdiv", "razgrad", "ruse", "shumen", "silistra", "sliven", "smolyan",
    "sofiya", "stara-zagora", "targovishte", "varna", "veliko-tarnovo", "vidin", "vratsa", "yambol")
    for kind in ("oblast", "grad")]
IMOT_MAX_PAGES = 40
IMOT_PAUSE = 0.8

_PRICE = re.compile(r"([\d\s ]{2,12})\s*€")
_AREA = re.compile(r"([\d\s]+)\s*кв\.м")
_YARD = re.compile(r"двор\s*([\d\s]+)\s*кв\.м")
_PHONE = re.compile(r",?\s*тел\.?:?\s*[\d\s+/-]{6,}.*$")
_ID = re.compile(r"obiava-([0-9a-z]+)-")


def _number(text: str | None) -> float | None:
    digits = re.sub(r"\D", "", text or "")
    return float(digits) if digits else None


def imot_cards(page_html: str) -> tuple[list[dict], int]:
    """(the cards with a price in euros, how many ads the page had)."""
    soup = BeautifulSoup(page_html, "html.parser")
    items = soup.select("div.item")
    cards = []
    for item in items:
        link = item.select_one("a.title")
        price = item.select_one("div.price")
        if not link or not price:
            continue
        found = _ID.search(link.get("href") or "")
        euros = _PRICE.search(price.get_text(" ", strip=True))
        if not found or not euros:
            continue                            # "price on request" or another currency
        where = link.find("location")
        place = where.get_text(" ", strip=True) if where else ""
        if where:
            where.extract()
        info = item.select_one("div.info")
        text = _PHONE.sub("", info.get_text(" ", strip=True)) if info else ""
        img = item.select_one("img.pic")
        src = img.get("src") if img else None
        cards.append({
            "id": found.group(1), "title": link.get_text(" ", strip=True), "place": place,
            "url": "https:" + link["href"] if link["href"].startswith("//") else link["href"],
            "price": _number(euros.group(1)), "text": text,
            "image": "https:" + src if src and src.startswith("//") else src,
        })
    return cards, len(items)


def _town_and_region(place: str) -> tuple[str | None, str | None]:
    """"с. Скорците, област Габрово" → ("Скорците", "Габрово"); "гр. Варна" → ("Варна", "Варна")."""
    parts = [p.strip() for p in place.split(",") if p.strip()]
    town = re.sub(r"^(?:гр|с|к\.к|м-т|вилна зона)\.?\s*", "", parts[0]) if parts else None
    region = next((re.sub(r"^област\s*", "", p) for p in parts[1:] if p.startswith("област")), None)
    return town or None, region or (town if parts and parts[0].startswith("гр") else None)


def parse_imot(card: dict, tipo: str) -> dict:
    town, region = _town_and_region(card["place"])
    built = _AREA.search(card["text"])
    yard = _YARD.search(card["text"])
    area = _number(yard.group(1)) if tipo == "terreno" and yard else _number(built.group(1)) if built else None
    description = card["text"] + (f" · Двор {yard.group(1).strip()} кв.м" if yard and tipo != "terreno" else "")
    return make_listing(
        "imot", card["id"], "BG", title=f"{card['title']}, {card['place']}".strip(", "), description=description,
        tipo=tipo, area_m2=area, price=card["price"], min_price=card["price"], district=region, concelho=town,
        url=card["url"], image_url=card["image"],
    )


@register("imot", "BG", description="imot.bg — houses, villas, plots and farmland all over Bulgaria")
def scrape_imot(db, max_price: float = 50000, config: dict | None = None, **_):
    """imot.bg — houses, villas, plots and farmland all over Bulgaria (owners and agents)."""
    session = make_session(timeout=30)
    total = 0
    for area in IMOT_AREAS:
        for slug, tipo in IMOT_KINDS.items():
            for page in range(1, IMOT_MAX_PAGES + 1):
                url = f"{IMOT}/obiavi/prodazhbi/{area}/{slug}" + (f"/p-{page}" if page > 1 else "")
                try:
                    resp = session.get(url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001 — one area failing is not the source failing
                    if total == 0 and area == IMOT_AREAS[0] and page == 1:
                        raise
                    LOG.info(f"imot.bg {area} {slug} p{page}: {type(e).__name__}")
                    break
                cards, ads = imot_cards(resp.content.decode("windows-1251", "replace"))
                for card in cards:
                    if card["price"] and card["price"] <= (land_max_price(config, max_price) if tipo == "terreno"
                                                           else max_price):
                        upsert_listing(db, parse_imot(card, tipo))
                        total += 1
                db.commit()
                if ads < 30:
                    break
                time.sleep(IMOT_PAUSE)
    LOG.info(f"imot.bg: {total} listings")
    return total
