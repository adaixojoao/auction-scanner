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


# "258.000,00 €", "4.800 EUR", "19.400,00" — German amounts, read one by one.
_ZVG_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{2})(?![\d.,])")
# Text that is not the value: the deposit and bank details after it, the
# accessories already counted inside a lot ("davon entfällt auf Zubehör: 100 €"),
# land-register sheet numbers ("Blatt 649") and lot labels ("Lfd. Nr. 2", "zu 3:").
_ZVG_NOT_VALUE = [
    re.compile(r"Bietsicherheit.*$", re.I | re.S),
    re.compile(r"davon\s+entf\S*\s+auf\s+Zubeh\S*\s*:?\s*[\d.,]+\s*(?:€|EUR)?", re.I),
    re.compile(r"Blatt\s+\d+", re.I),
    re.compile(r"(?:lfd\.?\s*Nr\.?|zu)\s*\d+\s*:?", re.I),
]


def zvg_value(text: str) -> float | None:
    """Verkehrswert of the whole sale: the lots added up.

    "Grundbuch ... Blatt 649 lfd.Nr. 1: 258.000,00 €" → 258000, not 649;
    "zu 1: 235.000 EUR zu 2: 4.800 EUR" → 239800, not 1.
    """
    s = (text or "").replace(" ", " ")
    for pattern in _ZVG_NOT_VALUE:
        s = pattern.sub(" ", s)
    amounts = [float(a.replace(".", "").replace(",", ".")) for a in _ZVG_AMOUNT.findall(s)]
    amounts = [a for a in amounts if a > 0]
    if amounts:
        return sum(amounts)
    return parse_price(s)


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
            "price": zvg_value(vw),
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


@register("zvg_de", "DE", default=False)
def scrape_zvg_de(db, max_price: float = 100000, **_):
    """zwangsversteigerung.de — private aggregator of German forced auctions.
    Never matched its pages (paged results, members-only parts); its lots are
    the court sales zvg already reads from zvg-portal.de (2026-10-24)."""
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


@register("kleinanzeigen", "DE", description="Kleinanzeigen — houses and plots for sale across Germany")
def scrape_kleinanzeigen(db, max_price: float = 50000, **_):
    """Kleinanzeigen — houses and plots for sale across Germany."""
    from sources._market import scrape_named
    return scrape_named(db, "kleinanzeigen", max_price)


@register("immowelt", "DE", description="Immowelt — houses for sale across Germany")
def scrape_immowelt(db, max_price: float = 50000, **_):
    """Immowelt — houses for sale across Germany."""
    from sources._market import scrape_named
    return scrape_named(db, "immowelt", max_price)




# The DGA group's shared object list: one page with every lot of DGA, SGA, NDGA,
# WDGA, DIIA and P&B (voluntary auctions, often for insolvency administrators,
# with low starting limits). Every lot is on the page; filtering is client-side.
GOAUKTION = "https://www.ndga.de"
GOAUKTION_LIST = f"{GOAUKTION}/immobilie-ersteigern/immobilie-suchen-und-finden.html"
GOAUKTION_HOUSES = {"D": "DGA", "S": "SGA", "N": "NDGA", "W": "WDGA", "I": "DIIA", "P": "P&B"}
# Icon names on each lot → tipo.
GOAUKTION_TYPES = {"EFHZFH": "moradia", "MFHWGH": "predio", "ETWTE": "apartamento",
                   "GRDBG": "terreno", "GE": "comercial", "Special": "outro"}
GOAUKTION_STATUS = {"Aktuell", "Nachverkauf"}   # Nachverkauf: unsold, still buyable at the limit


