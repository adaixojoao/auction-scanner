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

from common import (LOG, find_area, find_price, make_listing, make_session, normalize, parse_date_dmy,
                    parse_price, safe_url, stable_id, to_number, utcnow_iso)
from db import upsert_listing
from sources import SourceUnavailable, register
from sources._cards import CardSite, scrape_cards

# ─── e-leiloes.pt ───────────────────────────────────────────────────

ELEILOES_API = "https://e-leiloes.pt/api/Eventos/"
ELEILOES_DETAIL_API = "https://e-leiloes.pt/api/Eventos/{id}"

# e-leilões numbers subtypes within a type, so a subtype only means something
# together with tipoId. Read from 900 live lots (Sept 2026): tipoId 1 is
# property; 2 vehicles; 3 and 5 equipment; 4 furniture and household goods;
# 6 company shares and inheritance rights.
TIPO_MAP = {
    1: "imovel", 2: "veiculo", 3: "equipamento", 4: "mobiliario", 5: "equipamento", 6: "direitos",
}
# Property subtypes (tipoId 1). 2 is houses: it used to be read as "loja/escritorio",
# which marked houses without "moradia" in the title as not a home.
SUBTIPO_MAP = {
    1: "apartamento", 2: "moradia", 3: "garagem", 4: "outro_imovel",
    5: "terreno_urbano", 6: "loja", 7: "outro_imovel", 8: "outro_imovel",
    27: "terreno_rustico", 28: "outro_imovel",
    # seen in older data
    21: "moradia", 22: "apartamento", 23: "loja", 24: "garagem", 25: "armazem", 26: "terreno_urbano",
}


def eleiloes_tipo(item: dict) -> str:
    tipo_id = item.get("tipoId")
    if tipo_id not in (None, 1):
        return TIPO_MAP.get(tipo_id, "outro")
    return SUBTIPO_MAP.get(item.get("subtipoId"), "outro_imovel" if tipo_id == 1 else "outro")


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
            # Only property (tipoId 1): cars, machines, furniture, company shares and
            # inheritance rights are not what the owner buys, so they are not kept.
            # Rows saved before stay (listings are never deleted) and go stale.
            if item.get("tipoId") not in (None, 1):
                continue
            upsert_listing(db, _keep_details(db, _eleiloes_to_listing(item)))
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
        tipo=eleiloes_tipo(item),
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


# Fields the detail pass adds to raw_json. The next search-page scrape replaces
# raw_json with the thin list item, so these are carried over (_keep_details).
# Bumped when the detail pass reads more: sales read by an older pass are read again.
DETAIL_VERSION = 3
ELEILOES_DETAIL_KEYS = ("processo", "tribunal", "agente_nome", "agente_email",
                        "valor_abertura", "morada", "lat", "lon", "reg_district", "reg_concelho",
                        "reg_freguesia", "detail_checked")
_NOT_PROPERTY = ("outro", "direitos", "veiculo", "equipamento", "mobiliario")


def _keep_details(db, row: dict) -> dict:
    old = db.execute("SELECT raw_json FROM listings WHERE id = ?", (row["id"],)).fetchone()
    if not old or not old[0] or '"detail_checked"' not in old[0]:
        return row
    try:
        kept = {k: v for k, v in json.loads(old[0]).items() if k in ELEILOES_DETAIL_KEYS}
        row["raw_json"] = json.dumps({**json.loads(row["raw_json"] or "{}"), **kept}, ensure_ascii=False)
        for field in ("district", "concelho", "freguesia"):     # the land registry beats the address field
            if kept.get(f"reg_{field}"):
                row[field] = kept[f"reg_{field}"]
    except (TypeError, ValueError):
        pass
    return row


def _registry_place(item: dict) -> dict:
    """District, concelho and freguesia from the land-registry entry
    ("14 - Santarém" → "Santarém"), which says where the property is."""
    entries = item.get("descPredial") or []
    first = entries[0] if entries and isinstance(entries[0], dict) else {}
    clean = lambda v: re.sub(r"^\s*\d+\s*-\s*", "", str(v or "")).strip() or None  # noqa: E731
    return {"district": clean(first.get("distritoDesc")), "concelho": clean(first.get("concelhoDesc")),
            "freguesia": clean(first.get("freguesiaDesc"))}


