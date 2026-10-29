"""Spain: BOE judicial auctions, AEAT tax auctions, bad-bank / servicer portals."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html import unescape

from bs4 import BeautifulSoup

from common import (LOG, find_area, find_price, has_term, land_max_price, make_listing, make_session, normalize,
                    parse_date_dmy, parse_price, possession, prefer_stated_area, safe_url, stable_id, to_number)
from db import upsert_listing
from sources import ScanCount, SourceUnavailable, register
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
    return possession(text)


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
    """Look up BOE sales with a referencia catastral in Catastro, once each.

    A sale whose price is already over the cap is not looked up."""
    from db import still_worth_a_page_sql
    clause, extra = still_worth_a_page_sql()
    rows = db.execute(f"""
        SELECT id, raw_json FROM listings WHERE source = 'spain'
          AND raw_json LIKE '%"referencia_catastral"%' AND raw_json NOT LIKE '%"catastro_checked"%'
          {clause}
        ORDER BY last_seen DESC LIMIT ?""", (*extra, limit)).fetchall()
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
    left 3 of 4 open sales without a price (shown as €0). A sale whose price
    is already known and over the cap is not opened."""
    from db import still_worth_a_page_sql
    clause, extra = still_worth_a_page_sql()
    rows = db.execute(f"""
        SELECT * FROM listings WHERE source='spain'
          AND (price IS NULL OR raw_json IS NULL OR raw_json NOT LIKE '%"detail_checked"%')
          AND (date_end IS NULL OR date_end >= ?)
          {clause}
        ORDER BY (raw_json LIKE '%"detail_checked"%'), date_end IS NULL, date_end LIMIT ?
    """, (datetime.now().strftime("%Y-%m-%d"), *extra, limit)).fetchall()
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
# Blocked by this PC's firewall (WinError 10013) on every scan. Left runnable by
# name; a default scan no longer waits on it.
@register("haya", "ES", default=False)
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


# Blocked by this PC's firewall on every scan, same as haya.es.
@register("subastasactivas", "ES", default=False)
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


# ─── eActivos (insolvency and court liquidations) ──────────────────
# Online auctions run for administradores concursales and courts. Most start
# at €0 against a stated valuation; the administrator may refuse a result
# against the interest of the concurso. The homes list (asset type 16) comes
# 12 a page; the best bid is its own small JSON call.
EACTIVOS = "https://www.eactivos.com"
EACTIVOS_HOMES = 16
EACTIVOS_MAX_PAGES = 20


def eactivos_card(card) -> dict | None:
    """One div.liquidation-card of eActivos' list as a listing (no bid yet)."""
    lid = card.get("data-liquidation")
    link = card.select_one(".card-title a[href]")
    if not lid or not link:
        return None
    title = link.get_text(" ", strip=True)
    place = card.select_one(".card-subtitle")
    town, _, province = (place.get_text(" ", strip=True) if place else "").partition(",")
    value = card.select_one(".liquidation-price")
    start = card.select_one(".current-bid-amount")
    end = card.select_one("[data-end-date]")
    img = card.select_one("img.img-main[src]")
    valuation = parse_price(value.get_text()) if value else None
    return make_listing(
        "eactivos", lid, "ES", title=title[:200],
        description=f"{title} · valoración {valuation:,.0f} € · subasta concursal o judicial (eActivos)"
        if valuation else title,
        tipo="inmueble", price=valuation,
        min_price=float(start["data-initial-price"]) if start and start.get("data-initial-price") else None,
        concelho=town.strip() or None, district=province.strip() or None,
        date_end=end["data-end-date"].replace(" ", "T") if end else None,
        url=link["href"], image_url=img["src"] if img else None,
        raw_json=json.dumps({"valuation": valuation}),
    )


@register("eactivos", "ES", description="eActivos — insolvency (concurso) and court home auctions, all Spain")
def scrape_eactivos(db, max_price: float = 50000, **_):
    session = make_session(timeout=30)
    total, seen = 0, set()
    for page in range(1, EACTIVOS_MAX_PAGES + 1):
        resp = session.get(f"{EACTIVOS}/listado-de-liquidaciones/obtener",
                           params={"page": page, "asset_type[]": EACTIVOS_HOMES})
        resp.raise_for_status()
        cards = BeautifulSoup(resp.text, "html.parser").select("div.liquidation-card[data-liquidation]")
        if not cards:
            break
        for card in cards:
            row = eactivos_card(card)
            if not row or row["id"] in seen:
                continue
            seen.add(row["id"])
            try:
                bid = session.get(f"{EACTIVOS}/liquidacion/{card['data-liquidation']}/mejor-puja",
                                  timeout=15).json().get("amount")
            except Exception:  # noqa: BLE001 — the bid is a detail, the card is the listing
                bid = None
            row["current_bid"] = float(bid) if bid else None
            # A valuation far over the budget can still sell low, but not from €0
            # to under the budget: keep what a bid or a valuation puts within reach.
            if (row["current_bid"] or 0) > max_price or (not row["current_bid"] and (row["price"] or 0) > 4 * max_price):
                continue
            row.update(_keep_eactivos_text(db, session, row))
            upsert_listing(db, row)
            total += 1
        db.commit()
        time.sleep(0.5)
    return total


