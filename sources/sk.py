"""Slovakia: nehnutelnosti.sk, the country's main property portal."""
from __future__ import annotations

import json
import re
import time

from common import LOG, land_max_price, make_listing, make_session
from db import upsert_listing
from sources import register

# ─── nehnutelnosti.sk ────────────────────────────────────────────────
# Houses and land (1 ha or more) all over Slovakia, owners and agents. The
# results page carries its data in the Next.js stream (self.__next_f): each ad is
# an {"advertisement": {...}} record; its text is a reference ("$2d") to a text
# chunk ("2d:T<hex length>,…") elsewhere in the same stream. 30 ads a page.
# A price under €1,000 means "on request" and is skipped.

NEHNUTELNOSTI = "https://www.nehnutelnosti.sk"
NEHNUTELNOSTI_SEARCHES = {"domy": ("house", None), "pozemky": ("terreno", 10000)}
NEHNUTELNOSTI_MAX_PAGES = 40
NEHNUTELNOSTI_MIN_EUR = 1000

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_AD = re.compile(r'\{"advertisement":\{')


def nehnutelnosti_stream(page_html: str) -> str:
    return "".join(json.loads(f'"{chunk}"') for chunk in _PUSH.findall(page_html))


def nehnutelnosti_ads(stream: str) -> list[dict]:
    decoder, ads = json.JSONDecoder(), []
    for m in _AD.finditer(stream):
        try:
            ads.append(decoder.raw_decode(stream, m.start())[0]["advertisement"])
        except (ValueError, KeyError):
            continue
    return ads


def _text(stream: str, ref) -> str | None:
    """The text a "$2d"-style reference points to, else the value itself."""
    if not isinstance(ref, str) or not ref.startswith("$"):
        return ref
    m = re.search(r"(?:^|\n|\})" + re.escape(ref[1:]) + r":T([0-9a-f]+),", stream)
    if not m:
        return None
    return stream[m.end():].encode("utf-8")[:int(m.group(1), 16)].decode("utf-8", "ignore")


def parse_nehnutelnosti(ad: dict, tipo: str, stream: str = "") -> dict | None:
    price = (ad.get("price") or {}).get("priceNum")
    if not price or price < NEHNUTELNOSTI_MIN_EUR:
        return None
    where = ad.get("location") or {}
    params = ad.get("parameters") or {}
    state = params.get("realEstateState")
    kind = ((params.get("category") or {}).get("subValue") or "").replace("_", " ").lower()
    text = _text(stream, ad.get("description"))
    description = " · ".join(x for x in (text, f"Stav: {state}" if state else None, kind or None) if x)
    photos = ad.get("photos") or []
    return make_listing(
        "nehnutelnosti", ad["id"], "SK", title=ad.get("title"), description=description[:3000] or None, tipo=tipo,
        area_m2=params.get("area"), price=float(price), min_price=float(price),
        district=where.get("county"), concelho=where.get("city"), freguesia=where.get("district"),
        url=f"{NEHNUTELNOSTI}/detail/{ad['id']}/{ad.get('sefName') or ''}".rstrip("/"),
        image_url=(photos[0] or {}).get("url") if photos else None,
    )


@register("nehnutelnosti", "SK", description="nehnutelnosti.sk — houses and land (1 ha+) all over Slovakia")
def scrape_nehnutelnosti(db, max_price: float = 50000, config: dict | None = None, **_):
    """nehnutelnosti.sk — houses and land (1 ha+) all over Slovakia."""
    session = make_session(timeout=30)
    total = 0
    for slug, (tipo, min_area) in NEHNUTELNOSTI_SEARCHES.items():
        limit = land_max_price(config, max_price) if tipo == "terreno" else max_price
        params = {"priceTo": int(limit)}
        if min_area:
            params["areaFrom"] = min_area
        for page in range(1, NEHNUTELNOSTI_MAX_PAGES + 1):
            try:
                resp = session.get(f"{NEHNUTELNOSTI}/vysledky/{slug}/predaj",
                                   params={**params, **({"page": page} if page > 1 else {})})
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one page failing is not the source failing
                if total == 0 and page == 1 and slug == next(iter(NEHNUTELNOSTI_SEARCHES)):
                    raise
                LOG.info(f"nehnutelnosti.sk {slug} p{page}: {type(e).__name__}")
                break
            stream = nehnutelnosti_stream(resp.text)
            ads = nehnutelnosti_ads(stream)
            for ad in ads:
                row = parse_nehnutelnosti(ad, tipo, stream)
                if row and row["price"] <= limit:
                    upsert_listing(db, row)
                    total += 1
            db.commit()
            if len(ads) < 30:
                break
            time.sleep(1)
    LOG.info(f"nehnutelnosti.sk: {total} listings")
    return total


@register("bazos", "SK", description="Bazoš reality — homes for sale in Slovakia")
def scrape_bazos(db, max_price: float = 50000, **_):
    """Bazoš reality — homes for sale in Slovakia."""
    from sources._market import scrape_named
    return scrape_named(db, "bazos", max_price)


@register("realitysk", "SK", description="Reality.sk — homes for sale in Slovakia")
def scrape_realitysk(db, max_price: float = 50000, **_):
    """Reality.sk — homes for sale in Slovakia."""
    from sources._market import scrape_named
    return scrape_named(db, "realitysk", max_price)


@register("topreality", "SK", description="TopReality — homes for sale in Slovakia")
def scrape_topreality(db, max_price: float = 50000, **_):
    """TopReality — homes for sale in Slovakia."""
    from sources._market import scrape_named
    return scrape_named(db, "topreality", max_price)
