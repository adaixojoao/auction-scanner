"""Spain: BOE judicial auctions, AEAT tax auctions, bad-bank / servicer portals."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from common import (LOG, find_area, find_price, land_max_price, make_listing, make_session, normalize, parse_date_dmy,
                    parse_price, safe_url, stable_id, to_number)
from db import upsert_listing
from sources import SourceUnavailable, register
from sources._cards import CardSite, scrape_cards

BOE_SEARCH = "https://subastas.boe.es/subastas_ava.php"


@register("spain", "ES")
def scrape_spain(db, max_price: float = 50000, **_):
    """subastas.boe.es — Spanish judicial auctions (search + detail pages)."""
    session = make_session(timeout=30)
    session.get(BOE_SEARCH, timeout=15).raise_for_status()

    page_size = 50
    # POST the search form — exact field layout from the live form
    form_data = [
        ("campo[0]", "SUBASTA.ORIGEN"), ("dato[0]", ""),
        ("campo[1]", "SUBASTA.AUTORIDAD"), ("dato[1]", ""),
        ("campo[2]", "SUBASTA.ESTADO.CODIGO"), ("dato[2]", "EJ"),
        ("campo[3]", "BIEN.TIPO"), ("dato[3]", "I"),
        ("dato[4]", ""),  # subtype radio — no campo[4] hidden field
        ("campo[5]", "BIEN.DIRECCION"), ("dato[5]", ""),
        ("campo[6]", "BIEN.CODPOSTAL"), ("dato[6]", ""),
        ("campo[7]", "BIEN.LOCALIDAD"), ("dato[7]", ""),
        ("campo[8]", "BIEN.COD_PROVINCIA"), ("dato[8]", ""),
        ("campo[9]", "SUBASTA.POSTURA_MINIMA_MINIMA_LOTES"), ("dato[9]", ""),
        ("campo[10]", "SUBASTA.NUM_CUENTA_EXPEDIENTE_1"), ("dato[10]", ""),
        ("campo[11]", "SUBASTA.NUM_CUENTA_EXPEDIENTE_2"), ("dato[11]", ""),
        ("campo[12]", "SUBASTA.NUM_CUENTA_EXPEDIENTE_3"), ("dato[12]", ""),
        ("campo[13]", "SUBASTA.NUM_CUENTA_EXPEDIENTE_4"), ("dato[13]", ""),
        ("campo[14]", "SUBASTA.NUM_CUENTA_EXPEDIENTE_5"), ("dato[14]", ""),
        ("campo[15]", "SUBASTA.ID_SUBASTA_BUSCAR"), ("dato[15]", ""),
        ("campo[16]", "SUBASTA.ACREEDORES"), ("dato[16]", ""),
        ("campo[17]", "SUBASTA.FECHA_FIN"),
        ("dato[17][0]", ""), ("dato[17][1]", ""),
        ("campo[18]", "SUBASTA.FECHA_INICIO"),
        ("dato[18][0]", ""), ("dato[18][1]", ""),
        ("page_hits", str(page_size)),
        ("sort_field[0]", "SUBASTA.FECHA_FIN"),
        ("sort_order[0]", "asc"),
        ("accion", "Buscar"),
    ]
    resp = session.post(BOE_SEARCH, data=form_data)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    id_busqueda = None
    for link in soup.select("a[href*='id_busqueda']"):
        m = re.search(r'id_busqueda=([^&,]+)', link.get("href", ""))
        if m:
            id_busqueda = m.group(1)
            break

    total_results = 0
    total_text = soup.select_one(".paginar")
    if total_text:
        m = re.search(r'de\s+(\d+)', total_text.get_text())
        if m:
            total_results = int(m.group(1))
    LOG.info(f"  Spain: {total_results} total active inmuebles")

    total_scraped = _spain_parse_page(soup, db, max_price)
    db.commit()

    if id_busqueda and total_results > page_size:
        max_pages = min((total_results // page_size) + 1, 20)
        for page_num in range(1, max_pages):
            offset = page_num * page_size
            try:
                resp = session.get(f"{BOE_SEARCH}?accion=Mas&id_busqueda={id_busqueda},-{offset}-{page_size}")
                resp.raise_for_status()
            except Exception as e:
                LOG.warning(f"Spain page {page_num+1} error: {e}")
                break
            count = _spain_parse_page(BeautifulSoup(resp.text, "html.parser"), db, max_price)
            db.commit()
            total_scraped += count
            LOG.info(f"  Spain page {page_num+1}: {count} items (total: {total_scraped})")
            if count == 0:
                break
            time.sleep(1)

    enrich_spain_details(db, session)
    fetch_catastro(db, session)
    return total_scraped


def _spain_parse_page(soup, db, max_price) -> int:
    """Parse one page of Spain search results. Returns count."""
    count = 0
    for h3 in soup.select("#contenido h3"):
        m = re.match(r'SUBASTA\s+(SUB-\S+-\d+-\d+)', h3.get_text(strip=True))
        if not m:
            continue
        sub_id = m.group(1)

        court = ""
        description = ""
        date_end = None
        sib = h3.next_sibling
        while sib:
            if hasattr(sib, 'name'):
                if sib.name == "h3":
                    break
                txt = sib.get_text(strip=True)
                if sib.name == "h4":
                    court = txt
                elif "Conclusión prevista" in txt:
                    dm = re.search(r'(\d{2}/\d{2}/\d{4})\s+a\s+las\s+(\d{2}:\d{2})', txt)
                    if dm:
                        try:
                            date_end = datetime.strptime(
                                f"{dm.group(1)} {dm.group(2)}", "%d/%m/%Y %H:%M").isoformat()
                        except ValueError:
                            pass
                elif len(txt) > 40 and "Expediente" not in txt:
                    description = txt[:500]
            sib = sib.next_sibling

        location = court.split(" - ")[-1].strip() if " - " in court else court
        upsert_listing(db, make_listing(
            "spain", sub_id, "ES",
            title=description[:120] if description else f"Subasta {sub_id}",
            description=description,
            tipo="inmueble",
            district=location,
            url=f"https://subastas.boe.es/detalleSubasta.php?idSub={sub_id}",
            date_end=date_end,
        ))
        count += 1
    return count


def _spain_table_fields(soup) -> dict:
    data = {}
    for tr in soup.select("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            key = cells[0].get_text(" ", strip=True)
            if key:
                data[key] = cells[1].get_text(" ", strip=True)
    for dt in soup.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            key = dt.get_text(" ", strip=True)
            if key:
                data[key] = dd.get_text(" ", strip=True)
    return data


def _spain_field(data: dict, *needles: str) -> str | None:
    for key, val in data.items():
        kl = key.lower()
        if any(n.lower() in kl for n in needles):
            return val
    return None


def _spain_parse_end_date(text: str | None) -> str | None:
    if not text:
        return None
    iso = re.search(r"ISO:\s*([0-9T:+-]+)", text)
    if iso:
        return iso.group(1)
    dm = re.search(r"(\d{2})-(\d{2})-(\d{4})\s+(\d{2}:\d{2}:\d{2})", text)
    if dm:
        try:
            return datetime.strptime(
                f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)} {dm.group(4)}",
                "%Y-%m-%d %H:%M:%S",
            ).isoformat()
        except ValueError:
            return None
    return None


def _spain_parse_detail(html: str) -> dict:
    """Extract price, min bid, dates, and locality from a BOE detail page."""
    soup = BeautifulSoup(html, "html.parser")
    fields = _spain_table_fields(soup)
    text = soup.get_text(" ", strip=True)

    price = parse_price(_spain_field(fields, "Valor subasta", "Valor de subasta"))
    if price is None:
        m = re.search(r"Valor subasta\s+([\d.\s]+,\d{2}\s*€)", text, re.I)
        if m:
            price = parse_price(m.group(1))
    if price is None:
        price = parse_price(_spain_field(fields, "Tasación", "tasacion"))

    date_end = _spain_parse_end_date(_spain_field(fields, "Fecha de conclusión", "Fecha de conclusion"))
    if not date_end:
        date_end = _spain_parse_end_date(text)

    concelho = _spain_field(fields, "Localidad", "Municipio")
    district = _spain_field(fields, "Provincia")

    title = desc = None
    for label in ("Descripción", "Descripcion", "Dirección", "Direccion"):
        val = _spain_field(fields, label)
        if val and len(val) > 20:
            desc = val[:500]
            title = val[:120]
            break
    if not title:
        h = soup.select_one("#contenido h3, h3")
        if h:
            ht = h.get_text(" ", strip=True)
            if ht and not ht.upper().startswith("SUBASTA SUB-"):
                title = ht[:120]

    am = re.search(r"(\d{1,3}(?:[.\s]\d{3})+,\d{1,2}|\d+,\d{1,2}|\d+)\s*m[²2]", text, re.I)
    # In a judicial auction the "puja mínima" can be the bidding step (€1,743 on
    # a €174,300 flat): a bid that low does not buy it (LEC art. 670 asks 70%
    # of the value, or 50% with the court's approval), so it is no minimum price.
    floor = parse_price(_spain_field(fields, "Puja mínima", "Puja minima"))
    if floor and price and floor < 0.2 * price:
        floor = None
    return {
        "price": price,
        "min_price": floor,
        "date_end": date_end,
        "concelho": concelho.strip() if concelho else None,
        "district": district.strip() if district else None,
        "title": title,
        "description": desc,
        "area_m2": parse_price(am.group(1)) if am else None,
    }


def clear_step_min_price(db, row: dict) -> None:
    """Drop a stored puja mínima that is a bidding step, not a buying price.

    Updates keep the old value when the new one is missing, so €1,743 on a
    €174,300 flat would otherwise stay and look like a bargain forever."""
    listing_id = row.get("id")
    if not listing_id:
        return
    current = db.execute(
        "SELECT price, min_price FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not current:
        return
    price, floor = current["price"], current["min_price"]
    if price and floor and floor < 0.2 * price:
        db.execute("UPDATE listings SET min_price = NULL WHERE id = ?", (listing_id,))


# A BOE detail page has tabs: ver=1 general information, ver=2 the managing
# authority (court or agency: name, address, e-mail), ver=3 the goods (address,
# occupancy, visits). Tab numbers and labels are from the site's public layout
# and unconfirmed from here, so labels are matched loosely and every field is
# optional.
BOE_DETAIL = "https://subastas.boe.es/detalleSubasta.php"
BOE_TABS = (("general", 1), ("authority", 2), ("goods", 3))
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _spain_occupation(text: str | None) -> str | None:
    """BOE "Situación posesoria" → "occupied" / "vacant" / None (unknown)."""
    t = (text or "").lower()
    if not t or "no consta" in t:
        return None
    if "libre" in t or "desocupad" in t or "sin ocupantes" in t:
        return "vacant"
    if "ocupad" in t or "okupa" in t or "arrendad" in t or "inquilin" in t:
        return "occupied"
    return None


def _spain_parse_general(html: str) -> dict:
    f = _spain_table_fields(BeautifulSoup(html, "html.parser"))
    return {
        "tipo_subasta": _spain_field(f, "Tipo de subasta"),
        "expediente": _spain_field(f, "Cuenta expediente", "Expediente"),
        "deposito": parse_price(_spain_field(f, "Importe del depósito", "depósito", "deposito")),
    }


def _spain_parse_authority(html: str) -> dict:
    f = _spain_table_fields(BeautifulSoup(html, "html.parser"))
    email = _EMAIL_RE.search(_spain_field(f, "Correo", "E-mail", "Email") or "")
    return {
        "autoridad": _spain_field(f, "Descripción", "Descripcion", "Nombre"),
        "autoridad_direccion": _spain_field(f, "Dirección", "Direccion"),
        "autoridad_telefono": _spain_field(f, "Teléfono", "Telefono"),
        "autoridad_email": email.group(0) if email else None,
    }


def _spain_parse_goods(html: str) -> dict:
    f = _spain_table_fields(BeautifulSoup(html, "html.parser"))
    posesoria = _spain_field(f, "Situación posesoria", "Situacion posesoria")
    return {
        "situacion_posesoria": posesoria,
        "occupation": _spain_occupation(posesoria),
        "visitable": _spain_field(f, "Visitable"),
        "vivienda_habitual": _spain_field(f, "Vivienda habitual"),
        "cargas": _spain_field(f, "Cargas"),
        "referencia_catastral": _spain_field(f, "Referencia catastral"),
    }


_TAB_PARSERS = {"general": _spain_parse_general, "authority": _spain_parse_authority,
                "goods": _spain_parse_goods}


def spain_details(pages: dict[str, str]) -> tuple[dict, dict]:
    """(listing fields, raw_json extras) from the fetched detail tabs."""
    fields: dict = {}
    for tab in ("general", "goods"):          # the authority tab's "Descripción" is the court
        if tab in pages:
            for k, v in _spain_parse_detail(pages[tab]).items():
                if v is not None and fields.get(k) is None:
                    fields[k] = v
    extra = {}
    for tab, html in pages.items():
        extra.update({k: v for k, v in _TAB_PARSERS[tab](html).items() if v})
    return fields, extra


# ─── Catastro: Spain's land registry, free and public ───────────────
CATASTRO_API = "https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/COVCCallejero.svc/json/Consulta_DNPRC"
CATASTRO_PER_SCAN = 60


def _find(obj, key):
    """First value under `key` anywhere in a nested dict/list."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def parse_catastro(payload: dict) -> dict | None:
    """What Catastro's Consulta_DNPRC says about one property: address, use,
    built area (sfc), plot area (ss), year built (ant) and the buildings on it.
    None when it has no such property (or answers with an error)."""
    if not isinstance(payload, dict) or _find(payload, "lerr"):
        return None
    units = []
    lcons = _find(payload, "lcons")
    for c in (lcons if isinstance(lcons, list) else [lcons] if lcons else []):
        area = to_number(_find(c, "stl"))
        if c and _find(c, "lcd"):
            units.append({"use": _find(c, "lcd"), "m2": area})
    out = {
        "address": _find(payload, "ldt"),
        "use": _find(payload, "luso"),
        "built_m2": to_number(_find(payload, "sfc")),
        "plot_m2": to_number(_find(payload, "ss")),
        "year": to_number(_find(payload, "ant")),
        "units": units[:10] or None,
    }
    out = {k: v for k, v in out.items() if v not in (None, "", [])}
    return out or None


