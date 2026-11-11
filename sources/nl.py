"""Netherlands: openbareverkoop.nl, veilingnotaris.nl, vastgoedveiling.nl,
veilingbiljet.nl and RE/MAX."""
from __future__ import annotations

import html
import json
import re
import unicodedata

import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from common import LOG, make_listing, make_session, parse_price, safe_url
from db import upsert_listing
from sources import ScanCount, SourceUnavailable, register

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


_AUCTION_KIND = re.compile(
    r"\b(Executieveiling(?:\s+\w+)?|Vrijwillige\s+veiling|Beslagveiling|Internetbieden|"
    r"Internet-only(?:\s+(?:Live|Timed))?|Hybride(?:\s*\([^)]*\))?)\b", re.I)


def nl_details(html: str) -> dict:
    """What a Dutch auction page says about the property: the deed's
    description, type, use (rented?), year built, living and plot area, date."""
    text = _page_text(html)
    out: dict = {}
    stop = ("Gebruik|Soort eigendom|Bouwjaar|Oppervlakte|Inhoud|Aantal|Kadastrale|Gebruikssituatie|"
            "Bezichtiging|Over de veiling|Soort|Datum|Organisator|Informatie|$")
    for key, label in (("type", "Type registergoed"), ("type", "Type"), ("gebruik", "Gebruik"),
                       ("gebruik", "Gebruikssituatie"), ("bouwjaar", "Bouwjaar")):
        # Stop at another "Type …" label so registergoed does not swallow the sale kind.
        value = _field(text, label, stop if label != "Type registergoed" else stop + "|Type")
        if value and key not in out and len(value) < 60:
            out[key] = value
    # Sale kind (executie / vrijwillig / …), not the building type. A bare
    # "Type" field on OV is often the sale kind; registergoed already filled type.
    kind = _AUCTION_KIND.search(text)
    if kind:
        out["type_verkoop"] = kind.group(0)
    if out.get("type") and _AUCTION_KIND.fullmatch(out["type"].strip()):
        out["type_verkoop"] = out.pop("type")
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
    if re.search(r"\bbieden via internet\b|\binternet-?only\b|\binternetbieden\b", text, re.I):
        out["bieden_via_internet"] = True
    return out


