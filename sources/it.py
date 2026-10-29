"""Italy: astegiudiziarie.it, PVP Giustizia, Gobid Real, Astalegale, Fallco Aste."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from common import LOG, auction_occupation, find_area, find_price, make_listing, make_session, parse_price
from db import upsert_listing
from sources import register
from sources._cards import CardSite, scrape_cards


@register("italy", "IT")
def scrape_italy(db, max_price: float = 50000, **_):
    """astegiudiziarie.it — Italian judicial auctions (front page)."""
    session = make_session(timeout=30, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    })
    base = "https://www.astegiudiziarie.it"
    resp = session.get(f"{base}/immobili")
    resp.raise_for_status()

    total_scraped = 0
    # Detail links look like /vendita-asta-TYPE-LOCATION-...-lNNNNNNN-pNNNNNNN
    for a in BeautifulSoup(resp.text, "html.parser").select("a[href*='/vendita-asta-']"):
        href = a.get("href", "")
        m = re.search(r'-l(\d+)-p(\d+)', href)
        if not m:
            continue
        eid = f"{m.group(1)}-{m.group(2)}"

        title = a.get_text(strip=True)[:120]
        if not title:
            img = a.select_one("img")
            title = img.get("alt", "")[:120] if img else ""
        price = find_price(a.parent.get_text(" ", strip=True)) if a.parent else None

        upsert_listing(db, make_listing(
            "italy", eid, "IT", title=title or f"Immobile {eid}", tipo="immobile",
            price=price, url=href, base_url=base,
        ))
        total_scraped += 1
    return total_scraped


# The portal is an Angular app; its search is a JSON API. filtroAnnunci 1 is
# "sales to come" (about 15,000 lots); the price fields of the form are not
# honoured by the API, so the budget is applied here.
PVP = "https://pvp.giustizia.it"
PVP_API = f"{PVP}/ric-496b258c-986a1b71/ric-ms/ricerca/vendite"
PVP_PAGE = 2000
PVP_KINDS = {"IMMOBILE_RESIDENZIALE", "ALTRA_CATEGORIA"}          # homes, and land among "other"
PVP_OCCUPANCY = {"LIBER": "libero", "OCCUP": "occupato", "OCCST": "occupato senza titolo",
                 "INCOR": "in corso di liberazione"}


def pvp_listing(lot: dict) -> dict | None:
    lid = lot.get("id")
    if not lid:
        return None
    where = lot.get("indirizzo") or {}
    coords = where.get("coordinate") or {}
    desc = re.sub(r"\s+", " ", lot.get("descLotto") or "").strip()
    free = sorted({PVP_OCCUPANCY[d] for d in lot.get("disponibilita") or [] if d in PVP_OCCUPANCY})
    parts = [desc, f"Disponibilità: {', '.join(free)}" if free else "",
             f"{lot.get('tribunale') or ''}, procedura {lot.get('procedura') or ''}".strip(", ")]
    raw = {k: lot.get(k) for k in ("procedura", "tribunale", "numeroLotto", "categoriaLotto", "categoriaBene",
                                   "offertaMinima", "disponibilita") if lot.get(k)}
    if coords.get("latitudine") and coords.get("longitudine"):
        raw.update(lat=coords["latitudine"], lon=coords["longitudine"])
    occ = auction_occupation(lot.get("disponibilita"))
    if occ:
        raw["occupation"] = occ
    date = lot.get("dataOraVendita") or lot.get("dataVendita")
    return make_listing(
        "pvp_giustizia", str(lid), "IT", id_prefix="pvp",
        title=f"{(desc[:90] or 'Immobile')} · {where.get('citta') or ''}"[:200],
        description=". ".join(p for p in parts if p)[:3000] or None, tipo="immobile",
        area_m2=find_area(desc), price=lot.get("prezzoBaseAsta"), min_price=lot.get("offertaMinima"),
        district=where.get("provincia"), concelho=where.get("citta"),
        url=f"{PVP}/pvp/it/detail_annuncio.page?idAnnuncio={lid}",
        date_end=f"{date}:00" if date and len(date) == 16 else date, raw_json=raw,
    )


@register("pvp_giustizia", "IT")
def scrape_italy_pvp(db, max_price: float = 50000, **_):
    """pvp.giustizia.it — Portale Vendite Pubbliche (official judicial sales portal)."""
    session = make_session(timeout=60)
    total = 0
    for page in range(0, 30):
        resp = session.post(PVP_API, params={"page": page, "size": PVP_PAGE},
                            json={"tipoLotto": "IMMOBILI", "filtroAnnunci": 1})
        resp.raise_for_status()
        body = (resp.json() or {}).get("body") or {}
        for lot in body.get("content") or []:
            price = lot.get("prezzoBaseAsta") or 0
            if lot.get("categoriaLotto") not in PVP_KINDS or not price or price > max_price:
                continue
            row = pvp_listing(lot)
            if row:
                upsert_listing(db, row)
                total += 1
        db.commit()
        if body.get("last", True) or page + 1 >= (body.get("totalPages") or 0):
            break
        time.sleep(1)
    LOG.info(f"PVP: {total} listings")
    return total


GOBIDREAL = CardSite(
    source="gobidreal", country="IT", base="https://www.gobidreal.it", path="/en/real-estate-auctions/",
    card_selector="div.lot-card, article.auction, div[class*='lot'], div[class*='auction']",
    title_selector="h2,h3,.title,.lot-title",
    location_selector=".location,.city,.comune",
    date_selector=".date,.auction-date,.end-date",
    params={"price_to": "{max_price}"}, max_pages=19,
    description="Gobid Real Italian judicial auction", tipo="immobile",
)
ASTALEGALE = "https://www.astalegale.net"
ASTALEGALE_API = "https://api.astalegale.net/Search"
ASTALEGALE_MAX_PAGES = 300          # 12 a page; about 2,000 homes under €30,000


def _astalegale_date(text: str | None) -> str | None:
    """"16/12/2026 - 10:00" → "2026-12-16T10:00:00"."""
    m = re.match(r"\s*(\d{2})/(\d{2})/(\d{4})(?:\s*-\s*(\d{1,2}):(\d{2}))?", text or "")
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return f"{y}-{mo}-{d}T{int(h or 0):02d}:{mi or '00'}:00"


def astalegale_listing(lot: dict) -> dict | None:
    """One result of api.astalegale.net/Search as a listing."""
    lid = str(lot.get("id") or "").strip()
    # Lots copied from the PVP arrive masked ("XXXXXXXXXX", no price) unless logged in.
    if not lid or re.fullmatch(r"[X\s]*", lot.get("tipologia") or "X"):
        return None
    desc = re.sub(r"\s+", " ", lot.get("descrizione") or "").strip()
    pos = lot.get("posizione") or {}
    raw = {k: lot.get(k) for k in ("proceduraNumeroAnno", "tribunale", "tipoProceduraEsteso", "modalitaVendita",
                                   "codiceLotto", "offertaMinima", "dataAsta") if lot.get(k)}
    if pos.get("lat") and pos.get("lng"):
        raw.update(lat=pos["lat"], lon=pos["lng"])
    return make_listing(
        "astalegale", lid, "IT",
        title=f"{lot.get('tipologia') or 'Immobile'} · {lot.get('titolo') or ''} · {lot.get('comune') or ''}"[:200],
        description=desc[:3000] or None, tipo=(lot.get("tipologia") or "immobile").lower(),
        area_m2=find_area(desc), price=lot.get("prezzoNum") or parse_price(lot.get("prezzo") or ""),
        min_price=parse_price(lot.get("offertaMinima") or ""),
        district=lot.get("provincia"), concelho=lot.get("comune"),
        url=f"{ASTALEGALE}/Aste/Detail/{lot.get('friendlyId') or lid}",
        image_url=lot.get("urlImmaginePrincipale"), date_end=_astalegale_date(lot.get("dataAsta")),
        raw_json=raw,
    )


# Not in the default scan (Sept 2026): Gobid Real: behind a Cloudflare challenge (403); check gobidreal.it by hand.
# Bot walls are not worked around; it stays runnable by name in case the site opens up.
@register("gobidreal", "IT", default=False)
def scrape_gobidreal(db, max_price: float = 100000, **_):
    """gobidreal.it — Italian judicial property auctions."""
    return scrape_cards(db, GOBIDREAL, max_price)


@register("astalegale", "IT")
def scrape_astalegale(db, max_price: float = 100000, **_):
    """astalegale.net — Italian judicial auction aggregator (its search API:
    homes up to the budget, 12 a page)."""
    session = make_session(timeout=30)
    total, seen = 0, set()
    for page in range(1, ASTALEGALE_MAX_PAGES + 1):
        resp = session.post(ASTALEGALE_API, json={"categories": ["residenziali"], "prezzoA": int(max_price),
                                                  "page": page})
        resp.raise_for_status()
        results = (resp.json() or {}).get("results") or {}
        lots = results.get("currentPage") or []
        for lot in lots:
            row = astalegale_listing(lot)
            if not row or row["id"] in seen:
                continue
            seen.add(row["id"])
            upsert_listing(db, row)
            total += 1
        db.commit()
        # A page of masked PVP copies only is not the end: stop when the pages run out.
        if not lots or page * (results.get("pageSize") or 12) >= (results.get("totalResults") or 0):
            break
        time.sleep(0.3)
    LOG.info(f"Astalegale: {total} listings")
    return total


FALLCOASTE = "https://www.fallcoaste.it"
FALLCOASTE_HOMES = "macro|527^cat0|528^cat1|529"     # Immobili › residenziali (about 700 lots, Oct 2026)
FALLCOASTE_PAGE = 15
FALLCOASTE_MAX_PAGES = 80


def fallcoaste_listing(art) -> dict | None:
    """One article.horizontal-auction of fallcoaste.it's search as a listing."""
    lid, url = art.get("data-auction-id"), art.get("data-auction-url")
    if not lid or not url:
        return None
    h3 = art.select_one(".name_type h3")
    goods = [li.get_text(" ", strip=True) for li in art.select(".name_type li")]
    lines = [p.get_text(" ", strip=True) for p in art.select(".name_type p")]
    court = next((ln for ln in lines if "Tribunale" in ln or "Procedura" in ln), None)
    court_m = re.search(r"Tribunale di ([^-]+)", court or "")
    price_el = art.select_one(".price-block strong")
    price = parse_price(price_el.get_text()) if price_el else None
    d = re.match(r"(\d{2})/(\d{2})/(\d{4})\s*(\d{2}):(\d{2})", art.get("data-auction-data-termine") or "")
    desc = " · ".join(goods) or (h3.get_text(" ", strip=True) if h3 else "")
    img = art.select_one("figure img[src]")
    return make_listing(
        "fallcoaste", lid, "IT",
        title=(h3.get_text(" ", strip=True) if h3 else f"Fallco Aste {lid}")[:200],
        description=f"{desc} — {court}" if court else desc or None, tipo="immobile",
        area_m2=find_area(desc), price=price, min_price=None,
        district=court_m.group(1).strip() if court_m else None, url=url,
        image_url=img["src"] if img else None,
        date_end=f"{d[3]}-{d[2]}-{d[1]}T{d[4]}:{d[5]}:00" if d else None,
        raw_json={"procedura": court, "modalita": lines[0] if lines else None},
    )