def fetch_catastro(db, session, limit: int = CATASTRO_PER_SCAN) -> int:
    """Look up BOE sales with a referencia catastral in Catastro, once each."""
    rows = db.execute("""
        SELECT id, raw_json FROM listings WHERE source = 'spain'
          AND raw_json LIKE '%"referencia_catastral"%' AND raw_json NOT LIKE '%"catastro_checked"%'
        ORDER BY last_seen DESC LIMIT ?""", (limit,)).fetchall()
    done = 0
    for listing_id, raw_text in rows:
        raw = json.loads(raw_text)
        ref = re.sub(r"[^0-9A-Za-z]", "", str(raw.get("referencia_catastral") or "")).upper()
        if len(ref) not in (14, 20):
            raw["catastro_checked"] = True          # not a usable reference: do not ask again
        else:
            try:
                resp = session.get(CATASTRO_API, params={"RefCat": ref}, timeout=20)
                resp.raise_for_status()
                data = parse_catastro(resp.json())
            except Exception as e:
                # Catastro refuses some connections (seen from a Portuguese mobile
                # network); try again next scan rather than marking it checked.
                LOG.info(f"Catastro not reachable now ({type(e).__name__}); trying next scan")
                break
            raw["catastro_checked"] = True
            if data:
                raw["catastro"] = data
                done += 1
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), listing_id))
        time.sleep(0.3)
    db.commit()
    if done:
        LOG.info(f"Catastro: read {done} properties")
    return done


