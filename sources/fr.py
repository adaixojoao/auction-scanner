"""France: licitor.com, encheres-publiques.com."""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from common import LOG, find_price, land_max_price, make_listing, make_session, normalize, parse_price
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


def licitor_department(text: str) -> str | None:
    """The department code glued to the front of a licitor list title ("22Plémet")."""
    m = re.match(r"^\s*(\d{2,3}|2[ab])(?=\s*\D)", text or "", re.I)
    return m.group(1).upper() if m else None


def remember_department(db, listing_id: str, department: str | None) -> None:
    """Keep the department beside whatever the row already stores. A later page
    must not have to guess which of several communes of the same name it is."""
    if not department:
        return
    row = db.execute("SELECT raw_json FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not row:
        return
    try:
        raw = json.loads(row[0] or "{}")
    except ValueError:
        raw = {}
    if not isinstance(raw, dict) or raw.get("department"):
        return
    raw["department"] = department
    db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
               (json.dumps(raw, ensure_ascii=False), listing_id))


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
            if town:
                remember_department(db, f"france:{eid}", licitor_department(raw_title))
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

    # The list link is one truncated line. The annonce names each part, the
    # street and a map pin. The pin is used for the beach, the climate and the
    # distances; the note keeps the page's warning that its accuracy is not
    # guaranteed. A sponsored "prices nearby" widget is not read — it is not a
    # sale price for this property.
    ad = soup.select_one(".AdContent") or soup.select_one("article.LegalAd")
    if ad is not None:
        lots = []
        for lot in ad.select(".SousLot"):
            head = lot.find(["h2", "h3"])
            body = " ".join(p.get_text(" ", strip=True) for p in lot.find_all("p"))
            title = head.get_text(" ", strip=True) if head else ""
            piece = re.sub(r"\s+", " ", f"{title} {body}").strip()
            if piece:
                lots.append(piece)
        street_el = ad.select_one("p.Street")
        city_el = ad.select_one("p.City")
        street = re.sub(r"\s+", " ", street_el.get_text(" ", strip=True)) if street_el else ""
        city = re.sub(r"\s+", " ", city_el.get_text(" ", strip=True)) if city_el else ""
        if street:
            out["street"] = street
        if city:
            out["city"] = city
        where = ", ".join(p for p in (street, city) if p)
        if lots or where:
            out["description"] = ". ".join([*lots, where] if where else lots)
        link = ad.select_one("p.Map a[href*='q=']")
        found = re.search(r"q=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)", link.get("href") or "") if link else None
        if found:
            lat, lon = float(found.group(1)), float(found.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out["pin"] = (lat, lon)
    return out


def _remember_advertised_pin(raw: dict, info: dict) -> None:
    """Keep Licitor's map pin when nothing more exact is stored.

    Bare lat/lon would be read as the sale's own coordinates. This pin is used
    for the beach, the climate and the distances. The note keeps the page's
    warning that its accuracy is not guaranteed."""
    pin = info.get("pin")
    if not pin:
        return
    geo = raw.get("geo") if isinstance(raw.get("geo"), dict) else {}
    if raw.get("verified_geo") or geo.get("precision") in ("sale", "street", "cadastre", "verified"):
        return
    street, city = info.get("street") or "", info.get("city") or ""
    raw["geo"] = {"lat": pin[0], "lon": pin[1], "precision": "advertised",
                  "query": ", ".join(p for p in (street, city) if p) or "licitor",
                  "note": "Licitor's map pin; the page says its accuracy is not guaranteed"}
    # A missing key is filled back in from the old row (db._keep_learned).
    # Null says the old reading was for another pin and must not come back.
    raw["climate"] = None
    raw["water_check"] = None


def enrich_france_details(db, session, limit: int = 160):
    """Open the annonce page of licitor listings not checked yet, newest
    first: hearing date (so past sales expire), tribunal, mise à prix, the
    lawyer's name and e-mail for letters, occupancy for the score.

    A listing whose list page already shows a price over the cap is not opened."""
    from db import still_worth_a_page_sql
    clause, extra = still_worth_a_page_sql()
    rows = db.execute(f"""
        SELECT * FROM listings WHERE source='france' AND url IS NOT NULL
          AND (raw_json IS NULL OR raw_json NOT LIKE '%"body_checked"%')
          {clause}
        ORDER BY first_seen DESC LIMIT ?
    """, (*extra, limit)).fetchall()
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
        raw.update({k: v for k, v in info.items() if k not in ("description", "street", "city", "pin")})
        raw["detail_checked"] = raw["body_checked"] = datetime.now().strftime("%Y-%m-%d")
        if info.get("street"):
            raw["morada"] = info["street"]
        _remember_advertised_pin(raw, info)
        fields = {k: item.get(k) for k in (
            "title", "description", "tipo", "area_m2", "price", "current_bid", "min_price",
            "district", "concelho", "freguesia", "url", "image_url", "date_end")}
        fields["description"] = info.get("description") or fields["description"]
        fields["price"] = fields["price"] or info.get("mise_a_prix")
        fields["date_end"] = info.get("audience") or fields["date_end"]
        fields["district"] = fields["district"] or info.get("tribunal_ville")
        fields["concelho"] = fields["concelho"] or info.get("city")
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


@register("encheres_publiques", "FR", default=False)
def scrape_encheres_publiques(db, max_price: float = 100000, **_):
    """encheres-publiques.com — French judicial property auctions.
    Refuses automated requests (HTTP 403, 15 runs in a row by 2026-10-24);
    licitor.com ("france") covers the same tribunal sales."""
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
    if ad.get("energyClassification") in ("A", "B", "C", "D", "E", "F", "G"):
        raw["dpe"] = ad["energyClassification"]
    if pos.get("lat") and pos.get("lon"):
        raw["geo"] = {"lat": float(pos["lat"]), "lon": float(pos["lon"]),
                      "precision": "street" if blur.get("type") == "exact" else "village"}
    photos = ad.get("photos") or []
    town = ad.get("city")
    kind = "Terrain" if land else "Maison"
    extra = f" · terrain {ad['landSurfaceArea']:.0f} m²" if ad.get("landSurfaceArea") and not land else ""
    # Agents paste HTML: "travaux sont<br>à prévoir" must read as one phrase.
    text = html.unescape(re.sub(r"<[^>]+>", " ", ad.get("description") or ""))
    text = re.sub(r"[ \t\xa0]+", " ", text).strip()
    return make_listing(
        "bienici", ad["id"], "FR", title=f"{kind} à {town}" + (f" ({ad.get('postalCode')})" if ad.get("postalCode") else ""),
        description=((ad.get("title") or "") + " · " + text + extra)[:3000].strip(" ·"),
        tipo="terrain" if land else "maison",
        area_m2=(ad.get("landSurfaceArea") or ad.get("surfaceArea")) if land else ad.get("surfaceArea"),
        price=float(price), min_price=float(price), district=ad.get("departmentCode"), concelho=town,
        url=f"{BIENICI}/annonce/{ad['id']}", image_url=photos[0].get("url") if photos else None,
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


@register("bienici", "FR")
def scrape_bienici(db, max_price: float = 50000, config: dict | None = None, **_):
    """bienici — houses and land (1 ha+) from agents across France, with map positions."""
    session = make_session(timeout=30)
    total = 0
    # The service stops at 2,400 results a search: houses are asked in price bands.
    bands = [(0, max_price / 2), (max_price / 2, max_price * 0.75), (max_price * 0.75, max_price)]
    for kind, min_area in BIENICI_SEARCHES:
        limit = max_price if kind == "house" else land_max_price(config, max_price)
        for low, high in bands if kind == "house" else [(0, limit)]:
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
                    if row and row["price"] <= limit:
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
# of up to 100 listings per type, all of France, then a pause.

NOTAIRES = "https://www.immobilier.notaires.fr"
NOTAIRES_API = NOTAIRES + "/pub-services/inotr-www-annonces/v1/annonces"
NOTAIRES_TYPES = {"MAI": "maison", "TER": "terrain"}
NOTAIRES_PAUSE = 10
NOTAIRES_MAX_PAGES = 60   # all of France in one search: ~8 pages of houses, ~31 of land
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
    # Auction end date: notaires API uses several field names depending on sale type
    date_end = (ad.get("dateFinOffres") or ad.get("dateLimiteOffres")
                or ad.get("dateAudience") or ad.get("dateVente") or ad.get("dateCloture"))
    return make_listing(
        "notaires", ad["annonceId"], "FR",
        title=f"{'Maison' if tipo == 'maison' else 'Terrain'} à {town} ({ad.get('codePostal') or ''})",
        description=description[:3000], tipo=tipo,
        area_m2=(land if tipo == "terrain" else built) or None,
        price=float(price), min_price=float(price),
        district=ad.get("departementNom"), concelho=town,
        url=ad.get("urlDetailAnnonceFr"), image_url=ad.get("urlPhotoPrincipale"),
        raw_json=json.dumps(raw, ensure_ascii=False),
        date_end=str(date_end) if date_end else None,
    )


@register("notaires", "FR", description="Notaires de France — houses, land and notary auctions all over France")
def scrape_notaires(db, max_price: float = 50000, config: dict | None = None, **_):
    """Notaires de France — houses and land (and notary auctions) all over France."""
    session = make_session(timeout=40)
    session.headers["Accept"] = "application/json"
    total, first = 0, True
    for code in NOTAIRES_TYPES:          # all of France: the score decides, not the region (owner, 2026-10-05)
        limit = land_max_price(config, max_price) if NOTAIRES_TYPES[code] == "terrain" else max_price
        for page in range(1, NOTAIRES_MAX_PAGES + 1):
            if not first:
                time.sleep(NOTAIRES_PAUSE)
            first = False
            params = {"offset": (page - 1) * 100, "page": page, "parPage": 100,
                      "typeTransactions": "VENTE,VNI,VAE", "typeBiens": code, "prixMax": int(limit)}
            try:
                resp = session.get(NOTAIRES_API, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # noqa: BLE001 — one page failing is not the source failing
                if total == 0 and page == 1 and code == next(iter(NOTAIRES_TYPES)):
                    raise
                LOG.info(f"Notaires {code} p{page}: {type(e).__name__}")
                break
            for ad in data.get("annonceResumeDto") or []:
                row = parse_notaires(ad)
                if row and row["price"] <= limit:
                    upsert_listing(db, row)
                    total += 1
            db.commit()
            if page >= (data.get("nbPages") or 1):
                break
    LOG.info(f"Notaires: {total} listings")
    return total


# ─── SAFER (proprietes-rurales.com) ─────────────────────────────────
# The rural land agencies' own sale site: forests all over France. The list
# loads 30 at a time (prod_list.php?start=…). Ads name only the département,
# never the commune, so the land is placed at the département's préfecture.
SAFER = "https://www.proprietes-rurales.com"
SAFER_FOREST = "vente-propriete-agricole/foret,11"
SAFER_MAX = 3000

# Ads name only the département: the land is placed at its préfecture, so the map,
# the climate and the distances are approximate (to ~50 km).
SAFER_PREFECTURE = {"01": "Bourg-en-Bresse", "02": "Laon", "03": "Moulins", "04": "Digne-les-Bains", "05": "Gap",
                    "06": "Nice", "07": "Privas", "08": "Charleville-Mézières", "09": "Foix", "10": "Troyes", "11":
                    "Carcassonne", "12": "Rodez", "13": "Marseille", "14": "Caen", "15": "Aurillac", "16":
                    "Angoulême", "17": "La Rochelle", "18": "Bourges", "19": "Tulle", "2A": "Ajaccio", "2B":
                    "Bastia", "21": "Dijon", "22": "Saint-Brieuc", "23": "Guéret", "24": "Périgueux", "25":
                    "Besançon", "26": "Valence", "27": "Évreux", "28": "Chartres", "29": "Quimper", "30": "Nîmes",
                    "31": "Toulouse", "32": "Auch", "33": "Bordeaux", "34": "Montpellier", "35": "Rennes", "36":
                    "Châteauroux", "37": "Tours", "38": "Grenoble", "39": "Lons-le-Saunier", "40":
                    "Mont-de-Marsan", "41": "Blois", "42": "Saint-Étienne", "43": "Le Puy-en-Velay", "44":
                    "Nantes", "45": "Orléans", "46": "Cahors", "47": "Agen", "48": "Mende", "49": "Angers", "50":
                    "Saint-Lô", "51": "Châlons-en-Champagne", "52": "Chaumont", "53": "Laval", "54": "Nancy", "55":
                    "Bar-le-Duc", "56": "Vannes", "57": "Metz", "58": "Nevers", "59": "Lille", "60": "Beauvais",
                    "61": "Alençon", "62": "Arras", "63": "Clermont-Ferrand", "64": "Pau", "65": "Tarbes", "66":
                    "Perpignan", "67": "Strasbourg", "68": "Colmar", "69": "Lyon", "70": "Vesoul", "71": "Mâcon",
                    "72": "Le Mans", "73": "Chambéry", "74": "Annecy", "75": "Paris", "76": "Rouen", "77": "Melun",
                    "78": "Versailles", "79": "Niort", "80": "Amiens", "81": "Albi", "82": "Montauban", "83":
                    "Toulon", "84": "Avignon", "85": "La Roche-sur-Yon", "86": "Poitiers", "87": "Limoges", "88":
                    "Épinal", "89": "Auxerre", "90": "Belfort", "91": "Évry-Courcouronnes", "92": "Nanterre", "93":
                    "Bobigny", "94": "Créteil", "95": "Cergy"}

_SAFER_AD = re.compile(r'<div class="res_div1" id="res_div_(VN\d+)">(.*?)(?=<div class="res_div1"|\Z)', re.S)


def safer_hectares(text: str) -> float | None:
    """"23 ha 66 a 25 ca" → 23.6625."""
    m = re.search(r"(\d+)\s*ha(?:\s+(\d+)\s*a)?(?:\s+(\d+)\s*ca)?", text or "")
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2) or 0) / 100 + int(m.group(3) or 0) / 10000


def parse_safer(block_id: str, block: str) -> dict | None:
    price = re.search(r'itemprop="price" content="(\d+)"', block)
    ha = safer_hectares(" ".join(re.findall(r"safer_land_value'>([^<]+)<", block)))
    if not price or int(price.group(1)) <= 0 or not ha:
        return None                                     # "Nous consulter": no price
    link = re.search(r'<h2 itemprop="name"><a href="([^"]+)"[^>]*>([^<]+)</a>', block)
    dept = re.search(r'class="safer_region_link" href="[^"]*/([a-z-]+),(\d+[AB]?)">', block)
    text = re.search(r'<p itemprop="description">(.*?)</p>', block, re.S)
    image = re.search(r"background-image:url\(([^)]+)\)", block)
    name = dept.group(1).replace("-", " ").title().replace(" Et ", "-et-") if dept else None
    return make_listing(
        "safer", block_id, "FR", title=html.unescape(link.group(2)) if link else "Forêt",
        description=html.unescape(re.sub(r"<[^>]+>", " ", text.group(1))).strip() if text else None,
        tipo="terreno", area_m2=ha * 10000, price=float(price.group(1)), min_price=float(price.group(1)),
        district=dept.group(2) if dept else None,
        concelho=SAFER_PREFECTURE.get(dept.group(2)) if dept else None, freguesia=name,
        url=SAFER + link.group(1) if link else None, image_url=image.group(1) if image else None)


@register("safer", "FR", description="SAFER (proprietes-rurales.com) — forests for sale all over France")
def scrape_safer(db, max_price: float = 50000, config: dict | None = None, **_):
    """SAFER (proprietes-rurales.com) — forests for sale all over France."""
    session = make_session(timeout=30)
    limit = land_max_price(config, max_price)
    total = 0
    for start in range(0, SAFER_MAX, 30):
        resp = session.get(f"{SAFER}/prod_list.php", params={
            "type_prod": "prog", "type_offer": 1, "safer_voc": "Array", "lang": "fr", "ajax_list": 1,
            "start": start, "ajax_prod_list_url": SAFER_FOREST})
        resp.raise_for_status()
        blocks = _SAFER_AD.findall(resp.text)
        for block_id, block in blocks:
            row = parse_safer(block_id, block)
            if row and row["price"] <= limit:
                upsert_listing(db, row)
                total += 1
        db.commit()
        if not blocks:
            break
        time.sleep(1)
    LOG.info(f"SAFER: {total} forests")
    return total


@register("paruvendu", "FR", description="ParuVendu — houses and land for sale across France")
def scrape_paruvendu(db, max_price: float = 50000, **_):
    """ParuVendu — houses and land for sale across France."""
    from sources._market import scrape_named
    return scrape_named(db, "paruvendu", max_price)


@register("entreparticuliers", "FR",
          description="Entreparticuliers — private-sale houses across France")
def scrape_entreparticuliers(db, max_price: float = 50000, **_):
    """Entreparticuliers — private-sale houses across France."""
    from sources._market import scrape_named
    return scrape_named(db, "entreparticuliers", max_price)


@register("iadfrance", "FR", description="IAD — agency houses and land for sale across France")
def scrape_iadfrance(db, max_price: float = 50000, **_):
    """IAD — agency houses and land for sale across France."""
    from sources._market import scrape_named
    return scrape_named(db, "iadfrance", max_price)


@register("etreproprio", "FR", description="Être Proprio — houses for sale across France")
def scrape_etreproprio(db, max_price: float = 50000, **_):
    """Être Proprio — houses for sale across France."""
    from sources._market import scrape_named
    return scrape_named(db, "etreproprio", max_price)


@register("optimhome", "FR", description="Optimhome — houses and land for sale across France")
def scrape_optimhome(db, max_price: float = 50000, **_):
    """Optimhome — houses and land for sale across France."""
    from sources._market import scrape_named
    return scrape_named(db, "optimhome", max_price)


@register("laforet", "FR", description="Laforêt — agency houses and land for sale across France")
def scrape_laforet(db, max_price: float = 50000, **_):
    """Laforêt — agency houses and land for sale across France."""
    from sources._market import scrape_named
    return scrape_named(db, "laforet", max_price)


@register("safti", "FR", description="SAFTI — houses and land for sale in the cheaper French departments")
def scrape_safti(db, max_price: float = 50000, **_):
    """SAFTI — houses and land for sale in the cheaper French departments."""
    from sources._market import scrape_named
    return scrape_named(db, "safti", max_price)


@register("citya", "FR", description="Citya — houses for sale across France")
def scrape_citya(db, max_price: float = 50000, **_):
    """Citya — houses for sale across France."""
    from sources._market import scrape_named
    return scrape_named(db, "citya", max_price)


VENCH = "https://www.vench.fr"
VENCH_LIST = f"{VENCH}/prochaines-ventes-aux-encheres.html"
VENCH_MAX_PAGES = 60          # 12 sales a page; about 48 pages (Oct 2026)


def vench_listing(card) -> dict | None:
    """One div.featured-item of vench.fr's upcoming sales as a listing.
    Descriptions are for subscribers; the card has the title, town, mise à prix and date."""
    link = card.select_one(".down-content a[href]")
    m = re.search(r"vente-(\d+)", link["href"]) if link else None
    if not m:
        return None
    heading = card.select_one("h3").get_text(" ", strip=True) if card.select_one("h3") else ""
    what, _, town = heading.rpartition("•")
    price_el = card.select_one(".miseAPrixVignette")
    date_m = re.search(r"(\d{2})/(\d{2})/(\d{2,4})", card.select_one(".dateVenteVignette").get_text()
                       if card.select_one(".dateVenteVignette") else "")
    date_end = None
    if date_m:
        d, mo, y = date_m.groups()
        date_end = f"{'20' + y if len(y) == 2 else y}-{mo}-{d}"
    img = card.select_one("img[data-src]")
    price = parse_price(price_el.get_text()) if price_el else None
    return make_listing(
        "vench", m.group(1), "FR",
        title=heading[:200], description="Vente judiciaire aux enchères (saisie or liquidation); price is the mise à prix",
        tipo=(what or heading).strip().lower()[:60] or "bien", price=price, min_price=price,
        concelho=town.strip() or None, url=f"{VENCH}/{link['href'].lstrip('./')}",
        image_url=f"{VENCH}{img['data-src']}" if img else None, date_end=date_end,
    )


@register("vench", "FR", kind="aggregator", access="public_html", docs_url=VENCH_LIST)
def scrape_vench(db, max_price: float = 100000, **_):
    """Vench — French court property auctions (saisies and liquidations judiciaires)."""
    from sources import ScanCount
    session = make_session(timeout=40)
    total, scope, pages = 0, "partial", 0
    for page in range(1, VENCH_MAX_PAGES + 1):
        try:
            resp = session.get(VENCH_LIST, params={"p": page} if page > 1 else None)
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
            LOG.warning(f"vench: page {page} failed, keeping earlier pages")
            break
        pages += 1
        cards = BeautifulSoup(resp.text, "html.parser").select("div.featured-item")
        for card in cards:
            row = vench_listing(card)
            if not row or (row["price"] and row["price"] > max_price):
                continue
            upsert_listing(db, row)
            total += 1
        db.commit()
        if len(cards) < 12:
            scope = "full"
            break
        time.sleep(1)
    LOG.info(f"vench: {total} listings")
    return ScanCount(total, scope, pages)


# ─── Agorastore (public sellers' property auctions) ────────────────
# Towns, départements, regions, the State, the forestry office (ONF) and
# AGRASC (property seized by the courts) sell homes, buildings, offices and
# land in online auctions. The site's own search API returns them all; the
# price is the starting price until a bid comes in, then the current bid.
AGORASTORE = "https://www.agorastore-immo.fr"
AGORASTORE_PROPERTY = 118
AGORASTORE_PAGE = 100
# "Maison - 70 m² - Sainte-Livrade-sur-Lot (47)": the town and département come last.
_AGORA_TITLE = re.compile(r"(?:^|\s-\s*)([^()]+?)\s*-?\s*\((\d[\dAB]?)\)\s*$")
_AGORA_SEARCH = {
    "productsId": [], "categoryId": AGORASTORE_PROPERTY, "subCategoryId": 0, "regionIds": [],
    "superRegionCodes": [], "regionCodes": [], "departmentIds": [], "categoryIds": [], "subCategoryIds": [],
    "citiesCodeInsees": [], "siteIds": [], "minPrice": 0, "maxPrice": 0, "sellerType": 0, "sellType": 3,
    "keywords": [], "specificStringFilters": [], "specificNumberFilters": [], "specificDateFilters": [],
    "sortByEnum": 0, "showCanceled": False, "showFormationSites": False, "showUndisplayedProducts": False,
    "includeExtranetSites": False, "sortBy": None, "reserveFor": 0, "bidsCount": 0, "bidComparison": 0,
    "saleRdvId": 0,
}


def agorastore_listing(product: dict) -> dict | None:
    """One product of Agorastore's search API as a listing."""
    pid, name, sale = product.get("productId"), (product.get("productName") or "").strip(), product.get("sale") or {}
    if not pid or not name:
        return None
    kinds = ", ".join(c.get("name") or "" for c in product.get("subCategories") or [])
    place = _AGORA_TITLE.search(name.rsplit(" - ", 1)[-1] if " - " in name else name)
    price = sale.get("currentPrice")
    bids = sale.get("totalBids") or 0
    facts = " · ".join(f"{d.get('libelle')}: {d.get('value')}" for d in product.get("importantDescriptions") or [])
    from common import find_area
    return make_listing(
        "agorastore", pid, "FR", title=name[:200],
        description=" · ".join(x for x in (name, kinds, facts, f"vendu par {product.get('sellerName')}",
                                           "enchères en ligne Agorastore") if x),
        tipo=kinds or None, area_m2=find_area(name),
        price=price, current_bid=price if bids else None,
        concelho=place.group(1).strip() if place else None, district=place.group(2) if place else None,
        date_end=(sale.get("endDate") or "")[:19] or None,
        url=AGORASTORE + (product.get("productPageUrl") or ""),
        image_url=product.get("productImageMediumSizeUrl"),
        raw_json=json.dumps({"seller": product.get("sellerName"), "bids": bids,
                             "favourites": product.get("favoritesCount")}, ensure_ascii=False),
    )


@register("agorastore", "FR", description="Agorastore — homes, buildings and land sold by French towns and the State")
def scrape_agorastore(db, max_price: float = 50000, **_):
    session = make_session(timeout=30)
    total, start = 0, 0
    while True:
        resp = session.post(f"{AGORASTORE}/api/search/_get-products",
                            json={"parameters": _AGORA_SEARCH, "culture": "fr", "from": start, "size": AGORASTORE_PAGE})
        resp.raise_for_status()
        data = resp.json()
        products = data.get("products") or []
        for product in products:
            row = agorastore_listing(product)
            # A whole building can be worth more than the budget and still sell to it:
            # keep what starts within twice the budget.
            if row and (row["price"] or 0) <= 2 * max_price:
                upsert_listing(db, row)
                total += 1
        db.commit()
        start += len(products)
        if not products or start >= (data.get("countTotal") or 0):
            break
        time.sleep(0.5)
    return total
