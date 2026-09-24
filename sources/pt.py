"""Portugal: e-leilões, Citius, Portal das Finanças, bank portals, auction houses."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from common import (LOG, find_price, make_listing, make_session, parse_date_dmy,
                    parse_price, safe_url, stable_id, to_number, utcnow_iso)
from db import upsert_listing
from sources import register
from sources._cards import CardSite, listing_id_from_url, scrape_cards

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


def _eleiloes_session():
    # e-leiloes serves an incomplete certificate chain; verification stays off
    # for this host only, and only its warning is silenced.
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    return make_session(verify=False, timeout=30)


@register("eleiloes", "PT")
def scrape_eleiloes(db, max_price: float = 50000, page_size: int = 100, **_):
    """e-leiloes.pt — official judicial e-auctions (REST API)."""
    session = _eleiloes_session()
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
            resp = session.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if offset == 0:
                raise
            LOG.warning(f"e-leiloes API error at offset {offset}: {e}")
            break

        items = data.get("list", [])
        total = data.get("pagination", {}).get("total", 0)
        if not items:
            break

        for item in items:
            upsert_listing(db, _eleiloes_to_listing(item))
            total_scraped += 1

        db.commit()
        offset += page_size
        LOG.info(f"  ... {offset}/{total} processed")
        if offset >= total:
            break
        time.sleep(0.5)

    fetch_eleiloes_details(db, limit=80, max_price=max_price)
    return total_scraped


def _eleiloes_to_listing(item: dict) -> dict:
    eid = str(item["id"])
    return make_listing(
        "eleiloes", eid, "PT",
        title=item.get("titulo", ""),
        tipo=SUBTIPO_MAP.get(item.get("subtipoId"), "outro"),
        price=item.get("valorBase"),
        current_bid=item.get("lanceAtual"),
        min_price=item.get("valorMinimo"),
        district=item.get("moradaDistrito"),
        concelho=item.get("moradaConcelho"),
        freguesia=item.get("moradaFreguesia"),
        url=f"https://e-leiloes.pt/evento/{item.get('referencia') or eid}",
        image_url=f"https://e-leiloes.pt/api/{item['capa']}" if item.get("capa") else None,
        date_end=item.get("dataFim"),
        raw_json=item,
    )


def fetch_eleiloes_details(db, limit: int = 80, max_price: float = 50000):
    """Fetch descriptions/areas for the most interesting e-leiloes listings."""
    session = _eleiloes_session()
    rows = db.execute("""
        SELECT external_id FROM listings
        WHERE source='eleiloes' AND description IS NULL
          AND price <= ?
          AND (current_bid <= ? OR current_bid IS NULL OR current_bid = 0)
          AND date_end > ?
          AND tipo NOT IN ('outro','direitos')
        ORDER BY CASE
            WHEN LOWER(title) LIKE '%moradia%'
              OR LOWER(title) LIKE '%apartamento%'
              OR LOWER(title) LIKE '%casa%'
              OR LOWER(title) LIKE '%vivenda%' THEN 0
            ELSE 1
        END, price DESC
        LIMIT ?
    """, (max_price, max_price, utcnow_iso()[:10], limit)).fetchall()

    count = 0
    for (eid,) in rows:
        try:
            resp = session.get(ELEILOES_DETAIL_API.format(id=eid), timeout=15)
            resp.raise_for_status()
            data = resp.json()
            desc_parts = []
            for v in data.get("verbas", []):
                if v.get("descricao"):
                    desc_parts.append(v["descricao"])
                area = to_number(v.get("area"))
                if area:
                    db.execute("UPDATE listings SET area_m2=? WHERE id=?", (area, f"eleiloes:{eid}"))
            if desc_parts:
                db.execute("UPDATE listings SET description=? WHERE id=?",
                           ("\n".join(desc_parts), f"eleiloes:{eid}"))
            count += 1
            time.sleep(0.3)
        except Exception as e:
            LOG.debug(f"Detail fetch failed for {eid}: {e}")

    db.commit()
    LOG.info(f"Fetched details for {count} e-leiloes listings")
    return count


# ─── Idealista (Selenium) ────────────────────────────────────────────

@register("idealista", "PT", default=False)
def scrape_idealista(db, max_price: float = 50000, **_):
    """idealista.pt — regular listings via Selenium (needs Chrome; not in default runs)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    driver = None
    total_scraped = 0
    try:
        for attempt in ("standalone", "remote"):
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
                else:
                    LOG.info("Trying remote debugging on port 9222...")
                    opts.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
                driver = webdriver.Chrome(options=opts)
                break
            except Exception as e:
                LOG.debug(f"Driver attempt '{attempt}' failed: {e}")
        if driver is None:
            raise RuntimeError("Could not start Chrome. Install chromedriver or start "
                               "Chrome with --remote-debugging-port=9222")

        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })

        base = f"https://www.idealista.pt/comprar-casas/portugal/com-preco-max_{int(max_price)},publicado_ultimas-48-horas/"
        for page in range(1, 15):
            driver.get(base if page == 1 else base + f"pagina-{page}.htm")
            time.sleep(3 + page * 0.5)

            source = driver.page_source.lower()
            if "captcha" in source or "blocked" in source:
                LOG.warning(f"idealista: CAPTCHA/block on page {page}. Stopping.")
                break

            soup = BeautifulSoup(driver.page_source, "html.parser")
            articles = soup.select("article.item") or soup.select(".item-info-container")
            if not articles:
                break
            for art in articles:
                listing = _idealista_parse_article(art)
                if listing:
                    upsert_listing(db, listing)
                    total_scraped += 1
            db.commit()
            LOG.info(f"  idealista page {page}: {len(articles)} items")
            if not soup.select_one("a.icon-arrow-right-after"):
                break
    finally:
        if driver:
            driver.quit()
    return total_scraped


