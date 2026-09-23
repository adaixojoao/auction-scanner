"""
Auction Scanner — scrapes EU auction/real estate platforms.
Stores results in SQLite, generates investment reports.

Supported platforms:
  PT: e-leiloes.pt (REST API), idealista.pt (Selenium)
  HR: e-oglasna.pravosudje.hr (REST API)
  ES: subastas.boe.es (HTML scraping)
  FR: licitor.com (HTML scraping)
  IT: astegiudiziarie.it (HTML scraping)
  NL: openbareverkoop.nl (HTML scraping)

Usage:
  python scraper.py                  # scrape all, generate report
  python scraper.py --source eleiloes
  python scraper.py --source croatia
  python scraper.py --source spain
  python scraper.py --country PT     # scrape all PT sources
  python scraper.py --report-only    # just re-generate report from DB
"""

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timezone

import re

import requests
from bs4 import BeautifulSoup

DB_PATH = os.path.join(os.path.dirname(__file__), "auctions.db")
LOG = logging.getLogger("auction-scanner")

# ─── Database ────────────────────────────────────────────────────────

def init_db(db: sqlite3.Connection):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id          TEXT PRIMARY KEY,  -- source:external_id
            source      TEXT NOT NULL,     -- eleiloes, idealista, croatia, spain, france, italy, netherlands
            country     TEXT NOT NULL DEFAULT 'PT',  -- ISO 3166-1 alpha-2
            external_id TEXT NOT NULL,
            title       TEXT,
            description TEXT,
            tipo        TEXT,              -- apartamento, moradia, terreno, loja, etc.
            area_m2     REAL,
            price       REAL,             -- asking / valor base
            current_bid REAL,
            min_price   REAL,             -- valor minimo (85% of VB for auctions)
            district    TEXT,
            concelho    TEXT,
            freguesia   TEXT,
            url         TEXT,
            image_url   TEXT,
            date_end    TEXT,             -- auction end datetime (ISO)
            raw_json    TEXT,             -- full API response for this listing
            first_seen  TEXT NOT NULL,
            last_seen   TEXT NOT NULL,
            is_new      INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_source ON listings(source);
        CREATE INDEX IF NOT EXISTS idx_price ON listings(price);
        CREATE INDEX IF NOT EXISTS idx_district ON listings(district);
        CREATE INDEX IF NOT EXISTS idx_date_end ON listings(date_end);
        CREATE INDEX IF NOT EXISTS idx_country ON listings(country);

        CREATE TABLE IF NOT EXISTS scrape_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            count     INTEGER,
            status    TEXT,
            message   TEXT
        );
    """)
    db.commit()


def upsert_listing(db: sqlite3.Connection, row: dict):
    now = datetime.now(timezone.utc).isoformat()
    existing = db.execute("SELECT first_seen FROM listings WHERE id = ?", (row["id"],)).fetchone()
    if existing:
        db.execute("""
            UPDATE listings SET
                title=?, description=?, tipo=?, area_m2=?, price=?, current_bid=?,
                min_price=?, district=?, concelho=?, freguesia=?, url=?, image_url=?,
                date_end=?, raw_json=?, last_seen=?, is_new=0
            WHERE id=?
        """, (
            row.get("title"), row.get("description"), row.get("tipo"),
            row.get("area_m2"), row.get("price"), row.get("current_bid"),
            row.get("min_price"), row.get("district"), row.get("concelho"),
            row.get("freguesia"), row.get("url"), row.get("image_url"),
            row.get("date_end"), row.get("raw_json"), now, row["id"]
        ))
    else:
        db.execute("""
            INSERT INTO listings (id, source, country, external_id, title, description, tipo,
                area_m2, price, current_bid, min_price, district, concelho, freguesia,
                url, image_url, date_end, raw_json, first_seen, last_seen, is_new)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (
            row["id"], row["source"], row.get("country", "PT"), row["external_id"],
            row.get("title"), row.get("description"), row.get("tipo"),
            row.get("area_m2"), row.get("price"), row.get("current_bid"),
            row.get("min_price"), row.get("district"), row.get("concelho"),
            row.get("freguesia"), row.get("url"), row.get("image_url"),
            row.get("date_end"), row.get("raw_json"), now, now
        ))


# ─── e-leiloes.pt ───────────────────────────────────────────────────

ELEILOES_API = "https://e-leiloes.pt/api/Eventos/"
ELEILOES_DETAIL_API = "https://e-leiloes.pt/api/Eventos/{id}"

TIPO_MAP = {
    1: "imovel", 2: "veiculo", 3: "direito", 4: "outros"
}
SUBTIPO_MAP = {
    1: "apartamento/moradia", 2: "loja/escritorio", 3: "garagem",
    4: "armazem", 5: "terreno_urbano", 6: "terreno_rustico",
    7: "outro_imovel", 8: "direitos", 9: "comercio",
    10: "hotel", 11: "industrial",
    27: "terreno_rustico", 21: "moradia", 22: "apartamento",
    23: "loja", 24: "garagem", 25: "armazem", 26: "terreno_urbano",
}


def scrape_eleiloes(db: sqlite3.Connection, max_price: float = 50000, page_size: int = 100):
    """Scrape all active listings from e-leiloes.pt."""
    LOG.info("Scraping e-leiloes.pt...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    session.verify = False  # e-leiloes has cert issues

    total_scraped = 0
    offset = 0

    while True:
        params = {
            "first": offset,
            "rows": page_size,
            "sortField": "dataFim",
            "sortOrder": 1,
            "filters": {
                "terminado": {"value": False, "matchMode": "equals"},
            }
        }
        url = ELEILOES_API + "?" + urllib.parse.urlencode({"tableParams": json.dumps(params)})

        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            LOG.error(f"e-leiloes API error at offset {offset}: {e}")
            break

        items = data.get("list", [])
        total = data.get("pagination", {}).get("total", 0)

        if not items:
            break

        for item in items:
            listing = _eleiloes_to_listing(item)
            upsert_listing(db, listing)
            total_scraped += 1

        db.commit()
        offset += page_size
        LOG.info(f"  ... {offset}/{total} processed")

        if offset >= total:
            break
        time.sleep(0.5)

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("eleiloes", datetime.now(timezone.utc).isoformat(), total_scraped, "ok")
    )
    db.commit()
    LOG.info(f"e-leiloes.pt: {total_scraped} listings scraped")
    return total_scraped


