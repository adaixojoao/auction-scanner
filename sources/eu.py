"""Pan-European aggregators."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from common import LOG, land_max_price, make_listing, make_session, safe_url, stable_id, to_number
from db import upsert_listing
from sources import register

COURTBID_COUNTRIES = ["PT", "ES", "FR", "DE", "IT", "NL", "HR", "GR", "BE", "RO", "PL", "CY"]


@register("courtbid", "EU", default=False)
def scrape_courtbid(db, max_price: float = 100000, config: dict | None = None, **_):
    """CourtBid via Apify — 17-country distressed property feed. Needs
    "apify_token" in config.json; costs Apify credits, so it only runs with
    --source courtbid."""
    token = (config or {}).get("apify_token", "")
    if not token:
        raise RuntimeError("no apify_token in config.json")

    session = make_session(timeout=30, headers={"Authorization": f"Bearer {token}"})
    run_resp = session.post(
        "https://api.apify.com/v2/acts/studio-amba~distressed-property-feed/runs",
        json={"maxPrice": int(max_price), "propertyType": "residential",
              "countries": COURTBID_COUNTRIES})
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]

    for _attempt in range(30):
        time.sleep(10)
        try:
            status = session.get(f"https://api.apify.com/v2/actor-runs/{run_id}", timeout=15).json()["data"]["status"]
        except Exception:
            continue
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {status}")

    items_resp = session.get(f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items",
                             params={"format": "json", "limit": 5000}, timeout=60)
    items_resp.raise_for_status()

    total = 0
    for item in items_resp.json():
        price = to_number(item.get("reservePrice")) or to_number(item.get("price")) or 0
        if price and price > max_price:
            continue
        country = (item.get("country") or "EU").upper()[:2]
        eid = str(item.get("id") or item.get("lotId")
                  or stable_id(json.dumps(item, sort_keys=True, default=str)))
        upsert_listing(db, make_listing(
            "courtbid", eid, country,
            title=str(item.get("title") or item.get("description") or f"CourtBid #{eid}")[:200],
            description=item.get("description") or item.get("details"),
            tipo=item.get("propertyType") or "imovel",
            area_m2=item.get("area") or item.get("surfaceArea"),
            price=price, current_bid=item.get("currentBid"),
            min_price=item.get("minimumBid") or price,
            district=item.get("region") or item.get("province"),
            concelho=item.get("city") or item.get("municipality"),
            url=item.get("url") or item.get("sourceUrl"),
            image_url=item.get("imageUrl"),
            date_end=item.get("auctionDate") or item.get("endDate"),
            raw_json=json.dumps(item, ensure_ascii=False)[:2000],
        ))
        total += 1
    LOG.info(f"CourtBid (Apify): {total} listings across EU")
    return total


# ─── Green-Acres: rural homes and land from agents, one site per country ───
# Not auctions: agents' asking prices. Here for the farms and land by water in
# mild places that court sales rarely have. The search is in the URL
# ("searchQuery=hab_land-on-mx_p-50000-mn_l_s-10000"); a card has the price,
# town and sizes, the detail page the full text and the map position.
GREENACRES_SITES = {"FR": "https://www.green-acres.fr", "PT": "https://www.green-acres.pt",
                    "ES": "https://www.green-acres.es", "IT": "https://www.green-acres.it"}
GREENACRES_SEARCHES = (("hab_house-on", None), ("hab_land-on", 10000))   # homes; land from 1 ha
GREENACRES_MAX_PAGES = 30
GREENACRES_DETAILS_PER_SCAN = 400


def greenacres_query(kind: str, max_price: float, min_land: int | None) -> str:
    q = f"{kind}-mx_p-{int(max_price)}"
    return q + (f"-mn_l_s-{min_land}" if min_land else "")


def _ga_size(text: str) -> float | None:
    from common import find_area
    return find_area(text.replace("\u202f", " ").replace("\xa0", " ").replace("hectares", "ha"))


def _ga_town(place: str, country: str) -> str | None:
    """The municipality of a card. In Portugal "Pitões (Montalegre)" is the
    village and its municipality; elsewhere "Sedella (Málaga)", "Gerzat
    (Puy-de-Dôme)" and "San Remo (Imperia)" are the town and its province."""
    import re
    inside = re.search(r"\(([^)]+)\)", place)
    before = place.split("(")[0].split(",")[0].strip()
    if country == "PT" and inside:
        return inside.group(1).strip() or None
    return before or (inside.group(1).strip() if inside else None)