def eleiloes_detail_fields(item: dict) -> tuple[dict, dict]:
    """GET /api/Eventos/<referencia> → (listing fields, extra raw_json keys).
    Leaves out the executados: the people whose property is sold."""
    place = _registry_place(item)
    description = (item.get("descricao") or "").strip() or None
    area = to_number(item.get("areaTotal")) or to_number(item.get("areaUtilPrivativa")) or None
    written = find_area(description)
    # The area field is sometimes off by a factor of 100 ("1 695 000 m²" for a plot
    # described as "cerca de 16.950m2"): when the two disagree that much, the
    # description wins.
    if area and written and not (1 / 20 < area / written < 20):
        area = written
    fields = {
        "description": description,
        "area_m2": area or written,
        "freguesia": place["freguesia"] or item.get("moradaFreguesia") or None,
        "concelho": place["concelho"],
        "district": place["district"],
    }
    extra = {
        "processo": item.get("processoNumero") or None,
        "tribunal": item.get("processoTribunal") or item.get("processoComarca") or None,
        "agente_nome": item.get("gestorNome") or None,
        "agente_email": (item.get("gestorEmail") or "").strip() or None,
        "valor_abertura": to_number(item.get("valorAbertura")),
        "morada": " ".join(str(item.get(k) or "").strip() for k in ("morada", "moradaNumero", "moradaAndar")).strip()
                  or None,
        "lat": to_number(item.get("coordenadasLAT")) or None,
        "lon": to_number(item.get("coordenadasLON")) or None,
        "reg_district": place["district"], "reg_concelho": place["concelho"],
        "reg_freguesia": place["freguesia"],
        "detail_checked": DETAIL_VERSION,
    }
    return fields, {k: v for k, v in extra.items() if v is not None}


