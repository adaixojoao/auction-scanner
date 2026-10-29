"""Greece: eauction.gr judicial e-auctions."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, stable_id
from db import upsert_listing
from sources import register


# Not in the default scan (Sept 2026): eauction.gr: the auction list is behind an Incapsula proof-of-work check; check it by hand.
# Bot walls are not worked around; it stays runnable by name in case the site opens up.
@register("greece", "GR", default=False)
def scrape_greece(db, max_price: float = 50000, **_):
    """eauction.gr — Greek court-mandated electronic auctions."""
    session = make_session()
    base = "https://www.eauction.gr"
    resp = session.get(f"{base}/Auction/SearchResult", params={
        "AssetCategory": "REAL_ESTATE", "AuctionStatus": "ACTIVE",
        "PageSize": 100, "PageNumber": 1,
    })
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("div.auction-result-item, div.card, tr.auction-row") or soup.select("table tr")[1:]
    LOG.info(f"  Greece: {len(items)} items found")

    total_scraped = 0
    for item_el in items:
        link = item_el.select_one("a[href*='Auction']") or item_el.select_one("a[href]")
        if not link:
            continue
        title = link.get_text(strip=True)[:200]
        href = link["href"]
        eid_m = re.search(r'[/=](\d+)', href)
        eid = eid_m.group(1) if eid_m else stable_id(href, title)
        text = item_el.get_text(" ", strip=True)
        price = find_price(text)
        if price and price > max_price:
            continue
        loc_m = re.search(r"(?:Τοποθεσία|Location|Περιοχή)[:\s]+([^\n,]+)", text)
        upsert_listing(db, make_listing(
            "greece", eid, "GR",
            title=title or f"Greek auction {eid}", description=text[:500],
            tipo="imovel", price=price, min_price=price,
            district=loc_m.group(1).strip() if loc_m else None,
            url=href, base_url=base, date_end=parse_date_dmy(text),
        ))
        total_scraped += 1
    return total_scraped


# ─── ETAD (the Greek state's property company) ─────────────────────
# ETAD (hppc.gr) offers state property by open electronic tender: most of it
# to lease, some for sale (Πώληση). The tenders page links every property;
# each detail page lists its facts as "label: value".
ETAD = "https://hppc.gr"
ETAD_TENDERS = ETAD + "/axiopoiisi-akiniton/diagonismoi-akiniton/"
_ETAD_PID = re.compile(r"/axiopoiisi-akiniton/property/\?pid=([\w-]+)")
_ETAD_LABELS = ("Νομός", "Πόλη", "Περιοχή", "Οδός", "Ταχυδρομικός Κώδικας", "Εντός Σχεδίου Πόλεως",
                "Τετραγωνικά Μέτρα Οικοπέδου", "Τετραγωνικά Μέτρα Κτισμάτων", "Όροφος", "Δωμάτια", "Διαθεσιμότητα",
                "ΑΜ Ακινήτου", "Φορέας", "Είδος Διαγωνισμού", "Φάση Διαγωνισμού", "Κατηγορία Συναλλαγής",
                "Τρόπος Πληρωμής", "Χρόνος Έναρξης", "Χρόνος Λήξης", "Τιμή Εκκίνησης", "Βήμα Διαγωνισμού",
                "Κωδικός Διαγωνισμού")


def etad_facts(text: str) -> dict:
    """The "label: value" facts of one ETAD property page."""
    marks = sorted((m.start(), m.end(), label) for label in _ETAD_LABELS
                   for m in [re.search(re.escape(label) + r"\s*:", text)] if m)
    return {label: text[end:(marks[i + 1][0] if i + 1 < len(marks) else end + 120)].strip(" -")
            for i, (_, end, label) in enumerate(marks)}


def etad_listing(pid: str, html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    facts = etad_facts(text)
    if "Πώληση" not in (facts.get("Κατηγορία Συναλλαγής") or ""):
        return None                              # leases and building rights are not purchases
    title = soup.select_one("h1")
    title = title.get_text(" ", strip=True) if title else f"ETAD {pid}"
    end = re.match(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2}):(\d{2})", facts.get("Χρόνος Λήξης") or "")
    built = to_float(facts.get("Τετραγωνικά Μέτρα Κτισμάτων"))
    return make_listing(
        "etad", pid, "GR", title=title[:200],
        description=" · ".join(f"{k}: {v}" for k, v in facts.items() if v) + " · ETAD (e-publicrealestate.gr)",
        tipo="ακίνητο", area_m2=built or to_float(facts.get("Τετραγωνικά Μέτρα Οικοπέδου")),
        price=find_price(facts.get("Τιμή Εκκίνησης") or ""), concelho=facts.get("Πόλη") or facts.get("Περιοχή"),
        district=facts.get("Νομός"), date_end=f"{end[3]}-{end[2]}-{end[1]}T{end[4]}:{end[5]}:00" if end else None,
        url=f"{ETAD}/axiopoiisi-akiniton/property/?pid={pid}",
        raw_json={"plot_m2": to_float(facts.get("Τετραγωνικά Μέτρα Οικοπέδου")), "phase": facts.get("Φάση Διαγωνισμού"),
                  "availability": facts.get("Διαθεσιμότητα")},
    )


def to_float(value) -> float | None:
    try:
        return float(str(value).split()[0]) if value else None
    except ValueError:
        return None


@register("etad", "GR", description="ETAD — Greek state property for sale by electronic tender")
def scrape_etad(db, max_price: float = 50000, **_):
    session = make_session(timeout=30)
    resp = session.get(ETAD_TENDERS)
    resp.raise_for_status()
    total = 0
    for pid in sorted(set(_ETAD_PID.findall(resp.text))):
        try:
            row = etad_listing(pid, session.get(f"{ETAD}/axiopoiisi-akiniton/property/", params={"pid": pid}).text)
        except Exception as e:  # noqa: BLE001
            LOG.info(f"etad {pid}: {type(e).__name__}")
            continue
        if row:
            upsert_listing(db, row)
            total += 1
    db.commit()
    return total


# ─── Intrum Greece: repossessed homes, land and premises ──────────────
# Intrum services a large share of the Greek banks' bad loans and sells the
# repossessed property on its marketplace, with an open JSON search: map
# point, size, type, region and the distance to the beach. "Price on request"
# comes as 0 and is stored as no price.
INTRUM_API = "https://api.marketplace.intrum.gr/v1/listings/search"
_INTRUM_HEADERS = {"Accept": "application/json", "Origin": "https://marketplace.intrum.gr",
                   "Referer": "https://marketplace.intrum.gr/"}


def _intrum_text(values, locale: str = "en") -> str:
    for value in values or []:
        if value.get("locale") == locale:
            return value.get("value") or ""
    return (values or [{}])[0].get("value") or "" if values else ""


def intrum_listing(item: dict) -> dict | None:
    """One marketplace listing as a listing, or None when it is sold or reserved."""
    if item.get("acquired") or item.get("commercialization") != "sale":
        return None
    if (item.get("availability_status") or {}).get("slug") not in (None, "available"):
        return None
    title = _intrum_text(item.get("title")) or _intrum_text(item.get("title"), "el")
    region = _intrum_text(item.get("region"))
    raw = {"code": item.get("property_code"), "category": item.get("category"), "type": item.get("type"),
           "sea_front": item.get("sea_front"), "build_year": item.get("build_year")}
    coords = item.get("coordinates") or []
    if len(coords) == 2:
        raw.update(lat=coords[0], lon=coords[1])
    beach = item.get("proximity_beach_driving")
    price = item.get("price") or None
    town = (item.get("locations") or [""])[-1].split("-")[-1].replace("_", " ").title() or None
    images = item.get("images") or []
    return make_listing(
        "intrum_gr", item.get("id"), "GR", title=title[:200],
        description=" · ".join(x for x in (
            title, region, f"{item.get('type') or ''} {item.get('category') or ''}".strip(),
            f"built {item['build_year']}" if item.get("build_year") else "",
            "sea front" if item.get("sea_front") else (f"beach about {beach / 1000:.1f} km by road" if beach else ""),
            "price on request" if not price else "", "bank repossession sold by Intrum Hellas") if x),
        tipo=item.get("type") or item.get("category"), area_m2=item.get("size") or None, price=price,
        district=region or None, concelho=town, url=f"https://marketplace.intrum.gr/en/listings/{item.get('id')}",
        image_url=((images[0].get("sizes") or {}).get("md")) if images else None, raw_json=raw,
    )


@register("intrum_gr", "GR", description="Intrum Hellas — repossessed Greek homes, land and premises from the banks' bad loans")
def scrape_intrum_gr(db, max_price: float = 50000, **_):
    session = make_session(timeout=60)
    total = 0
    for page in range(1, 80):
        resp = session.get(INTRUM_API, headers=_INTRUM_HEADERS,
                           params={"filter_by": "commercialization:=sale", "page": page, "sort_by": "price:asc", "per_page": 50})
        resp.raise_for_status()
        data = resp.json()
        for item in (data.get("data") or {}).get("listings") or []:
            listing = intrum_listing(item)
            if listing and (listing["price"] or 0) <= 2 * max_price:
                upsert_listing(db, listing)
                total += 1
        db.commit()
        if page >= ((data.get("meta") or {}).get("last_page") or page):
            break
        time.sleep(0.5)
    return total