def parse_greenacres_page(html: str, country: str) -> list[dict]:
    import base64
    import re
    from bs4 import BeautifulSoup
    from common import parse_price
    rows = []
    for card in BeautifulSoup(html, "html.parser").select("div.announce-card[data-advertid]"):
        try:
            url = base64.b64decode(card.get("data-o") or "").decode()
        except ValueError:
            continue
        if not url.startswith("https://www.green-acres."):
            continue
        info = card.select_one(".announce-info")
        title = (info.get("title") if info else "") or ""
        price_el = card.select_one(".info-price")
        price = parse_price(price_el.get_text(" ", strip=True).replace("\u202f", "")) if price_el else None
        place = card.select_one(".announce-localisation")
        place = place.get_text(" ", strip=True) if place else ""
        tags = {t.get("title", ""): t.get_text(" ", strip=True) for t in card.select(".info-tag")}
        living = next((_ga_size(v) for k, v in tags.items() if "habitable" in k.lower()), None)
        land = next((_ga_size(v) for k, v in tags.items() if k.lower().startswith("terrain")), None)
        kind = url.split("/properties/")[1].split("/")[0] if "/properties/" in url else ""
        is_land = kind.startswith("terrain")
        img = card.select_one("img.announce-card-img")
        rows.append(make_listing(
            "greenacres", card["data-advertid"], country, title=title[:200],
            description=" · ".join(x for x in (title, place, f"terrain {land:.0f} m²" if land and not is_land else "")
                                   if x),
            tipo="terreno" if is_land else kind or None,
            area_m2=round(land if is_land else living) if (land if is_land else living) else None,
            price=price, min_price=price, concelho=_ga_town(place, country),
            url=url, image_url=img.get("src") if img else None,
            raw_json=json.dumps({"land_m2": land, "living_m2": living}, ensure_ascii=False),
        ))
    return rows


def greenacres_detail(html: str) -> dict:
    """The full text and the map position of a Green-Acres advert."""
    import re
    from bs4 import BeautifulSoup
    out: dict = {}
    el = BeautifulSoup(html, "html.parser").select_one(".description-container")
    if el:
        out["descricao"] = el.get_text(" ", strip=True)[:3000]
    m = re.search(r"coordinates:\s*\{\s*latitude:\s*(-?\d+\.\d+),\s*longitude:\s*(-?\d+\.\d+)", html)
    if m and float(m.group(1)) and float(m.group(2)):
        precise = re.search(r"isPreciseLocation['\"]?\s*[:=]\s*['\"]?true", html, re.I)
        out["geo"] = {"lat": float(m.group(1)), "lon": float(m.group(2)),
                      "precision": "street" if precise else "village"}
    return out


@register("greenacres", "EU")
def scrape_greenacres(db, max_price: float = 50000, config: dict | None = None, **_):
    """green-acres — rural homes and land (1 ha+) from agents in FR, PT, ES and IT."""
    session = make_session(timeout=30)
    total = 0
    for country, site in GREENACRES_SITES.items():
        details_left = GREENACRES_DETAILS_PER_SCAN // len(GREENACRES_SITES)   # each country gets its share
        found: list[dict] = []
        for kind, min_land in GREENACRES_SEARCHES:
            limit = land_max_price(config, max_price) if min_land else max_price
            query = greenacres_query(kind, limit, min_land)
            for page in range(1, GREENACRES_MAX_PAGES + 1):
                try:
                    resp = session.get(f"{site}/maison-a-vendre", params={"searchQuery": query, "p_n": page})
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001 — one search failing is not the source failing
                    if not found and country == "FR" and page == 1:
                        raise
                    LOG.info(f"Green-Acres {country} {kind} p{page}: {type(e).__name__}")
                    break
                rows = parse_greenacres_page(resp.text, country)
                if not rows:
                    break
                found += [r for r in rows if r["price"] and r["price"] <= limit]
                time.sleep(0.5)
        found = list({row["id"]: row for row in found}.values())      # a card can match both searches
        found.sort(key=lambda r: (r["tipo"] != "terreno", r["price"]))  # details for land first, then cheapest
        for row in found:
            known = db.execute("SELECT raw_json FROM listings WHERE id = ?", (row["id"],)).fetchone()
            kept = json.loads(known[0]) if known and known[0] else {}
            if not kept.get("detail_checked") and details_left > 0:
                details_left -= 1
                try:
                    resp = session.get(row["url"])
                    resp.raise_for_status()
                    kept.update(greenacres_detail(resp.text), detail_checked=1)
                except Exception as e:  # noqa: BLE001 — the card alone is still a listing
                    LOG.info(f"Green-Acres details of {row['id']} failed ({type(e).__name__})")
                time.sleep(0.4)
            raw = {**json.loads(row["raw_json"]), **kept}
            if raw.get("descricao"):
                row["description"] = f"{raw['descricao']} · {row['description'] or ''}"[:3000]
            row["raw_json"] = json.dumps(raw, ensure_ascii=False)
            upsert_listing(db, row)
            total += 1
        db.commit()
        LOG.info(f"Green-Acres {country}: {len(found)} listings")
    return total


