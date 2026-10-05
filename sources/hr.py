"""Croatia: e-oglasna court notices, FINA forced-sale registry (open CSV)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from datetime import datetime

from common import LOG, make_listing, make_session, parse_price
from db import upsert_listing
from sources import register

CROATIA_API = "https://e-oglasna.pravosudje.hr/api/v1/court-notice"
CROATIA_PROPERTY_KW = [
    "nekretnin", "stan", "kuć", "zemljišt", "poslovn", "garaž",
    "zgrada", "etaž", "parcela", "objekt", "dražb",
]

# "k.o. Oljasi" / "k.o. 332437, Budanica" — cadastral municipality, not the court.
_KO_PLACE = re.compile(
    r"k\.\s*o\.\s+(?:\d+[,\s]+)?([A-ZČĆŽŠĐ][^,;(]{1,40}?)"
    r"(?=\s*[,;(]|\s+k[čc]\.?b|\s+čkbr|\s+i\s+to\b|\s+na\s+adresi|\s+u\s+naravi|\s*$)",
    re.I,
)
_ODJEL_PLACE = re.compile(
    r"Zemlji[sš]noknji[zž]ni odjel\s+"
    r"([A-ZČĆŽŠĐ][\wČĆŽŠĐčćžšđ'\-]*(?:\s+[A-ZČĆŽŠĐ][\wČĆŽŠĐčćžšđ'\-]*){0,2})",
    re.I,
)
_COURT = re.compile(r"\bsud\b", re.I)


def fina_place(text: str) -> str | None:
    """The cadastral municipality in a FINA description, when it names one."""
    m = _KO_PLACE.search(text or "")
    if m:
        name = m.group(1).strip(" .)")
        if name and not name.isdigit() and len(re.sub(r"\W", "", name)) >= 3:
            return name
    m = _ODJEL_PLACE.search(text or "")
    return m.group(1).strip(" .)") if m else None


def _clear_court_district(db, row: dict) -> None:
    """Drop a stored court name that used to sit in district.

    Updates keep the old value when the new one is missing, so "Općinski sud …"
    would otherwise stay and be treated as the town for distances."""
    listing_id = row.get("id")
    if not listing_id or row.get("district"):
        return
    current = db.execute("SELECT district FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if current and current[0] and _COURT.search(current[0]):
        db.execute("UPDATE listings SET district = NULL WHERE id = ?", (listing_id,))


def _croatia_to_listing(item: dict) -> dict | None:
    uuid = item.get("uuid", "")
    title = item.get("title", "")
    if not uuid or not any(kw in title.lower() for kw in CROATIA_PROPERTY_KW):
        return None
    return make_listing(
        "croatia", uuid, "HR",
        title=title, tipo="nekretnina",
        url=item.get("publicUrl") or f"https://e-oglasna.pravosudje.hr/objava/{uuid}",
        date_end=item.get("expirationDate"),
        raw_json=json.dumps(item, ensure_ascii=False),
    )


@register("croatia", "HR")
def scrape_croatia(db, max_price: float = 50000, **_):
    """e-oglasna.pravosudje.hr — Croatian court auction notices (REST API)."""
    session = make_session(timeout=30, headers={"User-Agent": "AuctionScanner/1.0"})
    total_scraped = 0
    max_pages = 50  # limit to avoid rate-limiting

    for page in range(max_pages):
        try:
            resp = session.get(CROATIA_API, params={"filter": "", "page": page, "sort": "datePublished"})
            if resp.status_code == 429:
                LOG.warning(f"Croatia: rate limited at page {page}, stopping")
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if page == 0:
                raise
            break

        items = data.get("content", [])
        if not items:
            break
        for item in items:
            listing = _croatia_to_listing(item)
            if listing:
                upsert_listing(db, listing)
                total_scraped += 1

        db.commit()
        total_pages = data.get("totalPages", 1)
        LOG.info(f"  ... page {page+1}/{min(total_pages, max_pages)} ({total_scraped} property notices)")
        if page + 1 >= total_pages:
            break
        time.sleep(1.5)  # respect rate limits
    return total_scraped


FINA_CSV_URL = "https://ponip.fina.hr/ocevidnik-web/preuzmi/csv"


def parse_fina_csv(text: str, max_price: float, now: datetime | None = None):
    """Yield listing dicts from the FINA Očevidnik CSV export."""
    now = now or datetime.now()
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader)
    col = {h: i for i, h in enumerate(header)}

    for row in reader:
        d = {h: row[i] if i < len(row) else "" for h, i in col.items()}

        end_str = d.get("Datum i vrijeme završetka nadmetanja", "")
        if not end_str or end_str < now.strftime("%Y-%m-%d"):
            continue
        tipo = d.get("Vrsta predmeta prodaje", "").lower()
        if "nekretnina" not in tipo and "imovina" not in tipo:
            continue

        price = parse_price(d.get("Početna cijena za nadmetanje", "").replace(",", "."))
        if price and price > max_price:
            continue

        bid_id = d.get("ID nadmetanja", "")
        # md5 (not hash()) is stable across runs, so the fallback ID can stay as it was.
        eid = bid_id or hashlib.md5(
            f"{d.get('Poslovni broj spisa', '')}{end_str}".encode()).hexdigest()[:12]
        title = d.get("Opis", "")[:120]
        note = d.get("Napomena uz detalje predmeta prodaje", "")[:500] or None
        court = (d.get("Nadležno tijelo", "") or "").strip() or None
        place = fina_place(f"{title} {note or ''}")
        raw = {"sud": court} if court else None
        yield make_listing(
            "fina", eid, "HR",
            title=title or f"Nekretnina {eid}",
            description=note,
            tipo="nekretnina",
            price=price,
            min_price=parse_price(d.get(
                "Minimalna zakonska cijena ispod koje se predmet prodaje ne može prodati", ""
            ).replace(",", ".")),
            # The court is not a place: geo.municipality used to fall back to it.
            concelho=place,
            url=f"https://ponip.fina.hr/ocevidnik-web/#/predmet-prodaje/{eid}" if bid_id else None,
            date_end=end_str.replace(" ", "T"),
            raw_json=json.dumps(raw, ensure_ascii=False) if raw else None,
        )


@register("fina", "HR")
def scrape_fina_csv(db, max_price: float = 50000, **_):
    """ponip.fina.hr — Croatian forced-sale registry (open-data CSV, ~10 MB)."""
    session = make_session(timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp = session.get(FINA_CSV_URL)
    resp.raise_for_status()

    total_scraped = 0
    for listing in parse_fina_csv(resp.content.decode("utf-8-sig"), max_price):
        upsert_listing(db, listing)
        _clear_court_district(db, listing)
        total_scraped += 1
    return total_scraped


# ─── Index Oglasi (index.hr) ─────────────────────────────────────────
# Croatia's private sales (owners and agents), all over the country. The site's
# own JSON service, behind an ordinary session cookie: the search page is opened
# first. Houses and land between €1,000 and the budget (price 0 means "on request").
# The full ad (text, map position, mains water) is read once per listing, a few
# hundred a scan.

INDEX_OGLASI = "https://www.index.hr/oglasi"
INDEX_CATEGORIES = {"houses-for-sale": ("house", "prodaja-kuca"), "lands-for-sale": ("terreno", "prodaja-zemljista")}
INDEX_PAGE = 50
INDEX_MAX_PAGES = 60
INDEX_DETAILS_PER_SCAN = 400


def parse_index_ad(ad: dict, tipo: str, path: str, detail: dict | None = None) -> dict | None:
    if not ad.get("price") or (ad.get("priceCurrency") or "EUR") != "EUR":
        return None
    detail = detail or {}
    raw = {}
    if detail.get("latitude") and detail.get("longitude"):
        raw["geo"] = {"lat": detail["latitude"], "lon": detail["longitude"],
                      "precision": "street" if detail.get("isPreciseLocation") else "village"}
    notes = [detail.get("description"),
             "Gradski vodovod" if detail.get("cityWaterSupply") else None,
             "Gradska kanalizacija" if detail.get("citySewerage") else None,
             f"Godina izgradnje {detail['yearBuilt'][:4]}" if detail.get("yearBuilt") else None]
    area = (detail.get("area") or (ad.get("summary") or {}).get("area"))
    images = ad.get("images") or detail.get("images") or []
    return make_listing(
        "indexoglasi", ad["id"], "HR", title=ad.get("title"), tipo=tipo,
        description=" · ".join(n for n in notes if n)[:3000] or ad.get("title"),
        area_m2=area, price=float(ad["price"]), min_price=float(ad["price"]),
        district=ad.get("countyName"), concelho=ad.get("cityName"), freguesia=ad.get("settlementName"),
        url=f"{INDEX_OGLASI}/nekretnine/{path}/oglas/{ad.get('smartLink') or 'oglas'}/{ad['id']}",
        image_url=f"{INDEX_OGLASI}/api/image/direct/{images[0]}" if images else None,
        raw_json=json.dumps(raw) if raw else None,
    )


@register("indexoglasi", "HR", description="Index Oglasi — private houses and land all over Croatia")
def scrape_index_oglasi(db, max_price: float = 50000, **_):
    """Index Oglasi (index.hr) — private houses and land all over Croatia."""
    session = make_session(timeout=30)
    session.get(f"{INDEX_OGLASI}/nekretnine/prodaja-kuca/pretraga").raise_for_status()   # the session cookie
    headers = {"Accept": "application/json"}
    read = {r[0] for r in db.execute(
        """SELECT external_id FROM listings WHERE source = 'indexoglasi' AND raw_json LIKE '%"geo"%'""")}
    budget, total = INDEX_DETAILS_PER_SCAN, 0
    for category, (tipo, path) in INDEX_CATEGORIES.items():
        for page in range(1, INDEX_MAX_PAGES + 1):
            try:
                resp = session.get(f"{INDEX_OGLASI}/api/aditem", headers=headers, params={
                    "category": category, "module": "real-estate", "sortOption": 1, "itemPerPage": INDEX_PAGE,
                    "page": page, "priceFrom": 1000, "priceTo": int(max_price)})
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # noqa: BLE001 — one page failing is not the source failing
                if total == 0 and page == 1 and category == next(iter(INDEX_CATEGORIES)):
                    raise
                LOG.info(f"Index Oglasi {category} p{page}: {type(e).__name__}")
                break
            ads = data.get("data") or []
            for ad in ads:
                detail = None
                if ad.get("id") not in read and budget > 0 and ad.get("code"):
                    budget -= 1
                    try:
                        got = session.get(f"{INDEX_OGLASI}/api/aditem/single-ad", headers=headers,
                                          params={"code": ad["code"]})
                        detail = (got.json().get("data") or [None])[0] if got.ok else None
                    except Exception:  # noqa: BLE001 — the list entry alone is still worth keeping
                        detail = None
                    time.sleep(0.5)
                row = parse_index_ad(ad, tipo, path, detail)
                if not row or row["price"] > max_price:
                    continue
                if detail is None:
                    row.pop("raw_json", None)             # never drop a position read earlier
                    row.pop("description", None)          # nor the full text
                upsert_listing(db, row)
                total += 1
            db.commit()
            if not ads or (data.get("nextPage") or -1) < 0:
                break
            time.sleep(1)
    LOG.info(f"Index Oglasi: {total} listings")
    return total
