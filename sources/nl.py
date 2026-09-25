"""Netherlands: openbareverkoop.nl, veilingnotaris.nl, veilingbiljet.nl."""
from __future__ import annotations

import json
import re

import time
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from common import LOG, find_price, make_listing, make_session, parse_date_dmy, parse_price, safe_url
from db import upsert_listing
from sources import register
from sources._cards import listing_id_from_url

NL_DETAILS_PER_SCAN = 80
_MONTHS = {m: i for i, m in enumerate(("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
                                        "september", "oktober", "november", "december"), 1)}


def _page_text(html: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", BeautifulSoup(body, "html.parser").get_text(" ")).strip()


def dutch_date(text: str) -> str | None:
    """"8 oktober 2026 vanaf 13:30 uur" / "woensdag 30 september 2026 14:00" -> ISO."""
    m = re.search(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})(?:\D{0,12}?(\d{1,2}):(\d{2}))?",
                  text or "", re.I)
    if not m:
        return None
    d, month, y, h, mi = m.groups()
    return f"{y}-{_MONTHS[month.lower()]:02d}-{int(d):02d}T{int(h or 0):02d}:{mi or '00'}:00"


def _field(text: str, label: str, stop: str) -> str | None:
    m = re.search(re.escape(label) + r"\s+(.+?)\s+(?:" + stop + r")", text)
    return m.group(1).strip() if m else None


def nl_details(html: str) -> dict:
    """What a Dutch auction page says about the property: the deed's
    description, type, use (rented?), year built, living and plot area, date."""
    text = _page_text(html)
    out: dict = {}
    stop = ("Gebruik|Soort eigendom|Bouwjaar|Oppervlakte|Inhoud|Aantal|Kadastrale|Gebruikssituatie|"
            "Bezichtiging|Over de veiling|Soort|Datum|Organisator|Informatie|$")
    for key, label in (("type", "Type registergoed"), ("type", "Type"), ("gebruik", "Gebruik"),
                       ("gebruik", "Gebruikssituatie"), ("bouwjaar", "Bouwjaar")):
        value = _field(text, label, stop)
        if value and key not in out and len(value) < 60:
            out[key] = value
    wonen = re.search(r"Oppervlakte wonen\s+([\d.,]+)\s*m\s?2", text)
    perceel = re.search(r"Oppervlakte perceel\s+([\d.,]+)\s*m\s?2", text)
    if wonen:
        out["wonen_m2"] = parse_price(wonen.group(1))
    if perceel:
        out["perceel_m2"] = parse_price(perceel.group(1))
    # The deed's own words, the most telling kind first ("het woonpand met ondergrond…").
    for word in ("woonpand", "woonhuis", "appartementsrecht", "de woning", "recreatiewoning", "chalet",
                 "bedrijfspand", "perceel", "registergoed"):
        deed = re.search(r"((?:het |de )?" + word + r"\b(?:[^.]|\.(?=\S)){20,1200}?)(?:\s+Kadastrale gegevens|"
                         r"\s+Voor een indicatie|\s+Bekijk online|\s+Gebruikssituatie:|\.\s)", text, re.I)
        if deed:
            out["omschrijving"] = deed.group(1).strip()[:1500]
            break
    when = re.search(r"(?:Veiling|Datum)\s+((?:[a-z]+dag\s+)?\d{1,2}\s+[a-z]+\s+\d{4}[^A-Z]{0,25})", text)
    if when:
        out["datum"] = dutch_date(when.group(1))
    if re.search(r"\bUitgesteld\b|\bIngetrokken\b", text):
        out["uitgesteld"] = True
    return out