def goauktion_listing(item) -> dict | None:
    """One lot (div[data-jplist-item]) of the DGA group's list as a listing."""
    link = item.select_one(".katalogpos a[href]")
    lot = item.select_one(".katalogpos p")
    status = item.select_one(".verkaufsstatus p")
    if not link or not lot or (status and status.get_text(strip=True) not in GOAUKTION_STATUS):
        return None
    lot_no = lot.get_text(strip=True)
    limit = item.select_one(".auktionLimitAmoutForSorting")
    price = float(limit.get_text(strip=True)) if limit and limit.get_text(strip=True).isdigit() else None
    addr = item.select_one(".objekt .addr-list a")
    lines = [t.strip() for t in addr.stripped_strings] if addr else []
    city = re.sub(r"^\d{5}\s*", "", lines[-1]) if lines else None
    hidden = [s.get("class", [""])[0] for s in item.select(":scope > span[style*='display:none']")]
    icon = item.select_one(".ortIco img")
    kind = (icon.get("class") or [""])[0] if icon else ""
    pin = item.select_one(".ortIco a[data-lat]")
    raw = {"lot": lot_no, "house": GOAUKTION_HOUSES.get(lot_no[:1]),
           "status": status.get_text(strip=True) if status else None,
           "rented": "vermietet" in hidden, "object_type": kind}
    if pin:
        raw.update(lat=pin.get("data-lat"), lon=pin.get("data-lon"))
    img = item.select_one(".image-block img[src]")
    return make_listing(
        "goauktion_de", lot_no, "DE",
        title=f"{raw['house'] or 'Auktion'} {lot_no}: {' · '.join(lines)}"[:200],
        description="Freiwillige Grundstücksauktion (DGA-Gruppe); price is the Auktionslimit (starting bid)",
        tipo=GOAUKTION_TYPES.get(kind, "imovel"), price=price, min_price=price,
        district=hidden[0] if hidden else None, concelho=city,
        url=safe_url(link["href"], GOAUKTION), image_url=safe_url(img["src"], GOAUKTION) if img else None,
        raw_json=raw,
    )


@register("goauktion_de", "DE", kind="aggregator", access="public_html", docs_url=GOAUKTION_LIST)
def scrape_goauktion(db, max_price: float = 50000, **_):
    """DGA / SGA / NDGA / WDGA property auctions — low-limit lots, often from insolvency administrators."""
    resp = make_session(timeout=60).get(GOAUKTION_LIST)
    resp.raise_for_status()
    total = 0
    for item in BeautifulSoup(resp.text, "html.parser").select("[data-jplist-item]"):
        row = goauktion_listing(item)
        if not row or (row["price"] and row["price"] > max_price):
            continue
        upsert_listing(db, row)
        total += 1
    db.commit()
    LOG.info(f"goauktion: {total} listings")
    return total


# ─── BImA (the Federal Real Estate Agency) ─────────────────────────
# The federal state sells former barracks, staff houses, building plots and
# forest. Its portal's search API is public; nearly every offer is a flat to
# rent, so only the purchases (commercialization_type BUY) are kept. A price
# of €1 is a placeholder for a bidding procedure, not a price.
BIMA_API = "https://apis.bundesimmobilien.de/immo/real_estate_offers/search"
BIMA_PORTAL = "https://immobilienportal.bundesimmobilien.de/results"
BIMA_PAGE = 10                      # the API answers 10 at a time whatever is asked


def bima_listing(record: dict) -> dict | None:
    """One BImA search record as a listing, or None when it is not for sale."""
    a = record.get("attributes") or {}
    if a.get("commercialization_type") != "BUY":
        return None
    price = a.get("buy_price") or a.get("display_price")
    price = float(price) if price and float(price) > 1 else None
    text = " · ".join(str(a[k]).strip() for k in ("title", "description_note", "location_note", "furnishing_note",
                                                  "other_note") if a.get(k))
    raw = {"offer_id": a.get("offer_id"), "property_type": a.get("property_type"),
           "plot_m2": a.get("plot_area"), "until": a.get("published_until")}
    try:
        raw.update(lat=float(a["latitude"]), lon=float(a["longitude"]))
    except (KeyError, TypeError, ValueError):
        pass
    return make_listing(
        "bima", record.get("id"), "DE", title=(a.get("title") or f"BImA {a.get('city')}")[:200],
        description=f"{text[:3000]} · Bundesanstalt für Immobilienaufgaben (BImA)",
        tipo=a.get("property_type"), area_m2=a.get("living_space") or a.get("display_area"),
        price=price, concelho=a.get("city"), district=(a.get("federal_state") or "").replace("_", " ").title() or None,
        date_end=(a.get("published_until") or "")[:19] or None,
        url=f"{BIMA_PORTAL}?search=Bundesweit&region=national#offer-{record.get('id')}", raw_json=raw,
    )


@register("bima", "DE", description="BImA — houses, plots and land sold by the German federal state")
def scrape_bima(db, max_price: float = 50000, **_):
    session = make_session(timeout=30)
    total, offset = 0, 0
    while True:
        resp = session.get(BIMA_API, params={"q": "*", "filters[commercialization_type]": "BUY", "offset": offset})
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data") or []
        for record in records:
            row = bima_listing(record)
            if row and (row["price"] or 0) <= 2 * max_price:
                upsert_listing(db, row)
                total += 1
        db.commit()
        offset += len(records)
        if not records or offset >= ((data.get("meta") or {}).get("total") or 0):
            break
        time.sleep(0.3)
    return total
