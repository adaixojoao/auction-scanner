"""Latvia: ss.lv — forest land, and houses and plots for sale."""
from __future__ import annotations

import html
import json
import re
import time

from common import LOG, find_area, find_price, land_max_price, make_listing, make_session, to_number
from db import upsert_listing
from sources import register

# ─── ss.lv ───────────────────────────────────────────────────────────
# Forest (mežs) for sale, one table per district: title, parish, hectares and
# price. Plots of 10 ha or more get their own page fetched for the full text,
# the map position and the photos. robots.txt allows all of /lv/ and /msg/lv/.

SS = "https://www.ss.lv"
SS_FOREST = f"{SS}/lv/real-estate/wood/sell/"
SS_DETAIL_MIN_HA = 10
SS_MAX_PAGES = 20

_ROW = re.compile(r'<tr id="tr_(\d+)">(.*?)</tr>', re.S)
_CELL = re.compile(r'<td class="msga2-o pp6"[^>]*>(.*?)</td>', re.S)
_LINK = re.compile(r'class="am" href="([^"]+)">(.*?)</a>', re.S)


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<br\s*/?>", ", ", re.sub(r"(?s)<(?!br)[^>]+>", "", fragment))).split())


def _number(text: str) -> float | None:
    m = re.search(r"[\d.,\s]+", text or "")
    if not m:
        return None
    digits = m.group(0).strip().replace(" ", "")
    if "ha" in text and "," not in digits:          # "2.1 ha."
        try:
            return float(digits.rstrip("."))
        except ValueError:
            return None
    digits = digits.replace(",", "").rstrip(".")
    return float(digits) if digits.replace(".", "").isdigit() else None


def parse_ss_rows(page_html: str) -> list[dict]:
    """{"id", "url", "title", "place", "ha", "price"} for each forest on a list page."""
    out = []
    for ad_id, row in _ROW.findall(page_html):
        link, cells = _LINK.search(row), [_plain(c) for c in _CELL.findall(row)]
        if not link or len(cells) < 3:
            continue
        unit = cells[-2].lower()
        size = _number(cells[-2])
        if size is not None and "m" in unit and "ha" not in unit:
            size /= 10000                                  # "5000 m²"
        out.append({"id": ad_id, "url": SS + link.group(1), "title": _plain(link.group(2)),
                    "place": cells[0], "ha": size, "price": _number(cells[-1].replace("€", ""))})
    return out


def parse_ss_detail(page_html: str) -> dict:
    """The full text, map position and photos of one ad."""
    text = ""
    m = re.search(r'id="msg_div_msg">(.*?)<table', page_html, re.S)
    if m:
        text = _plain(m.group(1))
    pos = re.search(r"[?&]c=(-?\d+\.\d+),\s*(-?\d+\.\d+)", page_html)
    photos = list(dict.fromkeys(re.findall(r'https://i\.ss\.lv/gallery/[^"\']+\.800\.jpg', page_html)))
    district = re.search(r'id="tdo_20"[^>]*>\s*<b>(.*?)</b>', page_html, re.S)
    text = text.strip(" ,")
    return {"description": text or None, "lat": float(pos.group(1)) if pos else None,
            "lon": float(pos.group(2)) if pos else None, "photos": photos,
            "district": _plain(district.group(1)) if district else None}


def _district_pages(session) -> list[str]:
    index = session.get(SS_FOREST).text
    return sorted(set(SS + p for p in re.findall(r'href="(/lv/real-estate/wood/[a-z-]+/sell/)"', index)))


@register("sslv", "LV", description="ss.lv — forest land for sale all over Latvia")
def scrape_sslv(db, max_price: float = 50000, config: dict | None = None, **_):
    """ss.lv — forest land for sale all over Latvia."""
    session = make_session(timeout=30)
    limit = land_max_price(config, max_price)
    total = 0
    for district_url in _district_pages(session):
        for page in range(1, SS_MAX_PAGES + 1):
            url = district_url if page == 1 else f"{district_url}page{page}.html"
            try:
                resp = session.get(url)
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one district failing is not the source failing
                LOG.info(f"ss.lv {url}: {type(e).__name__}")
                break
            if page > 1 and resp.url.rstrip("/") == district_url.rstrip("/"):
                break                                       # past the last page: ss.lv sends you back
            rows = parse_ss_rows(resp.text)
            for r in rows:
                if not r["price"] or r["price"] > limit or not r["ha"]:
                    continue
                extra = {"description": None, "lat": None, "lon": None, "photos": [], "district": None}
                if r["ha"] >= SS_DETAIL_MIN_HA:
                    try:
                        extra = parse_ss_detail(session.get(r["url"]).text)
                    except Exception as e:  # noqa: BLE001
                        LOG.info(f"ss.lv ad {r['id']}: {type(e).__name__}")
                    time.sleep(1)
                parish, _, village = r["place"].partition(", ")
                raw = {"photos": extra["photos"]}
                if extra["lat"] and extra["lon"]:
                    raw.update(lat=extra["lat"], lon=extra["lon"])
                upsert_listing(db, make_listing(
                    "sslv", r["id"], "LV", title=r["title"], description=extra["description"] or r["title"],
                    tipo="terreno", area_m2=r["ha"] * 10000, price=r["price"], min_price=r["price"],
                    district=extra["district"], concelho=parish, freguesia=village or None, url=r["url"],
                    image_url=extra["photos"][0] if extra["photos"] else None, raw_json=json.dumps(raw)))
                total += 1
            db.commit()
            if len(rows) < 30:
                break
            time.sleep(1)
    LOG.info(f"ss.lv: {total} forests")
    return total


