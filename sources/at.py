"""Austria: willhaben.at, wohnnet.at and immowelt.at."""
from __future__ import annotations

from sources import register
from sources._market import scrape_named


@register("willhaben", "AT", description="willhaben — houses and plots for sale in Austria")
def scrape_willhaben(db, max_price: float = 50000, **_):
    """willhaben — houses and plots for sale in Austria."""
    return scrape_named(db, "willhaben", max_price)


@register("wohnnet", "AT", description="Wohnnet — homes for sale in Austria")
def scrape_wohnnet(db, max_price: float = 50000, **_):
    """Wohnnet — homes for sale in Austria."""
    return scrape_named(db, "wohnnet", max_price)


@register("immoweltat", "AT", description="Immowelt Austria — houses and plots for sale")
def scrape_immoweltat(db, max_price: float = 50000, **_):
    """Immowelt Austria — houses and plots for sale."""
    return scrape_named(db, "immoweltat", max_price)


# ─── Ediktsdatei: Austrian court auctions (Zwangsversteigerung) ────────
# The courts publish every forced sale of a property in the Ediktsdatei,
# searched one category at a time (house, flat, land, farm and forest…).
# Each notice has the valuation (Schätzwert), the lowest bid the court accepts
# (Geringstes Gebot, often half the valuation), the deposit and the date.
import re as _re
import time as _time

from bs4 import BeautifulSoup as _Soup

from common import LOG, make_listing, make_session, parse_date_dmy, parse_price
from db import upsert_listing

EDIKTE = "https://edikte.justiz.gv.at/edikte/ex/exedi3.nsf/"
EDIKTE_KINDS = ("EH", "ZH", "MH", "MW", "MS", "GG", "RH", "HA", "WE", "EW", "MA", "DT", "DG", "GA", "GW",
                "UL", "BB", "LF", "GL", "SE", "BR", "SO")
_AT_FIELD = r"{label}:\s*(.+?)(?=\s+(?:Grundstücksgröße|Objektgröße|Schätzwert|Beschreibung des|Wert des|Vadium|"\
            r"Geringstes Gebot|Sonstige Hinweise|Kategorie\(n\)|Beschreibung(?: \(\w+\))?|Grundbuch|Liegenschaftsadresse|"\
            r"PLZ/Ort|EZ|Grundstücksnr\.|BLNr|$):)"
_AT_DATE = _re.compile(r"(?:Neuer )?Versteigerungstermin:\s*am\s*(\d{1,2}\.\d{1,2}\.\d{4})")
_AT_GONE = _re.compile(r"\b(?:Entfall des Termins|Einstellung|Aufhebung)\b")


def _at_field(text: str, label: str) -> str | None:
    m = _re.search(_AT_FIELD.format(label=_re.escape(label)), text)
    return m[1].strip() if m else None


def edikt_listing(url: str, html: str) -> dict | None:
    """One court notice as a listing, or None when the date was cancelled."""
    soup = _Soup(html, "html.parser")
    text = _re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    head = text[:400]
    if _AT_GONE.search(head):
        return None
    kind = _at_field(text, "Kategorie(n)") or ""
    street = _at_field(text, "Liegenschaftsadresse") or ""
    place = _at_field(text, "PLZ/Ort") or ""
    m = _re.match(r"(\d{4})\s+(.+)", place)
    town = m[2].strip() if m else place or None
    date = _AT_DATE.search(text)
    plot = parse_price((_at_field(text, "Grundstücksgröße") or "").replace("m²", ""))
    floor = parse_price((_at_field(text, "Objektgröße") or "").replace("m²", ""))
    value = parse_price(_at_field(text, "Schätzwert") or "")
    lowest = parse_price(_at_field(text, "Geringstes Gebot") or "")
    about = _at_field(text, "Beschreibung (WE)") or _at_field(text, "Beschreibung") or ""
    court = _re.match(r"(B[GZ] [^,]+|LG [^,]+)", text)
    return make_listing(
        "edikte", url.rsplit("/", 1)[-1].split("!")[0], "AT", title=f"{kind or 'Liegenschaft'} — {street}, {place}".strip(" —,")[:200],
        description=" · ".join(x for x in (
            about[:2000], f"Grundstück {plot:g} m²" if plot else "", f"Objekt {floor:g} m²" if floor else "",
            f"Schätzwert {value:,.0f} €" if value else "", f"Vadium {_at_field(text, 'Vadium')}" if _at_field(text, "Vadium") else "",
            f"Zwangsversteigerung, {court[1]}" if court else "Zwangsversteigerung",
            "Bieter weisen ihre Staatsangehörigkeit nach; ausländische Bieter brauchen die Zustimmung der "
            "Grundverkehrsbehörde (EU-Bürger sind meist gleichgestellt)") if x),
        tipo=kind or None, area_m2=floor or plot, price=lowest or (value / 2 if value else None), min_price=lowest,
        concelho=town, date_end=parse_date_dmy(date[1]) if date else None, url=url,
        raw_json={"valuation": value, "lowest_bid": lowest, "plot_m2": plot, "floor_m2": floor,
                  "postcode": m[1] if m else None},
    )


@register("edikte", "AT", description="Ediktsdatei — Austrian court foreclosure auctions of houses, flats and land")
def scrape_edikte(db, max_price: float = 50000, **_):
    session = make_session(timeout=60)
    links: set[str] = set()
    for kind in EDIKTE_KINDS:
        try:
            page = session.get(f"{EDIKTE}suchedi?SearchView&subf=eex&SearchOrder=4&SearchMax=4999"
                               f"&retfields=~VKat={kind}&ftquery=&query=%5BVKat%5D%3D%28{kind}%29")
            page.raise_for_status()
        except Exception as e:  # noqa: BLE001 — one category failing is not the source failing
            LOG.info(f"edikte {kind}: {type(e).__name__}")
            continue
        links |= {EDIKTE + h for h in _re.findall(r'href="(alldoc/[0-9a-f]+!OpenDocument)"', page.text)}
        _time.sleep(0.3)
    total = 0
    for url in sorted(links):
        try:
            detail = session.get(url)
        except Exception as e:  # noqa: BLE001
            LOG.info(f"edikte {url}: {type(e).__name__}")
            continue
        listing = edikt_listing(url, detail.text) if detail.status_code == 200 else None
        if listing and (listing["price"] or 0) <= 2 * max_price:
            upsert_listing(db, listing)
            total += 1
        _time.sleep(0.3)
    db.commit()
    return total