@register("fallcoaste", "IT", kind="aggregator", access="public_html", docs_url=FALLCOASTE)
def scrape_fallcoaste(db, max_price: float = 100000, **_):
    """Fallco Aste — Italian bankruptcy (fallimento / liquidazione) and court home sales."""
    from sources import ScanCount
    session = make_session(timeout=40)
    total, seen, scope, pages = 0, set(), "partial", 0
    for page in range(1, FALLCOASTE_MAX_PAGES + 1):
        try:
            resp = session.get(f"{FALLCOASTE}/ricerca.html", params={"filter": FALLCOASTE_HOMES, "page": page})
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            LOG.warning(f"fallcoaste: page {page} failed, keeping earlier pages")
            break
        pages += 1
        arts = BeautifulSoup(resp.text, "html.parser").select("article.horizontal-auction")
        new = 0
        for art in arts:
            row = fallcoaste_listing(art)
            if not row or row["id"] in seen:
                continue
            seen.add(row["id"])
            new += 1
            if row["price"] and row["price"] > max_price:
                continue
            upsert_listing(db, row)
            total += 1
        db.commit()
        if len(arts) < FALLCOASTE_PAGE or not new:   # past the last page the site repeats page 1
            scope = "full"
            break
        time.sleep(1)
    LOG.info(f"fallcoaste: {total} listings")
    return ScanCount(total, scope, pages)