def _keep_eactivos_text(db, session, row: dict) -> dict:
    """The detail page's description, read once per listing."""
    old = db.execute("SELECT description FROM listings WHERE id = ?", (row["id"],)).fetchone()
    if old and old[0] and "Descripción:" in old[0]:
        return {"description": old[0], "area_m2": find_area(old[0])}
    try:
        resp = session.get(row["url"], timeout=30)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 — try again next scan
        return {}
    el = BeautifulSoup(resp.text, "html.parser").select_one("#tab-description")
    text = el.get_text(" ", strip=True).replace("Descripción ", "", 1) if el else ""
    if not text:
        return {}
    full = f"{row['description']} · Descripción: {text[:2500]}"
    return {"description": full, "area_m2": find_area(text)}
# ─── Patrimonio del Estado (the state's own property) ──────────────
# The Treasury's provincial offices sell what the state owns or inherits
# without heirs (abintestato), by sealed-bid auction. The search lists the
# open auctions office by office (homes, buildings, shops and land); the lots
# are only in the BOE announcement,
# a PDF written differently by each office. A round with no bid goes on to
# the next, cheaper one at the same sitting, so the last price is the floor.
HACIENDA = "https://www.hacienda.gob.es"
HACIENDA_LIST = (HACIENDA + "/es-ES/Areas%20Tematicas/Patrimonio%20del%20Estado/Gestion%20Patrimonial%20del%20Estado"
                 "/Paginas/Subastas/ListadoSubastasConcursos.aspx")
HACIENDA_OFFICES = range(1, 57)
_HAC_EVENT = re.compile(r'href="(https://www\.hacienda\.gob\.es/[^"]*/paginas/subastas/deh-[^"]+\.aspx)"', re.I)
_HAC_LOT = re.compile(r"\blote\s*(?:n[úu]m\.?|n[º°o.]*)?\s*(\d+)\s*(?:[.:–-]+|\s+(?=descripci|finca|en primera|convocatoria))",
                      re.I)
_HAC_PAGE_HEAD = re.compile(r"BOLETÍN OFICIAL DEL ESTADO.{0,240}?/arde", re.S)
_HAC_ROUND = re.compile(r"\b(1|2|3|4|primera|segunda|tercera|cuarta)\s*[ªa]?\s*subasta\b[^€\d]{0,60}?"
                        r"([\d.]+(?:,\d{1,2})?)\s*(?:€|euros)", re.I)
_HAC_ORDINAL = {"primera": "1", "segunda": "2", "tercera": "3", "cuarta": "4"}
# Madrid writes the rounds as a table: "Tipo de licitación 99.000,00 84.150,00 71.257,50 60.798,38".
_HAC_TABLE = re.compile(r"Tipo de licitaci[óo]n((?:\s+[\d.]+,\d{2}(?:\s*€)?){2,4})", re.I)
_HAC_POSTCODE = re.compile(r"\(\d{5}\s+([A-ZÁÉÍÓÚÑ][^)]{1,40})\)")
_HAC_TOWN = re.compile(r"(?:t[ée]rmino municipal de|municipio de|sit[oa]s? en|^\W*(?:inmueble|casa|vivienda) en)\s+([A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü' -]{1,40}?)"
                       r"\s*(?:\(|,|\.|;| con | calle| c/| avda)", re.I)


_HAC_TYPE = re.compile(r"(?:tipo|precio)(?: m[ií]nimo)? de (?:licitaci[óo]n|salida)[^\d€]{0,60}?([\d.]+(?:,\d{1,2})?)\s*(?:€|euros)",
                       re.I)


def hacienda_lots(text: str, single: bool = False) -> list[dict]:
    """The property lots of one BOE announcement: {"lot", "text", "town", "area", "price", "floor"}.

    Homes, plots, shops and whole buildings are all kept: the scorer tells them apart.
    With `single`, an announcement of one property with no "Lote" in it is that lot."""
    text = re.sub(r"\s+", " ", _HAC_PAGE_HEAD.sub(" ", text))
    parts = _HAC_LOT.split(text)
    if single and len(parts) < 3:
        parts = ["", "1", text]
    out = []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1][:10000]
        head = body[:400]
        found = {}
        for n, amount in _HAC_ROUND.findall(body):
            found.setdefault(_HAC_ORDINAL.get(n.lower(), n), parse_price(amount))
        rounds = [found[n] for n in sorted(found) if found[n]]
        table = _HAC_TABLE.search(body)
        if not rounds and table:
            rounds = [p for p in (parse_price(x) for x in table.group(1).replace("€", " ").split()) if p]
        if not rounds and _HAC_TYPE.search(body):
            rounds = [parse_price(_HAC_TYPE.search(body).group(1))]
        if len(parts) == 3 and single:
            head = body[:2500]                     # one property: its description follows the preamble
        town = _HAC_POSTCODE.search(head) or _HAC_TOWN.search(head)
        out.append({"lot": parts[i], "text": body[:1500].strip(), "town": town.group(1).strip().title() if town else None,
                    "area": find_area(head), "price": rounds[0] if rounds else None,
                    "floor": rounds[-1] if rounds else None})
    return out


