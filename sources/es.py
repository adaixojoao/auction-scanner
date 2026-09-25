"""Spain: BOE judicial auctions, AEAT tax auctions, bad-bank / servicer portals."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from common import (LOG, find_area, find_price, make_listing, make_session, parse_date_dmy,
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
    return {
        "price": price,
        "min_price": parse_price(_spain_field(fields, "Puja mínima", "Puja minima")),
        "date_end": date_end,
        "concelho": concelho.strip() if concelho else None,
        "district": district.strip() if district else None,
        "title": title,
        "description": desc,
        "area_m2": parse_price(am.group(1)) if am else None,
    }


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


def enrich_spain_details(db, session, limit: int = 100):
    """Fetch the BOE detail tabs for Spanish listings not checked yet (or still
    without a price), soonest first: price and dates for the listing, the
    court's name and e-mail for letters, occupancy for the score."""
    rows = db.execute("""
        SELECT * FROM listings WHERE source='spain'
          AND (price IS NULL OR raw_json IS NULL OR raw_json NOT LIKE '%"detail_checked"%')
        ORDER BY date_end IS NULL, date_end LIMIT ?
    """, (limit,)).fetchall()
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
        upsert_listing(db, make_listing("spain", item["external_id"], "ES", **fields))
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
        town = re.search(r",\s*([^,]+),\s*[^,]+$", title)
        rows.append(make_listing(
            "servihabitat", m.group(1), "ES", title=title, description=text[:500], tipo="vivienda",
            area_m2=find_area(text), price=price, min_price=price, district=province,
            concelho=town.group(1).strip() if town else None, url=href, base_url=SERVIHABITAT,
            image_url=(img.get("data-src") or img.get("src")) if img else None,
        ))
    return rows


@register("servihabitat", "ES")
def scrape_servihabitat(db, max_price: float = 100000, **_):
    """servihabitat.com — CaixaBank repossessions, the cheapest homes of each province."""
    session = make_session(timeout=30)
    total, full = 0, []
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
        for row in rows:
            if row["price"] and row["price"] <= max_price:
                upsert_listing(db, row)
                total += 1
        db.commit()
        time.sleep(0.5)
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