def fetch_eleiloes_details(db, limit: int = 80, max_price: float = 50000):
    """Description, area, court and the agente de execução's contact for the
    most interesting e-leilões property sales, once each.

    The detail API takes the sale's reference ("NP1229582026"), not its numeric
    id: asked by id it answers "Evento não disponível", which is why no detail
    had ever been read."""
    session = _eleiloes_session()
    rows = db.execute(f"""
        SELECT id, raw_json FROM listings
        WHERE source='eleiloes'
          AND (raw_json IS NULL OR raw_json NOT LIKE '%"detail_checked": ' || ? || '%')
          AND price <= ?
          AND (current_bid <= ? OR current_bid IS NULL OR current_bid = 0)
          AND date_end > ?
          AND tipo NOT IN ({",".join("?" * len(_NOT_PROPERTY))})
        ORDER BY CASE
            WHEN LOWER(title) LIKE '%moradia%'
              OR LOWER(title) LIKE '%apartamento%'
              OR LOWER(title) LIKE '%casa%'
              OR LOWER(title) LIKE '%vivenda%' THEN 0
            ELSE 1
        END, price DESC
        LIMIT ?
    """, (DETAIL_VERSION, max_price, max_price, utcnow_iso()[:10], *_NOT_PROPERTY, limit)).fetchall()

    count = 0
    for listing_id, raw_text in rows:
        try:
            raw = json.loads(raw_text or "{}")
            ref = raw.get("referencia") or listing_id.split(":", 1)[1]
            resp = session.get(ELEILOES_DETAIL_API.format(id=ref), timeout=15)
            resp.raise_for_status()
            item = resp.json().get("item")
            if not item:
                raw["detail_checked"] = DETAIL_VERSION  # gone or withdrawn: do not ask again
                db.execute("UPDATE listings SET raw_json=? WHERE id=?",
                           (json.dumps(raw, ensure_ascii=False), listing_id))
                continue
            fields, extra = eleiloes_detail_fields(item)
            sets = {k: v for k, v in fields.items() if v is not None}
            sets["raw_json"] = json.dumps({**raw, **extra}, ensure_ascii=False)
            db.execute(f"UPDATE listings SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                       (*sets.values(), listing_id))
            count += 1
            time.sleep(0.3)
        except Exception as e:
            LOG.debug(f"Detail fetch failed for {listing_id}: {e}")
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
    raw.update({k: f[k] for k in ("html_id", "detail_checked") if f.get(k)})
    return make_listing(
        "citius", eid, "PT",
        title=desc[:120] if desc else f"Citius judicial sale {f['processo']}",
        description=". ".join(desc_parts) if desc_parts else None,
        tipo="imovel",
        # "área coberta de 30 m2 e descoberta com 321 m2" → 30; "4,6905 ha" → 46905
        area_m2=find_area(desc),
        price=f["price"],
        min_price=f["price"],
        district=district, concelho=concelho, freguesia=freguesia,
        # Citius has no per-sale page; the search page is the best link there is.
        url=CITIUS_URL,
        date_end=f["date_end"],
        raw_json=raw,
    )


CITIUS_DETAILS = "https://www.citius.mj.pt/portal/consultas/ConsultasVenda.aspx/GetHtmlDetails"
CITIUS_DETAILS_PER_SCAN = 150
_CITIUS_NEXT = "ctl00$ContentPlaceHolder1$Pager1$btnNextPage"


def citius_blocks(html: str) -> list[str]:
    dl = BeautifulSoup(html, "html.parser").select_one("[id*='dlVenda']")
    return re.findall(r"Tipo de Bem:(.*?)(?=Tipo de Bem:|$)", str(dl), re.S) if dl else []


def _citius_pages(session, first_html: str, form: dict, max_pages: int = 30):
    """The first results page, then each next page (the pager is an image button)."""
    html = first_html
    for _ in range(max_pages):
        yield html
        nxt = BeautifulSoup(html, "html.parser").select_one("#ctl00_ContentPlaceHolder1_Pager1_btnNextPage")
        if nxt is None or nxt.has_attr("disabled"):
            return
        state = {i["name"]: i.get("value", "") for i in
                 BeautifulSoup(html, "html.parser").select("input[type=hidden][name]")}
        try:
            resp = session.post(CITIUS_URL, data={**state, "__EVENTTARGET": "", "__EVENTARGUMENT": "", **form,
                                                  f"{_CITIUS_NEXT}.x": "5", f"{_CITIUS_NEXT}.y": "5"})
            resp.raise_for_status()
        except Exception as e:
            LOG.debug(f"  Citius next page failed: {e}")
            return
        html = resp.text


def citius_detail_text(html: str) -> str:
    """A sale's "ver mais" details as text, without the Intervenientes (the
    people whose property is sold and their addresses)."""
    text = re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True))
    return text.split("Intervenientes")[0].strip()


def citius_full_description(detail: str) -> str | None:
    m = re.search(r"Descrição do Bem:\s*(.+?)(?:\s+Art\.\s*Matricial:|\s+Registo:|\s+Entidade de Registo:|$)", detail)
    return m.group(1).strip() if m else None


