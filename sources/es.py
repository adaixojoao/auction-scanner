"""Spain: BOE judicial auctions, AEAT tax auctions, bad-bank / servicer portals."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from common import (LOG, find_price, make_listing, make_session, parse_date_dmy,
                    parse_price, safe_url, stable_id, to_number)
from db import upsert_listing
from sources import register
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

    enrich_spain_details(db, session, limit=200)
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


def enrich_spain_details(db, session, limit: int = 200):
    """Fetch BOE detail pages for Spanish listings still missing a price."""
    rows = db.execute("""
        SELECT * FROM listings WHERE source='spain' AND price IS NULL LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        return 0

    LOG.info(f"  Spain details: fetching {len(rows)} listings missing price")
    filled = 0
    for row in rows:
        item = dict(row)
        url = item.get("url") or f"https://subastas.boe.es/detalleSubasta.php?idSub={item['external_id']}"
        try:
            resp = session.get(url)
            resp.raise_for_status()
        except Exception as e:
            LOG.debug(f"  Spain detail {item['external_id']} error: {e}")
            time.sleep(0.5)
            continue

        parsed = {k: v for k, v in _spain_parse_detail(resp.text).items() if v is not None}
        fields = {k: item.get(k) for k in (
            "title", "description", "tipo", "area_m2", "price", "current_bid", "min_price",
            "district", "concelho", "freguesia", "url", "image_url", "date_end", "raw_json")}
        fields.update(parsed)
        upsert_listing(db, make_listing("spain", item["external_id"], "ES", **fields))
        if parsed.get("price") is not None:
            filled += 1
        db.commit()
        time.sleep(0.6)

    LOG.info(f"  Spain details: filled price for {filled}/{len(rows)}")
    return filled


@register("aeat", "ES")
def scrape_aeat(db, max_price: float = 50000, **_):
    """sede.agenciatributaria.gob.es — Spanish tax-authority auctions."""
    session = make_session()
    base = "https://sede.agenciatributaria.gob.es"
    resp = session.get(f"{base}/Sede/procedimientos/subastas-electronicas.html")
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select("a[href*='subasta'], a[href*='lote']")
    LOG.info(f"  AEAT: {len(links)} auction links found")

    total_scraped = 0
    seen = set()
    for link in links:
        url = safe_url(link.get("href"), base)
        if not url or url in seen:
            continue
        seen.add(url)
        title = link.get_text(strip=True)[:200]
        text = link.parent.get_text(" ", strip=True) if link.parent else title
        price = find_price(text)
        if price and price > max_price:
            continue
        eid = re.sub(r"[^A-Za-z0-9]", "", url[-40:]) or stable_id(url)
        upsert_listing(db, make_listing(
            "aeat", eid, "ES",
            title=title or f"AEAT tax sale {eid}", description=text[:500],
            tipo="inmueble", price=price, min_price=price, url=url,
        ))
        total_scraped += 1
    return total_scraped


@register("sareb", "ES")
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
SERVIHABITAT = CardSite(
    source="servihabitat", country="ES", base="https://www.servihabitat.com", path="/en/buy-houses/",
    card_selector="div.property, article, div[class*='property'], li[class*='property']",
    title_selector="h2,h3,.title",
    location_selector=".location,.city,.municipio",
    params={"price_to": "{max_price}"}, max_pages=29,
    description="Servihabitat (CaixaBank)", tipo="inmueble", price_is_min_price=True,
)


@register("haya", "ES")
def scrape_haya(db, max_price: float = 100000, **_):
    """haya.es — Sareb/BBVA repossessions."""
    return scrape_cards(db, HAYA, max_price)


@register("servihabitat", "ES")
def scrape_servihabitat(db, max_price: float = 100000, **_):
    """servihabitat.com — CaixaBank repossessions."""
    return scrape_cards(db, SERVIHABITAT, max_price)


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