def _hacienda_events(session) -> set[str]:
    found = set()
    for office in HACIENDA_OFFICES:
        resp = session.get(HACIENDA_LIST, params={"den": "", "nat": "Inmuebles urbanos;Inmuebles rústicos;", "dels": f"{office};"})
        resp.raise_for_status()
        found.update(_HAC_EVENT.findall(resp.text))
    return found


@register("hacienda", "ES", description="Patrimonio del Estado — the state's own and heirless homes, by sealed bid")
def scrape_hacienda(db, max_price: float = 50000, **_):
    import pymupdf
    session = make_session(timeout=60)
    total = 0
    for event in sorted(_hacienda_events(session)):
        try:
            page = BeautifulSoup(session.get(event).text, "html.parser")
        except Exception as e:  # noqa: BLE001 — one office down is not the source down
            LOG.info(f"hacienda {event}: {type(e).__name__}")
            continue
        words = re.sub(r"\s+", " ", page.get_text(" "))
        deadline = re.search(r"Fecha límite presentación solicitudes\s*(\d{2}/\d{2}/\d{4})", words)
        pdfs = [a["href"] for a in page.select("a[href]") if ".pdf" in a["href"].lower()]
        boe = [h for h in pdfs if re.search(r"(?i)boe|anuncio|publicaci", h) and "cambio" not in h.lower()] or pdfs
        if not boe:
            continue
        url = safe_url(boe[0], HACIENDA)
        try:
            with pymupdf.open(stream=session.get(url).content, filetype="pdf") as doc:
                text = "\n".join(p.get_text() for p in doc)
        except Exception as e:  # noqa: BLE001
            LOG.info(f"hacienda {url}: {type(e).__name__}")
            continue
        office = event.split("/subastas/")[1].split("/")[0].removeprefix("deh-").replace("-", " ").title()
        for lot in hacienda_lots(text):
            if lot["floor"] and lot["floor"] > max_price * 2:
                continue
            upsert_listing(db, make_listing(
                "hacienda", f"{stable_id(event)}-{lot['lot']}", "ES",
                title=f"Lote {lot['lot']} — {lot['text'][:150]}",
                description=f"{lot['text']} · Subasta del Estado (Delegación de Hacienda de {office}), sobre cerrado",
                tipo="inmueble", area_m2=lot["area"], price=lot["price"], min_price=lot["floor"],
                concelho=lot["town"], district=office,
                date_end=parse_date_dmy(deadline.group(1)) if deadline else None, url=event,
            ))
            total += 1
        db.commit()
    return total


# ─── BOE V-B (every public body's property sales) ──────────────────
# Public bodies announce their property auctions in section V-B of the BOE:
# Social Security's own buildings, FOGASA, the drugs plan's seized property,
# port authorities, ADIF, Defence, ministries. The open-data summary of each
# day lists them; the announcement's text is read with hacienda_lots. The
# Treasury's own offices are read by scrape_hacienda, so they are left out here.
BOE_SUMMARY = "https://www.boe.es/datosabiertos/api/boe/sumario/{:%Y%m%d}"
BOE_DAYS = 45                      # an announcement is open for 20-40 days
_BOE_SALE = re.compile(r"\b(subasta|enajenaci[oó]n|venta)\b", re.I)
_BOE_PROPERTY = re.compile(r"inmueble|vivienda|finca|solar|parcela|local|edificio|terreno|bienes patrimoniales|faro",
                           re.I)
_BOE_SKIP = re.compile(r"suspensi|tanteo|anula|correcci[oó]n de errores|resultado|desierta|bienes muebles|veh[ií]culo|"
                       r"aprovechamiento|Econom[ií]a y Hacienda", re.I)


def _listed(x) -> list:
    return x if isinstance(x, list) else [x] if isinstance(x, dict) else []


def boe_sale_notices(summary: dict) -> list[dict]:
    """The V-B items of one day's BOE summary that announce a property sale."""
    out = []
    for day in _listed(summary.get("data", {}).get("sumario", {}).get("diario")):
        for section in _listed(day.get("seccion")):
            if section.get("codigo") != "5B":
                continue
            for dept in _listed(section.get("departamento")):
                items = _listed(dept.get("item")) + [i for ep in _listed(dept.get("epigrafe")) for i in _listed(ep.get("item"))]
                for item in items:
                    title = item.get("titulo") or ""
                    if _BOE_SALE.search(title) and _BOE_PROPERTY.search(title) and not _BOE_SKIP.search(title):
                        out.append({"id": item.get("identificador"), "title": title, "body": dept.get("nombre"),
                                    "url": item.get("url_html")})
    return out