def _eleiloes_to_listing(item: dict) -> dict:
    eid = str(item["id"])
    subtipo = SUBTIPO_MAP.get(item.get("subtipoId"), "outro")
    return {
        "id": f"eleiloes:{eid}",
        "source": "eleiloes",
        "external_id": eid,
        "title": item.get("titulo", ""),
        "description": None,
        "tipo": subtipo,
        "area_m2": None,
        "price": item.get("valorBase"),
        "current_bid": item.get("lanceAtual") if item.get("lanceAtual", 0) > 0 else None,
        "min_price": item.get("valorMinimo"),
        "district": item.get("moradaDistrito"),
        "concelho": item.get("moradaConcelho"),
        "freguesia": item.get("moradaFreguesia"),
        "url": f"https://e-leiloes.pt/item/{item.get('referencia', eid)}",
        "image_url": f"https://e-leiloes.pt/api/{item['capa']}" if item.get("capa") else None,
        "date_end": item.get("dataFim"),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


# ─── Idealista (Selenium) ────────────────────────────────────────────

def scrape_idealista(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape idealista.pt using Selenium to bypass bot detection."""
    LOG.info("Scraping idealista.pt via Selenium...")
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        LOG.error("selenium not installed. Run: pip install selenium")
        return 0

    driver = None
    total_scraped = 0

    try:
        # Strategy: try standalone Chrome, then remote debugging to existing Chrome
        got_driver = False
        for attempt in ("standalone", "remote"):
            if got_driver:
                break
            try:
                opts = Options()
                if attempt == "standalone":
                    opts.add_argument("--disable-blink-features=AutomationControlled")
                    opts.add_argument("--no-sandbox")
                    opts.add_argument("--disable-gpu")
                    opts.add_argument("--window-size=1920,1080")
                    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
                    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
                    opts.add_experimental_option("useAutomationExtension", False)
                    driver = webdriver.Chrome(options=opts)
                else:
                    LOG.info("Trying remote debugging on port 9222...")
                    opts.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
                    driver = webdriver.Chrome(options=opts)
                got_driver = True
            except Exception as e:
                LOG.debug(f"Driver attempt '{attempt}' failed: {e}")

        if not got_driver:
            LOG.error("Could not start Chrome. Install chromedriver or start Chrome with --remote-debugging-port=9222")
            return 0
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })

        max_p = int(max_price)
        base = f"https://www.idealista.pt/comprar-casas/portugal/com-preco-max_{max_p},publicado_ultimas-48-horas/"

        for page in range(1, 15):
            url = base if page == 1 else base + f"pagina-{page}.htm"
            driver.get(url)
            time.sleep(3 + page * 0.5)

            # Check for CAPTCHA or block
            if "captcha" in driver.page_source.lower() or "blocked" in driver.page_source.lower():
                LOG.warning(f"idealista: CAPTCHA/block on page {page}. Stopping.")
                break

            soup = BeautifulSoup(driver.page_source, "html.parser")
            articles = soup.select("article.item")

            if not articles:
                # Try alternative selectors
                articles = soup.select(".item-info-container")
                if not articles:
                    LOG.info(f"  idealista page {page}: no items found, stopping")
                    break

            for art in articles:
                listing = _idealista_parse_article(art)
                if listing:
                    upsert_listing(db, listing)
                    total_scraped += 1

            db.commit()
            LOG.info(f"  idealista page {page}: {len(articles)} items")

            # Check if there's a next page
            next_btn = soup.select_one("a.icon-arrow-right-after")
            if not next_btn:
                break

    except Exception as e:
        LOG.error(f"idealista Selenium error: {e}")
    finally:
        if driver:
            driver.quit()

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("idealista", datetime.now(timezone.utc).isoformat(), total_scraped, "ok")
    )
    db.commit()
    LOG.info(f"idealista.pt: {total_scraped} listings scraped")
    return total_scraped


def _idealista_parse_article(art) -> dict | None:
    try:
        link_el = art.select_one("a.item-link")
        if not link_el:
            return None
        href = link_el.get("href", "")
        eid = href.strip("/").split("/")[-1] if href else None
        if not eid:
            return None

        title = link_el.get_text(strip=True)
        price_el = art.select_one(".item-price")
        price_text = price_el.get_text(strip=True) if price_el else "0"
        price = float(price_text.replace("€", "").replace(".", "").replace(",", ".").strip() or 0)

        detail_el = art.select_one(".item-detail")
        detail_text = detail_el.get_text(" ", strip=True) if detail_el else ""

        area = None
        area_match = re.search(r"(\d+)\s*m[²2]", detail_text)
        if area_match:
            area = float(area_match.group(1))

        location_el = art.select_one(".item-detail-char .item-location") or art.select_one(".item-location")
        location = location_el.get_text(strip=True) if location_el else ""

        img_el = art.select_one("img")
        img_url = img_el.get("src") or img_el.get("data-src") if img_el else None

        return {
            "id": f"idealista:{eid}",
            "source": "idealista",
            "external_id": eid,
            "title": title,
            "description": detail_text,
            "tipo": _guess_tipo_from_title(title),
            "area_m2": area,
            "price": price,
            "current_bid": None,
            "min_price": None,
            "district": None,
            "concelho": location,
            "freguesia": None,
            "url": f"https://www.idealista.pt{href}" if href.startswith("/") else href,
            "image_url": img_url,
            "date_end": None,
            "raw_json": None,
        }
    except Exception as e:
        LOG.debug(f"idealista parse error: {e}")
        return None


def _guess_tipo_from_title(title: str) -> str:
    t = title.lower()
    if "apartamento" in t or "t0" in t or "t1" in t or "t2" in t or "t3" in t:
        return "apartamento"
    if "moradia" in t or "vivenda" in t:
        return "moradia"
    if "terreno" in t:
        return "terreno"
    if "loja" in t or "escritório" in t or "escritorio" in t:
        return "loja/escritorio"
    if "armazém" in t or "armazem" in t:
        return "armazem"
    if "garagem" in t:
        return "garagem"
    return "outro"


# ─── Croatia: e-oglasna.pravosudje.hr (REST API) ────────────────────

CROATIA_API = "https://e-oglasna.pravosudje.hr/api/v1/court-notice"
CROATIA_PROPERTY_KW = [
    "nekretnin", "stan", "kuć", "zemljišt", "poslovn", "garaž",
    "zgrada", "etaž", "parcela", "objekt", "dražb",
]

def scrape_croatia(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Croatian judicial auction notices via official API."""
    LOG.info("Scraping e-oglasna.pravosudje.hr...")
    session = requests.Session()
    session.headers["User-Agent"] = "AuctionScanner/1.0"

    total_scraped = 0
    max_pages = 50  # limit to avoid rate-limiting

    for page in range(max_pages):
        try:
            resp = session.get(CROATIA_API, params={
                "filter": "",
                "page": page,
                "sort": "datePublished",
            }, timeout=30)
            if resp.status_code == 429:
                LOG.warning(f"Croatia: rate limited at page {page}, stopping")
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            LOG.error(f"Croatia API error page {page}: {e}")
            break

        items = data.get("content", [])
        if not items:
            break

        for item in items:
            listing = _croatia_to_listing(item)
            if listing:
                upsert_listing(db, listing)
                total_scraped += 1

        db.commit()
        total_pages = data.get("totalPages", 1)
        LOG.info(f"  ... page {page+1}/{min(total_pages, max_pages)} ({total_scraped} property notices)")

        if page + 1 >= total_pages:
            break
        time.sleep(1.5)  # respect rate limits

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("croatia", datetime.now(timezone.utc).isoformat(), total_scraped, "ok")
    )
    db.commit()
    LOG.info(f"Croatia: {total_scraped} property notices scraped")
    return total_scraped


def _croatia_to_listing(item: dict) -> dict | None:
    uuid = item.get("uuid", "")
    title = item.get("title", "")
    t_lower = title.lower()
    if not any(kw in t_lower for kw in CROATIA_PROPERTY_KW):
        return None

    return {
        "id": f"croatia:{uuid}",
        "source": "croatia",
        "country": "HR",
        "external_id": uuid,
        "title": title,
        "description": None,
        "tipo": "nekretnina",
        "area_m2": None,
        "price": None,
        "current_bid": None,
        "min_price": None,
        "district": None,
        "concelho": None,
        "freguesia": None,
        "url": item.get("publicUrl") or f"https://e-oglasna.pravosudje.hr/objava/{uuid}",
        "image_url": None,
        "date_end": item.get("expirationDate"),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


# ─── Spain: subastas.boe.es (HTML POST) ─────────────────────────────

def scrape_spain(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Spanish judicial auctions from subastas.boe.es."""
    LOG.info("Scraping subastas.boe.es...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    try:
        session.get("https://subastas.boe.es/subastas_ava.php", timeout=15)
    except Exception as e:
        LOG.error(f"Spain session init failed: {e}")
        return 0

    total_scraped = 0
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

    try:
        resp = session.post("https://subastas.boe.es/subastas_ava.php", data=form_data, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        LOG.error(f"Spain POST search failed: {e}")
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract id_busqueda from pagination/nav links
    id_busqueda = None
    for link in soup.select("a[href*='id_busqueda']"):
        m = re.search(r'id_busqueda=([^&,]+)', link.get("href", ""))
        if m:
            id_busqueda = m.group(1)
            break

    # Extract total results
    total_text = soup.select_one(".paginar")
    total_results = 0
    if total_text:
        m = re.search(r'de\s+(\d+)', total_text.get_text())
        if m:
            total_results = int(m.group(1))
    LOG.info(f"  Spain: {total_results} total active inmuebles")

    # Parse first page
    total_scraped += _spain_parse_page(soup, db, max_price)
    db.commit()
    LOG.info(f"  Spain page 1: {total_scraped} items")

    # Paginate using id_busqueda
    if id_busqueda and total_results > page_size:
        max_pages = min((total_results // page_size) + 1, 20)
        for page_num in range(1, max_pages):
            offset = page_num * page_size
            page_url = (
                f"https://subastas.boe.es/subastas_ava.php"
                f"?accion=Mas&id_busqueda={id_busqueda},-{offset}-{page_size}"
            )
            try:
                resp = session.get(page_url, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                LOG.error(f"Spain page {page_num+1} error: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            count = _spain_parse_page(soup, db, max_price)
            db.commit()
            total_scraped += count
            LOG.info(f"  Spain page {page_num+1}: {count} items (total: {total_scraped})")

            if count == 0:
                break
            time.sleep(1)

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("spain", datetime.now(timezone.utc).isoformat(), total_scraped, "ok")
    )
    db.commit()
    LOG.info(f"Spain: {total_scraped} listings scraped")
    return total_scraped


def _spain_parse_page(soup, db, max_price) -> int:
    """Parse one page of Spain search results. Returns count."""
    count = 0
    h3s = soup.select("#contenido h3")
    for h3 in h3s:
        text = h3.get_text(strip=True)
        m = re.match(r'SUBASTA\s+(SUB-\S+-\d+-\d+)', text)
        if not m:
            continue
        sub_id = m.group(1)

        # Gather sibling info
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
                                f"{dm.group(1)} {dm.group(2)}", "%d/%m/%Y %H:%M"
                            ).isoformat()
                        except ValueError:
                            pass
                elif len(txt) > 40 and "Expediente" not in txt:
                    description = txt[:500]
            sib = sib.next_sibling

        # Find detail link
        detail_link = None
        for a in (h3.parent or soup).select(f'a[href*="idSub={sub_id}"]'):
            detail_link = a.get("href", "")
            break

        url = f"https://subastas.boe.es/detalleSubasta.php?idSub={sub_id}"
        title_short = description[:120] if description else f"Subasta {sub_id}"
        location = court.split(" - ")[-1].strip() if " - " in court else court

        listing = {
            "id": f"spain:{sub_id}",
            "source": "spain",
            "country": "ES",
            "external_id": sub_id,
            "title": title_short,
            "description": description,
            "tipo": "inmueble",
            "area_m2": None,
            "price": None,  # price is on detail page, not search results
            "current_bid": None,
            "min_price": None,
            "district": location,
            "concelho": None,
            "freguesia": None,
            "url": url,
            "image_url": None,
            "date_end": date_end,
            "raw_json": None,
        }
        upsert_listing(db, listing)
        count += 1

    return count


# ─── France: licitor.com (HTML — tribunal-based) ────────────────────

LICITOR_BASE = "https://www.licitor.com"

def scrape_france(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape French judicial auctions from licitor.com."""
    LOG.info("Scraping licitor.com...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    total_scraped = 0

    # Step 1: get list of tribunal pages from the main listing
    try:
        resp = session.get(f"{LICITOR_BASE}/ventes-aux-encheres-immobilieres/france.html", timeout=15)
        resp.raise_for_status()
    except Exception as e:
        LOG.error(f"France index error: {e}")
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")
    tribunal_links = []
    for a in soup.select("a[href*='/ventes-judiciaires-immobilieres/']"):
        href = a.get("href", "")
        if href and ".html" in href:
            full = href if href.startswith("http") else f"{LICITOR_BASE}{href}"
            tribunal_links.append(full)

    LOG.info(f"  France: {len(tribunal_links)} tribunal pages found")

    # Step 2: scrape each tribunal page for individual lots
    for i, trib_url in enumerate(tribunal_links[:60]):  # limit to 60 tribunals
        try:
            resp = session.get(trib_url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            LOG.debug(f"France tribunal error: {e}")
            continue

        trib_soup = BeautifulSoup(resp.text, "html.parser")
        count = 0

        # Find individual lot entries — links to /annonce/ detail pages
        for a in trib_soup.select("a[href*='/annonce/'], a[href*='/vente-aux-encheres']"):
            href = a.get("href", "")
            if not href:
                continue
            title = a.get_text(strip=True)[:120]
            if not title or len(title) < 5:
                continue

            m_id = re.search(r"/(\d+)\.html", href)
            eid = m_id.group(1) if m_id else href.strip("/").split("/")[-1].replace(".html", "")
            full_url = href if href.startswith("http") else f"{LICITOR_BASE}{href}"

            # Try to extract price from surrounding text
            price = None
            parent = a.parent
            if parent:
                txt = parent.get_text(" ", strip=True)
                m = re.search(r'([\d\s.,]+)\s*€', txt)
                if m:
                    price = float(m.group(1).replace(" ", "").replace(" ", "").replace(".", "").replace(",", "."))

            if price and price > max_price:
                continue

            listing = {
                "id": f"france:{eid}",
                "source": "france",
                "country": "FR",
                "external_id": eid,
                "title": title,
                "description": None,
                "tipo": "immobilier",
                "area_m2": None,
                "price": price,
                "current_bid": None,
                "min_price": None,
                "district": None,
                "concelho": None,
                "freguesia": None,
                "url": full_url,
                "image_url": None,
                "date_end": None,
                "raw_json": None,
            }
            upsert_listing(db, listing)
            count += 1
            total_scraped += 1

        if count > 0:
            db.commit()
            LOG.info(f"  France tribunal {i+1}/{len(tribunal_links)}: {count} lots")

        time.sleep(0.5)

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("france", datetime.now(timezone.utc).isoformat(), total_scraped, "ok")
    )
    db.commit()
    LOG.info(f"France: {total_scraped} listings scraped")
    return total_scraped


# ─── Italy: astegiudiziarie.it (HTML) ───────────────────────────────

def scrape_italy(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Italian judicial auctions from astegiudiziarie.it."""
    LOG.info("Scraping astegiudiziarie.it...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    session.headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.8"

    total_scraped = 0
    base = "https://www.astegiudiziarie.it"

    # The main /immobili page lists properties; detail links have pattern
    # /vendita-asta-TYPE-LOCATION-...-lNNNNNNN-pNNNNNNN
    try:
        resp = session.get(f"{base}/immobili", timeout=30)
        resp.raise_for_status()
    except Exception as e:
        LOG.error(f"Italy main page error: {e}")
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find all property detail links
    for a in soup.select("a[href*='/vendita-asta-']"):
        href = a.get("href", "")
        if not href:
            continue

        # Extract IDs from URL pattern -lNNNNNNN-pNNNNNNN
        m = re.search(r'-l(\d+)-p(\d+)', href)
        if not m:
            continue
        eid = f"{m.group(1)}-{m.group(2)}"

        title = a.get_text(strip=True)[:120]
        if not title:
            img = a.select_one("img")
            title = img.get("alt", "")[:120] if img else ""

        # Extract price from surrounding elements
        price = None
        parent = a.parent
        if parent:
            txt = parent.get_text(" ", strip=True)
            pm = re.search(r'€\s*([\d.,]+)', txt) or re.search(r'([\d.,]+)\s*€', txt)
            if pm:
                try:
                    price = float(pm.group(1).replace(".", "").replace(",", "."))
                except ValueError:
                    pass

        full_url = href if href.startswith("http") else f"{base}{href}"

        listing = {
            "id": f"italy:{eid}",
            "source": "italy",
            "country": "IT",
            "external_id": eid,
            "title": title if title else f"Immobile {eid}",
            "description": None,
            "tipo": "immobile",
            "area_m2": None,
            "price": price,
            "current_bid": None,
            "min_price": None,
            "district": None,
            "concelho": None,
            "freguesia": None,
            "url": full_url,
            "image_url": None,
            "date_end": None,
            "raw_json": None,
        }
        upsert_listing(db, listing)
        total_scraped += 1

    db.commit()

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("italy", datetime.now(timezone.utc).isoformat(), total_scraped, "ok")
    )
    db.commit()
    LOG.info(f"Italy: {total_scraped} listings scraped from main page")
    return total_scraped


# ─── Netherlands: openbareverkoop.nl (JSON API) ────────────────────

def scrape_netherlands(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Dutch public property auctions from openbareverkoop.nl."""
    LOG.info("Scraping openbareverkoop.nl...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    base = "https://www.openbareverkoop.nl"
    total_scraped = 0

    try:
        session.get(f"{base}/kavels?view=resultaten", timeout=30)
        resp = session.post(
            f"{base}/kavels/searchresults",
            data={"text": "", "view": "", "periode": "alles", "woningtype": ""},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        LOG.error(f"Netherlands error: {e}")
        return 0

    for zitting in data.get("results", []):
        for opr in zitting.get("objectenPerRegio", []):
            for obj in opr.get("objects", []):
                eid = str(obj.get("id", ""))
                if not eid:
                    continue

                # NL auctions don't publish the property price upfront;
                # "veilingkosten" is the auction FEE, not the property value.
                price = None
                for field in ("inzet", "afslag"):
                    val = obj.get(field, "")
                    if val:
                        pm = re.search(r"[\d.,]+", val.replace("€", ""))
                        if pm:
                            try:
                                price = float(pm.group().replace(".", "").replace(",", "."))
                            except ValueError:
                                pass
                            break

                title = obj.get("kavelNaam", "")
                wtype = obj.get("woningtype", "")
                if wtype:
                    title = f"{title} ({wtype})"

                url = obj.get("url", "")
                if url and not url.startswith("http"):
                    url = f"{base}{url}"

                img = obj.get("image", "")
                if img and not img.startswith("http"):
                    img = f"{base}{img}"

                vtype = obj.get("veilingwijze", "")
                listing = {
                    "id": f"netherlands:{eid}",
                    "source": "netherlands",
                    "country": "NL",
                    "external_id": eid,
                    "title": title,
                    "description": f"Executieveiling ({vtype})" if vtype else "Executieveiling",
                    "tipo": "vastgoed",
                    "area_m2": None,
                    "price": price,
                    "current_bid": None,
                    "min_price": None,
                    "district": None,
                    "concelho": None,
                    "freguesia": None,
                    "url": url,
                    "image_url": img or None,
                    "date_end": None,
                    "raw_json": json.dumps(obj, ensure_ascii=False, default=str),
                }
                upsert_listing(db, listing)
                total_scraped += 1

    db.commit()
    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("netherlands", datetime.now(timezone.utc).isoformat(), total_scraped, "ok"),
    )
    db.commit()
    LOG.info(f"Netherlands: {total_scraped} listings scraped")
    return total_scraped


# ─── Croatia: FINA Ocevidnik CSV (open data) ───────────────────────

def scrape_fina_csv(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Croatian forced-sale property registry from FINA open CSV."""
    import csv
    import io

    LOG.info("Downloading FINA Ocevidnik CSV (~10MB)...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    try:
        resp = session.get("https://ponip.fina.hr/ocevidnik-web/preuzmi/csv", timeout=60)
        resp.raise_for_status()
    except Exception as e:
        LOG.error(f"FINA CSV error: {e}")
        return 0

    text = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader)
    col = {h: i for i, h in enumerate(header)}

    now = datetime.now(timezone.utc)
    total_scraped = 0

    for row in reader:
        d = {h: row[i] if i < len(row) else "" for h, i in col.items()}

        end_str = d.get("Datum i vrijeme završetka nadmetanja", "")
        if not end_str or end_str < now.strftime("%Y-%m-%d"):
            continue

        tipo = d.get("Vrsta predmeta prodaje", "").lower()
        if "nekretnina" not in tipo and "imovina" not in tipo:
            continue

        price_str = d.get("Početna cijena za nadmetanje", "").replace(",", ".")
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            price = None

        if price and price > max_price:
            continue

        bid_id = d.get("ID nadmetanja", "")
        eid = bid_id or hashlib.md5(f"{d.get('Poslovni broj spisa','')}{end_str}".encode()).hexdigest()[:12]
        title = d.get("Opis", "")[:120]
        court = d.get("Nadležno tijelo", "")

        min_price_str = d.get("Minimalna zakonska cijena ispod koje se predmet prodaje ne može prodati", "").replace(",", ".")
        try:
            min_price = float(min_price_str)
        except (ValueError, TypeError):
            min_price = None

        listing = {
            "id": f"fina:{eid}",
            "source": "fina",
            "country": "HR",
            "external_id": str(eid),
            "title": title if title else f"Nekretnina {eid}",
            "description": d.get("Napomena uz detalje predmeta prodaje", "")[:500] or None,
            "tipo": "nekretnina",
            "area_m2": None,
            "price": price,
            "current_bid": None,
            "min_price": min_price,
            "district": court,
            "concelho": None,
            "freguesia": None,
            "url": f"https://ponip.fina.hr/ocevidnik-web/#/predmet-prodaje/{eid}" if bid_id else None,
            "image_url": None,
            "date_end": end_str.replace(" ", "T") if end_str else None,
            "raw_json": None,
        }
        upsert_listing(db, listing)
        total_scraped += 1

    db.commit()
    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("fina", datetime.now(timezone.utc).isoformat(), total_scraped, "ok"),
    )
    db.commit()
    LOG.info(f"FINA CSV: {total_scraped} Croatian listings scraped")
    return total_scraped


# ─── Netherlands: veilingnotaris.nl (HTML) ──────────────────────────

def scrape_veilingnotaris(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Dutch execution auctions from veilingnotaris.nl."""
    LOG.info("Scraping veilingnotaris.nl...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    base = "https://veilingnotaris.nl"
    total_scraped = 0

    try:
        resp = session.get(f"{base}/veilingen/", timeout=15)
        resp.raise_for_status()
    except Exception as e:
        LOG.error(f"Veilingnotaris error: {e}")
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"/veilingen/(\d+)/([^/]+)/", href)
        if not m:
            continue

        eid = m.group(1)
        slug = m.group(2)
        text = a.get_text(" ", strip=True)

        # Parse address from slug
        address = slug.replace("_", " ").replace("-", " ").title()

        # Extract type from text (Appartement, Woonhuis, etc.)
        wtype = ""
        for t in ["Appartement", "Woonhuis", "Tussenwoning", "Hoekwoning", "Bovenwoning",
                   "Twee-onder-een-kap", "Vrijstaand", "Bedrijfspand", "Winkel", "Kantoor"]:
            if t.lower() in text.lower():
                wtype = t
                break

        title = f"{address} ({wtype})" if wtype else address
        url = href if href.startswith("http") else f"{base}{href}"

        listing = {
            "id": f"veilingnotaris:{eid}",
            "source": "veilingnotaris",
            "country": "NL",
            "external_id": eid,
            "title": title,
            "description": None,
            "tipo": "vastgoed",
            "area_m2": None,
            "price": None,
            "current_bid": None,
            "min_price": None,
            "district": None,
            "concelho": None,
            "freguesia": None,
            "url": url,
            "image_url": None,
            "date_end": None,
            "raw_json": None,
        }
        upsert_listing(db, listing)
        total_scraped += 1

    db.commit()
    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("veilingnotaris", datetime.now(timezone.utc).isoformat(), total_scraped, "ok"),
    )
    db.commit()
    LOG.info(f"Veilingnotaris: {total_scraped} Dutch listings scraped")
    return total_scraped


# ─── Portugal: leilosoc.com (HTML) ─────────────────────────────────

def scrape_leilosoc(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape Portuguese auction house listings from leilosoc.com."""
    LOG.info("Scraping leilosoc.com...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    base = "https://leilosoc.com"
    total_scraped = 0

    for page in range(1, 10):
        url = f"{base}/en/category/5-real-estate/?page={page}"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            LOG.error(f"Leilosoc page {page} error: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        lot_links = soup.select("a[href*='/lot/']")
        if not lot_links:
            break

        seen = set()
        for a in lot_links:
            href = a.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)

            m = re.search(r"/lot/(\d+)/", href)
            if not m:
                continue
            eid = m.group(1)

            text = a.get_text(" ", strip=True)
            # Try to extract title (skip "Lot N" prefix)
            title = re.sub(r"^Lot\s+\d+\s*", "", text).strip()[:120]
            if not title:
                title = f"Leilosoc lot {eid}"

            # Extract price if visible
            price = None
            parent = a.parent
            if parent:
                pm = re.search(r"€\s*([\d\s.,]+)", parent.get_text(" "))
                if pm:
                    try:
                        price = float(pm.group(1).replace(" ", "").replace(".", "").replace(",", "."))
                    except ValueError:
                        pass

            if price and price > max_price:
                continue

            full_url = href if href.startswith("http") else f"{base}{href}"

            listing = {
                "id": f"leilosoc:{eid}",
                "source": "leilosoc",
                "country": "PT",
                "external_id": eid,
                "title": title,
                "description": None,
                "tipo": "imovel",
                "area_m2": None,
                "price": price,
                "current_bid": None,
                "min_price": None,
                "district": None,
                "concelho": None,
                "freguesia": None,
                "url": full_url,
                "image_url": None,
                "date_end": None,
                "raw_json": None,
            }
            upsert_listing(db, listing)
            total_scraped += 1

        db.commit()
        LOG.info(f"  Leilosoc page {page}: {len(seen)} lots")
        time.sleep(0.5)

    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("leilosoc", datetime.now(timezone.utc).isoformat(), total_scraped, "ok"),
    )
    db.commit()
    LOG.info(f"Leilosoc: {total_scraped} Portuguese listings scraped")
    return total_scraped


def scrape_bcp(db: sqlite3.Connection, max_price: float = 50000):
    """Scrape BCP Millennium bank repossession listings."""
    LOG.info("Scraping BCP Millennium imoveis...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    url = "https://millenniumimoveis.janeladigital.com/Search.aspx"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        LOG.error(f"BCP error: {e}")
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select('a[href*="Detail.aspx"][href*="obp=1"]')
    total_scraped = 0

    for a in links:
        href = a.get("href", "")
        m = re.search(r"UID=([a-f0-9-]+)", href)
        if not m:
            continue
        eid = m.group(1)
        text = a.get_text(" ", strip=True)

        # Extract price: "€ 102 000" or "€ 24 000"
        price = None
        pm = re.search(r"€\s*([\d\s]+)", text)
        if pm:
            try:
                price = float(pm.group(1).replace(" ", ""))
            except ValueError:
                pass
        if price and price > max_price:
            continue

        # Extract tipo from start: "Moradia T2", "Apartamento T3", "Loja", "Terreno"
        tipo_m = re.match(r"([\w\s]+?)(?:\s*€)", text)
        title_part = tipo_m.group(1).strip() if tipo_m else "BCP property"

        # Extract concelho/freguesia
        concelho = None
        freguesia = None
        cm = re.search(r"Concelho\s*:\s*(\S[\w\s]+?)(?:\s*Freguesia|$)", text)
        if cm:
            concelho = cm.group(1).strip()
        fm = re.search(r"Freguesia\s*:\s*(\S[\w\s]+?)(?:\s*Im[oó]vel|$)", text)
        if fm:
            freguesia = fm.group(1).strip()

        # Extract area
        area = None
        am = re.search(r"(\d[\d\s]*)\s*m", text)
        if am:
            try:
                area = float(am.group(1).replace(" ", ""))
            except ValueError:
                pass

        location = ", ".join(filter(None, [freguesia, concelho]))
        title = f"{title_part} - {location}" if location else title_part

        full_url = f"https://millenniumimoveis.janeladigital.com{href}"

        listing = {
            "id": f"bcp:{eid}",
            "source": "bcp",
            "country": "PT",
            "external_id": eid,
            "title": title[:120],
            "description": f"Bank repossession (BCP Millennium)",
            "tipo": "imovel",
            "area_m2": area,
            "price": price,
            "current_bid": None,
            "min_price": None,
            "district": None,
            "concelho": concelho,
            "freguesia": freguesia,
            "url": full_url,
            "image_url": None,
            "date_end": None,
            "raw_json": None,
        }
        upsert_listing(db, listing)
        total_scraped += 1

    db.commit()
    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status) VALUES (?,?,?,?)",
        ("bcp", datetime.now(timezone.utc).isoformat(), total_scraped, "ok"),
    )
    db.commit()
    LOG.info(f"BCP: {total_scraped} Portuguese bank repo listings scraped")
    return total_scraped


# ─── e-leiloes detail fetch ──────────────────────────────────────────

def fetch_eleiloes_details(db: sqlite3.Connection, limit: int = 20):
    """Fetch full details for interesting e-leiloes listings missing descriptions."""
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    session.verify = False

    rows = db.execute("""
        SELECT external_id FROM listings
        WHERE source='eleiloes' AND description IS NULL
          AND price <= 50000
          AND (current_bid <= 50000 OR current_bid IS NULL OR current_bid = 0)
          AND date_end > ?
          AND tipo NOT IN ('outro','direitos')
        ORDER BY price DESC
        LIMIT ?
    """, (datetime.now(timezone.utc).isoformat(), limit)).fetchall()

    count = 0
    for (eid,) in rows:
        try:
            resp = session.get(f"https://e-leiloes.pt/api/Eventos/{eid}", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            desc_parts = []
            for v in data.get("verbas", []):
                if v.get("descricao"):
                    desc_parts.append(v["descricao"])
                if v.get("area"):
                    db.execute("UPDATE listings SET area_m2=? WHERE id=?", (v["area"], f"eleiloes:{eid}"))
            if desc_parts:
                db.execute("UPDATE listings SET description=? WHERE id=?",
                           ("\n".join(desc_parts), f"eleiloes:{eid}"))
            count += 1
            time.sleep(0.3)
        except Exception as e:
            LOG.debug(f"Detail fetch failed for {eid}: {e}")

    db.commit()
    LOG.info(f"Fetched details for {count} e-leiloes listings")


# ─── Report generation ───────────────────────────────────────────────

IMOVEL_TYPES = {
    "apartamento/moradia", "apartamento", "moradia", "loja/escritorio",
    "terreno_urbano", "terreno_rustico", "armazem", "outro_imovel",
    "hotel", "industrial", "garagem", "terreno",
    "inmueble", "nekretnina", "immobilier", "immobile", "vastgoed",
}
GOLD_KEYWORDS = {"ouro", "joalharia", "bijutaria", "relojoaria", "cautela"}
VEHICLE_KEYWORDS = {
    "veículo", "veiculo", "automóvel", "automovel", "peugeot", "renault",
    "volkswagen", "toyota", "toyoya", "ford", "opel", "smart", "hyundai",
    "volvo", "bmw", "mercedes", "audi", "citroen", "fiat", "seat", "ktm",
    "motociclo", "moto ", "ligeiro", "pesado de mercadorias", "yaris",
    "focus", "scenic", "matricula",
}


def _categorize(item: dict) -> str:
    title = (item.get("title") or "").lower()
    tipo = (item.get("tipo") or "").lower()
    if any(kw in title for kw in GOLD_KEYWORDS):
        return "ouro_joias"
    if any(kw in title for kw in VEHICLE_KEYWORDS):
        return "outros"
    if tipo in IMOVEL_TYPES or "prédio" in title or "terreno" in title or "moradia" in title or "apartamento" in title or "fração" in title:
        return "imoveis"
    if tipo == "comercio":
        return "outros"
    return "outros"


def investment_score(item: dict) -> tuple[float, list[str]]:
    """Score a property 0-100 for investment value. Returns (score, [reasons])."""
    score = 50.0
    reasons = []
    title = (item.get("title") or "").lower()
    price = item.get("price") or 0
    bid = item.get("current_bid") or 0
    area = item.get("area_m2") or 0
    country = item.get("country", "PT")

    # --- PENALTIES (red flags) ---

    # Fractional ownership
    frac_patterns = ["1/2", "1/3", "1/4", "1/5", "1/6", "1/7", "1/8", "1/12",
                     "avos", "quota", "quinhão", "quinhao"]
    if any(p in title for p in frac_patterns):
        score -= 25
        reasons.append("fractional share")

    # Usufruct / limited rights
    if "usufruto" in title or "usufruct" in title or "nue-propri" in title:
        score -= 30
        reasons.append("usufruct only")

    if "direito" in title and ("herança" in title or "heranca" in title):
        score -= 20
        reasons.append("inheritance right")

    # Ruins / uninhabitable
    if "ruína" in title or "ruina" in title or "ruine" in title or "rudere" in title:
        score -= 10
        reasons.append("ruins")

    # Very cheap = likely worthless
    if price and price < 500:
        score -= 15
        reasons.append("suspiciously cheap")

    # Overbid (bid > 150% of asking)
    if bid and price and bid > price * 1.5:
        score -= 15
        reasons.append(f"overbid {bid/price:.0%}")

    # Rural/rustic with no area info
    if ("rústico" in title or "rustico" in title or "agricole" in title) and not area:
        score -= 5
        reasons.append("rural/no area")

    # Parking / storage only
    if any(w in title for w in ["parking", "garagem", "garage", "box", "emplacement", "magazzino"]):
        score -= 10
        reasons.append("parking/storage")

    # --- BONUSES ---

    # Full house/apartment
    house_words = ["moradia", "apartamento", "vivienda", "appartement", "maison",
                   "woonhuis", "appartamento", "casa", "logement", "tussenwoning"]
    if any(w in title for w in house_words):
        score += 15
        reasons.append("full dwelling")

    # Discount: bid well below asking
    if bid and price and bid < price * 0.7:
        bonus = min(20, (1 - bid / price) * 40)
        score += bonus
        reasons.append(f"discount {1-bid/price:.0%}")
    elif not bid and price:
        score += 5
        reasons.append("no bids yet")

    # Good size
    if area and area > 50:
        score += 5
        reasons.append(f"{area:.0f}m2")
    if area and area > 100:
        score += 5

    # Urban location signals
    urban_kw = ["lisboa", "porto", "madrid", "barcelona", "valencia", "paris",
                "lyon", "marseille", "amsterdam", "rotterdam", "den haag",
                "roma", "milano", "zagreb", "split"]
    loc = " ".join(filter(None, [item.get("concelho",""), item.get("district","")])).lower()
    full_text = f"{title} {loc}"
    if any(c in full_text for c in urban_kw):
        score += 10
        reasons.append("urban location")

    # Price sweet spot (5k-40k for habitable property)
    if price and 5000 <= price <= 40000 and any(w in title for w in house_words):
        score += 10
        reasons.append("price sweet spot")

    # Ending soon = urgency (within 7 days)
    if item.get("date_end"):
        try:
            end = datetime.fromisoformat(item["date_end"].replace("Z", "+00:00"))
            days_left = (end - datetime.now(timezone.utc)).days
            if 0 < days_left <= 7:
                score += 5
                reasons.append(f"{days_left}d left")
        except (ValueError, TypeError):
            pass

    return max(0, min(100, score)), reasons


def generate_report(db: sqlite3.Connection, max_price: float = 50000, max_bid: float = 50000):
    """Generate a Markdown investment report from the DB."""
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    rows = db.execute("""
        SELECT * FROM listings
        WHERE (price <= ? OR price IS NULL)
          AND (current_bid <= ? OR current_bid IS NULL OR current_bid = 0)
          AND (date_end IS NULL OR date_end > ?)
        ORDER BY price ASC
    """, (max_price * 2, max_bid, now.isoformat())).fetchall()

    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    items = [dict(zip(cols, r)) for r in rows]

    categories = {"imoveis": [], "ouro_joias": [], "outros": []}
    for item in items:
        cat = _categorize(item)
        price = item["price"] or 0
        bid = item["current_bid"] or 0
        if price <= max_price and bid <= max_bid:
            ratio = bid / price if price > 0 and bid > 0 else None
            categories[cat].append((item, ratio))

    # Score and sort by investment value
    for cat in categories:
        scored = []
        for item, ratio in categories[cat]:
            inv_score, inv_reasons = investment_score(item)
            scored.append((item, ratio, inv_score, inv_reasons))
        scored.sort(key=lambda x: (-x[2], x[1] or 999))
        categories[cat] = scored

    COUNTRY_NAMES = {"PT": "Portugal", "HR": "Croatia", "ES": "Spain", "FR": "France", "IT": "Italy", "NL": "Netherlands"}

    lines = [
        f"# EU Investment Scanner Report",
        f"**Generated**: {now_str}  ",
        f"**Budget**: €{max_price:,.0f}  ",
        "",
    ]

    section_names = {
        "imoveis": "Imóveis / Real Estate",
        "ouro_joias": "Ouro & Joias (Gold & Jewelry)",
        "outros": "Outros (Vehicles, Equipment, etc.)",
    }

    for cat, label in section_names.items():
        cat_items = categories[cat]
        lines.append(f"## {label} — {len(cat_items)} listings")
        lines.append("")

        if not cat_items:
            lines.append("_None found._\n")
            continue

        # Group by country
        by_country = {}
        for item, ratio, inv_score, inv_reasons in cat_items:
            c = item.get("country", "PT")
            by_country.setdefault(c, []).append((item, ratio, inv_score, inv_reasons))

        for country_code in ["PT", "ES", "FR", "IT", "HR", "NL"]:
            c_items = by_country.get(country_code, [])
            if not c_items:
                continue
            cname = COUNTRY_NAMES.get(country_code, country_code)
            lines.append(f"### {cname} ({len(c_items)})")
            lines.append("")
            lines.append("| # | Score | Title | Price | Bid | Location | Ends | Flags |")
            lines.append("|---|-------|-------|-------|-----|----------|------|-------|")

            show = c_items[:30] if cat == "imoveis" else c_items[:15]
            for i, (item, ratio, inv_score, inv_reasons) in enumerate(show, 1):
                price_str = f"€{item['price']:,.0f}" if item['price'] else "?"
                bid_str = f"€{item['current_bid']:,.0f}" if item['current_bid'] else "-"
                loc = ", ".join(filter(None, [item["concelho"], item["district"]]))
                ends = item["date_end"][:10] if item["date_end"] else "-"
                title_short = (item["title"] or "?")[:50]
                url = item["url"] or ""
                flags = ", ".join(inv_reasons)[:40]
                lines.append(
                    f"| {i} | {inv_score:.0f} | [{title_short}]({url}) | {price_str} | {bid_str} | {loc} | {ends} | {flags} |"
                )
            lines.append("")

    # LLM-ready compact summary for the top imóveis (saves tokens)
    lines.append("## Top Imóveis — Compact (for LLM analysis)")
    lines.append("```json")
    compact = []
    for item, ratio, inv_score, inv_reasons in categories["imoveis"][:15]:
        compact.append({
            "t": item["title"][:60],
            "vb": item["price"],
            "bid": item["current_bid"],
            "loc": ", ".join(filter(None, [item["concelho"], item["district"]])),
            "tipo": item["tipo"],
            "area": item["area_m2"],
            "ends": (item["date_end"] or "")[:10],
            "url": item["url"],
        })
    lines.append(json.dumps(compact, ensure_ascii=False, indent=1))
    lines.append("```")
    lines.append("")

    report = "\n".join(lines)
    report_path = os.path.join(os.path.dirname(__file__), "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    LOG.info(f"Report: {len(categories['imoveis'])} imóveis, {len(categories['ouro_joias'])} ouro/joias, {len(categories['outros'])} outros")
    LOG.info(f"Report written to {report_path}")
    return report_path


# ─── LLM Analysis ────────────────────────────────────────────────────

ANALYSIS_PROMPT = """You are a Portuguese real estate investment analyst. Budget: €{budget:,.0f}.
Analyze these auction/sale listings and rank them by investment potential.

For each listing, assess:
1. Is this a FULL property or a fractional share (quota-parte, 1/2, 1/12, avos)?
2. Location quality (urban vs rural, proximity to cities)
3. Red flags (very old listing, unrealistic price, legal complications like "direito de usufruto")
4. Realistic resale/rental potential

Output a JSON array of verdicts, one per listing:
{{"url":"...","score":1-10,"verdict":"BUY/WATCH/SKIP","reason":"one line"}}

Score 8-10 = strong buy opportunity, 5-7 = worth investigating, 1-4 = skip.
Be brutally honest. Most auction listings are bad deals — say so when they are.

Listings:
{listings_json}"""


def analyze_with_llm(db: sqlite3.Connection, max_price: float = 50000, category: str = "imoveis"):
    """Send top listings to Claude for investment analysis. Returns analysis path."""
    try:
        import anthropic
    except ImportError:
        LOG.error("anthropic not installed. Run: pip install anthropic")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No API key — dump the prompt to a file for manual analysis
        LOG.info("No ANTHROPIC_API_KEY set. Generating prompt file for manual analysis...")

    now = datetime.now(timezone.utc)
    rows = db.execute("""
        SELECT * FROM listings
        WHERE (price <= ? OR price IS NULL)
          AND (current_bid <= ? OR current_bid IS NULL OR current_bid = 0)
          AND (date_end IS NULL OR date_end > ?)
        ORDER BY price ASC
    """, (max_price * 2, max_price, now.isoformat())).fetchall()

    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    items = [dict(zip(cols, r)) for r in rows]

    filtered = []
    for item in items:
        cat = _categorize(item)
        if cat != category:
            continue
        price = item["price"] or 0
        bid = item["current_bid"] or 0
        if price <= max_price and bid <= max_price:
            filtered.append(item)

    if not filtered:
        LOG.info("No listings to analyze")
        return None

    # Build compact JSON (minimize tokens)
    compact = []
    for item in filtered[:25]:
        entry = {
            "title": (item["title"] or "")[:80],
            "price": item["price"],
            "bid": item["current_bid"],
            "loc": ", ".join(filter(None, [item["freguesia"], item["concelho"], item["district"]])),
            "tipo": item["tipo"],
            "area": item["area_m2"],
            "ends": (item["date_end"] or "")[:10],
            "url": item["url"],
            "src": item["source"],
        }
        if item.get("description"):
            entry["desc"] = item["description"][:200]
        compact.append(entry)

    listings_json = json.dumps(compact, ensure_ascii=False)
    prompt = ANALYSIS_PROMPT.format(budget=max_price, listings_json=listings_json)

    analysis_path = os.path.join(os.path.dirname(__file__), "analysis.md")
    prompt_path = os.path.join(os.path.dirname(__file__), "analysis_prompt.txt")

    if not api_key:
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        LOG.info(f"Prompt saved to {prompt_path}")
        LOG.info("Paste this into Claude or run with ANTHROPIC_API_KEY set.")

        # Also generate a basic rule-based analysis as fallback
        lines = [f"# Investment Analysis (rule-based)", f"**Generated**: {now.strftime('%Y-%m-%d %H:%M UTC')}  ", ""]
        for entry in compact:
            flags = []
            title = entry["title"].lower()
            if any(x in title for x in ["1/2", "1/3", "1/4", "1/6", "1/8", "1/12", "avos", "quota", "quinhão"]):
                flags.append("FRACTIONAL SHARE — limited utility")
            if "usufruto" in title:
                flags.append("USUFRUCT ONLY — not full ownership")
            if "direito" in title and "herança" in title:
                flags.append("INHERITANCE RIGHT — legal complexity")
            if entry.get("bid") and entry["price"] and entry["bid"] > entry["price"] * 2:
                flags.append(f"BID {entry['bid']/entry['price']:.0%} of VB — overheated")
            if entry["price"] and entry["price"] < 500:
                flags.append("VERY LOW VB — likely tiny plot or worthless fraction")
            if "rústico" in title and entry["price"] and entry["price"] < 10000:
                flags.append("CHEAP RURAL — probably remote/inaccessible")

            verdict = "SKIP" if flags else "INVESTIGATE"
            score = max(1, 6 - len(flags))
            reason = "; ".join(flags) if flags else "No obvious red flags — worth checking details"

            lines.append(f"**[{entry['title'][:50]}]({entry['url']})** — €{entry['price']:,.0f}")
            lines.append(f"  Score: {score}/10 | {verdict} | {reason}")
            lines.append("")

        with open(analysis_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        LOG.info(f"Rule-based analysis written to {analysis_path}")
        return analysis_path

    LOG.info(f"Sending {len(compact)} listings to Claude (prompt ~{len(prompt)} chars)...")

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    analysis_text = resp.content[0].text
    tokens_in = resp.usage.input_tokens
    tokens_out = resp.usage.output_tokens
    LOG.info(f"Claude analysis done. Tokens: {tokens_in} in + {tokens_out} out = {tokens_in + tokens_out} total")

    with open(analysis_path, "w", encoding="utf-8") as f:
        f.write(f"# Investment Analysis\n")
        f.write(f"**Generated**: {now.strftime('%Y-%m-%d %H:%M UTC')}  \n")
        f.write(f"**Model**: claude-haiku-4-5 | **Tokens**: {tokens_in + tokens_out}  \n")
        f.write(f"**Listings analyzed**: {len(compact)}  \n\n")
        f.write(analysis_text)
        f.write("\n")

    LOG.info(f"Analysis written to {analysis_path}")
    return analysis_path




def _safe_print(text: str):
    """Print text, replacing unencodable characters."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def print_console_summary(db: sqlite3.Connection, max_price: float = 50000):
    """Print a quick top-5-per-country summary to the console."""
    COUNTRY_NAMES = {"PT": "Portugal", "ES": "Spain", "FR": "France", "IT": "Italy", "HR": "Croatia", "NL": "Netherlands"}
    IMOVEL_TIPOS = {"imovel", "moradia", "apartamento", "terreno", "fracao", "armazem",
                    "garagem", "loja", "escritorio", "rustico", "urbano", "misto", "predio",
                    "inmueble", "nekretnina", "immobilier", "immobile", "vastgoed"}
    now = datetime.now(timezone.utc)

    counts = db.execute(
        "SELECT country, COUNT(*) FROM listings GROUP BY country ORDER BY COUNT(*) DESC"
    ).fetchall()
    total = sum(c for _, c in counts)

    _safe_print(f"\n{'='*60}")
    _safe_print(f"  EU AUCTION SCANNER - {now.strftime('%d %b %Y %H:%M')} UTC")
    _safe_print(f"  {total} listings across {len(counts)} countries (budget EUR {max_price:,.0f})")
    _safe_print(f"{'='*60}")

    for code in ["PT", "ES", "FR", "IT", "HR", "NL"]:
        # Build tipo filter for property-only results
        tipo_filter = " OR ".join(f"LOWER(tipo) LIKE '%{t}%'" for t in IMOVEL_TIPOS)
        cols = [d[1] for d in db.execute("PRAGMA table_info(listings)").fetchall()]
        rows = db.execute(f"""
            SELECT *
            FROM listings
            WHERE country = ?
              AND (price <= ? OR price IS NULL)
              AND (date_end IS NULL OR date_end > ?)
              AND ({tipo_filter})
        """, (code, max_price, now.isoformat())).fetchall()

        items = [dict(zip(cols, r)) for r in rows]
        scored = [(it, *investment_score(it)) for it in items]
        scored.sort(key=lambda x: -x[1])
        top5 = scored[:5]

        if not top5:
            continue

        count = db.execute("SELECT COUNT(*) FROM listings WHERE country = ?", (code,)).fetchone()[0]
        name = COUNTRY_NAMES.get(code, code)
        _safe_print(f"\n  {name} ({count} total)")
        _safe_print(f"  {'-'*56}")

        for i, (item, score, reasons) in enumerate(top5, 1):
            title_short = (item["title"] or "?")[:40]
            price = item["price"]
            price_str = f"EUR {price:,.0f}" if price else "?"
            loc = ", ".join(filter(None, [item["concelho"], item["district"]]))[:20]
            ends = item["date_end"][:10] if item["date_end"] else ""
            flags = ", ".join(reasons)[:30]
            _safe_print(f"  {i}. [{score:.0f}] {title_short}")
            _safe_print(f"     {price_str}  {loc}  {ends}")
            _safe_print(f"     {flags}")
            _safe_print(f"     {item['url']}")

    _safe_print(f"\n{'='*60}\n")


def main():
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    parser = argparse.ArgumentParser(description="EU Auction Scanner")
    parser.add_argument("--source", choices=[
        "eleiloes", "idealista", "croatia", "fina", "spain", "france", "italy",
        "netherlands", "veilingnotaris", "leilosoc", "bcp", "all"
    ], default="all")
    parser.add_argument("--country", choices=["PT", "HR", "ES", "FR", "IT", "NL", "all"], default=None,
                        help="Scrape all sources for a country")
    parser.add_argument("--max-price", type=float, default=50000)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--analyze", action="store_true", help="Run LLM analysis on top listings")
    parser.add_argument("--analyze-category", default="imoveis", choices=["imoveis", "ouro_joias", "outros"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    db = sqlite3.connect(DB_PATH)
    init_db(db)

    # Migrate: add country column if missing
    cols = [r[1] for r in db.execute("PRAGMA table_info(listings)").fetchall()]
    if "country" not in cols:
        db.execute("ALTER TABLE listings ADD COLUMN country TEXT NOT NULL DEFAULT 'PT'")
        db.commit()

    COUNTRY_SOURCES = {
        "PT": [("eleiloes", scrape_eleiloes), ("leilosoc", scrape_leilosoc), ("bcp", scrape_bcp)],
        "HR": [("croatia", scrape_croatia), ("fina", scrape_fina_csv)],
        "ES": [("spain", scrape_spain)],
        "FR": [("france", scrape_france)],
        "IT": [("italy", scrape_italy)],
        "NL": [("netherlands", scrape_netherlands), ("veilingnotaris", scrape_veilingnotaris)],
    }

    if not args.report_only and not args.analyze:
        sources_to_run = []

        if args.country:
            countries = COUNTRY_SOURCES.keys() if args.country == "all" else [args.country]
            for c in countries:
                sources_to_run.extend(COUNTRY_SOURCES.get(c, []))
        elif args.source == "all":
            for c_sources in COUNTRY_SOURCES.values():
                sources_to_run.extend(c_sources)
        else:
            source_map = {
                "eleiloes": ("eleiloes", scrape_eleiloes),
                "idealista": ("idealista", scrape_idealista),
                "croatia": ("croatia", scrape_croatia),
                "fina": ("fina", scrape_fina_csv),
                "spain": ("spain", scrape_spain),
                "france": ("france", scrape_france),
                "italy": ("italy", scrape_italy),
                "netherlands": ("netherlands", scrape_netherlands),
                "veilingnotaris": ("veilingnotaris", scrape_veilingnotaris),
                "leilosoc": ("leilosoc", scrape_leilosoc),
                "bcp": ("bcp", scrape_bcp),
            }
            if args.source in source_map:
                sources_to_run.append(source_map[args.source])

        for name, func in sources_to_run:
            try:
                func(db, max_price=args.max_price)
            except Exception as e:
                LOG.error(f"Source {name} failed: {e}")

        if any(n == "eleiloes" for n, _ in sources_to_run):
            fetch_eleiloes_details(db, limit=30)

    report_path = generate_report(db, max_price=args.max_price)

    if args.analyze:
        analysis_path = analyze_with_llm(db, max_price=args.max_price, category=args.analyze_category)
        if analysis_path:
            print(f"Analysis: {analysis_path}")

    # Console summary: top 5 properties per country
    print_console_summary(db, max_price=args.max_price)
    print(f"\nFull report: {report_path}")

    db.close()


if __name__ == "__main__":
    main()