def _keep_citius_details(db, listing_id: str, f: dict) -> dict:
    """The list shows a cut description ("… com uma ..."); once the full one has
    been read (fetch_citius_details) it is used on every later scan."""
    old = db.execute("SELECT raw_json FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if old and old[0] and '"detail_checked"' in old[0]:
        try:
            raw = json.loads(old[0])
        except ValueError:
            return f
        f = {**f, "detail_checked": raw.get("detail_checked"), "html_id": f.get("html_id") or raw.get("html_id")}
        if raw.get("descricao_completa"):
            f["desc"] = raw["descricao_completa"]
            f["descricao_completa"] = raw["descricao_completa"]
    return f


def fetch_citius_details(db, session, limit: int = CITIUS_DETAILS_PER_SCAN) -> int:
    """Read the full description of sales not read yet (one request each)."""
    rows = db.execute("""
        SELECT id, raw_json FROM listings WHERE source = 'citius'
          AND raw_json LIKE '%"html_id"%' AND raw_json NOT LIKE '%"detail_checked"%'
        ORDER BY last_seen DESC LIMIT ?""", (limit,)).fetchall()
    done = 0
    for listing_id, raw_text in rows:
        raw = json.loads(raw_text)
        try:
            resp = session.post(CITIUS_DETAILS, data="{htmlId:%s}" % int(raw["html_id"]),
                                headers={"Content-Type": "application/json; charset=utf-8"})
            resp.raise_for_status()
            detail = citius_detail_text(resp.json().get("d"))
        except Exception as e:
            LOG.debug(f"Citius details {listing_id}: {e}")      # tried again next scan
            continue
        full = citius_full_description(detail)
        raw["detail_checked"] = 1
        sets = {}
        if full:
            raw["descricao_completa"] = full[:3000]
            district, concelho, freguesia = _citius_extract_location(full)
            sets = {"title": full[:120], "area_m2": find_area(full),
                    "district": district, "concelho": concelho, "freguesia": freguesia}
            old_desc = db.execute("SELECT description FROM listings WHERE id = ?", (listing_id,)).fetchone()[0] or ""
            rest = old_desc.split(". Modalidade:", 1)
            sets["description"] = full[:3000] + (". Modalidade:" + rest[1] if len(rest) > 1 else "")
        sets = {k: v for k, v in sets.items() if v}
        sets["raw_json"] = json.dumps(raw, ensure_ascii=False)
        db.execute(f"UPDATE listings SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                   (*sets.values(), listing_id))
        done += 1
        time.sleep(0.2)
    db.commit()
    if rows:
        LOG.info(f"Citius: full details read for {done} of {len(rows)} sales")
    return done


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

        form = {k: v for k, v in (
            ("ctl00$ContentPlaceHolder1$ddlTribunais", trib_id),
            ("ctl00$ContentPlaceHolder1$txtCalendarDesde", past),
            ("ctl00$ContentPlaceHolder1$txtCalendarAte", today),
            ("ctl00$ContentPlaceHolder1$chkDatas", "on"),
            ("ctl00$ContentPlaceHolder1$ddlTiposBem", "1"),
            ("ctl00$ContentPlaceHolder1$ddlModalidades", "0"),
            ("ctl00$ContentPlaceHolder1$ddlEstados", "927"))}
        trib_count = 0
        # Results come 10 to a page; only the first page used to be read, so a
        # court with 35 sales showed 10.
        for page_html in _citius_pages(session, r2.text, form):
            for item_html in citius_blocks(page_html):
                item_text = BeautifulSoup(item_html, "html.parser").get_text(" ", strip=True)
                f = parse_citius_item(item_text, trib_name)
                hid = re.search(r"Viewer\.Abrir\(this,\s*(\d+)", item_html)
                f["html_id"] = hid.group(1) if hid else None

                if f["processo"]:
                    eid = re.sub(r"[^A-Za-z0-9]", "", f["processo"])[:40]
                else:
                    eid = stable_id(trib_id, f["desc"])
                # One processo can sell several bens; before, they overwrote each
                # other. The first keeps the old ID so existing rows still match.
                # Counted before the budget filter, so an ID does not depend on the budget.
                per_process[eid] += 1
                if per_process[eid] > 1:
                    eid = f"{eid}-{per_process[eid]}"
                if f["price"] and f["price"] > max_price:
                    continue

                upsert_listing(db, citius_listing(_keep_citius_details(db, f"citius:{eid}", f), eid))
                trib_count += 1

        if trib_count:
            total_scraped += trib_count
            db.commit()
        if (i + 1) % 20 == 0:
            LOG.info(f"  Citius: {i+1}/{len(tribunais)} tribunais, {total_scraped} listings so far")
        time.sleep(0.3)
    fetch_citius_details(db, session)
    return total_scraped


# ─── Portal das Finanças (tax seizures) ─────────────────────────────

@register("financas", "PT", default=False)
def scrape_financas(db, max_price: float = 50000, **_):
    """Portal das Finanças tax-debt seizures — needs a login, so not scanned."""
    # The public REST list (vendas.portaldasfinancas.gov.pt/bens/rest) is gone
    # (404); "Venda de bens" (/vendasat) now redirects to the acesso.gov.pt
    # login. Signing in needs the owner's NIF or Cartão de Cidadão, which a
    # scraper must not hold, so this source stays out of the default scan.
    raise SourceUnavailable(
        "Portal das Finanças now shows its sales only after signing in on acesso.gov.pt; "
        "check them there by hand")


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


# ─── Bank portals & auction houses ──────────────────────────────────

@register("novobanco", "PT", default=False)
def scrape_novobanco(db, max_price: float = 100000, **_):
    """Novo Banco repossessions — its portal closed, so not scanned."""
    raise SourceUnavailable(
        "novobancoimoveis.pt no longer exists (the domain is gone) and no public "
        "Novo Banco property list was found to replace it")


# Caixa Imobiliário is an Angular app. Its listings come from CGD's API, which
# wants the site's public API key plus a 10-minute token the site hands out.
CGD_SITE = "https://www.caixaimobiliario.pt"
CGD_API = "https://api.cgd.pt/cross-channel/drupal-cms/v2/rest/pt/rest/pesquisa-imoveis"
CGD_PAGE_SIZE = 50
_CGD_KEY_RE = re.compile(r'apigee:\{[^}]*clientId:"([^"]+)"')


def _cgd_api_key(session) -> str:
    """The API key the site's own scripts send. Read at run time, not stored:
    it is the site's, and it changes when the site is redeployed."""
    home = session.get(f"{CGD_SITE}/pt")
    home.raise_for_status()
    main = re.search(r'src="(main-[\w-]+\.js)"', home.text)
    if not main:
        raise SourceUnavailable("Caixa Imobiliário changed its page (main script not found)")
    js = session.get(f"{CGD_SITE}/{main.group(1)}").text
    m = _CGD_KEY_RE.search(js)
    for chunk in dict.fromkeys(re.findall(r"chunk-[A-Z0-9]+\.js", js)):
        if m:
            break
        m = _CGD_KEY_RE.search(session.get(f"{CGD_SITE}/{chunk}").text)
    if not m:
        raise SourceUnavailable("Caixa Imobiliário changed its page (API key not found)")
    return m.group(1)


def ruin_note(energy_rating) -> str:
    """Caixa and Imobancos put "Ruína" in the energy-rating field of ruins; say it in
    the description, where the score reads it (the owner does not want ruins)."""
    return " Estado: em ruína (classe energética: Ruína)." if normalize(energy_rating).startswith("ruin") else ""


def cgd_listing(item: dict, max_price: float) -> dict | None:
    """One pesquisa-imoveis result → listing row (None when over budget or not for sale)."""
    if item.get("field_objective") not in (None, "", "Comprar"):
        return None
    url = safe_url(item.get("field_url"), CGD_SITE)
    eid = url.rstrip("/").rsplit("/", 1)[-1] if url else str(item.get("nid") or "")
    if not eid:
        return None
    price = to_number(item.get("field_preco_venda_int")) or None   # 0 means "no price"
    if price and price > max_price:
        return None
    # "Alcoentre, Azambuja, Lisboa" is freguesia, concelho, distrito
    place = [p.strip() for p in (item.get("field_localizacao") or "").split(",") if p.strip()]
    place = [None] * (3 - len(place)) + place[-3:]
    title = " ".join((item.get("field_titulo") or "").split()).strip(" /") or f"Caixa #{eid}"
    notes = [item.get("field_morada_completa"), item.get("field_tarja_tarja")]
    return make_listing(
        "cgd", eid, "PT",
        title=title[:200],
        description=(" · ".join(n.strip() for n in notes if n and n.strip()) or "Imóvel Caixa Geral de Depósitos")
                    + ruin_note(item.get("field_cls")),
        tipo=title.split()[0].lower(),
        area_m2=to_number(item.get("field_area_bruta_int")),
        price=price, min_price=price,
        freguesia=place[0], concelho=place[1], district=place[2],
        url=url,
        raw_json={k: v for k, v in item.items() if k != "field_media_image"},
    )


@register("cgd", "PT")
def scrape_cgd(db, max_price: float = 100000, **_):
    """caixaimobiliario.pt — Caixa Geral de Depósitos properties for sale."""
    session = make_session()
    key = _cgd_api_key(session)
    token = session.post(f"{CGD_SITE}/bff/api/v1/auth/token")
    token.raise_for_status()
    headers = {"x-api-key": key, "x-authorization": f"Bearer {token.json()['access_token']}",
               "Accept": "application/json"}
    total = 0
    seen = set()
    for page in range(40):                        # Drupal pages count from 0
        resp = session.get(CGD_API, headers=headers, params={
            "field_country_op": "empty", "items_per_page": CGD_PAGE_SIZE, "page": page})
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        for item in results:
            row = cgd_listing(item, max_price)
            if row and row["external_id"] not in seen:
                seen.add(row["external_id"])
                upsert_listing(db, row)
                total += 1
        db.commit()
        pages = (data.get("pager") or {}).get("total_pages") or 0
        if not results or page + 1 >= pages:
            break
        time.sleep(0.5)
    return total


SANTANDER = "https://imoveis.santander.pt"


def parse_santander_page(html: str, max_price: float) -> list[dict]:
    """Cards of one imoveis.santander.pt results page → listing rows."""
    rows = []
    for card in BeautifulSoup(html, "html.parser").select("div.prop"):
        link = card.select_one("a[href*='/detalhe/']")
        url = safe_url(link.get("href"), SANTANDER) if link else None
        m = re.search(r"/detalhe/(\d+)", url or "")
        if not m:
            continue
        price_el = card.select_one(".prop-tag-sub")
        price = parse_price(price_el.get_text(" ", strip=True)) if price_el else None
        if price and price > max_price:
            continue
        tag = card.select_one(".prop-tag")
        tipo = tag.get_text(" ", strip=True).split("|")[0].strip() if tag else ""
        concelho_el = card.select_one(".prop-titleFirst")
        freguesia_el = card.select_one(".prop-titleSecond")
        concelho = concelho_el.get_text(" ", strip=True) if concelho_el else None
        freguesia = freguesia_el.get_text(" ", strip=True) if freguesia_el else None
        district = (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("distrito") or [None])[0]
        desc = card.select_one(".prop-description")
        rows.append(make_listing(
            "santander", m.group(1), "PT",
            title=", ".join(p for p in (tipo, freguesia or concelho) if p) or f"Santander #{m.group(1)}",
            description=desc.get_text(" ", strip=True) if desc else "Imóvel Santander",
            tipo=tipo.lower() or "imovel",
            price=price, min_price=price,
            district=district.replace("-", " ").title() if district else None,
            concelho=concelho, freguesia=freguesia, url=url,
        ))
    return rows


@register("santander", "PT")
def scrape_santander(db, max_price: float = 100000, **_):
    """imoveis.santander.pt — Santander properties for sale."""
    session = make_session()
    total = 0
    seen = set()
    for page in range(1, 31):
        resp = session.get(f"{SANTANDER}/imoveis/{page}/0/-1/-1/-1/-1/-1/-1/-1/-1/-1/-1/-/-1/-1/")
        if page > 1 and resp.status_code >= 400:
            LOG.warning(f"santander: page {page} failed, keeping earlier pages")
            break
        resp.raise_for_status()
        rows = parse_santander_page(resp.text, max_price=float("inf"))
        if not rows:
            break                                 # past the last page
        for row in rows:
            if row["external_id"] in seen or (row["price"] and row["price"] > max_price):
                continue
            seen.add(row["external_id"])
            upsert_listing(db, row)
            total += 1
        db.commit()
        time.sleep(0.8)
    return total


BPI = CardSite(
    source="bpi", country="PT", base="https://bpiexpressoimobiliario.net", path="/imoveis-bpi",
    card_selector="a.announce-details",
    title_selector=".announce-title",
    price_selector=".announce-price",
    location_selector=".announce-location",
    page_param=None, id_pattern=r"/a(\d+)$",
    description="Imóvel BPI (BPI Expresso Imobiliário)", price_is_min_price=True,
)


@register("bpi", "PT")
def scrape_bpi(db, max_price: float = 100000, **_):
    """bpiexpressoimobiliario.net — BPI's own properties for sale."""
    return scrape_cards(db, BPI, max_price)


IMOBANCOS_API = "https://imobancos.pt/api/properties/fetchProperties"


def imobancos_listing(item: dict, max_price: float) -> dict | None:
    """One Imobancos API hit → listing row (None when over budget, sold or for rent)."""
    if item.get("available") is False or item.get("prop_purpose") not in (None, "", "Comprar"):
        return None
    price = to_number(item.get("prop_price")) or None              # 0 means "no price"
    if price and price > max_price:
        return None
    eid = str(item.get("id") or "")
    if not eid:
        return None
    bank = item.get("site_name") or "banco"
    text = (item.get("prop_description") or "").strip()
    photos = item.get("photos") or []
    return make_listing(
        "imobancos", eid, "PT",
        title=(item.get("prop_title") or item.get("prop_name") or f"Imobancos #{eid}")[:200],
        description=f"Imóvel banco ({bank}) via Imobancos.pt. {text[:600]}".strip()
                    + ruin_note(item.get("prop_energy_rating")),
        tipo=(item.get("prop_type") or "imovel").lower(),
        area_m2=to_number(item.get("prop_area")),
        price=price, min_price=price,
        district=item.get("prop_district"), concelho=item.get("prop_county"),
        freguesia=item.get("prop_parish"),
        url=f"https://imobancos.pt/imoveis/{eid}",
        image_url=photos[0].get("photo_url") if photos and isinstance(photos[0], dict) else None,
        raw_json={k: v for k, v in item.items() if k not in ("photos", "prop_description")},
    )


# Imobancos only gathers listings: each of its pages has a "Ver anúncio original"
# link to the bank's own page. That is the link a listing should open, so it is
# read once per listing and kept (the API does not include it).
IMOBANCOS_ORIGINALS_PER_SCAN = 200
_ORIGINAL_LINK_RE = re.compile(r"an[uú]ncio\s+original", re.I)


def imobancos_original_url(html: str) -> str | None:
    """The bank's own page, from an Imobancos listing page."""
    for a in BeautifulSoup(html, "html.parser").select("a[href]"):
        if _ORIGINAL_LINK_RE.search(a.get_text(" ", strip=True)):
            url = safe_url(a.get("href"), "https://imobancos.pt")
            if url and "imobancos.pt" not in urllib.parse.urlsplit(url).netloc:
                return url
    return None


def _keep_original(db, row: dict) -> dict:
    """A rescan must not put the Imobancos page back in place of the bank's page."""
    old = db.execute("SELECT raw_json FROM listings WHERE id = ?", (row["id"],)).fetchone()
    if not old or not old[0] or '"original_checked"' not in old[0]:
        return row
    try:
        kept = {k: v for k, v in json.loads(old[0]).items() if k in ("original_url", "original_checked")}
    except (TypeError, ValueError):
        return row
    raw = {**json.loads(row["raw_json"] or "{}"), **kept}
    row["raw_json"] = json.dumps(raw, ensure_ascii=False)
    if kept.get("original_url"):
        row["url"] = kept["original_url"]
    return row


def resolve_imobancos_originals(db, session, limit: int = IMOBANCOS_ORIGINALS_PER_SCAN) -> int:
    rows = db.execute("""
        SELECT id, raw_json FROM listings WHERE source='imobancos'
          AND (raw_json IS NULL OR raw_json NOT LIKE '%"original_checked"%')
        ORDER BY last_seen DESC LIMIT ?""", (limit,)).fetchall()
    found = 0
    for listing_id, raw_text in rows:
        eid = listing_id.split(":", 1)[1]
        try:
            resp = session.get(f"https://imobancos.pt/imoveis/{eid}", timeout=15)
            resp.raise_for_status()
        except Exception as e:
            LOG.debug(f"Imobancos page {eid}: {e}")    # tried again next scan
            continue
        original = imobancos_original_url(resp.text)
        raw = json.loads(raw_text or "{}")
        raw["original_checked"] = True
        if original:
            raw["original_url"] = original
            db.execute("UPDATE listings SET url = ?, raw_json = ? WHERE id = ?",
                       (original, json.dumps(raw, ensure_ascii=False), listing_id))
            found += 1
        else:                                          # keep the Imobancos page
            db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
                       (json.dumps(raw, ensure_ascii=False), listing_id))
        time.sleep(0.3)
    db.commit()
    if rows:
        LOG.info(f"Imobancos: original bank page found for {found} of {len(rows)} listings")
    return found


@register("imobancos", "PT")
def scrape_imobancos(db, max_price: float = 100000, **_):
    """imobancos.pt — bank properties (Crédito Agrícola, Montepio, Caixa, Santander, Millennium)."""
    session = make_session()
    total = 0
    for page in range(1, 60):                     # the API counts pages from 1
        resp = session.post(IMOBANCOS_API, json={"page": page, "hitsPerPage": 100})
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits") or []
        for item in hits:
            row = imobancos_listing(item, max_price)
            if row:
                upsert_listing(db, _keep_original(db, row))
                total += 1
        db.commit()
        if not hits or page >= (data.get("totalPages") or 0):
            break
        time.sleep(0.5)
    resolve_imobancos_originals(db, session)
    return total


CENTROLEILOES = CardSite(
    source="centroleiloes", country="PT", base="https://centrodeleiloes.pt", path="/leiloes",
    card_selector="div.lot, div.lote, article, div[class*='lot']",
    title_selector="h2,h3,.title,.lot-title",
    date_selector=".date,.data,[class*='date']",
    params={"categoria": "imoveis"}, max_pages=14, delay=0.8,
    description="Leilão Centro de Leilões",
)


@register("centroleiloes", "PT")
def scrape_centroleiloes(db, max_price: float = 100000, **_):
    """centrodeleiloes.pt — bank auction house."""
    return scrape_cards(db, CENTROLEILOES, max_price)


BIDLEILOEIRA = "https://www.bidleiloeira.pt"


def parse_bidleiloeira_list(html: str) -> list[dict]:
    """The /leiloes page: one card per sale (a sale can hold several lots)."""
    sales = []
    for card in BeautifulSoup(html, "html.parser").select("a.leiloes_item[href]"):
        m = re.match(r"item-(\d+)$", card.get("id") or "")
        url = safe_url(card.get("href"), BIDLEILOEIRA + "/")
        if not m or not url:
            continue
        lots_el = card.select_one(".list_txt")
        lines = [ln.strip(" \xad") for ln in (lots_el.get_text("\n") if lots_el else "").split("\n")]
        lines = [ln for ln in lines if ln]
        kind = card.select_one(".subtitulos")
        card_text = card.get_text(" ", strip=True)
        n_lots = re.search(r"N[ºo°]\s*LOTES\s*(\d+)", card_text, re.I)
        sales.append({
            "id": m.group(1), "url": url,
            "title": lines[0].rstrip(" ,;:") if lines else "",
            "description": " ".join(lines),
            "modalidade": kind.get_text(" ", strip=True) if kind else None,
            "lots": int(n_lots.group(1)) if n_lots else None,
        })
    return sales


def bidleiloeira_opening_value(html: str) -> float | None:
    """The sale page's "Valor abertura" (the first lot's)."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).replace("\xa0", " ")
    m = re.search(r"Valor\s+(?:de\s+)?abertura\s*([\d .]+,\d{2})\s*€", text, re.I)
    return parse_price(m.group(1)) if m else None


@register("bidleiloeira", "PT")
def scrape_bidleiloeira(db, max_price: float = 100000, **_):
    """bidleiloeira.pt — online auction house (insolvency and bank sales)."""
    session = make_session()
    total = 0
    seen = set()
    for page in range(1, 11):
        resp = session.get(f"{BIDLEILOEIRA}/leiloes", params={"p": page} if page > 1 else None)
        if page > 1 and resp.status_code >= 400:
            break
        resp.raise_for_status()
        sales = [s for s in parse_bidleiloeira_list(resp.text) if s["id"] not in seen]
        if not sales:
            break
        for sale in sales:
            seen.add(sale["id"])
            price = None
            if sale["lots"] == 1:                 # with several lots one value would mislead
                try:
                    det = session.get(sale["url"])
                    det.raise_for_status()
                    price = bidleiloeira_opening_value(det.text)
                except Exception as e:
                    LOG.debug(f"bidleiloeira {sale['id']}: {e}")
                time.sleep(0.3)
            if price and price > max_price:
                continue
            upsert_listing(db, make_listing(
                "bidleiloeira", sale["id"], "PT",
                title=sale["title"][:200] or f"Bid Leiloeira #{sale['id']}",
                description=f"{sale['modalidade'] or 'Leilão'} · Bid Leiloeira. {sale['description']}"[:800],
                tipo="imovel", price=price, min_price=price, url=sale["url"],
                raw_json={"modalidade": sale["modalidade"], "lots": sale["lots"]},
            ))
            total += 1
        db.commit()
    return total