def enrich_spain_details(db, session, limit: int = 300):
    """Fetch the BOE detail tabs for Spanish listings not checked yet (or still
    without a price), soonest first: price and dates for the listing, the
    court's name and e-mail for letters, occupancy for the score.

    Never-checked sales go first: re-reading the same unpriced ones every scan
    left 3 of 4 open sales without a price (shown as €0)."""
    rows = db.execute("""
        SELECT * FROM listings WHERE source='spain'
          AND (price IS NULL OR raw_json IS NULL OR raw_json NOT LIKE '%"detail_checked"%')
          AND (date_end IS NULL OR date_end >= ?)
        ORDER BY (raw_json LIKE '%"detail_checked"%'), date_end IS NULL, date_end LIMIT ?
    """, (datetime.now().strftime("%Y-%m-%d"), limit)).fetchall()
    if not rows:
        return 0

    LOG.info(f"  Spain details: fetching {len(rows)} listings")
    filled = 0
    for row in rows:
        item = dict(row)
        pages = {}
        for tab, ver in BOE_TABS:
            try:
                resp = session.get(BOE_DETAIL, params={"idSub": item["external_id"], "ver": ver})
                resp.raise_for_status()
                pages[tab] = resp.text
            except Exception as e:
                LOG.debug(f"  Spain detail {item['external_id']} tab {tab}: {e}")
            time.sleep(0.4)
        if not pages:
            continue

        parsed, extra = spain_details(pages)
        try:
            raw = json.loads(item.get("raw_json") or "{}") or {}
        except ValueError:
            raw = {}
        raw.update(extra)
        raw["detail_checked"] = datetime.now().strftime("%Y-%m-%d")
        fields = {k: item.get(k) for k in (
            "title", "description", "tipo", "area_m2", "price", "current_bid", "min_price",
            "district", "concelho", "freguesia", "url", "image_url", "date_end")}
        fields.update(parsed)
        fields["raw_json"] = json.dumps(raw, ensure_ascii=False)
        listing = make_listing("spain", item["external_id"], "ES", **fields)
        upsert_listing(db, listing)
        clear_step_min_price(db, listing)
        if parsed.get("price") is not None:
            filled += 1
        db.commit()

    LOG.info(f"  Spain details: {len(rows)} checked, price found for {filled}")
    return filled


@register("aeat", "ES", default=False)
def scrape_aeat(db, max_price: float = 50000, **_):
    """Spanish tax-authority (AEAT) auctions — already in the BOE scan, so not scanned twice."""
    # AEAT's old list page is gone (404). Its auctions are published on
    # subastas.boe.es, which the "spain" source scans in full (IDs "SUB-AT-…").
    raise SourceUnavailable(
        "AEAT's own auction page is gone; its auctions are on subastas.boe.es and "
        "come in through the Spain (BOE) source")


# Not in the default scan (Sept 2026): Sareb: behind an Incapsula bot wall (403); check sareb.es by hand.
# Bot walls are not worked around; it stays runnable by name in case the site opens up.
@register("sareb", "ES", default=False)
def scrape_sareb(db, max_price: float = 100000, **_):
    """sareb.es — Spanish state bad bank."""
    session = make_session()
    base = "https://www.sareb.es"
    total = 0
    for page in range(1, 50):
        try:
            resp = session.get(f"{base}/inmuebles/search",
                               params={"page": page, "pageSize": 50, "precioMax": int(max_price),
                                       "tipoInmueble": "residencial"})
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            if "_Incapsula_Resource" in resp.text:
                raise SourceUnavailable("sareb.es answers scripts with an Incapsula bot check "
                                        "instead of listings; look at it in a browser")
            data = resp.json()
        except Exception:
            if page == 1:
                raise
            break
        items = data if isinstance(data, list) else data.get("inmuebles", data.get("items", []))
        if not items:
            break
        for item in items:
            price = to_number(item.get("precio")) or to_number(item.get("price")) or 0
            if price > max_price:
                continue
            eid = str(item.get("id") or item.get("referencia")
                      or stable_id(json.dumps(item, sort_keys=True, default=str)))
            upsert_listing(db, make_listing(
                "sareb", eid, "ES",
                title=str(item.get("titulo") or item.get("title") or f"Sareb #{eid}")[:200],
                description=item.get("descripcion") or "Sareb distressed property",
                tipo=item.get("tipo") or "inmueble",
                area_m2=item.get("superficie") or item.get("area"),
                price=price, min_price=price,
                district=item.get("provincia") or item.get("district"),
                concelho=item.get("municipio") or item.get("city"),
                url=item.get("url") or f"{base}/inmueble/{eid}", base_url=base,
                image_url=item.get("imagen") or item.get("image"),
                raw_json=json.dumps(item, ensure_ascii=False)[:2000],
            ))
            total += 1
        db.commit()
        if len(items) < 50:
            break
        time.sleep(0.8)
    return total


HAYA = CardSite(
    source="haya", country="ES", base="https://www.haya.es", path="/inmuebles/",
    card_selector="div.property-card, article.inmueble, div[class*='property'], div[class*='inmueble']",
    title_selector="h2,h3,.title,.property-title",
    location_selector=".location,.localidad,.municipio",
    area_selector=".area,.superficie,[class*='area']",
    params={"precio_max": "{max_price}"}, max_pages=29,
    description="Haya Real Estate (Sareb/BBVA)", tipo="inmueble", price_is_min_price=True,
)
@register("haya", "ES")
def scrape_haya(db, max_price: float = 100000, **_):
    """haya.es — Sareb/BBVA repossessions."""
    return scrape_cards(db, HAYA, max_price)


# servihabitat.com (Liferay): one page per province, cheapest first with ?o=4,
# 20 homes a page. Later pages load by script, so only the first is read; with a
# small budget the cheapest 20 cover it (a province where all 20 are within
# budget is logged).
SERVIHABITAT = "https://www.servihabitat.com"
SERVIHABITAT_PROVINCES = (
    "alava", "albacete", "alicante", "almeria", "asturias", "avila", "badajoz", "barcelona", "burgos", "caceres",
    "cadiz", "cantabria", "castellon", "ciudadreal", "cordoba", "lacoruna", "cuenca", "girona", "granada",
    "guadalajara", "gipuzkoa", "huelva", "huesca", "illesbalears", "jaen", "leon", "lleida", "lugo", "madrid",
    "malaga", "murcia", "navarra", "palencia", "laspalmas", "pontevedra", "larioja", "salamanca", "segovia",
    "sevilla", "soria", "tarragona", "teruel", "toledo", "valencia", "valladolid", "bizkaia", "zamora",
    "zaragoza", "ceuta", "melilla",
)


_ARTICLE = re.compile(r"^(?:el|la|los|las|l'|els|les|o|a|os|as)$", re.I)


def servihabitat_town(title: str) -> str | None:
    """The town in "Casa en venta en C. Larga, 26, Campo De Peñaranda, El, Salamanca":
    the part before the province, with a trailing article put back in front
    ("El Campo De Peñaranda"); alone, "El" found a beach in Tarragona."""
    parts = [p.strip() for p in (title or "").split(",") if p.strip()]
    if len(parts) < 3:
        return None
    town = parts[-2]
    if _ARTICLE.match(town):
        # Need a name before the article ("Campo De Peñaranda, El, Salamanca").
        if len(parts) < 4:
            return None
        prev = next((p for p in reversed(parts[:-2]) if p and not p[0].isdigit()), None)
        if not prev:
            return None
        town = f"{town} {prev}"
    if _ARTICLE.match(town):
        return None
    return town or None


