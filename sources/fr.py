"""France: licitor.com, encheres-publiques.com."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, normalize, parse_price
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

    enrich_france_details(db, session)
    return total_scraped


# An annonce page names the tribunal, the hearing date and time, the mise à
# prix, the lawyer handling the sale (avocat poursuivant) and often the visit
# dates and whether the property is occupied. Read from the page text, since
# the markup is unconfirmed from here; every field is optional.
_FR_MONTHS = {m: i for i, m in enumerate(
    ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
     "septembre", "octobre", "novembre", "decembre"], 1)}
_FR_DATE = re.compile(
    r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi)?\s*(\d{1,2})(?:er)?\s+("
    + "|".join(_FR_MONTHS) + r")\s+(\d{4})(?:\s+a\s+(\d{1,2})\s*h\s*(\d{2})?)?")
_TRIBUNAL = re.compile(r"(Tribunal (?:Judiciaire|de Grande Instance|de Proximit[ée])\s+d(?:e\s+|'|’)"
                       r"\s*([^(,;:\n]{2,40}?))\s*(?:\(|,|;|:| - |$)", re.I)
_AVOCAT = re.compile(r"Ma[îi]tre\s+((?:[A-ZÀ-Ý][\w\-’']+\.?\s?){1,4})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TEL = re.compile(r"T[ée]l(?:[ée]phone)?\.?\s*:?\s*((?:\+33\s?|0)[1-9](?:[\s.]?\d{2}){4})")


def _licitor_occupation(norm: str) -> str | None:
    if re.search(r"libre (?:de toute occupation|d'occupation|d'occupant)|inoccupe|vide de tout"
                 r"|(?:non|pas) occupe", norm):
        return "vacant"
    if re.search(r"\boccupee?s?\b|\bloue(?:e|s)?\b|bail en cours|\blocataire", norm):
        return "occupied"
    return None


def parse_licitor_annonce(html: str) -> dict:
    """Details for letters and scoring from a licitor annonce page."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    norm = normalize(text)
    out: dict = {}

    # One text node at a time, so the city does not run into the next line.
    t = next(filter(None, (_TRIBUNAL.search(line) for line in soup.stripped_strings)), None)
    if t:
        out["tribunal"] = t.group(1).strip()
        out["tribunal_ville"] = t.group(2).strip()

    # The hearing: the date after "audience", else the first date with a time.
    after = re.search(r"audience", norm)
    d = (_FR_DATE.search(norm, after.start()) if after else None) \
        or next((x for x in _FR_DATE.finditer(norm) if x.group(4)), None) or _FR_DATE.search(norm)
    if d:
        try:
            out["audience"] = datetime(int(d.group(3)), _FR_MONTHS[d.group(2)], int(d.group(1)),
                                       int(d.group(4) or 0), int(d.group(5) or 0)).isoformat()
        except ValueError:
            pass

    m = re.search(r"mise a prix\s*:?\s*([\d\s.]+(?:,\d{2})?)\s*(?:€|eur)", norm)
    if m:
        out["mise_a_prix"] = parse_price(m.group(1))

    a = _AVOCAT.search(text)
    if a:
        out["avocat_nom"] = a.group(1).strip()
    emails = [a["href"][7:].split("?")[0] for a in soup.select("a[href^='mailto:']")] + _EMAIL.findall(text)
    emails = [e for e in emails if _EMAIL.fullmatch(e) and "licitor" not in e.lower()]
    if emails:
        out["avocat_email"] = emails[0]
    tel = _TEL.search(text)
    if tel:
        out["avocat_tel"] = tel.group(1).strip()

    v = re.search(r"Visites?\s*:?\s*([^.]{5,160})", text)
    if v:
        out["visite"] = v.group(1).strip()
    occ = _licitor_occupation(norm)
    if occ:
        out["occupation"] = occ
    return out


def enrich_france_details(db, session, limit: int = 60):
    """Open the annonce page of licitor listings not checked yet, newest
    first: hearing date (so past sales expire), tribunal, mise à prix, the
    lawyer's name and e-mail for letters, occupancy for the score."""
    rows = db.execute("""
        SELECT * FROM listings WHERE source='france' AND url IS NOT NULL
          AND (raw_json IS NULL OR raw_json NOT LIKE '%"detail_checked"%')
        ORDER BY first_seen DESC LIMIT ?
    """, (limit,)).fetchall()
    for row in rows:
        item = dict(row)
        try:
            resp = session.get(item["url"])
            resp.raise_for_status()
        except Exception as e:
            LOG.debug(f"  France annonce {item['external_id']}: {e}")
            continue
        info = parse_licitor_annonce(resp.text)
        try:
            raw = json.loads(item.get("raw_json") or "{}") or {}
        except ValueError:
            raw = {}
        raw.update(info)
        raw["detail_checked"] = datetime.now().strftime("%Y-%m-%d")
        fields = {k: item.get(k) for k in (
            "title", "description", "tipo", "area_m2", "price", "current_bid", "min_price",
            "district", "concelho", "freguesia", "url", "image_url", "date_end")}
        fields["price"] = fields["price"] or info.get("mise_a_prix")
        fields["date_end"] = info.get("audience") or fields["date_end"]
        fields["district"] = fields["district"] or info.get("tribunal_ville")
        fields["raw_json"] = json.dumps(raw, ensure_ascii=False)
        upsert_listing(db, make_listing("france", item["external_id"], "FR", **fields))
        db.commit()
        time.sleep(0.5)
    if rows:
        LOG.info(f"  France: {len(rows)} annonce pages checked")
    return len(rows)


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