def _with_details(row: dict, details: dict) -> dict:
    """The listing, filled in from its page."""
    raw = json.loads(row.get("raw_json") or "{}")
    raw.update(details, detail_checked=1)
    if str(details.get("bouwjaar", "")).isdigit():
        raw["ano_construcao"] = details["bouwjaar"]              # scoring.built_year reads this
    parts = [details.get("omschrijving"), f"Type: {details['type']}" if details.get("type") else None,
             f"Gebruik: {details['gebruik']}" if details.get("gebruik") else None,
             f"Bouwjaar {details['bouwjaar']}" if details.get("bouwjaar") else None,
             f"Perceel {details['perceel_m2']:.0f} m2" if details.get("perceel_m2") else None,
             "Veiling uitgesteld" if details.get("uitgesteld") else None, row.get("description")]
    row["description"] = ". ".join(p for p in parts if p)[:3000]
    # Only the living area: the deed's "groot 498 m2" is the plot, or a flat's whole building.
    row["area_m2"] = details.get("wonen_m2") or row.get("area_m2")
    row["date_end"] = details.get("datum") or row.get("date_end")
    row["raw_json"] = json.dumps(raw, ensure_ascii=False, default=str)
    return row


def _read_details(db, session, rows: list[dict], budget: list[int]) -> None:
    """Each listing's page once (kept in raw_json), a few a scan."""
    for row in rows:
        known = db.execute("SELECT raw_json FROM listings WHERE id = ?", (row["id"],)).fetchone()
        if known and known[0] and '"detail_checked"' in known[0]:
            kept = json.loads(known[0])
            row["raw_json"] = json.dumps({**kept, **json.loads(row.get("raw_json") or "{}")}, ensure_ascii=False)
            continue
        if budget[0] <= 0 or not row.get("url"):
            continue
        budget[0] -= 1
        try:
            resp = session.get(row["url"])
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001: the list entry is still a listing
            LOG.info(f"NL details of {row['id']} failed ({type(e).__name__})")
            continue
        _with_details(row, nl_details(resp.text))
        time.sleep(0.3)


def _ms_date(value) -> str | None:
    """"/Date(1791459000000)/" -> ISO (UTC)."""
    m = re.search(r"\d{10,}", str(value or ""))
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(0)) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def netherlands_listing(obj: dict, base: str = "https://www.openbareverkoop.nl") -> dict | None:
    eid = str(obj.get("id", "") or "")
    if not eid:
        return None
    # NL auctions don't publish the property price upfront;
    # "veilingkosten" is the auction FEE, not the property value.
    price = None
    for field in ("inzet", "afslag"):
        val = obj.get(field, "")
        if val:
            price = parse_price(str(val).replace("€", ""))
            break
    title = obj.get("kavelNaam", "")
    wtype = obj.get("woningtype", "")
    if wtype:
        title = f"{title} ({wtype})"
    vtype = obj.get("veilingwijze", "")
    town = (obj.get("kavelNaam") or "").rsplit(",", 1)
    raw = {k: obj.get(k) for k in ("id", "woningtype", "veilingwijze", "status", "organisatie", "zittingId")}
    if obj.get("lat") and obj.get("lng"):
        raw.update(lat=obj["lat"], lon=obj["lng"])                  # geo.position reads these
    return make_listing(
        "netherlands", eid, "NL",
        title=title,
        description=f"Executieveiling ({vtype})" if vtype else "Executieveiling",
        tipo="vastgoed", price=price,
        concelho=town[1].strip().title() if len(town) == 2 else None,
        url=obj.get("url", ""), image_url=obj.get("image", ""), base_url=base,
        date_end=_ms_date(obj.get("zittingdatum")),
        raw_json=json.dumps(raw, ensure_ascii=False, default=str),
    )


@register("netherlands", "NL")
def scrape_netherlands(db, max_price: float = 50000, **_):
    """openbareverkoop.nl — Dutch public (notarial) property auctions (JSON)."""
    session = make_session(timeout=30)
    base = "https://www.openbareverkoop.nl"
    session.get(f"{base}/kavels?view=resultaten")
    resp = session.post(f"{base}/kavels/searchresults",
                        data={"text": "", "view": "", "periode": "alles", "woningtype": ""})
    resp.raise_for_status()
    data = resp.json()

    rows = [row for zitting in data.get("results", []) for opr in zitting.get("objectenPerRegio", [])
            for row in (netherlands_listing(obj, base) for obj in opr.get("objects", [])) if row]
    _read_details(db, session, rows, [NL_DETAILS_PER_SCAN])
    for row in rows:
        upsert_listing(db, row)
    db.commit()
    return len(rows)


