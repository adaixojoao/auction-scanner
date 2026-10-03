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

# List-page titles glue department, town and kind with no spaces
# ("22PlémetUne maison…"). Split them so the town can be the place field.
_FR_KIND = (
    r"Une?|Un|Des|Appartement|Maison|Immeuble|Box|Terrain|Local|Garage|"
    r"Ensemble|Ferme|Propri[eé]t[eé]|Parcelle|Studio|B[aâ]timent"
)
_FR_LIST_TITLE = re.compile(
    rf"^(\d{{2,3}})\s*(.+?)(?=(?:{_FR_KIND})\b)((?:{_FR_KIND})\b.*)$",
    re.I | re.S,
)


def parse_licitor_list_title(text: str) -> tuple[str, str | None]:
    """Return (readable title, town) from a licitor list-page link text."""
    title = re.sub(r"\s+", " ", (text or "").strip())
    m = _FR_LIST_TITLE.match(title)
    if not m:
        title = _unglue_french(title)
        return title[:120], None
    town = m.group(2).strip(" -")
    if len(re.sub(r"\W", "", town)) < 2:
        return _unglue_french(title)[:120], None
    return _unglue_french(m.group(3).strip() or title)[:120], town


def _unglue_french(title: str) -> str:
    """Insert spaces the list page left out ('habitationde', 'occupationMise')."""
    title = re.sub(r"(habitation)(de)\b", r"\1 \2", title, flags=re.I)
    title = re.sub(r"(individuelle)(de)\b", r"\1 \2", title, flags=re.I)
    title = re.sub(r"(occupation)(Mise)\b", r"\1 \2", title, flags=re.I)
    title = re.sub(r"(habitation)(Mise)\b", r"\1 \2", title, flags=re.I)
    title = re.sub(r"(immeuble)(sur)\b", r"\1 \2", title, flags=re.I)
    title = re.sub(r"(appartement)(de)\b", r"\1 \2", title, flags=re.I)
    return title


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
            # Space between child nodes: strip=True alone glued "22PlémetUne…".
            raw_title = a.get_text(" ", strip=True)
            if not href or not raw_title or len(raw_title) < 5:
                continue
            title, town = parse_licitor_list_title(raw_title)
            m_id = re.search(r"/(\d+)\.html", href)
            eid = m_id.group(1) if m_id else href.strip("/").split("/")[-1].replace(".html", "")
            price = find_price(a.parent.get_text(" ", strip=True)) if a.parent else None
            if price and price > max_price:
                continue
            upsert_listing(db, make_listing(
                "france", eid, "FR", title=title, tipo="immobilier", price=price,
                concelho=town, url=href, base_url=LICITOR_BASE,
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
        # Older scrapes glued "22PlémetUne…"; split when the stored title still is.
        title, town = parse_licitor_list_title(fields.get("title") or "")
        if town:
            fields["title"] = title
            fields["concelho"] = fields.get("concelho") or town
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


# ─── Bien'ici: a big French portal with an open search service ─────
# Not auctions: agents' asking prices. Houses within budget across France and
# land from 1 ha, 100 a page, with the text and the map position. Residences
# de tourisme (leaseback flats sold as "houses") are skipped: not a home.
BIENICI = "https://www.bienici.com"
BIENICI_SEARCHES = (("house", None), ("terrain", 10000))
BIENICI_PAGE = 100
BIENICI_MAX_PAGES = 60


def parse_bienici(ad: dict) -> dict | None:
    price = ad.get("price")
    if isinstance(price, list):
        price = min((p for p in price if isinstance(p, (int, float))), default=None)   # a range of lots
    if not ad.get("id") or not price or ad.get("isInTourismResidence"):
        return None
    land = ad.get("propertyType") == "terrain"
    blur = ad.get("blurInfo") or {}
    pos = blur.get("position") or {}
    raw: dict = {"land_m2": ad.get("landSurfaceArea")}
    if pos.get("lat") and pos.get("lon"):
        raw["geo"] = {"lat": float(pos["lat"]), "lon": float(pos["lon"]),
                      "precision": "street" if blur.get("type") == "exact" else "village"}
    photos = ad.get("photos") or []
    town = ad.get("city")
    kind = "Terrain" if land else "Maison"
    extra = f" · terrain {ad['landSurfaceArea']:.0f} m²" if ad.get("landSurfaceArea") and not land else ""
    return make_listing(
        "bienici", ad["id"], "FR", title=f"{kind} à {town}" + (f" ({ad.get('postalCode')})" if ad.get("postalCode") else ""),
        description=((ad.get("title") or "") + " · " + (ad.get("description") or "") + extra)[:3000].strip(" ·"),
        tipo="terrain" if land else "maison",
        area_m2=(ad.get("landSurfaceArea") or ad.get("surfaceArea")) if land else ad.get("surfaceArea"),
        price=float(price), min_price=float(price), district=ad.get("departmentCode"), concelho=town,
        url=f"{BIENICI}/annonce/{ad['id']}", image_url=photos[0].get("url") if photos else None,
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


@register("bienici", "FR")
def scrape_bienici(db, max_price: float = 50000, **_):
    """bienici — houses and land (1 ha+) from agents across France, with map positions."""
    session = make_session(timeout=30)
    total = 0
    # The service stops at 2,400 results a search: houses are asked in price bands.
    bands = [(0, max_price / 2), (max_price / 2, max_price * 0.75), (max_price * 0.75, max_price)]
    for kind, min_area in BIENICI_SEARCHES:
        for low, high in bands if kind == "house" else [(0, max_price)]:
            for page in range(BIENICI_MAX_PAGES):
                filters = {"size": BIENICI_PAGE, "from": page * BIENICI_PAGE, "filterType": "buy",
                           "propertyType": [kind], "minPrice": int(low), "maxPrice": int(high),
                           "onTheMarket": [True]}
                if min_area:
                    filters["minArea"] = min_area
                resp = session.get(f"{BIENICI}/realEstateAds.json", params={"filters": json.dumps(filters)})
                if resp.status_code == 400 and page:
                    LOG.info(f"Bien'ici {kind} {low:.0f}-{high:.0f}: stopped at {page * BIENICI_PAGE}")
                    break
                resp.raise_for_status()
                data = resp.json()
                ads = data.get("realEstateAds") or []
                for ad in ads:
                    row = parse_bienici(ad)
                    if row and row["price"] <= max_price:
                        upsert_listing(db, row)
                        total += 1
                db.commit()
                if not ads or (page + 1) * BIENICI_PAGE >= int(data.get("total") or 0):
                    break
                time.sleep(0.5)
    LOG.info(f"Bien'ici: {total} listings")
    return total


# ─── Notaires de France ──────────────────────────────────────────────
# Houses and land sold through notaries, including their online auctions
# (36h-immo / VNI) and notarial auctions (VAE). A public JSON service behind the
# listing page; its robots.txt asks for 10 s between requests, so one request
# of up to 100 listings per department and type, then a pause.

NOTAIRES = "https://www.immobilier.notaires.fr"
NOTAIRES_API = NOTAIRES + "/pub-services/inotr-www-annonces/v1/annonces"
# The west and north-west: Brittany, Normandy, Pays de la Loire, Poitou and Limousin.
NOTAIRES_DEPARTMENTS = ["29", "22", "56", "35", "50", "14", "61", "27", "76", "44", "49", "53", "72", "85",
                        "79", "86", "16", "17", "87", "23", "19", "24"]
NOTAIRES_TYPES = {"MAI": "maison", "TER": "terrain"}
NOTAIRES_PAUSE = 10
NOTAIRES_MAX_PAGES = 5
NOTAIRES_SALE = {"VENTE": "sale", "VNI": "online auction (36h-immo)", "VAE": "notarial auction"}


def parse_notaires(ad: dict) -> dict | None:
    if not ad.get("annonceId") or ad.get("viager") == "OUI" or ad.get("bienVendu") == "OUI":
        return None                    # a life annuity is not a purchase; sold is sold
    price = ad.get("prixTotal") or ad.get("prixAffiche")
    if not price:
        return None
    tipo = NOTAIRES_TYPES.get(ad.get("typeBien"), "maison")
    land = ad.get("surfaceTerrain")
    built = ad.get("surface")
    sale = NOTAIRES_SALE.get(ad.get("typeTransaction"), "sale")
    town = ad.get("communeNom") or ad.get("localiteNom")
    extras = [f"Terrain {land:,.0f} m²" if land else None, f"Surface {built:,.0f} m²" if built else None,
              f"Prix frais de notaire inclus: {price:,.0f} €",
              "Vente aux enchères" if ad.get("typeTransaction") in ("VNI", "VAE") else None]
    text = re.sub(r"\s*<br\s*/?>\s*", "\n", ad.get("descriptionFr") or "")
    text = re.sub(r"<[^>]+>", " ", text).strip()
    if re.match(r"\W*sous (?:compromis|offre)", text, re.I):
        return None                    # already promised to a buyer
    description = " · ".join(x for x in [text] + extras if x)
    raw = {"sale": sale}
    if ad.get("origineJudiciaire") == "OUI":
        raw["judicial"] = True
    return make_listing(
        "notaires", ad["annonceId"], "FR",
        title=f"{'Maison' if tipo == 'maison' else 'Terrain'} à {town} ({ad.get('codePostal') or ''})",
        description=description[:3000], tipo=tipo,
        area_m2=(land if tipo == "terrain" else built) or land or built,
        price=float(price), min_price=float(price),
        district=ad.get("departementNom"), concelho=town,
        url=ad.get("urlDetailAnnonceFr"), image_url=ad.get("urlPhotoPrincipale"),
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


@register("notaires", "FR", description="Notaires de France — houses, land and notary auctions in the west")
def scrape_notaires(db, max_price: float = 50000, **_):
    """Notaires de France — houses and land (and notary auctions) in the west of France."""
    session = make_session(timeout=40)
    session.headers["Accept"] = "application/json"
    total, first = 0, True
    for department in NOTAIRES_DEPARTMENTS:
        for code in NOTAIRES_TYPES:
            for page in range(1, NOTAIRES_MAX_PAGES + 1):
                if not first:
                    time.sleep(NOTAIRES_PAUSE)
                first = False
                params = {"offset": (page - 1) * 100, "page": page, "parPage": 100,
                          "typeTransactions": "VENTE,VNI,VAE", "typeBiens": code, "prixMax": int(max_price),
                          "departements": department}
                try:
                    resp = session.get(NOTAIRES_API, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:  # noqa: BLE001 — one department failing is not the source failing
                    if total == 0 and department == NOTAIRES_DEPARTMENTS[0]:
                        raise
                    LOG.info(f"Notaires {department} {code} p{page}: {type(e).__name__}")
                    break
                for ad in data.get("annonceResumeDto") or []:
                    row = parse_notaires(ad)
                    if row and row["price"] <= max_price:
                        upsert_listing(db, row)
                        total += 1
                db.commit()
                if page >= (data.get("nbPages") or 1):
                    break
    LOG.info(f"Notaires: {total} listings")
    return total