def repair_servihabitat_place(db, row: dict) -> None:
    """Replace a stored article-only concelho, or clear it when the title has none.

    Updates keep the old value when the new one is missing, so "El" from an
    earlier parse would otherwise stay forever and pick the wrong beach."""
    listing_id = row.get("id")
    if not listing_id:
        return
    current = db.execute("SELECT concelho FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not current or not current[0] or not _ARTICLE.match(current[0].strip()):
        return
    town = row.get("concelho") or servihabitat_town(row.get("title") or "")
    if town and not _ARTICLE.match(town.strip()):
        db.execute("UPDATE listings SET concelho = ? WHERE id = ?", (town, listing_id))
    else:
        db.execute("UPDATE listings SET concelho = NULL WHERE id = ?", (listing_id,))


def parse_servihabitat_page(html: str, province: str) -> list[dict]:
    rows = []
    for a in BeautifulSoup(html, "html.parser").select("a.features[href]"):
        href = a["href"]
        m = re.search(r"/(\d+)/?$", href)
        if not m:
            continue
        text = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        price_el = a.select_one(".price")
        price = parse_price(price_el.get_text(" ")) if price_el else find_price(text)
        title_m = re.search(r"((?:Vivienda|Casa|Piso|Chalet|Apartamento|Dúplex|Ático|Estudio|Finca|Terreno)[^0-9]*?"
                            r"en venta en .+?)(?=\s\d+\s*m\s?2|$)", text)
        title = (title_m.group(1) if title_m else text)[:200]
        item = a.find_parent("div", class_="product-item")                  # the card: photos and details
        img = item.select_one("img.img-car") if item else None
        town = servihabitat_town(title)
        rows.append(make_listing(
            "servihabitat", m.group(1), "ES", title=title, description=text[:500], tipo="vivienda",
            area_m2=find_area(text), price=price, min_price=price, district=province,
            concelho=town, url=href, base_url=SERVIHABITAT,
            image_url=(img.get("data-src") or img.get("src")) if img else None,
        ))
    return rows


SERVIHABITAT_DETAILS_PER_SCAN = 80


def servihabitat_description(html: str) -> str | None:
    """The "Descripción" block of a listing page: where "sin posesión" (occupied,
    no visits, no mortgage) is written; the search card does not say it."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", BeautifulSoup(body, "html.parser").get_text(" ")).strip()
    m = re.search(r"Descripción\s+(.+?)(?:\s+Descargas\b|\s+Ubicación del inmueble|\s+Que no te lo quiten|$)", text)
    return m.group(1).strip()[:2000] if m else None


def _servihabitat_details(db, session, rows: list[dict], budget: list[int]) -> None:
    for row in rows:
        known = db.execute("SELECT raw_json FROM listings WHERE id = ?", (row["id"],)).fetchone()
        if known and known[0] and '"detail_checked"' in known[0]:
            kept = json.loads(known[0])
            if kept.get("descripcion"):
                row["description"] = f"{kept['descripcion']} · {row.get('description') or ''}"[:3000]
            row["raw_json"] = known[0]
            continue
        if budget[0] <= 0:
            continue
        budget[0] -= 1
        try:
            resp = session.get(row["url"])
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001: the card alone is still a listing
            LOG.info(f"Servihabitat details of {row['id']} failed ({type(e).__name__})")
            continue
        text = servihabitat_description(resp.text)
        row["raw_json"] = json.dumps({"detail_checked": 1, "descripcion": text}, ensure_ascii=False)
        if text:
            row["description"] = f"{text} · {row.get('description') or ''}"[:3000]
        time.sleep(0.3)


@register("servihabitat", "ES")
def scrape_servihabitat(db, max_price: float = 100000, **_):
    """servihabitat.com — CaixaBank repossessions, the cheapest homes of each province."""
    session = make_session(timeout=30)
    total, full = 0, []
    details_left = [SERVIHABITAT_DETAILS_PER_SCAN]
    found: list[dict] = []
    for province in SERVIHABITAT_PROVINCES:
        try:
            resp = session.get(f"{SERVIHABITAT}/es/venta/vivienda/{province}", params={"o": 4})
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001: one province failing is not the source failing
            LOG.info(f"Servihabitat {province}: {type(e).__name__}")
            continue
        rows = parse_servihabitat_page(resp.text, province)
        if len(rows) >= 20 and all((r["price"] or 0) <= max_price for r in rows):
            full.append(province)
        found += [row for row in rows if row["price"] and row["price"] <= max_price]
        time.sleep(0.5)
    # The detail pages, cheapest first: read province by province, the budget ran
    # out before the last provinces (Sevilla) and their "sin posesión" went unseen.
    found.sort(key=lambda row: row["price"])
    _servihabitat_details(db, session, found, details_left)
    for row in found:
        upsert_listing(db, row)
        repair_servihabitat_place(db, row)
        total += 1
    db.commit()
    if full:
        LOG.info(f"Servihabitat: more within budget than one page in {', '.join(full)}")
    LOG.info(f"Servihabitat: {total} listings")
    return total


@register("subastasactivas", "ES")
def scrape_subastasactivas(db, max_price: float = 100000, **_):
    """subastasactivas.com — aggregator of BOE/AEAT/Social Security/notarial auctions."""
    session = make_session()
    base = "https://subastasactivas.com"
    resp = session.get(f"{base}/subastas",
                       params={"tipo": "inmueble", "precioMax": int(max_price), "estado": "activa"})
    resp.raise_for_status()
    total = 0
    seen = set()
    for card in BeautifulSoup(resp.text, "html.parser").select(
            "div.subasta, article, div[class*='subasta'], tr[class*='subasta']"):
        link = card.select_one("a[href]")
        url = safe_url(link.get("href"), base) if link else None
        if not url:
            continue
        m = re.search(r"/(\w+)/?$", url.split("?")[0])
        eid = m.group(1) if m else stable_id(url)
        if eid in seen:
            continue
        seen.add(eid)
        text = card.get_text(" ", strip=True)
        price = find_price(text)
        if price and price > max_price:
            continue
        upsert_listing(db, make_listing(
            "subastasactivas", eid, "ES",
            title=text[:120], description="SubastasActivas aggregator",
            tipo="inmueble", price=price, url=url, date_end=parse_date_dmy(text),
        ))
        total += 1
    return total


# ─── Aliseda (Santander's repossessions) ────────────────────────────
# The site's own search API: price, the map position, the full text and who
# holds it, 12 a page. Homes (tipo 10) and land (tipo 8) within budget, all Spain.
ALISEDA_API = "https://laravel.alisedainmobiliaria.com/api/v2/new-search"
ALISEDA_SITE = "https://www.alisedainmobiliaria.com"
ALISEDA_TYPES = {10: "vivienda", 8: "terreno"}
ALISEDA_MAX_PAGES = 80


def parse_aliseda(item: dict, tipo: str) -> dict | None:
    op = item.get("operacion") or {}
    addr = item.get("address") or {}
    price = op.get("Precio")
    if not item.get("id") or not price:
        return None
    town = (addr.get("Ciudad") or "").title() or None
    street = " ".join(str(x) for x in (addr.get("TipoVia"), addr.get("StreetName"), addr.get("StreetNumber")) if x)
    area = item.get("SupParcela") or item.get("SuperficieTotal") if tipo == "terreno" else \
        item.get("ConstructedArea") or item.get("SuperficieTotal")
    raw = {"posesion": item.get("posesion"), "referencia_catastral": item.get("RefCatastral") or None,
           "precio_anterior": op.get("PrecioAnterior")}
    if addr.get("Latitude") and addr.get("Longitude"):
        raw["geo"] = {"lat": float(addr["Latitude"]), "lon": float(addr["Longitude"]), "precision": "street"}
    posesion = (item.get("posesion") or "").upper()
    if posesion.startswith("LIBRE"):
        raw["occupation"] = "vacant"
    elif "OCUPA" in posesion or "SIN POSES" in posesion or "ARREND" in posesion:
        raw["occupation"] = "occupied"
    title = f"{'Terreno' if tipo == 'terreno' else 'Vivienda'} en {town or ''}" + (f", {street}" if street else "")
    images = item.get("imagenes") or []
    return make_listing(
        "aliseda", item["id"], "ES", title=title[:200],
        description=(item.get("Description") or "")[:3000] or None, tipo=tipo,
        area_m2=area or None, price=float(price), min_price=float(price),
        district=(item.get("provinciaUrl") or "").replace("-", " ").title() or None, concelho=town,
        url=f"{ALISEDA_SITE}/inmueble/{item['id']}",
        image_url=images[0].get("Uri") if images else item.get("Imagen"),
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


@register("aliseda", "ES")
def scrape_aliseda(db, max_price: float = 50000, config: dict | None = None, **_):
    """aliseda — Santander's repossessed homes and land, with map positions."""
    session = make_session(timeout=30)
    total = 0
    for code, tipo in ALISEDA_TYPES.items():
        limit = land_max_price(config, max_price) if tipo == "terreno" else max_price
        for page in range(1, ALISEDA_MAX_PAGES + 1):
            resp = session.get(ALISEDA_API, params={"tipo": code, "precio": f"0-{int(limit)}", "page": page})
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("data") or []:
                row = parse_aliseda(item, tipo)
                if row and row["price"] <= limit:
                    upsert_listing(db, row)
                    total += 1
            db.commit()
            if page >= (data.get("last_page") or 1):
                break
            time.sleep(0.4)
    LOG.info(f"Aliseda: {total} listings")
    return total


# ─── Altamira (bank and fund repossessions, doValue) ────────────────
# The site's own results service: price, map position and the owner, 100 a
# page. Homes (tipología 1) and land (9) within budget, all Spain.
ALTAMIRA_API = "https://www.altamirainmuebles.com/nodejs/getResultados"
ALTAMIRA_SITE = "https://www.altamirainmuebles.com"
ALTAMIRA_TYPES = {1: "vivienda", 9: "terreno"}
ALTAMIRA_PAGE = 100


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(text or "")).strip("-")