# ─── Rete Aste Notarili (notaries' auctions: state and public bodies) ─
# The notaries' network runs the sales of the Agenzia del Demanio, public
# housing (ATER), the Red Cross liquidation (ESaCRI), seized assets (ANBSC),
# other public bodies and voluntary sales. The search is a form kept in the
# session: post it once for every province, then page through 5 at a time.
# "GAST" notices are court sales already read from PVP, so only "GAV" stay.
RAN = "https://www.avvisinotarili.notariato.it"
RAN_MAX_PAGES = 200
_RAN_PLACE = re.compile(r",\s*([^,()]+?)\s*\(([^)]+)\)\s*$")


def ran_listings(html: str) -> list[dict]:
    """The notary notices on one result page of the Rete Aste Notarili."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for link in soup.select('a[href*="/dettaglio-avviso/GAV/"]'):
        aid = link["href"].rstrip("/").rsplit("/", 1)[-1]
        info = link.select_one(".divSchedaInfo")
        if not aid.isdigit() or not info:
            continue
        text = info.select_one("p").get_text(" ", strip=True) if info.select_one("p") else ""
        facts = {li.get_text(" ", strip=True).split(":", 1)[0]: (li.select_one("span").get_text(strip=True)
                                                                 if li.select_one("span") else "")
                 for li in info.select("li")}
        sale = re.match(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})",
                        facts.get("Data e Ora fine raccolta offerte") or facts.get("Data e Ora vendita") or "")
        where = link.select_one(".divSchedaDett small")
        where = where.get_text(" ", strip=True) if where else ""
        place = _RAN_PLACE.search(where)
        lat, lon = soup.select_one(f"#lat_{aid}"), soup.select_one(f"#lon_{aid}")
        raw = {"address": where}
        try:
            raw.update(lat=float(lat["value"]), lon=float(lon["value"]))
        except (TypeError, ValueError, KeyError):
            pass
        img = link.select_one("img[src]")
        rows.append(make_listing(
            "ran", aid, "IT", title=text[:200] or f"Avviso notarile {aid}", description=text[:3000],
            tipo="immobile", area_m2=find_area(text), price=parse_price(facts.get("Prezzo dell'Avviso") or ""),
            concelho=place.group(1).strip().title() if place else None,
            district=place.group(2).strip().title() if place else None,
            date_end=f"{sale[3]}-{sale[2]}-{sale[1]}T{sale[4]}:{sale[5]}:00" if sale else None,
            url=RAN + link["href"], image_url=img["src"] if img else None, raw_json=raw,
        ))
    return rows


@register("ran", "IT", description="Rete Aste Notarili — Demanio, public housing and other public bodies' sales")
def scrape_ran(db, max_price: float = 50000, **_):
    session = make_session(timeout=40)
    home = session.get(RAN + "/")
    home.raise_for_status()
    provinces = [o["value"] for o in BeautifulSoup(home.text, "html.parser").select("select[name=provincia] option")]
    form = [("tipoBene", "I"), ("statoAvviso", "inCorso"), ("ordinamento", "prezzo:ASC"), ("coords", "")]
    form += [("provincia", p) for p in provinces] + [("tipologia", "GAV:(*);GAST:(*);GAST_PUB:(*)")]
    resp = session.post(RAN + "/beni-immobili/?", data=form)
    resp.raise_for_status()
    total, seen = 0, set()
    for page in range(1, RAN_MAX_PAGES + 1):
        rows = ran_listings(resp.text)
        for row in rows:
            if row["id"] in seen or (row["price"] or 0) > 2 * max_price:
                continue
            seen.add(row["id"])
            upsert_listing(db, row)
            total += 1
        db.commit()
        # Sorted by price: once a whole page is over twice the budget, the rest is too.
        if rows and all((r["price"] or 0) > 2 * max_price for r in rows):
            break
        nxt = session.get(f"{RAN}/beni-immobili/?&page={page}")
        if nxt.status_code != 200 or "dettaglio-avviso" not in nxt.text:
            break
        resp = nxt
        time.sleep(0.3)
    return total


# ─── Case a 1 euro: towns selling old houses for €1 ───────────────────
# casea1euro.it keeps one post per town scheme ("NARO – Agrigento – Sicilia").
# The house is free, the renovation is not: a deposit and works within about
# three years. Each scheme is a lead with no price; the town's own call has the
# houses. Schemes not updated for 18 months are left out as likely closed.
CASEA1EURO_POSTS = "https://casea1euro.it/wp-json/wp/v2/posts"
CASEA1EURO_CATEGORY = 3
CASEA1EURO_MONTHS = 18
_IT_REGIONS = re.compile(r"\b(?:Abruzzo|Basilicata|Calabria|Campania|Emilia[- ]?Romagna|Friuli[- ]Venezia Giulia|Lazio|Liguria|"
                         r"Lombardia|Marche|Molise|Piemonte|Puglia|Sardegna|Sicilia|Toscana|Trentino[- ]Alto Adige|Umbria|"
                         r"Valle d['’]Aosta|Veneto)\b", re.I)


def casea1euro_listing(post: dict, now: datetime | None = None) -> dict | None:
    """One town scheme as a listing, or None when it is old or not a town."""
    from html import unescape
    now = now or datetime.now()
    modified = (post.get("modified") or "")[:10]
    if not modified or modified < (now - timedelta(days=30 * CASEA1EURO_MONTHS)).strftime("%Y-%m-%d"):
        return None
    title = _IT_REGIONS.sub(" ", unescape(post.get("title", {}).get("rendered") or ""))
    parts = [p.strip() for p in re.split(r"\s*[–-]\s+|\s+[–-]\s*", title) if p.strip()]
    if not parts or CASEA1EURO_CATEGORY not in (post.get("categories") or []):
        return None
    town, province = parts[0].title(), (parts[1].title() if len(parts) > 1 else None)
    excerpt = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", post.get("excerpt", {}).get("rendered") or ""))).strip()
    return make_listing(
        "casea1euro", post.get("id") or post.get("link"), "IT", title=f"Case a 1 euro — {town} ({province})",
        description=" · ".join(x for x in (excerpt[:1500], "casa a 1 euro: deposito cauzionale e ristrutturazione entro circa 3 anni; "
                                           "gli immobili e le scadenze sono nel bando del Comune") if x),
        tipo="casa (case a 1 euro)", price=None, concelho=town, district=province,
        url=post.get("link"), raw_json={"scheme": "case a 1 euro", "updated": modified},
    )


@register("casea1euro", "IT", description="Case a 1 euro — Italian towns selling empty old houses for €1 against renovation")
def scrape_casea1euro(db, max_price: float = 50000, **_):
    session = make_session(timeout=60)
    total = 0
    for page in range(1, 6):
        resp = session.get(CASEA1EURO_POSTS, params={"per_page": 100, "page": page, "categories": CASEA1EURO_CATEGORY,
                                                     "_fields": "id,title,excerpt,categories,link,modified"})
        if resp.status_code == 400:      # past the last page
            break
        resp.raise_for_status()
        posts = resp.json()
        for post in posts:
            listing = casea1euro_listing(post)
            if listing:
                upsert_listing(db, listing)
                total += 1
        db.commit()
        if len(posts) < 100:
            break
    return total