# ─── Troostwijk (TBAuctions) ─────────────────────────────────────────
# Troostwijk and BVA run on one platform; its real-estate category (houses,
# land, commercial and industrial property across Europe) is read from each
# sub-category page's Next.js data, so a lot's kind comes from where it sits.
TROOSTWIJK = "https://www.troostwijkauctions.com"
TROOSTWIJK_CATEGORIES = {
    "huizen/22d00fcf-7627-452d-9dd4-746cad8dadee": "vastgoed",
    "gronden/dfc469c5-d053-4ff5-858a-5eef223d80e2": "terreno",
    "commercieel-vastgoed/cbe5b336-370a-4b5d-891e-9a289a5b30b6": "commercieel",
    "industrieel-onroerend-goed/12672a1a-8b59-492c-9dd8-40dbaf4f3f4b": "commercieel",
}


def troostwijk_listing(lot: dict, tipo: str) -> dict | None:
    lid = lot.get("displayId") or lot.get("id")
    if not lid or not lot.get("urlSlug"):
        return None
    place = lot.get("location") or {}
    bid = ((lot.get("currentBidAmount") or {}).get("cents") or 0) / 100
    end = lot.get("endDate")
    raw = {k: lot.get(k) for k in ("auctionId", "biddingStatus", "bidsCount", "saleTerm", "platform")}
    # The bid so far, not a price: an opening bid of €10 says nothing of the value.
    return make_listing(
        "troostwijk", lid, (place.get("countryCode") or "nl").upper(),
        title=lot.get("title"), description=lot.get("description"), tipo=tipo,
        current_bid=bid or None, concelho=place.get("city"),
        url=safe_url(f"{TROOSTWIJK}/nl/l/{lot['urlSlug']}"),
        image_url=safe_url((lot.get("image") or {}).get("url") or ""),
        date_end=datetime.fromtimestamp(end, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if end else None,
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


def troostwijk_lots(html: str) -> tuple[list[dict], int]:
    """The lots on a category page and how many the category holds."""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    data = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("lotsData") or {} if m else {}
    return data.get("results") or [], data.get("totalSize") or 0


@register("troostwijk", "EU", description="Troostwijk / BVA — real estate and land auctions across Europe")
def scrape_troostwijk(db, max_price: float = 100000, **_):
    """Troostwijk Auctions (also BVA) — houses, land and commercial property."""
    session = make_session(timeout=30)
    rows: dict[str, dict] = {}
    first = True
    for path, tipo in TROOSTWIJK_CATEGORIES.items():
        for page in range(1, 20):
            try:
                resp = session.get(f"{TROOSTWIJK}/nl/c/vastgoed/{path}", params={"page": page} if page > 1 else None)
                resp.raise_for_status()
            except Exception:
                if first:
                    raise                     # a dead site must show on the Sources page
                LOG.info(f"Troostwijk {path} p{page} failed")
                break
            first = False
            lots, total = troostwijk_lots(resp.text)
            for row in (troostwijk_listing(lot, tipo) for lot in lots):
                if row:
                    rows.setdefault(row["id"], row)
            if not lots or page * max(len(lots), 1) >= total:
                break
            time.sleep(0.5)
    for row in rows.values():
        upsert_listing(db, row)
    db.commit()
    LOG.info(f"Troostwijk: {len(rows)} real-estate lots")
    return len(rows)