@register("veilingnotaris", "NL")
def scrape_veilingnotaris(db, max_price: float = 50000, **_):
    """veilingnotaris.nl and vastgoedveiling.nl — Dutch execution auctions (one platform)."""
    session = make_session(timeout=15)
    base = "https://veilingnotaris.nl"
    rows, seen = [], set()
    for page in range(1, 20):
        resp = session.get(f"{base}/veilingen/", params={"page": page} if page > 1 else None)
        resp.raise_for_status()
        new = 0
        for a in BeautifulSoup(resp.text, "html.parser").select("a[href]"):
            href = a.get("href", "")
            m = re.search(r"/veilingen/(\d+)/([^/]+)/", href)
            if not m or m.group(1) in seen:
                continue
            eid, slug = m.group(1), m.group(2)
            seen.add(eid)
            new += 1
            text = a.get_text(" ", strip=True)
            place, _, street = slug.replace("-", " ").partition("_")
            address = f"{place.title()}, {street.replace('_', ' ').title()}" if street else place.title()
            wtype = ""
            for t in ["Appartement", "Woonhuis", "Tussenwoning", "Hoekwoning", "Bovenwoning",
                      "Twee-onder-een-kap", "Vrijstaand", "Recreatiewoning", "Bedrijfspand", "Winkel", "Kantoor"]:
                if t.lower() in text.lower():
                    wtype = t
                    break
            rows.append(make_listing(
                "veilingnotaris", eid, "NL",
                title=f"{address} ({wtype})" if wtype else address, concelho=place.title() or None,
                tipo="vastgoed", url=href, base_url=base,
            ))
        if not new:
            break
        time.sleep(0.3)
    try:
        richer = {row["id"]: row for row in vastgoedveiling_rows(session)}
    except Exception as e:  # noqa: BLE001: veilingnotaris.nl alone is still worth it
        LOG.warning(f"vastgoedveiling.nl failed ({type(e).__name__}); veilingnotaris.nl only")
        richer = {}
    rows = [richer.pop(row["id"], row) for row in rows] + list(richer.values())
    _read_details(db, session, [r for r in rows if '"detail_checked"' not in (r.get("raw_json") or "")],
                  [NL_DETAILS_PER_SCAN])
    for row in rows:
        upsert_listing(db, row)
    db.commit()
    return len(rows)


