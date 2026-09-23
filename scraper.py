"""
Auction Scanner — scrapes Portuguese auction/real estate platforms.
Stores results in SQLite, generates investment reports.

Supported platforms:
  - e-leiloes.pt (REST API, no auth needed)
  - idealista.pt (HTML scraping)
  - Portal Finanças (requires manual session cookie)

Usage:
  python scraper.py                  # scrape all, generate report
  python scraper.py --source eleiloes
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

import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "auctions.db")
LOG = logging.getLogger("auction-scanner")

# ─── Database ────────────────────────────────────────────────────────

def init_db(db: sqlite3.Connection):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id          TEXT PRIMARY KEY,  -- source:external_id
            source      TEXT NOT NULL,     -- eleiloes, idealista, financas, bank_*
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
            INSERT INTO listings (id, source, external_id, title, description, tipo,
                area_m2, price, current_bid, min_price, district, concelho, freguesia,
                url, image_url, date_end, raw_json, first_seen, last_seen, is_new)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (
            row["id"], row["source"], row["external_id"],
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

            from bs4 import BeautifulSoup
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
        import re
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

    for cat in categories:
        categories[cat].sort(key=lambda x: (x[1] or 999, x[0]["price"] or 999))

    lines = [
        f"# Investment Scanner Report",
        f"**Generated**: {now_str}  ",
        f"**Budget**: €{max_price:,.0f}  ",
        "",
    ]

    section_names = {
        "imoveis": "Imóveis (Real Estate)",
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

        lines.append("| # | Title | VB | Bid | Bid/VB | Location | Ends |")
        lines.append("|---|-------|-----|-----|--------|----------|------|")

        show = cat_items[:30] if cat == "imoveis" else cat_items[:15]
        for i, (item, ratio) in enumerate(show, 1):
            price_str = f"€{item['price']:,.0f}" if item['price'] else "?"
            bid_str = f"€{item['current_bid']:,.0f}" if item['current_bid'] else "-"
            ratio_str = f"{ratio:.0%}" if ratio else "-"
            loc = ", ".join(filter(None, [item["concelho"], item["district"]]))
            ends = item["date_end"][:10] if item["date_end"] else "-"
            title_short = (item["title"] or "?")[:55]
            url = item["url"] or ""
            lines.append(
                f"| {i} | [{title_short}]({url}) | {price_str} | {bid_str} | {ratio_str} | {loc} | {ends} |"
            )

        lines.append("")

    # LLM-ready compact summary for the top imóveis (saves tokens)
    lines.append("## Top Imóveis — Compact (for LLM analysis)")
    lines.append("```json")
    compact = []
    for item, ratio in categories["imoveis"][:15]:
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




def main():
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    parser = argparse.ArgumentParser(description="Auction Scanner")
    parser.add_argument("--source", choices=["eleiloes", "idealista", "all"], default="all")
    parser.add_argument("--max-price", type=float, default=50000)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--analyze", action="store_true", help="Run LLM analysis on top listings")
    parser.add_argument("--analyze-category", default="imoveis", choices=["imoveis", "ouro_joias", "outros"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    db = sqlite3.connect(DB_PATH)
    init_db(db)

    if not args.report_only and not args.analyze:
        if args.source in ("eleiloes", "all"):
            scrape_eleiloes(db, max_price=args.max_price)
            fetch_eleiloes_details(db, limit=30)
        if args.source in ("idealista", "all"):
            scrape_idealista(db, max_price=args.max_price)

    report_path = generate_report(db, max_price=args.max_price)
    print(f"Report: {report_path}")

    if args.analyze:
        analysis_path = analyze_with_llm(db, max_price=args.max_price, category=args.analyze_category)
        if analysis_path:
            print(f"Analysis: {analysis_path}")

    db.close()


if __name__ == "__main__":
    main()
