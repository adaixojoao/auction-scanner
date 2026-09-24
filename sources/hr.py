"""Croatia: e-oglasna court notices, FINA forced-sale registry (open CSV)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
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
        yield make_listing(
            "fina", eid, "HR",
            title=title or f"Nekretnina {eid}",
            description=d.get("Napomena uz detalje predmeta prodaje", "")[:500] or None,
            tipo="nekretnina",
            price=price,
            min_price=parse_price(d.get(
                "Minimalna zakonska cijena ispod koje se predmet prodaje ne može prodati", ""
            ).replace(",", ".")),
            district=d.get("Nadležno tijelo", ""),
            url=f"https://ponip.fina.hr/ocevidnik-web/#/predmet-prodaje/{eid}" if bid_id else None,
            date_end=end_str.replace(" ", "T"),
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
        total_scraped += 1
    return total_scraped