def _idealista_parse_article(art) -> dict | None:
    link_el = art.select_one("a.item-link")
    if not link_el:
        return None
    href = link_el.get("href", "")
    eid = href.strip("/").split("/")[-1] if href else None
    if not eid:
        return None

    title = link_el.get_text(strip=True)
    price_el = art.select_one(".item-price")
    detail_el = art.select_one(".item-detail")
    detail_text = detail_el.get_text(" ", strip=True) if detail_el else ""
    area_match = re.search(r"(\d+)\s*m[²2]", detail_text)
    location_el = art.select_one(".item-detail-char .item-location") or art.select_one(".item-location")
    img_el = art.select_one("img")

    return make_listing(
        "idealista", eid, "PT",
        title=title,
        description=detail_text,
        tipo=_guess_tipo_from_title(title),
        area_m2=float(area_match.group(1)) if area_match else None,
        price=parse_price(price_el.get_text(strip=True)) if price_el else None,
        concelho=location_el.get_text(strip=True) if location_el else None,
        url=href, base_url="https://www.idealista.pt",
        image_url=(img_el.get("src") or img_el.get("data-src")) if img_el else None,
    )


def _guess_tipo_from_title(title: str) -> str:
    t = title.lower()
    if "apartamento" in t or re.search(r"\bt[0-9]\b", t):
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


# ─── leilosoc.com ───────────────────────────────────────────────────