@register("boe_public", "ES", description="BOE V-B — property sales of Spanish public bodies (Social Security, ports, FOGASA…)")
def scrape_boe_public(db, max_price: float = 50000, **_):
    session = make_session(timeout=40)
    today = datetime.now().date()
    from datetime import timedelta
    total = 0
    for back in range(BOE_DAYS):
        day = today - timedelta(days=back)
        try:
            resp = session.get(BOE_SUMMARY.format(day), headers={"Accept": "application/json"})
            if resp.status_code != 200:          # no BOE on Sundays
                continue
            notices = boe_sale_notices(resp.json())
        except Exception as e:  # noqa: BLE001
            LOG.info(f"boe_public {day}: {type(e).__name__}")
            continue
        for notice in notices:
            if db.execute("SELECT 1 FROM listings WHERE id LIKE ? LIMIT 1", (f"boe_public:{notice['id']}-%",)).fetchone():
                continue                         # an announcement does not change once published
            try:
                page = BeautifulSoup(session.get(notice["url"]).text, "html.parser")
            except Exception as e:  # noqa: BLE001
                LOG.info(f"boe_public {notice['id']}: {type(e).__name__}")
                continue
            body = page.select_one("#textoxslt") or page
            for lot in hacienda_lots(body.get_text(" "), single=True):
                if lot["floor"] and lot["floor"] > 2 * max_price:
                    continue
                upsert_listing(db, make_listing(
                    "boe_public", f"{notice['id']}-{lot['lot']}", "ES",
                    title=re.sub(r"^\W*(?:Urbana|R[úu]stica)?\W*", "", lot["text"])[:200],
                    description=f"{lot['text']} · {notice['body']} (BOE {notice['id']}, {day:%d/%m/%Y})",
                    tipo="inmueble", area_m2=lot["area"], price=lot["price"], min_price=lot["floor"],
                    concelho=lot["town"], url=notice["url"],
                    # The deadline is written a dozen ways; offers close within about 40 days.
                    date_end=f"{day + timedelta(days=40):%Y-%m-%d}T00:00:00",
                    raw_json={"published": f"{day:%Y-%m-%d}", "seller": notice["body"], "date_end": "estimated"},
                ))
                total += 1
            db.commit()
            time.sleep(0.3)
    return total


# ─── AVRA (Andalucía's public housing and land agency) ─────────────
# AVRA's catalogue is one JSON file of every property it holds in
# Andalucía; the ones on sale ("oferta": "S") carry a price and the way they
# are sold (subasta or concurso). Homes, garages, shops, building plots and
# rural land alike: the scorer tells them apart.
AVRA = "https://www.juntadeandalucia.es/avra"
AVRA_JSON = AVRA + "/opencms/catalogo-inmuebles/recursos-propios/inmuebles.json"
_AVRA_POINT = re.compile(r"POINT\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")


def avra_listing(item: dict) -> dict | None:
    """One AVRA catalogue entry as a listing, or None when it is not on sale."""
    if item.get("oferta") != "S" or not item.get("referencia_inmueble"):
        return None
    kind = (item.get("tipo_inmueble") or "").title()
    town = (item.get("municipio") or "").title() or None
    built = to_number(item.get("mconstruidos"))
    land = to_number(item.get("superficie"))
    raw = {"type": item.get("id_tipo_inmueble"), "cadastre": item.get("ref_catastral"),
           "sale": item.get("tipo_oferta"), "plot_m2": land, "buildable": item.get("edificabilidad")}
    point = _AVRA_POINT.search(item.get("centroide") or "")
    if point:
        raw.update(lon=float(point.group(1)), lat=float(point.group(2)))
    notes = " ".join(str(item.get(k) or "") for k in ("observaciones", "observaciones_oferta"))
    return make_listing(
        "avra", item["referencia_inmueble"], "ES",
        title=f"{kind} en {town} — {item.get('nombre') or ''}".strip(" —")[:200],
        description=f"{kind} en {town} ({(item.get('provincia') or '').title()}). {notes[:2500]} · "
                    f"{(item.get('tipo_oferta') or 'venta').lower()} de AVRA (Junta de Andalucía)",
        tipo=kind or None, area_m2=built or land, price=to_number(item.get("precio")), min_price=to_number(item.get("precio")),
        concelho=town, district=(item.get("provincia") or "").title() or None,
        url=f"{AVRA}/catalogo-inmuebles/#/inmueble/{item['referencia_inmueble']}", raw_json=raw,
    )


@register("avra", "ES", description="AVRA — Andalucía's public homes, garages, plots and land for sale")
def scrape_avra(db, max_price: float = 50000, **_):
    resp = make_session(timeout=90).get(AVRA_JSON, params={"_start": 0, "_limit": 10000})
    resp.raise_for_status()
    total = 0
    for item in resp.json():
        row = avra_listing(item)
        if row and (row["price"] or 0) <= 2 * max_price:
            upsert_listing(db, row)
            total += 1
    db.commit()
    return total


# ─── Aliseda (Santander's repossessions) ────────────────────────────
# The site's own search API: price, the map position, the full text and who
# holds it, 12 a page. Homes (tipo 10) and land (tipo 8) within budget, all Spain.
ALISEDA_API = "https://laravel.alisedainmobiliaria.com/api/v2/new-search"
ALISEDA_SITE = "https://www.alisedainmobiliaria.com"
ALISEDA_TYPES = {10: "vivienda", 8: "terreno"}
ALISEDA_MAX_PAGES = 80