def _with_details(row: dict, details: dict) -> dict:
    """The listing, filled in from its page."""
    raw = json.loads(row.get("raw_json") or "{}")
    raw.update(details, detail_checked=1)
    if details.get("bieden_via_internet"):
        raw["bieden_via_internet"] = True
    if details.get("type_verkoop"):
        raw["type_verkoop"] = details["type_verkoop"]
    if str(details.get("bouwjaar", "")).isdigit():
        raw["ano_construcao"] = details["bouwjaar"]              # scoring.built_year reads this
    sale = details.get("type_verkoop") or raw.get("type_verkoop")
    parts = [details.get("omschrijving"), f"Type: {details['type']}" if details.get("type") else None,
             f"Veiling: {sale}" if sale else None,
             f"Gebruik: {details['gebruik']}" if details.get("gebruik") else None,
             f"Bouwjaar {details['bouwjaar']}" if details.get("bouwjaar") else None,
             f"Perceel {details['perceel_m2']:.0f} m2" if details.get("perceel_m2") else None,
             "bieden via internet" if raw.get("bieden_via_internet") else None,
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


# ─── What Dutch auctions really sold for ─────────────────────────────
# Dutch notarial auctions give no starting price, so the best guide to what a
# house will go for is what the ones nearby went for. openbareverkoop.nl keeps a
# year of results: the last bid ("afslag") of each lot. Kept in the kv table as
# NL_RESULTS_KEY and shown on a Dutch listing's page (listing_info.past_results).

NL_RESULTS_KEY = "nl_results"
NL_RESULTS_DAYS = 365
_SOLD = ("Gegund", "Gesloten", "Veiling")      # "Niet Gegund": the seller refused the bid


def parse_nl_results(data: dict, now: datetime | None = None) -> list[dict]:
    """The lots of openbareverkoop's results view that have a final bid."""
    now = now or datetime.now(timezone.utc)
    out = []
    for zitting in data.get("results", []):
        for regio in zitting.get("objectenPerRegio", []):
            for obj in regio.get("objects", []):
                price = parse_price(obj.get("afslag") or "")
                date = _ms_date(obj.get("zittingdatum"))
                if not price or obj.get("status") not in _SOLD or not date or date > now.strftime("%Y-%m-%dT%H:%M:%S"):
                    continue
                name = obj.get("kavelNaam") or ""
                out.append({"name": name, "town": name.rpartition(",")[2].strip().title() or None,
                            "type": obj.get("woningtype") or "", "price": price, "date": date[:10],
                            "lat": obj.get("lat"), "lon": obj.get("lng"), "status": obj.get("status"),
                            "url": safe_url("https://www.openbareverkoop.nl" + obj["url"]) if obj.get("url") else None})
    return out


def fetch_nl_results(db, session, now: datetime | None = None) -> int:
    from datetime import timedelta
    from db import set_kv
    now = now or datetime.now(timezone.utc)
    resp = session.post("https://www.openbareverkoop.nl/kavels/searchresults", data={
        "text": "", "view": "resultaten", "periode": "vantot", "woningtype": "",
        "Van": (now - timedelta(days=NL_RESULTS_DAYS)).strftime("%d-%m-%Y"), "TotEnMet": now.strftime("%d-%m-%Y")})
    resp.raise_for_status()
    results = parse_nl_results(resp.json(), now)
    if results:
        set_kv(db, NL_RESULTS_KEY, json.dumps(results, ensure_ascii=False))
    LOG.info(f"openbareverkoop.nl: {len(results)} auction results of the last year")
    return len(results)


def bieden_via_internet(obj: dict) -> bool:
    """Whether the lot can be bid on online (no visit to the notary's room).

    openbareverkoop sets ``internetonly`` for Internet-only Live/Timed; Hybride
    (zaal en internet) still takes internet bids even when that flag is false.
    """
    if obj.get("internetonly") in (True, "true", "True", 1, "1"):
        return True
    wijze = str(obj.get("veilingwijze") or "")
    return bool(re.search(r"internet", wijze, re.I))


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
    online = bieden_via_internet(obj)
    raw = {k: obj.get(k) for k in ("id", "woningtype", "veilingwijze", "status", "organisatie", "zittingId")}
    raw["internetonly"] = bool(obj.get("internetonly"))
    raw["bieden_via_internet"] = online
    if obj.get("lat") and obj.get("lng"):
        raw.update(lat=obj["lat"], lon=obj["lng"])                  # geo.position reads these
    parts = [f"Executieveiling ({vtype})" if vtype else "Executieveiling",
             "bieden via internet" if online else None]
    return make_listing(
        "netherlands", eid, "NL",
        title=title,
        description=". ".join(p for p in parts if p),
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
    try:
        fetch_nl_results(db, session)
    except Exception as e:  # noqa: BLE001: the sales still count without the results
        LOG.warning(f"openbareverkoop.nl results failed ({type(e).__name__})")
    return len(rows)


@register("veilingnotaris", "NL")
def scrape_veilingnotaris(db, max_price: float = 50000, **_):
    """veilingnotaris.nl — Dutch notarial execution auctions."""
    session = make_session(timeout=15)
    base = "https://veilingnotaris.nl"
    rows, seen = [], set()
    pages = 0
    incomplete = False
    for page in range(1, 20):
        resp = session.get(f"{base}/veilingen/", params={"page": page} if page > 1 else None)
        resp.raise_for_status()
        pages += 1
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
    else:
        incomplete = True
    _read_details(db, session, [r for r in rows if '"detail_checked"' not in (r.get("raw_json") or "")],
                  [NL_DETAILS_PER_SCAN])
    for row in rows:
        upsert_listing(db, row)
    db.commit()
    return ScanCount(len(rows), "partial" if incomplete else "full", pages)


# vastgoedveiling.nl (Hekkelman) shares auction numbers with veilingnotaris.nl
# and often the same house as openbareverkoop.nl. Own source, but a lot already
# stored under veilingnotaris:{id} or matched on openbareverkoop keeps that ID
# (AGENTS.md: IDs must be stable) and is marked a duplicate instead of a new one.
VASTGOEDVEILING = "https://vastgoedveiling.nl"
_COUNTRIES = {"nl": "NL", "de": "DE", "be": "BE", "fr": "FR", "es": "ES", "pt": "PT"}
_USE = {"huurbeding_is_niet_ingeroepen": "bewoond (huurbeding niet ingeroepen)", "verhuurd": "verhuurd",
        "leeg": "leeg opgeleverd", "eigen_gebruik": "bewoond/in eigen gebruik"}
_TYPE_VERKOOP = {
    "executieveiling": "executieveiling",
    "vrijwillige_veiling": "vrijwillige veiling",
    "beslagveiling": "beslagveiling",
    "partnerveiling": "partnerveiling",
    "te_koop": "te koop",
    "inschrijving": "inschrijving",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _bag_number(value) -> float | None:
    """"BAG: 114" / "92" / "1.250" -> a number."""
    return parse_price(re.sub(r"^\s*BAG:\s*", "", str(value or ""))) or None


def nl_place_key(title: str | None, town: str | None = None) -> str | None:
    """Street + town tokens, order-free, so OV and VGV titles of one house match."""
    blob = f"{title or ''} {town or ''}"
    blob = unicodedata.normalize("NFKD", blob)
    blob = "".join(ch for ch in blob if not unicodedata.combining(ch)).casefold()
    blob = re.sub(r"\([^)]*\)", " ", blob)
    drop = ("woonhuis", "appartement", "tussenwoning", "hoekwoning", "bovenwoning",
            "recreatiewoning", "bedrijfspand", "winkel", "kantoor", "massa", "en")
    parts = [p for p in re.findall(r"[a-z0-9]+", blob) if p not in drop and len(p) > 1]
    return " ".join(sorted(set(parts))) if len(parts) >= 2 else None


def vastgoedveiling_listing(a: dict) -> dict | None:
    lid = a.get("id")
    country = _COUNTRIES.get((a.get("land") or "nl").lower())
    if not lid or not country:
        return None
    use = a.get("gebruikssituatie") or ""
    year = re.sub(r"^\s*BAG:\s*", "", str(a.get("bouwjaar") or "")).strip()
    sale = _TYPE_VERKOOP.get(a.get("type_verkoop") or "", (a.get("type_verkoop") or "").replace("_", " "))
    # startbod is the Dutch-auction start (often €1 / a million), not a sale price.
    bid = _bag_number(a.get("huidig_bod")) or None
    parts = [re.sub(r"\s+", " ", a.get("kavelbeschrijving") or a.get("kadastrale_gegevens") or "").strip()[:1500],
             f"Type: {a['object_type']}" if a.get("object_type") else None,
             f"Veiling: {sale}" if sale else None,
             f"Gebruik: {_USE.get(use, use.replace('_', ' '))}" if use else None,
             f"Bouwjaar {year}" if year else None,
             f"Perceel {a['oppervlakte_perceel']} m2" if a.get("oppervlakte_perceel") else None]
    raw = {"detail_checked": 1, "object_type": a.get("object_type"), "categorie": a.get("object_type_categorie"),
           "type_verkoop": sale or a.get("type_verkoop"), "gebruikssituatie": use, "provincie": a.get("provincie"),
           "vgv_id": lid, "bieden_via_internet": True}
    if year.isdigit():
        raw["ano_construcao"] = year
    lat = a.get("latitude_custom") or a.get("latitude")
    lon = a.get("longitude_custom") or a.get("longitude")
    if lat and lon:
        raw.update(lat=lat, lon=lon)
    name = a.get("name") or f"{a.get('plaats') or ''}, {a.get('straat') or ''}"
    wtype = a.get("object_type")
    return make_listing(
        "vastgoedveiling", str(lid), country,
        title=f"{name} ({wtype})" if wtype else name,
        description=". ".join(p for p in parts if p)[:3000] or None, tipo="vastgoed",
        area_m2=_bag_number(a.get("oppervlakte_object")), current_bid=bid,
        district=a.get("provincie"), concelho=a.get("plaats"),
        url=f"{VASTGOEDVEILING}/veiling/{lid}/{_slug(name)}",
        image_url=a.get("thumb"), date_end=(a.get("eindtijd") or a.get("starttijd") or "")[:19] or None,
        raw_json=raw,
    )


def vastgoedveiling_auctions(html: str) -> list[dict]:
    """Open auctions from vastgoedveiling.nl's Next.js payload."""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    auctions = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("auctions") or []
    return [a for a in auctions if (a.get("status") or "open") == "open"]


def vastgoedveiling_rows(session) -> list[dict]:
    """The open auctions on vastgoedveiling.nl (the page's Next.js data)."""
    resp = session.get(f"{VASTGOEDVEILING}/veilingen")
    resp.raise_for_status()
    return [row for row in (vastgoedveiling_listing(a) for a in vastgoedveiling_auctions(resp.text)) if row]


def _nl_twin_id(db, auction_id: str, title: str | None, town: str | None) -> str | None:
    """An openbareverkoop or veilingnotaris row that is the same lot, if any.

    Same auction number on veilingnotaris always wins (shared platform). Else a
    unique address match on either Dutch notarial source.
    """
    vn = f"veilingnotaris:{auction_id}"
    if db.execute("SELECT 1 FROM listings WHERE id = ?", (vn,)).fetchone():
        return vn
    key = nl_place_key(title, town)
    if not key:
        return None
    found = []
    for r in db.execute(
        "SELECT id, title, concelho FROM listings "
        "WHERE source IN ('netherlands', 'veilingnotaris') AND country IN ('NL', 'DE', 'BE')"
    ):
        if nl_place_key(r["title"], r["concelho"]) == key:
            found.append(r["id"])
    return found[0] if len(found) == 1 else None


def _retarget(row: dict, existing_id: str) -> dict:
    """Keep the twin's stable id/source; hang the VGV URL on raw_json."""
    source, eid = existing_id.split(":", 1)
    raw = json.loads(row.get("raw_json") or "{}")
    raw["vastgoedveiling_url"] = row.get("url")
    raw["vgv_id"] = raw.get("vgv_id") or row.get("external_id")
    return {**row, "id": existing_id, "source": source, "external_id": eid,
            "raw_json": json.dumps(raw, ensure_ascii=False, default=str)}


@register("vastgoedveiling", "NL")
def scrape_vastgoedveiling(db, max_price: float = 50000, **_):
    """vastgoedveiling.nl (Hekkelman) — Dutch executie / vrijwillig auctions."""
    session = make_session(timeout=15)
    rows = vastgoedveiling_rows(session)
    kept, adopted = 0, 0
    for row in rows:
        twin = _nl_twin_id(db, row["external_id"], row.get("title"), row.get("concelho"))
        if twin:
            row = _retarget(row, twin)
            adopted += 1
        else:
            kept += 1
        upsert_listing(db, row)
    db.commit()
    LOG.info(f"vastgoedveiling.nl: {len(rows)} open ({kept} own ids, {adopted} already on OV/VN)")
    return len(rows)


# The same site software as openbareverkoop.nl, and the same lots: kept out of
# the default scan so every Dutch auction is listed once.
@register("veilingbiljet", "NL", default=False)
def scrape_veilingbiljet(db, max_price: float = 100000, **_):
    """veilingbiljet.nl — Dutch foreclosure auctions (same lots as openbareverkoop.nl).

    The old /objecten/ HTML list redirects to an error page (Nov 2026). The site
    still serves the same JSON as openbareverkoop.nl at /kavels/searchresults;
    that feed is already read by the netherlands source.
    """
    raise SourceUnavailable(
        "veilingbiljet.nl no longer has an /objecten/ list; the same lots come "
        "through the netherlands (openbareverkoop.nl) source")


@register("remaxnl", "NL", description="RE/MAX — homes for sale in the Netherlands")
def scrape_remaxnl(db, max_price: float = 50000, **_):
    """RE/MAX — homes for sale in the Netherlands."""
    from sources._market import scrape_named
    return scrape_named(db, "remaxnl", max_price)


# ─── Biedboek (Dutch public bodies' property) ──────────────────────
# The Rijksvastgoedbedrijf, Staatsbosbeheer, provinces, water boards and
# municipalities sell land, forest, houses and buildings by public tender
# here. There is no asking price: you bid what you offer. Most records are
# leases (pacht, huur) or closed tenders; status 100 is the state's open
# sale, the other bodies' objects carry their own statuses.
BIEDBOEK = "https://www.biedboek.nl"
BIEDBOEK_OPEN = {100, 200, 300, 500, 900, 1000, 1400, 1900}
_BIEDBOEK_LEASE = re.compile(r"\b(?:pacht\w*|verhuur\w*|huurrecht\w*|te huur|in gebruik geven|ingebruikgeving)\b", re.I)
_BIEDBOEK_CLOSED = re.compile(r"\b(?:gesloten|niet meer reageren|teruggetrokken|verkocht|gegund)\b", re.I)


def biedboek_m2(size) -> float | None:
    """A parcel size in m²: "5220", or the Dutch "1.68.40" (1 ha, 68 are, 40 centiare)."""
    parts = str(size or "").strip().split(".")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 10000 + int(parts[1]) * 100 + int(parts[2])
        return float(str(size).replace(",", ".")) if size else None
    except ValueError:
        return None


def biedboek_listing(detail: dict) -> dict | None:
    """One Biedboek object's detail as a listing, or None when it is a lease or closed."""
    tr = detail.get("translation") or {}
    html = (tr.get("descriptionHtml") or "") + " ".join(p.get("html") or "" for p in tr.get("realEstateParagraphs") or [])
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")).strip()
    name = (tr.get("name") or detail.get("title") or "").strip()
    if _BIEDBOEK_LEASE.search(f"{name} {text[:600]}") or _BIEDBOEK_CLOSED.search(text[:300]):
        return None
    bidding = detail.get("biddingData") or {}
    raw = {"use": tr.get("currentUsage"), "destination": tr.get("destinationUsage"),
           "procedure": detail.get("salesProcedure"), "reference": detail.get("fileReference")}
    try:
        raw.update(lat=float(detail["latitude"]), lon=float(detail["longitude"]))
    except (KeyError, TypeError, ValueError):
        pass
    size = detail.get("parcelSize")
    return make_listing(
        "biedboek", detail.get("shortId") or detail.get("id"), "NL", title=f"{name} — {tr.get('city') or ''}".strip(" —")[:200],
        description=" · ".join(x for x in (tr.get("address"), tr.get("currentUsage"), tr.get("destinationUsage"),
                                           text[:2500], "openbare inschrijving (Biedboek), geen vraagprijs") if x),
        tipo=tr.get("currentUsage") or None, area_m2=biedboek_m2(size),
        price=float(bidding["minimumAmount"]) if bidding.get("minimumAmount") else None,
        concelho=(tr.get("city") or detail.get("city") or "").strip() or None,
        date_end=(bidding.get("endDate") or "")[:19] or None,
        url=f"{BIEDBOEK}/nl/realestate/{detail.get('shortId')}", raw_json=raw,
    )


@register("biedboek", "NL", description="Biedboek — land, forest and buildings sold by Dutch public bodies by tender")
def scrape_biedboek(db, max_price: float = 50000, **_):
    session = make_session(timeout=30)
    total = 0
    pages = 0
    incomplete = False
    for page in range(1, 60):
        resp = session.get(f"{BIEDBOEK}/api/real-estate/preview",
                           params={"pageSize": 100, "pageNumber": page, "adv": "false", "language": "nl"})
        resp.raise_for_status()
        pages += 1
        rows = (resp.json().get("realEstates") or {}).get("data") or []
        if not rows:
            break
        for row in rows:
            if row.get("archivedObject") or row.get("realEstateStatus") not in BIEDBOEK_OPEN:
                continue
            try:
                detail = session.get(f"{BIEDBOEK}/api/real-estate/{row['shortId']}", params={"language": "nl"}).json()
            except Exception as e:  # noqa: BLE001 — one object failing is not the source failing
                LOG.info(f"biedboek {row.get('shortId')}: {type(e).__name__}")
                continue
            listing = biedboek_listing(detail)
            if listing and (listing["price"] or 0) <= max_price:
                upsert_listing(db, listing)
                total += 1
            time.sleep(0.2)
        db.commit()
    else:
        incomplete = True
    return ScanCount(total, "partial" if incomplete else "full", pages)


# ─── Gemeenteblad: municipal sale notices (Didam) ─────────────────────
# Since the Didam ruling (Hoge Raad, 26 Nov 2021) a town must publish its
# intention to sell land or a building. Most notices sell a garden strip to
# the neighbour; kept are open bidding, and real plots or buildings where a
# serious bidder ("serieuze gegadigde") can still step in before the deadline.
GEMEENTEBLAD_SRU = "https://repository.overheid.nl/sru"
GEMEENTEBLAD_DAYS = 60
GEMEENTEBLAD_MIN_M2 = 1000
GEMEENTEBLAD_MIN_BUILDING_M2 = 50
_GB_SMALL = re.compile(r"snipper|groenstr|strook|stroken|transformator|trafo|erfpacht|bloot eigendom|enexis|liander|stedin|"
                       r"tennet|alliander|nutsvoorziening|aangrenzend|grenzend aan|achterzijde|achtertuin|zijtuin|"
                       r"parkeerplaats|vaststellingsovereenkomst|woningcorporatie|sociale huur|stichting|vergunning|ontheffing|\bAPV\b|"
                       r"achterkant|voorkant|zijkant|voorzijde|zijkant|ruiling|grondruil|waterschap", re.I)
_GB_OPEN = re.compile(r"hoogste bieder|openbare (?:inschrijving|verkoop|selectie|biedprocedure)|selectieprocedure|"
                      r"inschrijvingsprocedure|biedprocedure|bieding\w* (?:kunnen|kenbaar)|tenderned|via (?:een )?makelaar|"
                      r"funda|kunnen (?:zich )?inschrijven", re.I)
_GB_ONE = re.compile(r"één[- ]op[- ]één|1[- ]op[- ]1|een[- ]op[- ]een|geen (?:andere )?(?:serieuze )?gegadigden die in aanmerking", re.I)
_GB_BUILDING = re.compile(r"\b(?:woning|woonhuis|pand|gebouw|schoolgebouw|school|boerderij|kerk|kantoor|loods|bedrijfspand|"
                          r"woonwagen|opstal\w*)\b", re.I)
_GB_M2 = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?\s*(?:m2|m²|vierkante meter)", re.I)
_GB_HA = re.compile(r"(\d+(?:,\d+)?)\s*(?:ha\b|hectare)", re.I)
_GB_DAYS = re.compile(r"binnen\s+(\d{1,2})\s+(?:kalender|werk)?dagen", re.I)
_GB_PRICE = re.compile(r"(?:koopprijs|verkoopprijs|vraagprijs|minimumprijs|prijs van|bedrag van)\D{0,25}€\s*([\d.]+(?:,\d+)?)", re.I)


def gemeenteblad_m2(text: str) -> float | None:
    """The largest size in a notice, in m² ("1.250 m²", "2,5 ha")."""
    sizes = [float(n.replace(".", "")) for n in _GB_M2.findall(text)]
    sizes += [float(n.replace(",", ".")) * 10000 for n in _GB_HA.findall(text)]
    return max(sizes) if sizes else None


def gemeenteblad_listing(notice: dict) -> dict | None:
    """One notice (id, title, town, date, text) as a listing, or None when it is
    a neighbour's strip, a one-to-one sale with no way in, or too small."""
    title, text = (notice.get("title") or "").strip(), notice.get("text") or ""
    if "verko" not in f"{title} {text[:400]}".lower() or _GB_SMALL.search(title):
        return None
    m2 = gemeenteblad_m2(text)
    building = bool(_GB_BUILDING.search(title))
    is_open = bool(_GB_OPEN.search(text)) and not _GB_ONE.search(text)
    if m2 is not None and m2 < (GEMEENTEBLAD_MIN_BUILDING_M2 if building else GEMEENTEBLAD_MIN_M2 if not is_open else 500):
        return None
    if not is_open and (_GB_ONE.search(title) or _GB_SMALL.search(text[:800]) or not (building or m2)):
        return None
    days = _GB_DAYS.search(text)
    try:
        published = datetime.strptime(notice["date"][:10], "%Y-%m-%d")
        date_end = (published + timedelta(days=int(days[1]) if days else 20)).strftime("%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        date_end = None
    price = _GB_PRICE.search(text)
    how = ("open bidding — send a bid before the deadline" if is_open else
           "Didam notice — come forward as a serious bidder (serieuze gegadigde) before the deadline to force a competition")
    return make_listing(
        "gemeenteblad", notice["id"], "NL", title=f"{title} — {notice.get('town') or ''}".strip(" —")[:200],
        description=" · ".join((how, text[:2500])), tipo="gebouw" if building else "grond", area_m2=m2,
        price=parse_price(price[1]) if price else None, concelho=notice.get("town") or None, date_end=date_end,
        url=f"https://zoek.officielebekendmakingen.nl/{notice['id']}.html",
        raw_json={"procedure": "open" if is_open else "didam", "published": notice.get("date")},
    )


def gemeenteblad_records(xml: str) -> list[dict]:
    """The notices in one SRU answer: id, title, town, date and the URL of the text."""
    out = []
    for rec in re.findall(r"<sru:record>.*?</sru:record>", xml, re.S):
        def get(pattern):
            m = re.search(pattern, rec, re.S)
            return html.unescape(m[1]).strip() if m else None
        out.append({"id": get(r"<dcterms:identifier>(.*?)<"), "title": get(r"<dcterms:title>(.*?)<") or "",
                    "town": get(r"<dcterms:creator[^>]*>(.*?)<"), "date": get(r"<dcterms:modified>(.*?)<"),
                    "xml": get(r"<gzd:url>([^<]*\.xml)<")})
    return [r for r in out if r["id"] and r["xml"]]


@register("gemeenteblad", "NL", description="Gemeenteblad — land and buildings Dutch towns intend to sell (Didam notices)")
def scrape_gemeenteblad(db, max_price: float = 50000, **_):
    session = make_session(timeout=60)
    since = (datetime.now(timezone.utc) - timedelta(days=GEMEENTEBLAD_DAYS)).strftime("%Y-%m-%d")
    query = f'w.publicatienaam=="Gemeenteblad" and dt.title any "verkoop verkopen" and dt.modified>={since}'
    total = 0
    for start in range(1, 3001, 100):
        resp = session.get(GEMEENTEBLAD_SRU, params={"query": query, "maximumRecords": 100, "startRecord": start})
        resp.raise_for_status()
        records = gemeenteblad_records(resp.text)
        if not records:
            break
        for notice in records:
            if _GB_SMALL.search(notice["title"]) or _GB_ONE.search(notice["title"]):
                continue
            try:
                body = session.get(notice["xml"]).text
            except Exception as e:  # noqa: BLE001 — one notice failing is not the source failing
                LOG.info(f"gemeenteblad {notice['id']}: {type(e).__name__}")
                continue
            notice["text"] = re.sub(r"\s+", " ", BeautifulSoup(body, "html.parser").get_text(" ")).strip()
            listing = gemeenteblad_listing(notice)
            if listing and (listing["price"] or 0) <= 2 * max_price:
                upsert_listing(db, listing)
                total += 1
            time.sleep(0.2)
        db.commit()
    return total