@register("leilosoc", "PT")
def scrape_leilosoc(db, max_price: float = 50000, **_):
    """leilosoc.com — private auction house."""
    session = make_session(timeout=15)
    base = "https://leilosoc.com"
    total_scraped = 0

    for page in range(1, 10):
        try:
            resp = session.get(f"{base}/en/category/5-real-estate/?page={page}")
            resp.raise_for_status()
        except Exception:
            if page == 1:
                raise
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

            title = re.sub(r"^Lot\s+\d+\s*", "", a.get_text(" ", strip=True)).strip()[:120]
            price = find_price(a.parent.get_text(" ")) if a.parent else None
            if price and price > max_price:
                continue

            upsert_listing(db, make_listing(
                "leilosoc", eid, "PT",
                title=title or f"Leilosoc lot {eid}", tipo="imovel",
                price=price, url=href, base_url=base,
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  Leilosoc page {page}: {len(seen)} lots")
        time.sleep(0.5)
    return total_scraped


# ─── BCP Millennium ─────────────────────────────────────────────────

@register("bcp", "PT")
def scrape_bcp(db, max_price: float = 50000, **_):
    """BCP Millennium — bank repossessions."""
    session = make_session(timeout=15)
    base = "https://millenniumimoveis.janeladigital.com"
    resp = session.get(f"{base}/Search.aspx")
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    total_scraped = 0
    for a in soup.select('a[href*="Detail.aspx"][href*="obp=1"]'):
        href = a.get("href", "")
        m = re.search(r"UID=([a-f0-9-]+)", href)
        if not m:
            continue
        eid = m.group(1)
        text = a.get_text(" ", strip=True)

        # "€ 102 000"
        price = None
        pm = re.search(r"€\s*([\d\s]+)", text)
        if pm:
            price = to_number(pm.group(1).replace(" ", ""))
        if price and price > max_price:
            continue

        tipo_m = re.match(r"([\w\s]+?)(?:\s*€)", text)
        title_part = tipo_m.group(1).strip() if tipo_m else "BCP property"
        cm = re.search(r"Concelho\s*:\s*(\S[\w\s]+?)(?:\s*Freguesia|$)", text)
        fm = re.search(r"Freguesia\s*:\s*(\S[\w\s]+?)(?:\s*Im[oó]vel|$)", text)
        concelho = cm.group(1).strip() if cm else None
        freguesia = fm.group(1).strip() if fm else None
        am = re.search(r"(\d[\d\s]*)\s*m(?:2|²|\b)", text)
        location = ", ".join(filter(None, [freguesia, concelho]))

        upsert_listing(db, make_listing(
            "bcp", eid, "PT",
            title=(f"{title_part} - {location}" if location else title_part)[:120],
            description="Bank repossession (BCP Millennium)",
            tipo="imovel",
            area_m2=to_number(am.group(1).replace(" ", "")) if am else None,
            price=price, concelho=concelho, freguesia=freguesia,
            url=href, base_url=base,
        ))
        total_scraped += 1
    return total_scraped


# ─── Citius (court forced sales) ────────────────────────────────────

CITIUS_URL = "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx"


def _citius_extract_location(desc: str) -> tuple[str | None, str | None, str | None]:
    """Return (district, concelho, freguesia) parsed from a Citius description."""
    if not desc:
        return None, None, None
    freguesia = concelho = district = None
    fm = re.search(r"freguesia(?:\s+de)?\s+([^,.;]+)", desc, re.I)
    if fm:
        freguesia = fm.group(1).strip()
    cm = re.search(r"concelho(?:\s+de)?\s+([^,.;]+)", desc, re.I)
    if cm:
        concelho = cm.group(1).strip()
    dm = re.search(r"distrito(?:\s+de)?\s+([^,.;]+)", desc, re.I)
    if dm:
        district = dm.group(1).strip()
    if not concelho:
        sm = re.search(r"sito\s+(?:em|na|no)\s+([^,.;]+)", desc, re.I)
        if sm:
            concelho = sm.group(1).strip()
    return district, concelho, freguesia


def _citius_form_state(session) -> dict:
    s0 = BeautifulSoup(session.get(CITIUS_URL, timeout=15).text, "html.parser")
    return {
        "__VIEWSTATE": s0.select_one("#__VIEWSTATE")["value"],
        "__EVENTVALIDATION": s0.select_one("#__EVENTVALIDATION")["value"],
        "__VIEWSTATEGENERATOR": s0.select_one("#__VIEWSTATEGENERATOR")["value"],
    }


def parse_citius_item(item_text: str, trib_name: str) -> dict:
    """Pull the labelled fields out of one "Tipo de Bem: …" block."""
    # The field regexes use "." — a stray newline would cut a field short.
    item_text = re.sub(r"\s+", " ", item_text)

    def grab(pattern, flags=0):
        m = re.search(pattern, item_text, flags)
        return m.group(1).strip() if m else ""

    price = None
    base_m = re.search(r"Valor Base:\s*([\d\s.,]+)\s*€", item_text, re.I)
    if base_m:
        price = parse_price(base_m.group(1))
    if not price:
        for lab in ("Valor mínimo", "Valor minimo", "Valor da venda"):
            extra = re.search(lab + r":\s*([\d\s.,]+)\s*€", item_text, re.I)
            if extra:
                price = parse_price(extra.group(1))
                if price:
                    break

    intervenientes = []
    for iv_m in re.finditer(
            r"Interveniente:\s*(\w+)\s+Nome:\s*(.+?)(?:Morada:|Interveniente:|$)", item_text):
        intervenientes.append({"role": iv_m.group(1).strip(), "name": iv_m.group(2).strip()})
    morada = grab(r"Morada:\s*(.+?)(?:Interveniente:|$)")
    if morada and intervenientes:
        intervenientes[-1]["morada"] = morada

    date_end = None
    end_m = re.search(
        r"(?:Data(?:\s+da)?(?:\s+venda)?|Até|Prazo)[^:]{0,30}:\s*(\d{2}/\d{2}/\d{4})", item_text, re.I)
    if end_m:
        date_end = parse_date_dmy(end_m.group(1))

    return {
        "price": price,
        "desc": grab(r"Descrição do Bem:\s*(.+?)(?:Processo|$)"),
        "processo": grab(r"Processo:\s*(.+?)(?:Espécie|$)"),
        "modalidade": grab(r"Modalidade:\s*(.+?)(?:Descrição|$)"),
        "estado": grab(r"Estado:\s*(.+?)(?:Valor|Modalidade|$)"),
        "especie": grab(r"Espécie:\s*(.+?)(?:Tipo|$)"),
        "registo": grab(r"Registo:\s*(\S+)"),
        "art_matricial": grab(r"Art\.?\s*Matricial:\s*(\S+)"),
        "entidade_registo": grab(r"Entidade de Registo:\s*(.+?)(?:Art|Registo:|$)"),
        "intervenientes": intervenientes,
        "agente_nome": grab(
            r"(?:Agente de Execução|Solicitador|Encarregado)\s*(?:Nome:)?\s*(.+?)"
            r"(?:Morada:|Contacto:|Email:|Telefone:|$)", re.I),
        "agente_contacto": grab(r"(?:Contacto|Telefone|Tel)\s*:?\s*([\d\s+()-]+)", re.I),
        "agente_email": grab(r"Email:\s*(\S+@\S+)", re.I),
        "date_end": date_end,
        "tribunal": trib_name,
    }


def citius_listing(f: dict, eid: str) -> dict:
    desc = f["desc"]
    district, concelho, freguesia = _citius_extract_location(desc)
    am = re.search(r"(\d[\d\s.]*)\s*m[²2]", desc, re.I)
    desc_parts = [p for p in (
        desc[:500],
        f"Modalidade: {f['modalidade']}" if f["modalidade"] else None,
        f"Estado: {f['estado']}" if f["estado"] else None,
        f"Processo: {f['processo']}" if f["processo"] else None,
        f"Tribunal: {f['tribunal']}",
        f"Registo: {f['registo']} ({f['entidade_registo']})" if f["registo"] else None,
        f"Art. Matricial: {f['art_matricial']}" if f["art_matricial"] else None,
        f"Agente: {f['agente_nome']}" if f["agente_nome"] else None,
        f"Tel: {f['agente_contacto']}" if f["agente_contacto"] else None,
        f"Email: {f['agente_email']}" if f["agente_email"] else None,
    ) if p]
    raw = {k: f[k] for k in (
        "processo", "tribunal", "modalidade", "estado", "especie", "registo",
        "art_matricial", "entidade_registo", "intervenientes", "agente_nome",
        "agente_contacto", "agente_email")}
    return make_listing(
        "citius", eid, "PT",
        title=desc[:120] if desc else f"Citius judicial sale {f['processo']}",
        description=". ".join(desc_parts) if desc_parts else None,
        tipo="imovel",
        area_m2=parse_price(am.group(1)) if am else None,
        price=f["price"],
        min_price=f["price"],
        district=district, concelho=concelho, freguesia=freguesia,
        # Citius has no per-sale page; the search page is the best link there is.
        url=CITIUS_URL,
        date_end=f["date_end"],
        raw_json=raw,
    )


@register("citius", "PT")
def scrape_citius(db, max_price: float = 50000, **_):
    """citius.mj.pt — court forced sales (carta fechada, negociação particular)."""
    session = make_session(timeout=30)
    r = session.get(CITIUS_URL, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    tribunais = [
        (o["value"], o.get_text(strip=True))
        for o in soup.select("#ctl00_ContentPlaceHolder1_ddlTribunais option")
        if o["value"] != "0"
    ]
    if not tribunais:
        raise RuntimeError("Citius: tribunal list not found — page layout changed?")
    LOG.info(f"  Found {len(tribunais)} tribunais to query")

    today = datetime.now().strftime("%d/%m/%Y")
    past = (datetime.now() - timedelta(days=180)).strftime("%d/%m/%Y")
    per_process = defaultdict(int)

    total_scraped = 0
    for i, (trib_id, trib_name) in enumerate(tribunais):
        try:
            state = _citius_form_state(session)
            r2 = session.post(CITIUS_URL, data={
                "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__VIEWSTATEENCRYPTED": "",
                **state,
                "ctl00$ContentPlaceHolder1$ddlTribunais": trib_id,
                "ctl00$ContentPlaceHolder1$txtCalendarDesde": past,
                "ctl00$ContentPlaceHolder1$txtCalendarAte": today,
                "ctl00$ContentPlaceHolder1$chkDatas": "on",
                "ctl00$ContentPlaceHolder1$ddlTiposBem": "1",
                "ctl00$ContentPlaceHolder1$ddlModalidades": "0",
                "ctl00$ContentPlaceHolder1$ddlEstados": "927",
                "ctl00$ContentPlaceHolder1$btnSearch": "Pesquisar",
            })
        except Exception as e:
            LOG.debug(f"  Tribunal {trib_id} error: {e}")
            continue

        dl = BeautifulSoup(r2.text, "html.parser").select_one("[id*='dlVenda']")
        if not dl:
            continue

        trib_count = 0
        for item_html in re.findall(r"Tipo de Bem:(.*?)(?=Tipo de Bem:|$)", str(dl), re.S):
            item_text = BeautifulSoup(item_html, "html.parser").get_text(" ", strip=True)
            f = parse_citius_item(item_text, trib_name)
            if f["price"] and f["price"] > max_price:
                continue

            if f["processo"]:
                eid = re.sub(r"[^A-Za-z0-9]", "", f["processo"])[:40]
            else:
                eid = stable_id(trib_id, f["desc"])
            # One processo can sell several bens; before, they overwrote each
            # other. The first keeps the old ID so existing rows still match.
            per_process[eid] += 1
            if per_process[eid] > 1:
                eid = f"{eid}-{per_process[eid]}"

            upsert_listing(db, citius_listing(f, eid))
            trib_count += 1

        if trib_count:
            total_scraped += trib_count
            db.commit()
        if (i + 1) % 20 == 0:
            LOG.info(f"  Citius: {i+1}/{len(tribunais)} tribunais, {total_scraped} listings so far")
        time.sleep(0.3)
    return total_scraped


# ─── Portal das Finanças (tax seizures) ─────────────────────────────

@register("financas", "PT")
def scrape_financas(db, max_price: float = 50000, **_):
    """vendas.portaldasfinancas.gov.pt — tax-debt seizures."""
    session = make_session()
    base = "https://vendas.portaldasfinancas.gov.pt/bens/rest"
    total_scraped = 0

    for page in range(1, 30):
        params = {"tipoVenda": "VI", "tipoBem": "I", "pagina": page,
                  "tamanhoPagina": 50, "ordenacao": "dataFimDesc"}
        try:
            resp = session.get(f"{base}/listaVendas", params=params)
            if resp.status_code == 404 or not resp.text.strip():
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if page == 1:
                raise
            break

        items = data if isinstance(data, list) else data.get("vendas", data.get("items", []))
        if not items:
            break

        for item in items:
            eid = str(item.get("idVenda", item.get("id", "")) or "")
            if not eid:
                continue
            price = to_number(item.get("valorBase")) or to_number(item.get("valorMinimo")) or 0
            if price > max_price:
                continue
            upsert_listing(db, make_listing(
                "financas", eid, "PT",
                title=str(item.get("descricao") or item.get("designacao") or f"AT tax sale {eid}")[:200],
                description=item.get("observacoes") or item.get("descricaoCompleta"),
                tipo="imovel",
                area_m2=item.get("area"),
                price=price,
                current_bid=item.get("melhorProposta"),
                min_price=item.get("valorMinimo") or price,
                district=item.get("distrito") or item.get("localidade"),
                concelho=item.get("concelho"),
                freguesia=item.get("freguesia"),
                url=f"https://vendas.portaldasfinancas.gov.pt/bens/detalheVenda.action?idVenda={eid}",
                date_end=item.get("dataFim") or item.get("dataLimite"),
                raw_json=json.dumps(item, ensure_ascii=False)[:2000],
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  Finanças page {page}: {len(items)} items (total: {total_scraped})")
        if len(items) < 50:
            break
        time.sleep(1)
    return total_scraped


# ─── Whitestar (NPL bank portfolios) ────────────────────────────────

@register("whitestar", "PT")
def scrape_whitestar(db, max_price: float = 50000, **_):
    """whitestarproperties.pt — NPL portfolios (Novo Banco etc.), with detail pages."""
    session = make_session(timeout=15)
    base = "https://www.whitestarproperties.pt"
    total_scraped = 0
    seen_ids = set()
    failures_in_a_row = 0

    for dist_id in [""] + [str(i) for i in range(1, 21)]:
        try:
            resp = session.post(f"{base}/Assets",
                                data={"District": dist_id, "County": "", "PropertyType": ""},
                                allow_redirects=True)
            resp.raise_for_status()
            failures_in_a_row = 0
        except Exception as e:
            failures_in_a_row += 1
            if failures_in_a_row >= 3:   # the site is down, not one district
                if total_scraped == 0:
                    raise
                LOG.warning(f"Whitestar: giving up after 3 failed districts ({e})")
                break
            LOG.debug(f"Whitestar district {dist_id}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select('a[href*="/Assets/Details/"]'):
            m = re.search(r"/Assets/Details/(\d+)", a.get("href", ""))
            if not m or m.group(1) in seen_ids:
                continue
            eid = m.group(1)
            seen_ids.add(eid)

            card_text = a.get_text(" ", strip=True)
            price = find_price(card_text)
            if price and price > max_price:
                continue

            tipo = ""
            for t in ["Apartamento", "Moradia", "Terreno", "Loja", "Armazém", "Garagem", "Escritório", "Prédio"]:
                if t.lower() in card_text.lower():
                    tipo = t
                    break
            title = card_text[:80].strip() or f"Whitestar #{eid}"
            distrito = concelho = freguesia = description = area = None

            try:
                det = session.get(f"{base}/Assets/Details/{eid}", timeout=15)
                det.raise_for_status()
                dsoup = BeautifulSoup(det.text, "html.parser")
                labels = {}
                for lbl in dsoup.select(".wsi-assetview-label"):
                    val_el = lbl.find_next_sibling(class_="wsi-assetview-value")
                    if val_el:
                        labels[lbl.get_text(strip=True)] = val_el.get_text(strip=True)

                distrito = labels.get("Distrito")
                concelho = labels.get("Concelho")
                freguesia = labels.get("Freguesia")
                area = parse_price(labels.get("Área ( m 2 )") or labels.get("Área (m2)"))
                desc_el = dsoup.select_one(".wsi-assetview-description")
                description = desc_el.get_text(" ", strip=True)[:500] if desc_el else None
                h1 = dsoup.find("h1")
                if h1:
                    title = re.sub(r"\s*\d[\d\s\xa0.,]*€.*", "", h1.get_text(" ", strip=True))[:80] or title
                if not price:
                    price = find_price(dsoup.get_text(" "))
                    if price and price > max_price:
                        continue
            except Exception as e:
                LOG.debug(f"Whitestar detail {eid}: {e}")

            upsert_listing(db, make_listing(
                "whitestar", eid, "PT",
                title=title, description=description,
                tipo=tipo.lower() if tipo else "imóvel",
                area_m2=area, price=price, min_price=price,
                district=distrito, concelho=concelho, freguesia=freguesia,
                url=f"{base}/Assets/Details/{eid}",
            ))
            total_scraped += 1

        db.commit()
        time.sleep(0.3)
    return total_scraped


# ─── Bank portals & auction houses (card grids) ─────────────────────

NOVOBANCO = CardSite(
    source="novobanco", country="PT", base="https://www.novobancoimoveis.pt", path="/imoveis",
    card_selector="div.property-card, div.imovel-card, article.property, div[class*='property'], div[class*='imovel']",
    title_selector="h2,h3,.title,.property-title",
    price_selector=".price,.preco,[class*='price']",
    location_selector=".location,.localizacao,[class*='location']",
    area_selector=".area,[class*='area']",
    params={"preco_max": "{max_price}"}, max_pages=29, min_cards=10,
    description="NPL Novo Banco", price_is_min_price=True,
)
CGD = CardSite(
    source="cgd", country="PT", base="https://www.caixaimobiliario.pt", path="/imoveis",
    card_selector="div.imovel, article.property, div[class*='imovel'], li.property-item",
    title_selector="h2,h3,.titulo,.title",
    price_selector=".preco,.price,[class*='preco'],[class*='price']",
    location_selector=".localizacao,.location,.concelho",
    page_param="pagina", max_pages=19,
    description="Imóvel Caixa Geral de Depósitos", price_is_min_price=True,
)
BPI = CardSite(
    source="bpi", country="PT", base="https://imoveis.bpi.pt", path="/imoveis",
    card_selector="div.imovel, article, div[class*='property'], div[class*='imovel']",
    title_selector="h2,h3,.titulo,.title",
    location_selector=".localizacao,.location,.concelho",
    max_pages=19, description="Imóvel BPI", price_is_min_price=True,
)


def _imobancos_description(card) -> str:
    bank_el = card.select_one(".bank,.banco,[class*='bank']")
    bank = bank_el.get_text(strip=True) if bank_el else "banco"
    return f"Imóvel banco ({bank}) via Imobancos.pt"


IMOBANCOS = CardSite(
    source="imobancos", country="PT", base="https://imobancos.pt", path="/imoveis",
    card_selector="div.property, article.imovel, div[class*='imovel'], div[class*='property']",
    title_selector="h2,h3,.title,.titulo",
    location_selector=".location,.localizacao,.concelho",
    area_selector=".area,[class*='area']",
    params={"preco_max": "{max_price}"}, max_pages=29, delay=0.8,
    description=_imobancos_description, price_is_min_price=True,
)
CENTROLEILOES = CardSite(
    source="centroleiloes", country="PT", base="https://centrodeleiloes.pt", path="/leiloes",
    card_selector="div.lot, div.lote, article, div[class*='lot']",
    title_selector="h2,h3,.title,.lot-title",
    date_selector=".date,.data,[class*='date']",
    params={"categoria": "imoveis"}, max_pages=14, delay=0.8,
    description="Leilão Centro de Leilões",
)


@register("novobanco", "PT")
def scrape_novobanco(db, max_price: float = 100000, **_):
    """novobancoimoveis.pt — Novo Banco NPL portfolio."""
    return scrape_cards(db, NOVOBANCO, max_price)


@register("cgd", "PT")
def scrape_cgd(db, max_price: float = 100000, **_):
    """caixaimobiliario.pt — Caixa Geral de Depósitos repos + leilões."""
    total = scrape_cards(db, CGD, max_price)
    # The leilões page used to be re-fetched once per results page; once is enough.
    try:
        resp = make_session().get(f"{CGD.base}/leiloes")
        resp.raise_for_status()
        for a in BeautifulSoup(resp.text, "html.parser").select("a[href*='/leilao/'], a[href*='/leiloes/']"):
            url = safe_url(a.get("href"), CGD.base)
            if not url:
                continue
            eid = listing_id_from_url(url)
            upsert_listing(db, make_listing(
                "cgd", eid, "PT", id_prefix="cgd_leilao",
                title=a.get_text(strip=True)[:200] or f"CGD leilão {eid}",
                description="Leilão CGD", tipo="imovel", url=url,
            ))
            total += 1
        db.commit()
    except Exception as e:
        LOG.warning(f"CGD leilões page failed: {e}")
    return total


@register("santander", "PT")
def scrape_santander(db, max_price: float = 100000, **_):
    """imoveis.santander.pt — Santander repos (JSON API if exposed, else HTML)."""
    session = make_session()
    base = "https://imoveis.santander.pt"
    resp = session.get(f"{base}/imoveis")
    resp.raise_for_status()
    total = 0

    for api_url in (f"{base}/api/imoveis", f"{base}/api/properties", f"{base}/imoveis/search"):
        try:
            r = session.get(api_url, params={"pageSize": 200}, timeout=15)
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                continue
            data = r.json()
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("items", data.get("results", []))
        for item in items:
            price = to_number(item.get("price")) or to_number(item.get("preco")) or 0
            if price > max_price:
                continue
            eid = str(item.get("id") or item.get("referencia") or stable_id(json.dumps(item, sort_keys=True, default=str)))
            upsert_listing(db, make_listing(
                "santander", eid, "PT",
                title=str(item.get("title") or item.get("titulo") or f"Santander #{eid}")[:200],
                description=item.get("description") or "Imóvel Santander Portugal",
                tipo=item.get("type") or item.get("tipo") or "imovel",
                area_m2=item.get("area") or item.get("area_m2"),
                price=price, min_price=price,
                district=item.get("distrito") or item.get("district"),
                concelho=item.get("concelho") or item.get("city"),
                freguesia=item.get("freguesia"),
                url=item.get("url") or f"{base}/imovel/{eid}", base_url=base,
                image_url=item.get("image") or item.get("foto"),
                raw_json=json.dumps(item, ensure_ascii=False)[:2000],
            ))
            total += 1
        db.commit()
        break

    if total == 0:
        soup = BeautifulSoup(resp.text, "html.parser")
        seen = set()
        for card in soup.select("div[class*='property'], article, div[class*='imovel']"):
            link = card.select_one("a[href]")
            url = safe_url(link.get("href"), base) if link else None
            if not url:
                continue
            eid = listing_id_from_url(url)
            if eid in seen:
                continue
            seen.add(eid)
            price = find_price(card.get_text(" "))
            if price and price > max_price:
                continue
            upsert_listing(db, make_listing(
                "santander", eid, "PT",
                title=card.get_text(" ", strip=True)[:120], description="Imóvel Santander",
                tipo="imovel", price=price, min_price=price, url=url,
            ))
            total += 1
        db.commit()
    return total


@register("bpi", "PT")
def scrape_bpi(db, max_price: float = 100000, **_):
    """imoveis.bpi.pt — BPI bank repos."""
    return scrape_cards(db, BPI, max_price)


@register("imobancos", "PT")
def scrape_imobancos(db, max_price: float = 100000, **_):
    """imobancos.pt — aggregator of repos from all Portuguese banks."""
    return scrape_cards(db, IMOBANCOS, max_price)


@register("centroleiloes", "PT")
def scrape_centroleiloes(db, max_price: float = 100000, **_):
    """centrodeleiloes.pt — bank auction house."""
    return scrape_cards(db, CENTROLEILOES, max_price)


@register("bidleiloeira", "PT")
def scrape_bidleiloeira(db, max_price: float = 100000, **_):
    """bidleiloeira.pt — online auction house."""
    session = make_session()
    base = "https://www.bidleiloeira.pt"
    resp = session.get(f"{base}/leiloes")
    resp.raise_for_status()
    total = 0
    seen = set()
    for a in BeautifulSoup(resp.text, "html.parser").select(
            "a[href*='/leilao/'], a[href*='/lot/'], a[href*='/lote/']"):
        url = safe_url(a.get("href"), base)
        if not url or not re.search(r"/\d+", url):
            continue
        eid = listing_id_from_url(url)
        if eid in seen:
            continue
        seen.add(eid)
        price = find_price(a.parent.get_text(" ")) if a.parent else None
        if price and price > max_price:
            continue
        upsert_listing(db, make_listing(
            "bidleiloeira", eid, "PT",
            title=a.get_text(" ", strip=True)[:200] or f"BidLeiloeira #{eid}",
            description="Leilão Bid Leiloeira", tipo="imovel", price=price, url=url,
        ))
        total += 1
    return total