def bank_share_sentence(card: dict) -> str | None:
    """A co-ownership flag the search JSON carries beside the description.

    Aliseda's is Proindiviso. Altamira and Solvia use the same idea under other
    names; a price-off percentage is not one of them."""
    if not isinstance(card, dict):
        return None
    for key, value in card.items():
        if not isinstance(key, str):
            continue
        nk = normalize(key).replace("_", "").replace(" ", "")
        if "proindiviso" not in nk and nk not in ("copropiedad", "escopropiedad"):
            continue
        if value in (None, False, 0, "0", "false", "False", "N", "NO", "no", ""):
            continue
        return "Transmisión de una participación indivisa (copropiedad)."
    for key in ("porcentajeTitularidad", "participacionIndivisa", "participacion"):
        raw = card.get(key)
        try:
            pct = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if 0 < pct < 100:
            return f"Participación del {pct:g}% (copropiedad), no el pleno dominio."
    return None


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
    description = item.get("Description") or ""
    # The search text says "terreno" for every plot. The use (rústico, residencial)
    # is a separate field, and a plot under 5,000 m² with no rural word was scored
    # as building land. Pumariño, 2026-10-09: SubUso "RÚstico", €5,460 for 3,962 m².
    use = str(((item.get("suelonave") or {}).get("SubUso") or "")).strip()
    if tipo == "terreno" and use and has_term(use, ["rustico", "rustica", "agricola", "forestal"]):
        raw_use = "Terreno rústico."
        if raw_use not in description and not has_term(description, ["rustico", "rustica"]):
            description = f"{raw_use} {description}".strip()
    if tipo == "terreno":
        # The feed's total is often the whole sector (Calella 135 ha; the ad says 0.8–2.8).
        stated, feed = prefer_stated_area(area, description)
        if feed:
            raw_area_note = feed
            area = stated
        else:
            raw_area_note = None
    else:
        raw_area_note = None
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
    if raw_area_note:
        raw["area_feed_m2"] = raw_area_note
    coprop_note = bank_share_sentence(item)
    if item.get("Proindiviso") or coprop_note:
        # Only a share of the property, shown on the page in a box the API's text lacks.
        raw["proindiviso"] = True
        prefix = coprop_note or "Transmisión de una participación indivisa (copropiedad)."
        if prefix.rstrip(".") not in description:
            description = prefix + " " + description
    return make_listing(
        "aliseda", item["id"], "ES", title=title[:200],
        description=description[:3000] or None, tipo=tipo,
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
# The results card is only the address: the state, "Ocupado" and the special
# conditions ("ocupado ilegalmente por terceros") are on the listing page, read
# once per home, a few dozen a scan.
ALTAMIRA_DETAILS_PER_SCAN = 150


def altamira_detail(html: str) -> dict:
    """The full text, the special conditions and the "Ocupado" mark of a listing page."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: dict = {}
    body = soup.select_one("section.descripcion #capaDescripcion") or soup.select_one("section.descripcion")
    if body:
        text = re.sub(r"\s+", " ", body.get_text(" ", strip=True))
        if text:
            out["description"] = text
    special = soup.select_one(".condicionespeciales #modal_contenido_2")
    if special:
        text = re.sub(r"^Condiciones Especiales\s*", "", re.sub(r"\s+", " ", special.get_text(" ", strip=True)),
                      flags=re.I)
        if text:
            out["conditions"] = text
    if soup.select_one(".marcas_agua .marca.ocupado"):
        out["occupied"] = True
    return out


def with_altamira_detail(row: dict, detail: dict) -> dict:
    """The card's row with what the listing page adds."""
    raw = json.loads(row.get("raw_json") or "{}")
    raw["detail_checked"] = 1
    if detail.get("occupied"):
        raw["occupation"] = "occupied"
    parts = (detail.get("description"),
             f"Condiciones especiales: {detail['conditions']}" if detail.get("conditions") else None,
             row.get("description"))
    return {**row, "description": " · ".join(p for p in parts if p)[:3000],
            "raw_json": json.dumps(raw, ensure_ascii=False)}


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
    share = bank_share_sentence(card)
    return make_listing(
        "altamira", card["referencia"], "ES", title=f"{kind} en {town}" + (f", {street}" if street else ""),
        description=" · ".join(str(x) for x in (share, kind, street, card.get("cp"), town, card.get("provinciaurl"),
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
    read = {r[0] for r in db.execute(
        """SELECT external_id FROM listings WHERE source = 'altamira' AND raw_json LIKE '%"detail_checked"%'""")}
    budget = ALTAMIRA_DETAILS_PER_SCAN
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
                if not row or row["price"] > limit:
                    continue
                if row["external_id"] in read:
                    row["description"] = None          # keep the listing page's text and marks
                    row["raw_json"] = None
                elif tipo == "vivienda" and budget > 0:
                    budget -= 1
                    try:
                        got = session.get(row["url"])
                        detail = altamira_detail(got.text) if got.status_code < 400 else {}
                    except Exception:  # noqa: BLE001 — the card alone is still worth keeping
                        detail = {}
                    if detail.get("description"):
                        row = with_altamira_detail(row, detail)
                        read.add(row["external_id"])
                    time.sleep(0.4)
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
FOTOCASA_DETAILS_PER_SCAN = 300
# Fotocasa refuses a scan partway (every search from Lleida on failed within
# seconds, 2026-10-29; only 1-2k of 13k listings read since 10-24). On a refusal
# it waits this long and asks again once; still refused, the scan stops there
# and the rest is read next time, instead of failing every remaining province.
FOTOCASA_BLOCK_WAIT_S = 120
FOTOCASA_REFUSED = (403, 429)


def fotocasa_detail(html: str) -> dict:
    """The full text, the portal's own state and the photos of a listing page.

    The search card keeps a short description. The listing page is the same
    JSON the card uses, under the page's own props."""
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return {}
    try:
        page = json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError, TypeError):
        return {}
    ad = page.get("realEstate") or page.get("realEstateAd") or {}
    if not isinstance(ad, dict):
        return {}
    out: dict = {}
    text = ad.get("description") or ""
    if isinstance(text, dict):
        text = text.get("es-ES") or next((v for v in text.values() if isinstance(v, str)), "")
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if text:
        out["description"] = text
    feats = {f.get("key"): f.get("value") for f in ad.get("features") or [] if isinstance(f, dict)}
    if isinstance(feats.get("conservationStatus"), int):
        out["conservation"] = feats["conservationStatus"]
    images = [img.get("src") for img in ad.get("multimedia") or []
              if isinstance(img, dict) and img.get("src")]
    if images:
        out["fotos"] = images
    return out


def with_fotocasa_detail(row: dict, detail: dict) -> dict:
    raw = json.loads(row.get("raw_json") or "{}")
    if detail.get("conservation") is not None and "conservation" not in raw:
        raw["conservation"] = detail["conservation"]
    if detail.get("fotos"):
        raw["fotos"] = detail["fotos"]
    raw["detail_checked"] = 1
    description = row.get("description") or ""
    if len(detail.get("description") or "") > len(description):
        description = detail["description"][:3000]
    image = row.get("image_url") or (detail["fotos"][0] if detail.get("fotos") else None)
    return {**row, "description": description or None, "image_url": image,
            "raw_json": json.dumps(raw, ensure_ascii=False)}


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
    # The card's own state ("A reformar" = 4) and rooms: "0 rooms, 0 bathrooms" is land.
    if isinstance(feats.get("conservationStatus"), int):
        raw["conservation"] = feats["conservationStatus"]
    for key in ("rooms", "bathrooms"):
        if isinstance(feats.get(key), int):
            raw[key] = feats[key]
    coords = ad.get("coordinates") or {}
    if coords.get("latitude") and coords.get("longitude"):
        raw["geo"] = {"lat": float(coords["latitude"]), "lon": float(coords["longitude"]),
                      "precision": "street" if ad.get("accuracy") else "village"}
    detail = (ad.get("detail") or {}).get("es-ES")
    images = [m.get("src") for m in ad.get("multimedia") or [] if m.get("src")]
    description = ad.get("description") or ""
    if ad.get("isBareOwnership"):
        # The page flags bare ownership without putting it in the text, so a
        # keyword filter never sees it. Same idea as a proindiviso share.
        raw["nuda_propiedad"] = True
        description = f"{description} nuda propiedad".strip()
    kind = "Terreno" if tipo == "terreno" else "Casa" if "House" in (ad.get("buildingSubtype") or "") else "Vivienda"
    location = ad.get("location") or ""
    return make_listing(
        "fotocasa", ad["id"], "ES", title=f"{kind} en {town or addr.get('province') or ''}"
                                          + (f", {location}" if location else ""),
        description=description[:3000] or None, tipo=tipo,
        area_m2=feats.get("surface") or None, price=float(price), min_price=float(price),
        district=addr.get("province"), concelho=town, url=f"{FOTOCASA}{detail}" if detail else None,
        image_url=images[0] if images else None, raw_json=json.dumps(raw, ensure_ascii=False) if raw else None,
    )


@register("fotocasa", "ES")
def scrape_fotocasa(db, max_price: float = 50000, config: dict | None = None, **_):
    """fotocasa — private homes and land (1 ha+) all over Spain."""
    session = make_session(timeout=30)
    total = 0
    read = {r[0] for r in db.execute(
        """SELECT external_id FROM listings WHERE source = 'fotocasa'
           AND raw_json LIKE '%"detail_checked"%'""")}
    budget = FOTOCASA_DETAILS_PER_SCAN
    failed = 0
    refused = False

    def get_page(path, params):
        resp = session.get(path, params=params)
        if resp.status_code in FOTOCASA_REFUSED:
            LOG.info(f"Fotocasa refused ({resp.status_code}); waiting {FOTOCASA_BLOCK_WAIT_S}s")
            time.sleep(FOTOCASA_BLOCK_WAIT_S)
            resp = session.get(path, params=params)
        return resp

    for province in FOTOCASA_PROVINCES:
        if refused:
            break
        for kind, min_surface in FOTOCASA_SEARCHES:
            if refused:
                break
            limit = land_max_price(config, max_price) if kind == "terrenos" else max_price
            params = {"maxPrice": int(limit)}
            if min_surface:
                params["minSurface"] = min_surface
            seen = 0
            for page in range(1, FOTOCASA_MAX_PAGES + 1):
                path = f"{FOTOCASA}/es/comprar/{kind}/{province}-provincia/todas-las-zonas/l" + \
                       (f"/{page}" if page > 1 else "")
                resp = None
                try:
                    resp = get_page(path, params)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001 — one province failing is not the source failing
                    if total == 0 and province == FOTOCASA_PROVINCES[0]:
                        raise
                    LOG.info(f"Fotocasa {province} {kind} p{page}: {type(e).__name__}")
                    failed += 1
                    refused = getattr(resp, "status_code", None) in FOTOCASA_REFUSED
                    break
                ads, count = fotocasa_page(resp.text)
                for ad in ads:
                    row = parse_fotocasa(ad, "terreno" if kind == "terrenos" else "vivienda")
                    if row and row["price"] <= limit:
                        if row["external_id"] not in read and budget > 0 and row.get("url"):
                            budget -= 1
                            try:
                                got = session.get(row["url"])
                                detail = fotocasa_detail(got.text) if got.status_code < 400 else {}
                            except Exception:  # noqa: BLE001 — the card is still worth keeping
                                detail = {}
                            if detail:
                                row = with_fotocasa_detail(row, detail)
                                read.add(row["external_id"])
                            time.sleep(0.4)
                        upsert_listing(db, row)
                        total += 1
                db.commit()
                seen += len(ads)
                if not ads or seen >= count:
                    break
                time.sleep(0.8)
    LOG.info(f"Fotocasa: {total} listings" + (f", {failed} searches failed" if failed else "")
             + ("; stopped: Fotocasa kept refusing" if refused else ""))
    # A search that failed (Fotocasa cut the scan off halfway, 2026-10-29) left its
    # listings unseen: partial, so they are not taken for gone.
    return ScanCount(total, "partial") if failed else total


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


_TGSS_HOME = re.compile(r"\s*(?:\d+(?:[.,]\d+)?\s*%\s*(?:pleno dominio\s*)?(?:de\s*)?)?"
                        r"(?:vivienda|casa|piso|chalet|apartamento|d[uú]plex|[aá]tico|adosado)\b", re.I)


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
        # A "finca urbana" is as often a garage, a shop or a plot as a home: only
        # a description that opens with a home says "vivienda".
        description=description, tipo="terreno" if rural else "vivienda" if _TGSS_HOME.match(description)
        else "inmueble",
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
    images = [img.get("src") for img in soup.select(".detail-gallery img[src], .gallery img[src]") if img.get("src")]
    if images:
        out["fotos"] = images
    if out:
        out["detail_checked"] = 1
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
    raw = {}
    if detail.get("geo"):
        raw["geo"] = detail["geo"]
    if detail.get("fotos"):
        raw["fotos"] = detail["fotos"]
    if detail.get("detail_checked"):
        raw["detail_checked"] = 1
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
        """SELECT external_id FROM listings WHERE source = 'pisos'
           AND raw_json LIKE '%"detail_checked"%'""")}
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
                    if detail and detail.get("detail_checked"):
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
    notes = [bank_share_sentence(item), item.get("tituloFicha"), sub,
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


@register("habitaclia", "ES",
          description="Habitaclia — homes for sale in Lugo, Ourense and León")
def scrape_habitaclia(db, max_price: float = 50000, **_):
    """Habitaclia — homes for sale in Lugo, Ourense and León."""
    from sources._market import scrape_named
    return scrape_named(db, "habitaclia", max_price)


# ─── PLACSP: property sales by every Spanish public body ──────────────
# The State's procurement platform syndicates every tender of every public
# body (town halls, regions, the Social Security, public companies), its own
# and those of the regional platforms. A property sale is a "patrimonial"
# contract (type 50) with a real-estate CPV (70…) or a sale title; only the
# ones still open for offers (PUB) are kept. The feeds are large (a page of
# the State's is ~7 MB, a few hundred tenders), so each scan reads back only
# to where the last one stopped, kept in kv; the first one goes back a few days.
PLACSP_FEEDS = (
    ("643", "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom"),
    ("1044", "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/PlataformasAgregadasSinMenores.atom"),
)
PLACSP_FIRST_DAYS = 4
PLACSP_MAX_PAGES = 80
_PLACSP_SALE = re.compile(r"enajenaci[oó]n|venta|subasta|alienaci[oó]|besterentze|alleanament|transmisi[oó]n onerosa", re.I)
_PLACSP_NOT = re.compile(r"aprovechamiento|madera|pastos|\bcaza\b|corcho|piñ[ao]|leñas?|chatarra|vehículos?|veh[ií]culo|"
                         r"arrendamiento|alquiler|concesi[oó]n|explotaci[oó]n|bar\b|cafeter|kiosco|quiosco|servicio|"
                         r"batidas?|monter[ií]as?|cacer[ií]as?|coto|reses|ganado|caballos?|animales", re.I)
_PLACSP_BODY = re.compile(r"\b(?:Junta|Consejer[ií]a|Ministerio|Diputaci[oó]n|Gobierno|Generalitat|Xunta|Comunidad|"
                          r"Direcci[oó]n|Tesorer[ií]a|Instituto|Agencia|Sociedad|Entidad)\b", re.I)


def _placsp_get(entry: str, pattern: str) -> str | None:
    m = re.search(pattern, entry, re.S)
    return re.sub(r"\s+", " ", unescape(m[1])).strip() if m else None


def placsp_listing(entry: str, today: str | None = None) -> dict | None:
    """One feed entry as a listing, or None unless it is an open property sale."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    title = _placsp_get(entry, r"<title>(.*?)</title>") or ""
    kind = _placsp_get(entry, r"<cac:ProcurementProject>.*?<cbc:TypeCode[^>]*>(\d+)<")
    cpv = _placsp_get(entry, r"<cbc:ItemClassificationCode[^>]*>(\d+)<") or ""
    status = _placsp_get(entry, r"ContractFolderStatusCode[^>]*>(\w+)<")
    if status != "PUB" or kind != "50" or _PLACSP_NOT.search(title):
        return None
    if not (cpv.startswith("70") or _PLACSP_SALE.search(title)):
        return None
    deadline = _placsp_get(entry, r"<cac:TenderSubmissionDeadlinePeriod>.*?<cbc:EndDate>([\d-]+)<")
    if deadline and deadline < today:
        return None
    price = (_placsp_get(entry, r"<cbc:TaxExclusiveAmount[^>]*>([\d.]+)<")
             or _placsp_get(entry, r"<cbc:EstimatedOverallContractAmount[^>]*>([\d.]+)<"))
    body = _placsp_get(entry, r"<cac:LocatedContractingParty>.*?<cac:PartyName>\s*<cbc:Name>(.*?)<")
    town = (_placsp_get(entry, r"<cac:RealizedLocation>.*?<cbc:CityName>(.*?)<")
            or _placsp_get(entry, r"<cac-place-ext:ParentLocatedParty>\s*<cac:PartyName>\s*<cbc:Name>(.*?)<")
            or _placsp_get(entry, r"<cac:PostalAddress>\s*<cbc:CityName>(.*?)<"))
    if town and _PLACSP_BODY.search(town):          # the seller's name, not a place
        town = None
    town = town.title() if town and town.isupper() else town
    province = _placsp_get(entry, r"<cbc:CountrySubentity>(.*?)<")
    link = _placsp_get(entry, r'<link href="([^"]+)"')
    ident = _placsp_get(entry, r"<id>(.*?)</id>") or link
    try:
        price_eur = float(price) if price else None
    except ValueError:
        price_eur = None
    return make_listing(
        "placsp", ident.rsplit("/", 1)[-1], "ES", title=title[:200],
        description=" · ".join(x for x in (title, body, f"{town or ''}, {province or ''}".strip(", "),
                                           f"plazo de ofertas hasta {deadline}" if deadline else "",
                                           "enajenación patrimonial de un organismo público (PLACSP); pliego en el enlace") if x),
        area_m2=find_area(title), price=price_eur or None, concelho=town, district=province,
        date_end=deadline, url=link, raw_json={"seller": body, "cpv": cpv},
    )


def _placsp_feed(db, session, key: str, url: str, today: str, max_price: float) -> int:
    """Read one feed back to its saved cursor; returns the sales kept."""
    from datetime import timedelta
    from db import get_kv, set_kv
    stop = get_kv(db, f"placsp_cursor_{key}") or (datetime.now() - timedelta(days=PLACSP_FIRST_DAYS)).strftime("%Y-%m-%dT%H:%M")
    newest, total = None, 0
    for _ in range(PLACSP_MAX_PAGES):
        resp = session.get(url)
        resp.raise_for_status()
        text = resp.text
        entries = re.findall(r"<entry>.*?</entry>", text, re.S)
        reached = False
        for entry in entries:
            updated = (_placsp_get(entry, r"<updated>(.*?)</updated>") or "")[:16]
            newest = max(newest or updated, updated)
            if updated and updated < stop:
                reached = True
                continue
            listing = placsp_listing(entry, today)
            if listing and (listing["price"] or 0) <= 2 * max_price:
                upsert_listing(db, listing)
                total += 1
        db.commit()
        nxt = re.search(r'<link href="([^"]+)" rel="next"', text)
        if reached or not nxt or not entries:
            break
        url = unescape(nxt[1])
    if newest:
        set_kv(db, f"placsp_cursor_{key}", newest)
        db.commit()
    return total


@register("placsp", "ES", description="PLACSP — land and buildings sold by Spanish town halls, regions and state bodies")
def scrape_placsp(db, max_price: float = 50000, **_):
    session = make_session(timeout=180)
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0
    for key, url in PLACSP_FEEDS:
        total += _placsp_feed(db, session, key, url, today, max_price)
    return total