# vastgoedveiling.nl runs on the same auction platform as veilingnotaris.nl (the
# same auction numbers) and lists more of it: 85 open auctions against 42, with
# size, year built, use and position in its page data. Both are read into one
# source, so an auction on both sites is one listing.
VASTGOEDVEILING = "https://vastgoedveiling.nl"
_COUNTRIES = {"nl": "NL", "de": "DE", "be": "BE", "fr": "FR", "es": "ES", "pt": "PT"}
_USE = {"huurbeding_is_niet_ingeroepen": "bewoond (huurbeding niet ingeroepen)", "verhuurd": "verhuurd",
        "leeg": "leeg opgeleverd", "eigen_gebruik": "bewoond/in eigen gebruik"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _bag_number(value) -> float | None:
    """"BAG: 114" / "92" / "1.250" -> a number."""
    return parse_price(re.sub(r"^\s*BAG:\s*", "", str(value or ""))) or None


def vastgoedveiling_listing(a: dict) -> dict | None:
    lid = a.get("id")
    country = _COUNTRIES.get((a.get("land") or "nl").lower())
    if not lid or not country:
        return None
    use = a.get("gebruikssituatie") or ""
    year = re.sub(r"^\s*BAG:\s*", "", str(a.get("bouwjaar") or "")).strip()
    parts = [re.sub(r"\s+", " ", a.get("kavelbeschrijving") or a.get("kadastrale_gegevens") or "").strip()[:1500],
             f"Type: {a['object_type']}" if a.get("object_type") else None,
             f"Gebruik: {_USE.get(use, use.replace('_', ' '))}" if use else None,
             f"Bouwjaar {year}" if year else None,
             f"Perceel {a['oppervlakte_perceel']} m2" if a.get("oppervlakte_perceel") else None,
             (a.get("type_verkoop") or "").capitalize() or None]
    raw = {"detail_checked": 1, "object_type": a.get("object_type"), "categorie": a.get("object_type_categorie"),
           "type_verkoop": a.get("type_verkoop"), "gebruikssituatie": use, "provincie": a.get("provincie")}
    if year.isdigit():
        raw["ano_construcao"] = year
    lat = a.get("latitude_custom") or a.get("latitude")
    lon = a.get("longitude_custom") or a.get("longitude")
    if lat and lon:
        raw.update(lat=lat, lon=lon)
    name = a.get("name") or f"{a.get('plaats') or ''}, {a.get('straat') or ''}"
    wtype = a.get("object_type")
    return make_listing(
        "veilingnotaris", str(lid), country,
        title=f"{name} ({wtype})" if wtype else name,
        description=". ".join(p for p in parts if p)[:3000] or None, tipo="vastgoed",
        area_m2=_bag_number(a.get("oppervlakte_object")), district=a.get("provincie"),
        concelho=a.get("plaats"), url=f"{VASTGOEDVEILING}/veiling/{lid}/{_slug(name)}",
        image_url=a.get("thumb"), date_end=(a.get("eindtijd") or a.get("starttijd") or "")[:19] or None,
        raw_json=raw,
    )


def vastgoedveiling_rows(session) -> list[dict]:
    """The open auctions on vastgoedveiling.nl (the page's Next.js data)."""
    resp = session.get(f"{VASTGOEDVEILING}/veilingen")
    resp.raise_for_status()
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return []
    auctions = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("auctions") or []
    return [row for row in (vastgoedveiling_listing(a) for a in auctions
                            if (a.get("status") or "open") == "open") if row]


# The same site software as openbareverkoop.nl, and the same lots: kept out of
# the default scan so every Dutch auction is listed once.
@register("veilingbiljet", "NL", default=False)
def scrape_veilingbiljet(db, max_price: float = 100000, **_):
    """veilingbiljet.nl — Dutch foreclosure auctions (the same lots as openbareverkoop.nl)."""
    session = make_session()
    base = "https://www.veilingbiljet.nl"
    resp = session.get(f"{base}/objecten/")
    resp.raise_for_status()
    total = 0
    seen = set()
    for card in BeautifulSoup(resp.text, "html.parser").select(
            "div.object, article, div[class*='object'], li[class*='object']"):
        link = card.select_one("a[href]")
        url = safe_url(link.get("href"), base) if link else None
        if not url:
            continue
        eid = listing_id_from_url(url)
        if eid in seen:
            continue
        seen.add(eid)
        title_el = card.select_one("h2,h3,.title,.object-title")
        price = find_price(card.get_text(" "))
        if price and price > max_price:
            continue
        loc_el = card.select_one(".location,.city,.plaats")
        date_el = card.select_one(".date,.veilingdatum")
        upsert_listing(db, make_listing(
            "veilingbiljet", eid, "NL",
            title=title_el.get_text(strip=True)[:200] if title_el else f"Veilingbiljet #{eid}",
            description="Veilingbiljet.nl executieveiling", tipo="vastgoed",
            price=price, concelho=loc_el.get_text(strip=True) if loc_el else None,
            url=url, date_end=parse_date_dmy(date_el.get_text()) if date_el else None,
        ))
        total += 1
    return total
