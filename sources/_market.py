"""Public sale portals whose search pages were seen to list homes (October 2026).

Asking prices are in euros. A page with no cards returns nothing. The first
request of a scrape is allowed to raise so a dead site shows on Sources.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from common import (LOG, find_area, find_price, make_listing, make_session,
                    normalize, safe_url, to_number)
from db import upsert_listing


@dataclass(frozen=True)
class Portal:
    name: str
    country: str
    base: str
    urls: tuple[str, ...]          # "{max}" is the buyer's price ceiling
    href_re: str
    id_re: str
    parser: str = "links"          # links | custojusto | casasapo | willhaben | kvee | xe
    price_mode: str = "first"     # first euro amount, or the largest (a fee or €/m² listed first)
    id_last: bool = False
    min_amount: float = 0          # ignore a smaller figure left after the price rule above


_LAND = re.compile(
    r"terreno|terrain|grundst|tontti|parcelle|bauland|baugrund|grond|\bland\b|\bground\b", re.I)
# A card that says "Maison … Terrain 2 758 m²" is still a house. The path wins
# when it names one, and otherwise whichever word comes first.
_HOME_URL = re.compile(r"/(?:maison|appartement|haeuser|wohnung)(?:/|-)", re.I)
_HOME_WORD = re.compile(
    r"\b(?:maison|appartement|logement|pavillon|einfamilienhaus|zweifamilienhaus|"
    r"mehrfamilienhaus|reihenhaus|doppelhaushälfte|wohnung|haus|huis|woning|house|apartment)\b",
    re.I)
_TITLE_PREFIX = re.compile(
    r"^(?:avaa kohteen tiedot|nouvel onglet|\(nouvel onglet\)|photo n[°o]?\s*\d+|en savoir plus sur)\s*:?\s*",
    re.I)
_PATH_SKIP = {"acheter", "en", "buy", "s anzeige", "agence immobiliere", "annonce", "annonces",
              "immobilier", "vente", "kohde", "id"}
_RENT = re.compile(
    r"(/location/|zu-vermieten|/mieten\b|-miete-|for-rent|aluguer|arrendamento|/rent/|/prenajom/"
    r"|te-huur|/huur/)",
    re.I)
_UUID = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"


PORTALS: dict[str, Portal] = {
    "custojusto": Portal(
        "custojusto", "PT", "https://www.custojusto.pt",
        ("https://www.custojusto.pt/portugal/imobiliario/terrenos-venda",
         "https://www.custojusto.pt/guarda/imobiliario/moradias-venda",
         "https://www.custojusto.pt/castelo-branco/imobiliario/moradias-venda",
         "https://www.custojusto.pt/braganca/imobiliario/moradias-venda",
         "https://www.custojusto.pt/viseu/imobiliario/moradias-venda"),
        "", "", parser="custojusto"),
    "casasapo": Portal(
        "casasapo", "PT", "https://casa.sapo.pt",
        ("https://casa.sapo.pt/Venda/Terrenos/",),
        "", "", parser="casasapo"),
    "paruvendu": Portal(
        "paruvendu", "FR", "https://www.paruvendu.fr",
        ("https://www.paruvendu.fr/immobilier/vente/maison/?pxm={max}",
         "https://www.paruvendu.fr/immobilier/vente/terrain/?pxm={max}"),
        r"/immobilier/vente/", r"/(\d{7,})"),
    "entreparticuliers": Portal(
        "entreparticuliers", "FR", "https://www.entreparticuliers.com",
        ("https://www.entreparticuliers.com/annonces-immobilieres/vente/maison",),
        r"ref-\d+", r"ref-(\d+)"),
    "iadfrance": Portal(
        "iadfrance", "FR", "https://www.iadfrance.fr",
        ("https://www.iadfrance.fr/annonces/achat/maison",
         "https://www.iadfrance.fr/annonces/achat/terrain"),
        r"/annonce/", r"/r(\d{5,})"),
    "etreproprio": Portal(
        "etreproprio", "FR", "https://www.etreproprio.com",
        ("https://www.etreproprio.com/annonces/vente-maison",),
        r"/immobilier-\d+", r"/immobilier-(\d+)"),
    "optimhome": Portal(
        "optimhome", "FR", "https://www.optimhome.com",
        ("https://www.optimhome.com/fr/immobilier/vente",),
        r"/immobilier/vente/.+/\d", r"/(\d{5,})"),
    "laforet": Portal(
        "laforet", "FR", "https://www.laforet.com",
        ("https://www.laforet.com/acheter/achat-maison",
         "https://www.laforet.com/acheter/achat-terrain"),
        r"/acheter/", r"(\d{6,})", id_last=True),
    "kleinanzeigen": Portal(
        "kleinanzeigen", "DE", "https://www.kleinanzeigen.de",
        ("https://www.kleinanzeigen.de/s-haus-kaufen/preis::{max}/c208",
         "https://www.kleinanzeigen.de/s-grundstuecke-garten/preis::{max}/c207"),
        r"/s-anzeige/", r"/(\d{8,})"),
    "immowelt": Portal(
        "immowelt", "DE", "https://www.immowelt.de",
        ("https://www.immowelt.de/liste/deutschland/haeuser/kaufen?pmi=0&pma={max}",
         "https://www.immowelt.de/liste/deutschland/grundstuecke/kaufen?pmi=0&pma={max}"),
        r"/expose/", _UUID),
    "willhaben": Portal(
        "willhaben", "AT", "https://www.willhaben.at",
        ("https://www.willhaben.at/iad/immobilien/haus-kaufen/haus-angebote?PRICE_TO={max}&rows=30",
         "https://www.willhaben.at/iad/immobilien/grundstuecke/grundstueck-angebote?PRICE_TO={max}&rows=30"),
        "", "", parser="willhaben"),
    "wohnnet": Portal(
        "wohnnet", "AT", "https://www.wohnnet.at",
        ("https://www.wohnnet.at/immobilien/haus-kaufen/preis-bis-{max}",),
        r"/immobilien/.+-\d{6,}", r"-(\d{6,})"),
    "athome": Portal(
        "athome", "LU", "https://www.athome.lu",
        ("https://www.athome.lu/en/buy?price_max={max}",
         "https://www.athome.lu/en/buy/land?price_max={max}"),
        r"/id-\d+", r"id-(\d+)"),
    "myhome": Portal(
        "myhome", "IE", "https://www.myhome.ie",
        ("https://www.myhome.ie/residential/ireland/property-for-sale?maxprice={max}",),
        r"/brochure/", r"/(\d{5,})"),
    "kvee": Portal(
        "kvee", "EE", "https://www.kv.ee",
        ("https://www.kv.ee/search?deal_type=1&price_max={max}",),
        "", "", parser="kvee"),
    "habitaclia": Portal(
        "habitaclia", "ES", "https://www.habitaclia.com",
        ("https://www.habitaclia.com/viviendas-lugo.htm",
         "https://www.habitaclia.com/viviendas-ourense.htm",
         "https://www.habitaclia.com/viviendas-leon.htm"),
        r"/comprar/", _UUID),
    "etuovi": Portal(
        "etuovi", "FI", "https://www.etuovi.com",
        ("https://www.etuovi.com/myytavat-asunnot?hinta_max={max}",
         "https://www.etuovi.com/myytavat-tontit?hinta_max={max}"),
        r"/kohde/\d+", r"/kohde/(\d+)"),
    "bazos": Portal(
        "bazos", "SK", "https://reality.bazos.sk",
        ("https://reality.bazos.sk/",),
        r"/inzerat/\d+", r"/inzerat/(\d+)"),
    "realitysk": Portal(
        "realitysk", "SK", "https://www.reality.sk",
        ("https://www.reality.sk/vyhladavanie",),
        r"/", r"/((?=[A-Za-z0-9]*\d)[A-Za-z0-9]{8,16})/?$",
        price_mode="max", min_amount=5000),
    "topreality": Portal(
        "topreality", "SK", "https://www.topreality.sk",
        ("https://www.topreality.sk/",),
        "", "", parser="topreality"),
    "immovlan": Portal(
        "immovlan", "BE", "https://immovlan.be",
        ("https://immovlan.be/nl/vastgoed?transactiontypes=for-sale&propertytypes=house&maxprice={max}",
         "https://immovlan.be/nl/vastgoed?transactiontypes=for-sale&propertytypes=apartment&maxprice={max}",
         "https://immovlan.be/nl/vastgoed?transactiontypes=for-sale&propertytypes=land&maxprice={max}"),
        r"/detail/", r"/([A-Za-z]{2,4}\d{4,})", id_last=True),
    # Newest page of houses and of land in departments where prices are still low.
    # A national search 404s; these department pages were seen in October 2026.
    "safti": Portal(
        "safti", "FR", "https://www.safti.fr",
        tuple(
            f"https://www.safti.fr/annonces/achat/{kind}/{dept}"
            for kind in ("maison", "terrain")
            for dept in ("creuse-23", "lozere-48", "cantal-15", "nievre-58",
                         "indre-36", "correze-19", "meuse-55", "haute-marne-52",
                         "ariege-09", "allier-03")),
        r"/annonces/achat/(?:maison|terrain)/", r"/(\d{6,})", id_last=True),
    "citya": Portal(
        "citya", "FR", "https://www.citya.com",
        ("https://www.citya.com/annonces/vente/maison",),
        r"/annonces/vente/maison/", r"/(TMAI[A-Z0-9-]+)", id_last=True),
    # The house search only accepts a few price buckets; €300,000 is one that
    # answers. The buyer's own ceiling is applied when the row is saved.
    # Plots honour pma. A stray €120 in a card is not a price.
    "immoweltat": Portal(
        "immoweltat", "AT", "https://www.immowelt.at",
        ("https://www.immowelt.at/suche/kaufen/haus/preis--300000/osterreich/ad02at1",
         "https://www.immowelt.at/liste/oesterreich/grundstuecke/kaufen?pmi=0&pma={max}"),
        r"/expose/", _UUID, min_amount=1000),
    # The results page mixes houses and flats. The path (town/type/slug) is the
    # site's own id; there is no separate number on the card.
    "era": Portal(
        "era", "BE", "https://www.era.be",
        ("https://www.era.be/fr/a-vendre",),
        r"/fr/a-vendre/[^/]+/(?:maison|appartement|terrain)/",
        r"/a-vendre/([^?#]+)"),
    # prijs_max is the buyer's ceiling. The page is homes for sale; a card
    # marked sold or "price on request" is not an asking price.
    "remaxnl": Portal(
        "remaxnl", "NL", "https://www.remax.nl",
        ("https://www.remax.nl/koop?prijs_max={max}",),
        r"/aanbod/", r"-(\d{4,})", id_last=True),
    # Houses on the first pages start around €500,000. Land is where a plot
    # under the budget actually appears, including just over the German border.
    "wortimmo": Portal(
        "wortimmo", "LU", "https://www.wortimmo.lu",
        ("https://www.wortimmo.lu/fr/vente/terrain",
         "https://www.wortimmo.lu/fr/vente/terrain?page=2",
         "https://www.wortimmo.lu/fr/vente/terrain?page=3"),
        r"-id_\d+", r"id_(\d+)"),
}


def _amounts(text: str) -> list[float]:
    found = []
    rest = str(text or "")
    for _ in range(8):
        price = find_price(rest)
        if not price:
            break
        found.append(price)
        cut = rest.find("€")
        if cut < 0:
            cut = rest.upper().find("EUR")
        if cut < 0:
            break
        rest = rest[cut + 1:]
    return found


def _card_text(a) -> str:
    """Text of the listing card, not the whole results page."""
    label = " ".join(x for x in (a.get("title"), a.get("aria-label")) if x)
    if find_price(label):
        return f"{label} {a.get_text(' ', strip=True)}"
    node = a
    for _ in range(6):
        parent = getattr(node, "parent", None)
        if parent is None or getattr(parent, "name", None) in ("body", "html", "[document]"):
            break
        text = parent.get_text(" ", strip=True)
        if len(text) > 4000:
            break
        if find_price(text):
            return text
        node = parent
    chunk = [a.get_text(" ", strip=True)]
    sib = a.next_sibling
    for _ in range(6):
        if sib is None:
            break
        if getattr(sib, "get_text", None):
            chunk.append(sib.get_text(" ", strip=True))
        if find_price(" ".join(chunk)):
            break
        sib = sib.next_sibling
    return f"{label} {' '.join(chunk)}".strip()


def _clean_url(href: str, base: str) -> str | None:
    absolute = urljoin(base if base.endswith("/") else base + "/", href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def _path_title(path: str) -> str:
    parts = []
    for part in path.strip("/").split("/"):
        part = re.sub(r"\d{5,}", "", part).strip("- ")
        part = re.sub(r"\s+", " ", part.replace("-", " ")).strip()
        if not part or part.lower() in _PATH_SKIP or _letters(part) < 3:
            continue
        parts.append(part)
    return " ".join(parts[-3:])[:200]


def _title_from(a, text: str, path: str) -> str:
    label = a.get("title") or a.get("aria-label") or ""
    label = _TITLE_PREFIX.sub("", re.sub(r"\s+", " ", label)).strip(" -:")
    visible = _TITLE_PREFIX.sub("", re.sub(r"\s+", " ", a.get_text(" ", strip=True))).strip(" -:")
    # The photo link often comes before the heading. Prefer the one with words.
    chosen = visible if _letters(visible) > _letters(label) else label
    if _letters(chosen) >= 4:
        return chosen[:200]
    return (_path_title(path) or text)[:200]


def _image(node) -> str | None:
    img = node.find("img") if getattr(node, "find", None) else None
    src = (img.get("src") if img else None) or ""
    if not src.startswith("http") or any(x in src for x in ("1x1", "placeholder", "sprite", "data:")):
        return None
    return src


def _tipo(url: str, title: str) -> str:
    """Land when the path or the leading words say so. A house that mentions its plot stays a house."""
    if _LAND.search(url) and not _HOME_URL.search(url):
        return "terreno"
    if _HOME_URL.search(url):
        return "imovel"
    home = _HOME_WORD.search(title or "")
    land = _LAND.search(title or "")
    if home and (land is None or home.start() < land.start()):
        return "imovel"
    if land:
        return "terreno"
    return "imovel"


def _row(portal: Portal, eid: str, *, title: str, text: str, url: str,
         price: float, area=None, district=None, concelho=None, freguesia=None,
         image=None, country=None) -> dict:
    return {
        "external_id": str(eid),
        "title": (title or f"{portal.name} {eid}")[:200],
        "description": (text or "")[:1500] or None,
        "tipo": _tipo(url, title or ""),
        "area_m2": area if area is not None else find_area(text),
        "price": price,
        "district": district,
        "concelho": concelho,
        "freguesia": freguesia,
        "url": url,
        "image_url": image,
        "country": country or portal.country,
    }


def parse_links(html: str, portal: Portal) -> list[dict]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    href_re = re.compile(portal.href_re)
    id_re = re.compile(portal.id_re)
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        anchor = a.get_text(" ", strip=True)
        if not href_re.search(href) or _RENT.search(href) or _RENT.search(anchor[:80]):
            continue
        if portal.name == "remaxnl" and re.search(r"verkocht|op aanvraag", anchor, re.I):
            continue
        url = _clean_url(href, portal.base)
        if not url:
            continue
        path = urlsplit(url).path
        found = id_re.findall(path) or id_re.findall(href)
        if not found:
            continue
        eid = found[-1] if portal.id_last else found[0]
        title = _title_from(a, "", path)
        if eid in seen:
            # A later link to the same ad often carries the heading the photo link lacked.
            previous = next(row for row in out if row["external_id"] == eid)
            if _letters(title) > _letters(previous["title"]):
                previous["title"] = title
                previous["tipo"] = _tipo(url, title)
            continue
        text = _card_text(a)
        amounts = _amounts(text)
        if not amounts:
            continue
        price = max(amounts) if portal.price_mode == "max" else amounts[0]
        if price < portal.min_amount:
            continue
        seen.add(eid)
        country = portal.country
        if portal.name == "athome":
            mark = re.search(r"\(([A-Z]{2})\)", text)
            if mark and mark.group(1) in {"FR", "BE", "DE", "LU"}:
                country = mark.group(1)
        if portal.name == "wohnnet" and "deutschland" in path:
            country = "DE"
        town = None
        if portal.name == "wortimmo":
            if re.search(r"rheinland-pfalz|saarland", path, re.I):
                country = "DE"
            spot = re.search(r"-([A-Za-z]+)-id_\d+", path)
            if spot:
                town = spot.group(1).title()
        place = re.search(r"/([^/]+)/id-\d+", path)
        if place and portal.name in {"athome", "immoregion"}:
            town = place.group(1).replace("-", " ").strip().title() or None
        row = _row(
            portal, eid, title=title or _path_title(path), text=text, url=url,
            price=price, concelho=town, image=_image(a.parent or a), country=country)
        # The search is homes for sale, and the card names the street, not the type.
        if portal.name == "remaxnl" and row["tipo"] == "imovel":
            row["tipo"] = "woning"
        out.append(row)
    return out


def parse_custojusto(html: str, portal: Portal) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    items = ((data.get("props") or {}).get("pageProps") or {}).get("listItems") or []
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") not in (None, "sell"):
            continue
        price = to_number(it.get("price"))
        eid = str(it.get("listID") or "").strip()
        href = it.get("url") or ""
        if not price or not eid or not href:
            continue
        loc = it.get("locationNames") or {}
        if not isinstance(loc, dict):
            loc = {}

        def pick(*prefixes):
            for key, val in loc.items():
                if isinstance(val, str) and any(str(key).lower().startswith(p) for p in prefixes):
                    return val.strip() or None
            return None

        text = str(it.get("body") or "")
        out.append(_row(
            portal, eid, title=str(it.get("title") or ""), text=text,
            url=_clean_url(href, portal.base) or "",
            price=price, area=find_area(text),
            district=pick("dist", "region"),
            concelho=pick("mun", "council", "city"),
            freguesia=pick("par", "freg"),
            image=it.get("imageFullURL")))
    return [r for r in out if r["url"]]


def parse_casasapo(html: str, portal: Portal) -> list[dict]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for card in soup.select("div.property"):
        eid = (card.get("id") or "").split("property_", 1)[-1].strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{16,}", eid) or eid in seen:
            continue
        target = None
        for a in card.select("a[href]"):
            wanted = parse_qs(urlsplit(a["href"]).query).get("l")
            if wanted:
                target = wanted[0]
                break
        url = _clean_url(target, portal.base) if target else None
        text = card.get_text(" ", strip=True)
        price = find_price(text)
        if not url or not price:
            continue
        seen.add(eid)
        named = card.select_one("[data-title]")
        title = (named.get("data-title") if named else None) or text[:120]
        district = None
        where = re.search(r"Distrito de ([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ -]{1,40})", text)
        if where:
            district = where.group(1).strip(" -")
        out.append(_row(
            portal, eid, title=title, text=text, url=url, price=price,
            district=district, image=_image(card)))
    return out


def _willhaben_attrs(ad: dict) -> dict[str, str]:
    raw = ad.get("attributes") or {}
    items = raw.get("attribute") if isinstance(raw, dict) else raw
    out = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("values"):
            continue
        out[str(item.get("name") or "")] = str(item["values"][0])
    return out


def parse_willhaben(html: str, portal: Portal) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    result = ((data.get("props") or {}).get("pageProps") or {}).get("searchResult") or {}
    summary = ((result.get("advertSummaryList") or {}).get("advertSummary"))
    ads = summary if isinstance(summary, list) else ([summary] if isinstance(summary, dict) else [])
    out = []
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        attrs = _willhaben_attrs(ad)
        country = attrs.get("COUNTRY") or ""
        if country and "sterreich" not in normalize(country) and normalize(country) not in {"at", "austria"}:
            continue
        eid = (attrs.get("ADID") or "").strip()
        seo = attrs.get("SEO_URL") or ""
        price = to_number(attrs.get("PRICE"))
        if not eid or not seo or not price:
            continue
        url = _clean_url(seo, "https://www.willhaben.at/iad/")
        if not url:
            continue
        living = to_number(attrs.get("ESTATE_SIZE/LIVING_AREA"))
        plot = to_number(attrs.get("FREE_AREA/FREE_AREA_AREA_TOTAL"))
        text = attrs.get("BODY_DYN") or ""
        out.append(_row(
            portal, eid, title=attrs.get("HEADING") or "", text=text, url=url,
            price=price, area=living or plot,
            district=attrs.get("STATE") or None,
            concelho=attrs.get("LOCATION") or attrs.get("DISTRICT") or None))
    return out


def _ld_nodes(node, found: list):
    if isinstance(node, list):
        for item in node:
            _ld_nodes(item, found)
    elif isinstance(node, dict):
        offers = node.get("offers")
        if isinstance(offers, dict) and node.get("url") and offers.get("price"):
            found.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _ld_nodes(value, found)


def parse_kvee(html: str, portal: Portal) -> list[dict]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    nodes: list = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        try:
            _ld_nodes(json.loads(raw), nodes)
        except json.JSONDecodeError:
            continue
    out, seen = [], set()
    for node in nodes:
        offers = node.get("offers") or {}
        currency = str(offers.get("priceCurrency") or "EUR").upper()
        if currency not in {"EUR", "€"}:
            continue
        url = _clean_url(str(node.get("url")), portal.base)
        if not url or url in seen:
            continue
        price = to_number(offers.get("price"))
        if not price:
            continue
        eid_m = re.search(r"(\d{5,})", urlsplit(url).path)
        eid = eid_m.group(1) if eid_m else urlsplit(url).path.rstrip("/").split("/")[-1]
        if not eid or eid in seen:
            continue
        seen.add(eid)
        seen.add(url)
        addr = node.get("address") or {}
        size = node.get("floorSize") or {}
        area = to_number(size.get("value")) if isinstance(size, dict) else None
        out.append(_row(
            portal, eid, title=str(node.get("name") or ""), text=str(node.get("name") or ""),
            url=url, price=price, area=area,
            district=addr.get("addressRegion") if isinstance(addr, dict) else None,
            concelho=addr.get("addressLocality") if isinstance(addr, dict) else None,
            image=node.get("image") if isinstance(node.get("image"), str) else None))
    return out


def parse_topreality(html: str, portal: Portal) -> list[dict]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for el in soup.select("[data-ga4-container-item_id]"):
        if (el.get("data-ga4-container-currency") or "").upper() != "EUR":
            continue
        eid = (el.get("data-ga4-container-item_id") or "").strip()
        price = to_number(el.get("data-ga4-container-price"))
        if not eid or not price or eid in seen:
            continue
        link = el if el.name == "a" and el.get("href") else el.find("a", href=True)
        if link is None:
            link = el.find_parent("a")
        if link is None or not link.get("href"):
            continue
        url = _clean_url(link["href"], portal.base)
        if not url or _RENT.search(url):
            continue
        seen.add(eid)
        title = el.get("data-ga4-container-item_name") or ""
        out.append(_row(
            portal, eid, title=title, text=title, url=url, price=price,
            district=el.get("data-ga4-container-location_id") or None))
    return out


PARSERS = {
    "links": parse_links,
    "custojusto": parse_custojusto,
    "casasapo": parse_casasapo,
    "willhaben": parse_willhaben,
    "kvee": parse_kvee,
    "topreality": parse_topreality,
}


def parse_portal(name: str, html: str) -> list[dict]:
    portal = PORTALS[name]
    return PARSERS[portal.parser](html, portal)


def scrape_named(db, name: str, max_price: float) -> int:
    """Save listings at or under max_price. The first page's failure propagates."""
    portal = PORTALS[name]
    parser = PARSERS[portal.parser]
    session = make_session(timeout=25)
    total = 0
    seen: set[str] = set()
    first = True
    for template in portal.urls:
        url = template.format(max=int(max_price))
        try:
            resp = session.get(url)
            resp.raise_for_status()
        except Exception:
            if first:
                raise
            LOG.info(f"{name}: skipped a later page ({url})")
            continue
        first = False
        kept = 0
        for item in parser(resp.text, portal):
            eid = item["external_id"]
            price = item.get("price")
            if eid in seen or not price or price > max_price:
                continue
            seen.add(eid)
            upsert_listing(db, make_listing(
                name, eid, item.get("country") or portal.country,
                title=item["title"], description=item["description"], tipo=item["tipo"],
                area_m2=item["area_m2"], price=price, district=item["district"],
                concelho=item["concelho"], freguesia=item["freguesia"],
                url=item["url"], image_url=safe_url(item["image_url"]),
            ))
            kept += 1
        if kept:
            total += kept
            db.commit()
            time.sleep(0.8)
    LOG.info(f"{name}: {total} listings")
    return total