def parse_altamira(card: dict, tipo: str) -> dict | None:
    price = card.get("precio")
    if not card.get("referencia") or not price or card.get("preciovisible") == 0:
        return None
    kind = card.get("tipologia") or ("Suelo" if tipo == "terreno" else "Vivienda")
    town = card.get("poblacion") or card.get("poblacionurl")
    raw = {"sociedad": card.get("sociedadpropietaria"), "riesgo_ocupacion": card.get("riesgoocupacion")}
    if card.get("latitud") and card.get("longitud"):
        raw["geo"] = {"lat": float(card["latitud"]), "lon": float(card["longitud"]), "precision": "street"}
    if card.get("riesgoocupacion"):
        raw["occupation"] = "occupied"
    url = (f"{ALTAMIRA_SITE}/venta-de-{_slug(kind)}/{_slug(card.get('provinciaurl'))}/{_slug(card.get('poblacionurl'))}"
           f"/segunda-mano/{card['referencia']}/{card.get('cinmueble')}/1")
    street = card.get("calle") or ""
    return make_listing(
        "altamira", card["referencia"], "ES", title=f"{kind} en {town}" + (f", {street}" if street else ""),
        description=" · ".join(str(x) for x in (kind, street, card.get("cp"), town, card.get("provinciaurl"),
                                                f"{card['numhab']} habs" if card.get("numhab") else None) if x),
        tipo=tipo, area_m2=card.get("superficie") or None, price=float(price), min_price=float(price),
        district=card.get("provinciaurl"), concelho=town, url=url,
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


@register("altamira", "ES")
def scrape_altamira(db, max_price: float = 50000, config: dict | None = None, **_):
    """altamira — bank and fund repossessions (homes and land) with map positions."""
    session = make_session(timeout=30)
    total = 0
    for code, tipo in ALTAMIRA_TYPES.items():
        limit = land_max_price(config, max_price) if tipo == "terreno" else max_price
        page, seen = 1, 0
        while True:
            body = {"buscador": {"idGestion": 1, "idTipologia": code, "idProvincia": None, "idPoblacion": None},
                    "filtros": {"precioMaximo": int(limit), "order": 1, "pagina": page,
                                "limite": str(ALTAMIRA_PAGE), "cntxParamSubastasActivo": "1",
                                "cntxParamSubastasSarebActivo": "1", "cntxParamSubastasCodSocsAAM": "1,2,7",
                                "modoVisualizacion": "L"}, "user": None}
            resp = session.post(ALTAMIRA_API, json=body)
            resp.raise_for_status()
            data = resp.json()
            cards = data.get("minifichas") or []
            for card in cards:
                row = parse_altamira(card, tipo)
                if row and row["price"] <= limit:
                    upsert_listing(db, row)
                    total += 1
            db.commit()
            seen += len(cards)
            if not cards or seen >= int(data.get("totalResultados") or 0) or page >= 50:
                break
            page += 1
            time.sleep(0.4)
    LOG.info(f"Altamira: {total} listings")
    return total


# ─── Fotocasa: Spain's big private portal ───────────────────────────
# Not auctions: owners' and agents' asking prices, all over Spain (the score
# judges heat, cold and water, not the source). The results page carries its data as JSON, 30 a page, with the
# text, the map position and whether it is occupied.
FOTOCASA = "https://www.fotocasa.es"
FOTOCASA_PROVINCES = (   # all of Spain: the score decides, not the region (owner, 2026-10-05)
    "a-coruna", "albacete", "alicante", "almeria", "asturias", "badajoz", "barcelona", "bizkaia", "burgos",
    "cantabria", "castellon", "ceuta", "ciudad-real", "cuenca", "caceres", "cadiz", "cordoba", "gipuzkoa",
    "girona", "granada", "guadalajara", "huelva", "huesca", "illes-balears", "jaen", "la-rioja",
    "las-palmas", "leon", "lleida", "lugo", "madrid", "melilla", "murcia", "malaga", "navarra", "ourense",
    "palencia", "pontevedra", "salamanca", "santa-cruz-de-tenerife", "segovia", "sevilla", "soria",
    "tarragona", "teruel", "toledo", "valencia", "valladolid", "zamora", "zaragoza", "araba-alava", "avila",
)
FOTOCASA_SEARCHES = (("viviendas", None), ("terrenos", 10000))     # homes; land from 1 ha
FOTOCASA_MAX_PAGES = 25


def fotocasa_page(html: str) -> tuple[list[dict], int]:
    """(ads, total ads) from a results page."""
    m = re.search(r'<script[^>]*id="__initial_props__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return [], 0
    props = json.loads(m.group(1))
    result = (props.get("initialSearch") or {}).get("result") or {}
    return result.get("realEstates") or [], int((props.get("counters") or {}).get("realEstates") or 0)


def parse_fotocasa(ad: dict, tipo: str) -> dict | None:
    price = ad.get("rawPrice")
    if not ad.get("id") or not price:
        return None
    addr = ad.get("address") or {}
    feats = {f.get("key"): f.get("value") for f in ad.get("features") or []}
    town = re.sub(r"\s*\(.*?\)\s*$", "", addr.get("municipality") or addr.get("city") or "") or None
    raw: dict = {"occupation": "occupied"} if ad.get("isOccupied") or ad.get("isRentedWithTenants") else {}
    if ad.get("isBareOwnership"):
        raw["nuda_propiedad"] = True
    coords = ad.get("coordinates") or {}
    if coords.get("latitude") and coords.get("longitude"):
        raw["geo"] = {"lat": float(coords["latitude"]), "lon": float(coords["longitude"]),
                      "precision": "street" if ad.get("accuracy") else "village"}
    detail = (ad.get("detail") or {}).get("es-ES")
    images = [m.get("src") for m in ad.get("multimedia") or [] if m.get("src")]
    kind = "Terreno" if tipo == "terreno" else "Casa" if "House" in (ad.get("buildingSubtype") or "") else "Vivienda"
    location = ad.get("location") or ""
    return make_listing(
        "fotocasa", ad["id"], "ES", title=f"{kind} en {town or addr.get('province') or ''}"
                                          + (f", {location}" if location else ""),
        description=(ad.get("description") or "")[:3000] or None, tipo=tipo,
        area_m2=feats.get("surface") or None, price=float(price), min_price=float(price),
        district=addr.get("province"), concelho=town, url=f"{FOTOCASA}{detail}" if detail else None,
        image_url=images[0] if images else None, raw_json=json.dumps(raw, ensure_ascii=False) if raw else None,
    )


@register("fotocasa", "ES")
def scrape_fotocasa(db, max_price: float = 50000, config: dict | None = None, **_):
    """fotocasa — private homes and land (1 ha+) all over Spain."""
    session = make_session(timeout=30)
    total = 0
    for province in FOTOCASA_PROVINCES:
        for kind, min_surface in FOTOCASA_SEARCHES:
            limit = land_max_price(config, max_price) if kind == "terrenos" else max_price
            params = {"maxPrice": int(limit)}
            if min_surface:
                params["minSurface"] = min_surface
            seen = 0
            for page in range(1, FOTOCASA_MAX_PAGES + 1):
                path = f"{FOTOCASA}/es/comprar/{kind}/{province}-provincia/todas-las-zonas/l" + \
                       (f"/{page}" if page > 1 else "")
                try:
                    resp = session.get(path, params=params)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001 — one province failing is not the source failing
                    if total == 0 and province == FOTOCASA_PROVINCES[0]:
                        raise
                    LOG.info(f"Fotocasa {province} {kind} p{page}: {type(e).__name__}")
                    break
                ads, count = fotocasa_page(resp.text)
                for ad in ads:
                    row = parse_fotocasa(ad, "terreno" if kind == "terrenos" else "vivienda")
                    if row and row["price"] <= limit:
                        upsert_listing(db, row)
                        total += 1
                db.commit()
                seen += len(ads)
                if not ads or seen >= count:
                    break
                time.sleep(0.8)
    LOG.info(f"Fotocasa: {total} listings")
    return total


# ─── Seguridad Social (TGSS) ─────────────────────────────────────────
# Property seized for social-security debts, auctioned by the Tesorería. Not in
# the BOE portal. A three-step form kept in the session: types, area, results.

TGSS = "https://w6.seg-social.es/subastas/"
TGSS_APP = TGSS + "SubaSeControladorInter"
TGSS_TYPES = [("EMB_TIPOBIEN", "0101"), ("EMB_TIPOBIEN", "0102")]   # rural and urban property
TGSS_MAX_PAGES = 40


def _euros(text: str) -> float | None:
    m = re.search(r"([\d.]+,\d{2})", text or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def tgss_rows(html: str) -> list[dict]:
    """The results table: one dict per lot with id, address, values and date."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in soup.select("div.tablas-resultados table"):
        caption = table.caption.get_text(" ", strip=True) if table.caption else ""
        kind = caption.split(" - ")[0].strip()
        for tr in table.select("tbody tr"):
            cells = tr.find_all("td")
            link = tr.find("a", href=True)
            m = re.search(r"EMB_ID=(\d+)", link["href"]) if link else None
            if not m or len(cells) < 7:
                continue
            rows.append({"id": m.group(1), "kind": kind, "address": " ".join(link.get_text(" ", strip=True).split()),
                         "valuation": _euros(cells[2].get_text()), "charges": _euros(cells[3].get_text()) or 0.0,
                         "price": _euros(cells[5].get_text()), "date": cells[6].get_text(" ", strip=True)})
    return rows


def tgss_detail(html: str) -> dict:
    """Description and full location from a lot's detail page."""
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    out = {}
    for key, label in (("description", r"Descripci[óo]n General del Bien:"), ("location", r"Localizaci[óo]n:")):
        m = re.search(label + r"\s*(.+?)\s+(?:Localizaci|Subasta Fecha|Lote:|Tipo de enajenaci)", text)
        if m:
            out[key] = m.group(1).strip()
    return out


# The registry text names the owner and their ID; neither is kept.
_TGSS_OWNER = re.compile(r"TITULARIDAD:.*?(?=\d+\s*%|PLENO DOMINIO|FINCA|URBANA|R[ÚU]STICA|$)|"
                         r"\b(?:D\.?N\.?I\.?|N\.?I\.?F\.?|N\.?I\.?E\.?)\s*:?\s*[XYZ]?\d{7,8}\s*-?\s*[A-Z]?\b", re.I)
_TGSS_HECTARES = re.compile(r"Superficie:\s*([\d.]+(?:,\d+)?)\s*\(hect[áa]reas\)", re.I)


def _tgss_area(text: str) -> float | None:
    m = _TGSS_HECTARES.search(text or "")
    if m:
        return round(float(m.group(1).replace(".", "").replace(",", ".")) * 10000, 1)
    return find_area(text)


def parse_tgss(row: dict, detail: dict) -> dict | None:
    if not row.get("price"):
        return None
    place = re.search(r"\(\s*([^)]+?)\s*\)\s*$", row["address"])
    town = place.group(1).title() if place else None
    location = detail.get("location") or row["address"]
    postcode = re.search(r"\((\d{5})\)\s*(.+)$", location)
    if postcode:
        town = postcode.group(2).strip().title()
    rural = "stica" in row["kind"]
    description = " · ".join(x for x in (detail.get("description"), location,
                                         f"Tasación {row['valuation']:,.2f} €" if row.get("valuation") else None,
                                         f"Cargas {row['charges']:,.2f} € (a cargo del comprador)"
                                         if row.get("charges") else None,
                                         "Subasta de la Seguridad Social") if x)
    description = re.sub(r"\s{2,}", " ", _TGSS_OWNER.sub(" ", description)).strip()
    raw = {"charges_eur": row["charges"]} if row.get("charges") else {}
    return make_listing(
        "tgss", row["id"], "ES", title=f"{row['kind']} en {town or row['address']}",
        description=description, tipo="terreno" if rural else "vivienda",
        area_m2=_tgss_area(description), price=row["price"], min_price=row["price"],
        concelho=town, date_end=parse_date_dmy(row.get("date")),
        url=f"{TGSS_APP}?opcion=13&EMB_ID={row['id']}&opcion2=1&tipoOperacion=1",
        raw_json=json.dumps(raw) if raw else None,
    )


@register("tgss", "ES", description="Seguridad Social auctions of seized property (all of Spain)")
def scrape_tgss(db, max_price: float = 50000, **_):
    """TGSS — property seized for social-security debts, all of Spain."""
    session = make_session(timeout=60)
    session.get(TGSS).raise_for_status()
    session.get(TGSS_APP, params=[("opcion", "10")] + TGSS_TYPES).raise_for_status()
    total = 0
    for page in range(1, TGSS_MAX_PAGES + 1):
        params = {"opcion": "8", "tipoOperacion": "1"}
        if page > 1:                   # the first page refuses "pagina=1"
            params = {"pagina": page, **params}
        resp = session.get(TGSS_APP, params=params)
        resp.raise_for_status()
        rows = tgss_rows(resp.text)
        for row in rows:
            if not row.get("price") or row["price"] + row["charges"] > max_price:
                continue
            detail = session.get(TGSS_APP, params={"opcion": "13", "EMB_ID": row["id"], "opcion2": "1",
                                                   "tipoOperacion": "1"})
            listing = parse_tgss(row, tgss_detail(detail.text) if detail.ok else {})
            if listing:
                upsert_listing(db, listing)
                total += 1
            time.sleep(0.4)
        db.commit()
        if len(rows) < 20:
            break
        time.sleep(0.8)
    LOG.info(f"TGSS: {total} listings")
    return total


# ─── pisos.com ───────────────────────────────────────────────────────
# Private sales all over Spain, like fotocasa. The search page has price,
# size, town and the start of the text; the full text and the map position are
# on the detail page, read once for listings not seen before.

PISOS = "https://www.pisos.com"
# Coolest and wettest first: they get the detail budget before the hot inland.
PISOS_PROVINCES = {   # all of Spain
    "a_coruna": "A Coruña", "albacete": "Albacete", "alicante": "Alicante", "almeria": "Almería",
    "asturias": "Asturias", "badajoz": "Badajoz", "barcelona": "Barcelona", "vizcaya_bizkaia": "Bizkaia",
    "burgos": "Burgos", "cantabria": "Cantabria", "castellon_castello": "Castellón", "ceuta": "Ceuta",
    "ciudad_real": "Ciudad Real", "cuenca": "Cuenca", "caceres": "Cáceres", "cadiz": "Cádiz",
    "cordoba": "Córdoba", "guipuzcoa_gipuzkoa": "Gipuzkoa", "girona": "Girona", "granada": "Granada",
    "guadalajara": "Guadalajara", "huelva": "Huelva", "huesca": "Huesca",
    "islas_baleares_illes_balears": "Illes Balears", "jaen": "Jaén", "la_rioja": "La Rioja",
    "las_palmas": "Las Palmas", "leon": "León", "lleida": "Lleida", "lugo": "Lugo", "madrid": "Madrid",
    "melilla": "Melilla", "murcia": "Murcia", "malaga": "Málaga", "navarra": "Navarra", "ourense": "Ourense",
    "palencia": "Palencia", "pontevedra": "Pontevedra", "salamanca": "Salamanca",
    "santa_cruz_de_tenerife": "Santa Cruz de Tenerife", "segovia": "Segovia", "sevilla": "Sevilla",
    "soria": "Soria", "tarragona": "Tarragona", "teruel": "Teruel", "toledo": "Toledo",
    "valencia": "Valencia", "valladolid": "Valladolid", "zamora": "Zamora", "zaragoza": "Zaragoza",
    "alava_araba": "Álava", "avila": "Ávila",
}
PISOS_SEARCHES = [("casas", "vivienda"), ("fincas_rusticas", "terreno")]
PISOS_MAX_PAGES = 15
PISOS_DETAILS_PER_SCAN = 400


def pisos_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for card in soup.select("div.ad-preview[id]"):
        link = card.select_one("a.ad-preview__title")
        box = card.select_one("[data-ad-price]")
        if not link or not box:
            continue
        try:
            price = float(box["data-ad-price"])
        except (TypeError, ValueError):
            continue
        chars = [p.get_text(" ", strip=True) for p in card.select(".ad-preview__char")]
        img = card.select_one("img[src]")
        town = card.select_one(".ad-preview__subtitle")
        text = card.select_one(".ad-preview__description")
        cards.append({"id": card["id"], "title": link.get_text(" ", strip=True), "path": link["href"],
                      "price": price, "town": town.get_text(" ", strip=True) if town else None,
                      "area": find_area(" ".join(chars)), "excerpt": text.get_text(" ", strip=True) if text else "",
                      "image": img["src"] if img else None})
    return cards


def pisos_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    body = soup.select_one(".description__content")
    if body:
        out["description"] = body.get_text(" ", strip=True)
    m = re.search(r"latitude=(-?\d+\.\d+)&(?:amp;)?longitude=(-?\d+\.\d+)", html)
    if m:
        out["geo"] = {"lat": float(m.group(1)), "lon": float(m.group(2)), "precision": "street"}
    return out


_PISOS_TOWN_CENTRE = {"casco urbano", "centro urbano", "centro", "capital", "nucleo urbano"}


def pisos_municipality(town: str | None) -> str | None:
    """pisos.com writes "Baldedo (Allande)": the village, then its municipality in
    brackets, or "Ourol (Casco Urbano)": the town centre. The municipality is what
    the other portals call the place."""
    m = re.fullmatch(r"\s*(.+?)\s*\((.+?)\)\s*", town or "")
    if not m:
        return town
    return m.group(1) if normalize(m.group(2)).strip() in _PISOS_TOWN_CENTRE else m.group(2)


def parse_pisos(card: dict, tipo: str, province: str, detail: dict | None = None) -> dict:
    detail = detail or {}
    raw = {"geo": detail["geo"]} if detail.get("geo") else {}
    return make_listing(
        "pisos", card["id"], "ES", title=card["title"], tipo=tipo,
        description=(detail.get("description") or card["excerpt"] or "")[:3000] or None,
        area_m2=card["area"], price=card["price"], min_price=card["price"],
        district=PISOS_PROVINCES.get(province, province), concelho=pisos_municipality(card["town"]),
        url=f"{PISOS}{card['path']}", image_url=card["image"],
        raw_json=json.dumps(raw) if raw else None,
    )


@register("pisos", "ES", description="pisos.com — private homes and rural land all over Spain")
def scrape_pisos(db, max_price: float = 50000, config: dict | None = None, **_):
    """pisos.com — private homes and rural land all over Spain."""
    session = make_session(timeout=30)
    # Listings whose detail page was read (it holds the map position); the rest
    # are read as the budget allows, a few hundred a scan.
    read = {r[0] for r in db.execute(
        """SELECT external_id FROM listings WHERE source = 'pisos' AND raw_json LIKE '%"geo"%'
           AND description NOT LIKE '%...'""")}           # only the preview line was kept: read again
    budget, total = PISOS_DETAILS_PER_SCAN, 0
    seen: set[str] = set()   # a thin province's pages are padded with houses from elsewhere
    for province in PISOS_PROVINCES:
        for slug, tipo in PISOS_SEARCHES:
            limit = land_max_price(config, max_price) if tipo == "terreno" else max_price
            for page in range(1, PISOS_MAX_PAGES + 1):
                url = f"{PISOS}/venta/{slug}-{province}/hasta-{int(limit)}/" + (f"{page}/" if page > 1 else "")
                try:
                    resp = session.get(url)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001 — one province failing is not the source failing
                    if total == 0 and page == 1 and province == next(iter(PISOS_PROVINCES)):
                        raise
                    LOG.info(f"pisos.com {slug}-{province} p{page}: {type(e).__name__}")
                    break
                cards = pisos_cards(resp.text)
                for card in cards:
                    if card["price"] > limit or card["id"] in seen:
                        continue
                    seen.add(card["id"])
                    detail = None
                    if card["id"] not in read and budget > 0:
                        budget -= 1
                        try:
                            got = session.get(f"{PISOS}{card['path']}")
                            detail = pisos_detail(got.text) if got.ok else None
                        except Exception:  # noqa: BLE001 — the card alone is still worth keeping
                            detail = None
                        time.sleep(0.5)
                    row = parse_pisos(card, tipo, province, detail)
                    if detail is None:
                        row.pop("raw_json", None)          # never drop a position read earlier
                    upsert_listing(db, row)
                    if detail and detail.get("geo"):
                        read.add(card["id"])
                    total += 1
                db.commit()
                if len(cards) < 30 or f"/{page + 1}/" not in resp.text:
                    break
                time.sleep(0.8)
    LOG.info(f"pisos.com: {total} listings")
    return total


# ─── thinkSPAIN ──────────────────────────────────────────────────────
# A portal for buyers from abroad: many stone houses and fincas in Galicia and
# Asturias listed by agencies that are on no Spanish portal we read. The search
# page carries a schema.org ItemList (name, price, image, start of the text);
# the full text is read from the detail page for new listings.

THINKSPAIN = "https://www.thinkspain.com"
THINKSPAIN_PROVINCES = {   # all of Spain (the Canaries as one)
    "a-coruna": "A Coruña", "albacete": "Albacete", "alicante": "Alicante", "almeria": "Almería",
    "asturias": "Asturias", "badajoz": "Badajoz", "barcelona": "Barcelona", "vizcaya": "Bizkaia",
    "burgos": "Burgos", "cantabria": "Cantabria", "castellon": "Castellón", "ciudad-real": "Ciudad Real",
    "cuenca": "Cuenca", "caceres": "Cáceres", "cadiz": "Cádiz", "cordoba": "Córdoba",
    "guipuzcoa": "Gipuzkoa", "girona": "Girona", "granada": "Granada", "guadalajara": "Guadalajara",
    "huelva": "Huelva", "huesca": "Huesca", "balearic-islands": "Illes Balears", "jaen": "Jaén",
    "la-rioja": "La Rioja", "canary-islands": "Canarias", "leon": "León", "lleida": "Lleida", "lugo": "Lugo",
    "madrid": "Madrid", "murcia": "Murcia", "malaga": "Málaga", "navarra": "Navarra", "orense": "Ourense",
    "palencia": "Palencia", "pontevedra": "Pontevedra", "salamanca": "Salamanca", "segovia": "Segovia",
    "seville": "Sevilla", "soria": "Soria", "tarragona": "Tarragona", "teruel": "Teruel", "toledo": "Toledo",
    "valencia": "Valencia", "valladolid": "Valladolid", "zamora": "Zamora", "zaragoza": "Zaragoza",
    "alava": "Álava", "avila": "Ávila",
}
THINKSPAIN_MAX_PAGES = 10
THINKSPAIN_DETAILS_PER_SCAN = 150
_TS_NAME = re.compile(r"^(?P<what>.+?) for sale in (?P<town>.+?)(?: with .+?)? - € [\d,]+", re.I)


def thinkspain_items(html: str) -> list[dict]:
    m = re.search(r'id="item-list-structured-data"[^>]*>\s*(\{.*?\})\s*</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    items = []
    for entry in data.get("itemListElement") or []:
        item = entry.get("item") or {}
        try:
            price = float((item.get("offers") or {}).get("price"))
        except (TypeError, ValueError):
            continue
        name = item.get("name") or ""
        parts = _TS_NAME.match(name)
        items.append({"id": str(item.get("productID") or ""), "name": name, "price": price,
                      "what": parts.group("what") if parts else name, "town": parts.group("town") if parts else None,
                      "excerpt": item.get("description") or "", "image": item.get("image"), "url": item.get("url")})
    return [i for i in items if i["id"]]


def thinkspain_detail(html: str) -> str | None:
    body = BeautifulSoup(html, "html.parser").select_one("#property-description")
    return body.get_text(" ", strip=True) if body else None


_TS_AREA = re.compile(r"(\d{1,3}(?:[.,]\d{3})*|\d+)\s*(?:square\s+met(?:er|re)s?|sq\.?\s*m|m2|m²)\b", re.I)


def _thinkspain_area(text: str) -> float | None:
    """The first size given in square metres (the text is English); 10 m² or
    less is a stray number, not a size."""
    m = _TS_AREA.search(text or "")
    if m:
        value = float(re.sub(r"[.,]", "", m.group(1)))
    else:
        value = find_area(text) or 0
    return value if value > 10 else None


def parse_thinkspain(item: dict, province: str, description: str | None = None) -> dict:
    text = description or item["excerpt"]
    land = re.search(r"\b(?:plot|land|ruin)\b", item["what"], re.I)
    return make_listing(
        "thinkspain", item["id"], "ES", title=f"{item['what']} in {item['town'] or province}",
        description=text[:3000] or None, tipo="terreno" if land else "vivienda",
        area_m2=_thinkspain_area(text), price=item["price"], min_price=item["price"],
        district=THINKSPAIN_PROVINCES.get(province, province), concelho=item["town"],
        url=item["url"] or f"{THINKSPAIN}/property-for-sale/{item['id']}", image_url=item["image"],
    )


@register("thinkspain", "ES", description="thinkSPAIN — agency listings in Galicia, Asturias, Cantabria and León")
def scrape_thinkspain(db, max_price: float = 50000, **_):
    """thinkSPAIN — agency listings for buyers from abroad all over Spain."""
    session = make_session(timeout=40)
    full = {r[0] for r in db.execute(
        "SELECT external_id FROM listings WHERE source = 'thinkspain' AND length(description) > 400")}
    budget, total = THINKSPAIN_DETAILS_PER_SCAN, 0
    for province in THINKSPAIN_PROVINCES:
        for page in range(1, THINKSPAIN_MAX_PAGES + 1):
            params = {"maxprice": int(max_price)}
            if page > 1:
                params["numpag"] = page
            try:
                resp = session.get(f"{THINKSPAIN}/property-for-sale/{province}", params=params)
                resp.raise_for_status()
                resp.encoding = "utf-8"          # not declared in the headers: "€" came out as "â¬"
            except Exception as e:  # noqa: BLE001 — one province failing is not the source failing
                if total == 0 and page == 1 and province == next(iter(THINKSPAIN_PROVINCES)):
                    raise
                LOG.info(f"thinkSPAIN {province} p{page}: {type(e).__name__}")
                break
            items = thinkspain_items(resp.text)
            for item in items:
                if item["price"] > max_price:
                    continue
                description = None
                if item["id"] not in full and budget > 0:
                    budget -= 1
                    try:
                        got = session.get(item["url"] or f"{THINKSPAIN}/property-for-sale/{item['id']}")
                        got.encoding = "utf-8"
                        description = thinkspain_detail(got.text) if got.ok else None
                    except Exception:  # noqa: BLE001 — the search card alone is still worth keeping
                        description = None
                    time.sleep(1.0)
                upsert_listing(db, parse_thinkspain(item, province, description))
                if description and len(description) > 400:
                    full.add(item["id"])
                total += 1
            db.commit()
            if len(items) < 16:
                break
            time.sleep(1.5)
    LOG.info(f"thinkSPAIN: {total} listings")
    return total


# ─── Solvia ──────────────────────────────────────────────────────────
# Banco Sabadell's servicer (stock now run by Haya). Its own site searches a JSON
# API by province; pages count from 0. The API says whether the bank has
# possession ("sinPosesion": occupied) and whether it needs work.

SOLVIA = "https://www.solvia.es"
SOLVIA_PROVINCES = {   # all of Spain, by INE code
    "15": "A Coruña", "02": "Albacete", "03": "Alicante", "04": "Almería", "33": "Asturias", "06": "Badajoz",
    "08": "Barcelona", "48": "Bizkaia", "09": "Burgos", "39": "Cantabria", "12": "Castellón", "51": "Ceuta",
    "13": "Ciudad Real", "16": "Cuenca", "10": "Cáceres", "11": "Cádiz", "14": "Córdoba", "20": "Gipuzkoa",
    "17": "Girona", "18": "Granada", "19": "Guadalajara", "21": "Huelva", "22": "Huesca",
    "07": "Illes Balears", "23": "Jaén", "26": "La Rioja", "35": "Las Palmas", "24": "León", "25": "Lleida",
    "27": "Lugo", "28": "Madrid", "52": "Melilla", "30": "Murcia", "29": "Málaga", "31": "Navarra",
    "32": "Ourense", "34": "Palencia", "36": "Pontevedra", "37": "Salamanca", "38": "Santa Cruz de Tenerife",
    "40": "Segovia", "41": "Sevilla", "42": "Soria", "43": "Tarragona", "44": "Teruel", "45": "Toledo",
    "46": "Valencia", "47": "Valladolid", "49": "Zamora", "50": "Zaragoza", "01": "Álava", "05": "Ávila",
}
SOLVIA_KINDS = {"Viviendas": "vivienda", "Suelos": "terreno"}
SOLVIA_PAGE = 50
SOLVIA_MAX_PAGES = 20


def parse_solvia(item: dict) -> dict | None:
    kind = SOLVIA_KINDS.get((item.get("categoriaTipoVivienda") or {}).get("nombre"))
    if not kind or not item.get("precio") or not item.get("mostrarPrecio", True):
        return None
    sub = (item.get("tipoVivienda") or {}).get("nombre") or ""
    town = (item.get("poblacion") or {}).get("nombre")
    province = (item.get("provincia") or {}).get("nombre")
    notes = [item.get("tituloFicha"), sub,
             "Sin posesión: inmueble ocupado" if item.get("sinPosesion") else None,
             "Para reformar" if item.get("reformar") else None,
             "En subasta" if item.get("enSubasta") else None,
             "En la costa" if item.get("enCosta") else None]
    ref = str(item["id"]).rsplit("-", 1)[0]           # "192413-155113-O" → "192413-155113"
    images = item.get("listaImagenesInmueble_vPC") or []
    return make_listing(
        "solvia", ref, "ES", title=f"{sub or kind.title()} en {town}" if town else (item.get("tituloFicha") or sub),
        description=" · ".join(n for n in notes if n), tipo=kind, area_m2=item.get("m2") or item.get("totalM2"),
        price=float(item["precio"]), min_price=float(item["precio"]), district=province, concelho=town,
        url=f"{SOLVIA}/es/propiedades/comprar/{kind}-{ref}", image_url=images[0] if images else None,
    )


@register("solvia", "ES", description="Solvia — bank homes and land all over Spain (Sabadell / Haya stock)")
def scrape_solvia(db, max_price: float = 50000, config: dict | None = None, **_):
    """Solvia — bank homes and land all over Spain, from the site's own search API."""
    session = make_session(timeout=40)
    headers = {"Accept": "application/json", "Origin": SOLVIA}
    total = 0
    for pid, name in SOLVIA_PROVINCES.items():
        for page in range(SOLVIA_MAX_PAGES):
            try:
                resp = session.post(f"{SOLVIA}/api/inmuebles/v2/buscarInmuebles", headers=headers,
                                    json={"idProvincia": pid, "paginacion": {"numeroPagina": page,
                                                                             "tamanoPagina": SOLVIA_PAGE}})
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # noqa: BLE001 — one province failing is not the source failing
                if total == 0 and page == 0 and pid == next(iter(SOLVIA_PROVINCES)):
                    raise
                LOG.info(f"Solvia {name} p{page}: {type(e).__name__}")
                break
            for item in data.get("inmuebles") or []:
                row = parse_solvia(item)
                if row and row["price"] <= (land_max_price(config, max_price) if row["tipo"] == "terreno" else max_price):
                    upsert_listing(db, row)
                    total += 1
            db.commit()
            if not (data.get("paginacion") or {}).get("hayPaginaSiguiente") or not data.get("inmuebles"):
                break
            time.sleep(1)
        time.sleep(1)
    LOG.info(f"Solvia: {total} listings")
    return total