# ─── ss.lv houses and plots ──────────────────────────────────────────
# The same table as the forest ads, with different columns.
# Houses: place, living m², floors, land, price.
# Plots:  place, area, €/m², price. The €/m² is not the price.

SS_SALES = (
    (f"{SS}/lv/real-estate/homes-summer-residences/sell/", "house",
     r'href="(/lv/real-estate/homes-summer-residences/[a-z-]+/sell/)"'),
    (f"{SS}/lv/real-estate/plots-and-lands/sell/", "terreno",
     r'href="(/lv/real-estate/plots-and-lands/[a-z-]+/sell/)"'),
)
SS_SALE_MAX_PAGES = 2
_SS_IMG = re.compile(r'src="(https://i\.ss\.(?:lv|com)/gallery/[^"]+)"')


def parse_ss_sale_rows(page_html: str, kind: str) -> list[dict]:
    """{"id", "url", "title", "place", "area_m2", "price", "image"} for houses or plots."""
    out = []
    for ad_id, row in _ROW.findall(page_html or ""):
        link, cells = _LINK.search(row), [_plain(c) for c in _CELL.findall(row)]
        if not link or len(cells) < 4:
            continue
        price = find_price(cells[-1])
        if not price:
            continue
        if kind == "terreno":
            area = find_area(cells[1])
        else:
            area = to_number(cells[1]) or find_area(cells[-2])
        photo = _SS_IMG.search(row)
        out.append({
            "id": ad_id, "url": SS + link.group(1), "title": _plain(link.group(2)),
            "place": cells[0], "area_m2": area, "price": price,
            "image": photo.group(1) if photo else None,
        })
    return out


def _sale_districts(session, index_url: str, pattern: str) -> list[str]:
    page = session.get(index_url)
    page.raise_for_status()
    return sorted(set(SS + path for path in re.findall(pattern, page.text)))


@register("sshomes", "LV", description="ss.lv — houses and plots for sale all over Latvia")
def scrape_sshomes(db, max_price: float = 50000, **_):
    """ss.lv — houses, summer homes and plots for sale all over Latvia.

    Forest land is the separate sslv source."""
    session = make_session(timeout=30)
    total = 0
    seen: set[str] = set()
    for index, (index_url, tipo, pattern) in enumerate(SS_SALES):
        try:
            districts = _sale_districts(session, index_url, pattern)
        except Exception:
            if total == 0 and index == 0:
                raise
            LOG.info(f"ss.lv {tipo}: index failed")
            continue
        for district_url in districts:
            region = re.search(r"/([a-z0-9-]+)-and-reg/", district_url)
            district = region.group(1).replace("-", " ").title() if region else None
            for page in range(1, SS_SALE_MAX_PAGES + 1):
                url = district_url if page == 1 else f"{district_url}page{page}.html"
                try:
                    resp = session.get(url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001
                    LOG.info(f"ss.lv {url}: {type(e).__name__}")
                    break
                if page > 1 and resp.url.rstrip("/") == district_url.rstrip("/"):
                    break
                rows = parse_ss_sale_rows(resp.text, tipo)
                for r in rows:
                    if r["id"] in seen or r["price"] > max_price:
                        continue
                    seen.add(r["id"])
                    parish, _, village = r["place"].partition(", ")
                    upsert_listing(db, make_listing(
                        "sshomes", r["id"], "LV", title=r["title"], description=r["title"],
                        tipo=tipo, area_m2=r["area_m2"], price=r["price"], min_price=r["price"],
                        district=district, concelho=parish or None, freguesia=village or None,
                        url=r["url"], image_url=r["image"]))
                    total += 1
                db.commit()
                if len(rows) < 25:
                    break
                time.sleep(0.8)
    LOG.info(f"ss.lv homes and plots: {total}")
    return total
