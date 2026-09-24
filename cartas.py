"""
Portuguese court sales in bulk: which Citius processes are still "Em venda",
and `python scraper.py --cartas`, which writes a PDF offer for each of the best.

Letters themselves are written in letters.py (the one letter builder); the names
below are re-exported so older imports keep working.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

from letters import (
    CASA_KEYWORDS, FONT_DIR, GENERIC_TEMPLATES, MIN_HERDADE_M2, RUSTICO_KEYWORDS,
    TERRENO_CONSTRUCAO_KEYWORDS, Letter, _pt_letter_body, _safe_latin1, _today_for_country,
    build_letter, classify_property, format_bid, letter_pdf, parse_bid, por_extenso, sale_kind,
    suggest_bid,
)

__all__ = [  # re-exported from letters.py for older callers
    "CASA_KEYWORDS", "FONT_DIR", "GENERIC_TEMPLATES", "MIN_HERDADE_M2", "RUSTICO_KEYWORDS",
    "TERRENO_CONSTRUCAO_KEYWORDS", "Letter", "_pt_letter_body", "_safe_latin1", "_today_for_country",
    "build_letter", "classify_property", "format_bid", "letter_pdf", "parse_bid", "por_extenso",
    "sale_kind", "suggest_bid", "CARTA_TEMPLATES", "build_carta_for_country", "check_citius_active",
    "generate_cartas",
]

LOG = logging.getLogger("cartas")

CITIUS_URL = "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx"
CARTA_TEMPLATES = GENERIC_TEMPLATES


def build_carta_for_country(item: dict, raw: dict, bid: str, bid_text: str,
                            proponente: dict, country: str) -> str:
    """Old entry point; the text of letters.build_letter()."""
    return build_letter({**item, "country": country, "raw_json": json.dumps(raw)},
                        bid, bid_text, proponente).text


def check_citius_active(processes: dict[str, str]) -> dict[str, str]:
    """Check which processes are active on Citius.

    Args:
        processes: {process_number: tribunal_name}

    Returns:
        {process_number: estado_string} — "Em venda" means active
    """
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    r = session.get(CITIUS_URL, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    tribunal_options = {
        o.get_text(strip=True): o["value"]
        for o in soup.select("#ctl00_ContentPlaceHolder1_ddlTribunais option")
        if o["value"] != "0"
    }

    by_tribunal = defaultdict(list)
    for proc, trib in processes.items():
        by_tribunal[trib].append(proc)

    results = {}

    for trib_name, proc_list in by_tribunal.items():
        trib_id = None
        for opt_name, opt_val in tribunal_options.items():
            if trib_name in opt_name or opt_name in trib_name:
                trib_id = opt_val
                break
        if not trib_id:
            key = trib_name.split(" - ")[0]
            for opt_name, opt_val in tribunal_options.items():
                if key in opt_name:
                    trib_id = opt_val
                    break

        if not trib_id:
            for proc in proc_list:
                results[proc] = "TRIBUNAL NAO ENCONTRADO"
            continue

        try:
            r0 = session.get(CITIUS_URL, timeout=15)
            s0 = BeautifulSoup(r0.text, "html.parser")
            vs = s0.select_one("#__VIEWSTATE")["value"]
            ev = s0.select_one("#__EVENTVALIDATION")["value"]
            vsg = s0.select_one("#__VIEWSTATEGENERATOR")["value"]
        except Exception:
            for proc in proc_list:
                results[proc] = "ERRO"
            continue

        data = {
            "__EVENTTARGET": "", "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg,
            "__VIEWSTATEENCRYPTED": "", "__EVENTVALIDATION": ev,
            "ctl00$ContentPlaceHolder1$ddlTribunais": trib_id,
            "ctl00$ContentPlaceHolder1$txtCalendarDesde": "01/01/2000",
            "ctl00$ContentPlaceHolder1$txtCalendarAte": "31/12/2030",
            "ctl00$ContentPlaceHolder1$chkDatas": "on",
            "ctl00$ContentPlaceHolder1$ddlTiposBem": "0",
            "ctl00$ContentPlaceHolder1$ddlModalidades": "0",
            "ctl00$ContentPlaceHolder1$ddlEstados": "0",
            "ctl00$ContentPlaceHolder1$btnSearch": "Pesquisar",
        }

        try:
            r2 = session.post(CITIUS_URL, data=data, timeout=30)
            page_text = r2.text
        except Exception:
            for proc in proc_list:
                results[proc] = "ERRO"
            continue

        for proc in proc_list:
            if proc in page_text:
                idx = page_text.find(proc)
                snippet = page_text[max(0, idx - 2000):idx + 2000]
                snippet_soup = BeautifulSoup(snippet, "html.parser")
                snippet_text = snippet_soup.get_text(" ", strip=True)

                estado_m = re.search(
                    r"Estado:\s*([^\n]+?)(?:Valor|Modalidade|Tipo|$)",
                    snippet_text, re.I,
                )
                if estado_m:
                    results[proc] = estado_m.group(1).strip()
                else:
                    for pat in ["Em venda", "Vendido", "Suspenso", "Concluído", "Sem efeito", "Deserto"]:
                        if pat.lower() in snippet_text.lower():
                            results[proc] = pat
                            break
                    else:
                        results[proc] = "ENCONTRADO"
            else:
                results[proc] = "NAO ENCONTRADO"

        LOG.info(f"  Checked {trib_name}: {len(proc_list)} processes")
        time.sleep(1)

    return results


def generate_cartas(
    db: sqlite3.Connection,
    score_fn,
    categorize_fn,
    proponente: dict,
    out_dir: str,
    max_price: float = 50000,
    top_n: int = 15,
    filters: dict | None = None,
) -> list[dict]:
    """Batch mode (`python scraper.py --cartas`): the top Citius listings that
    Citius still shows as "Em venda" get a PDF letter each in `out_dir`.

    score_fn and categorize_fn are ignored (kept for old callers); scores come
    from db.load_listings() like everywhere else. The Offers page in the app is
    the interactive version of this.
    """
    missing = [k for k in ("nome", "nif", "morada") if not (proponente or {}).get(k)]
    if missing:
        LOG.error(f"Settings → your details is missing {', '.join(missing)}; no letters generated.")
        return []
    import importlib.util
    if importlib.util.find_spec("fpdf") is None:
        LOG.error("fpdf2 not installed. Run: pip install fpdf2")
        return []

    from db import load_listings
    items = load_listings(db, filters=filters, where="country='PT' AND source='citius'")

    candidates = []
    for it in items:
        if it["category"] != "imoveis" or it["score"] == 0 or (it.get("current_bid") or 0) > 10000:
            continue
        if it.get("offer_outcome") and it["offer_outcome"] != "cancelled":
            continue  # an offer was already sent
        cat = classify_property(it.get("title") or "", it.get("description") or "", it.get("area_m2") or 0)
        if cat is not None:
            candidates.append((it, cat))
    candidates.sort(key=lambda c: -c[0]["score"])
    candidates = candidates[:top_n * 2]
    if not candidates:
        LOG.info("No quality properties found for letters")
        return []

    by_proc = {}
    for it, cat in candidates:
        raw = json.loads(it.get("raw_json") or "{}")
        proc = str(raw.get("processo") or "").split(",")[0].strip()
        if proc and raw.get("tribunal"):
            by_proc[proc] = (it, cat, raw["tribunal"])

    print(f"\nChecking {len(by_proc)} processes on Citius...\n")
    estados = check_citius_active({p: v[2] for p, v in by_proc.items()})
    active = []
    for proc, estado in estados.items():
        is_active = "em venda" in estado.lower()
        print(f"  [{'OK' if is_active else 'XX'}] {proc} — {estado} ({by_proc[proc][1]})")
        if is_active:
            active.append(proc)
    print(f"\n{len(active)} of {len(estados)} still for sale\n")
    if not active:
        return []

    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("carta_") and f.endswith(".pdf"):
            os.remove(os.path.join(out_dir, f))

    generated = []
    for proc in active[:top_n]:
        it, cat, _trib = by_proc[proc]
        valor, valor_texto = suggest_bid(cat, it.get("price"), it.get("area_m2"))
        letter = build_letter(it, valor, valor_texto, proponente)
        filepath = os.path.join(out_dir, letter.filename)
        letter_pdf(letter, filepath)
        print(f"  EUR {valor} | {letter.kind} | {cat}")
        print(f"       {(it.get('title') or '')[:60]}")
        print(f"       -> {letter.filename}\n")
        generated.append({"processo": proc, "tribunal": _trib, "modalidade": letter.kind,
                          "valor": valor, "categoria": cat, "filepath": filepath})

    total = sum(parse_bid(g["valor"]) or 0 for g in generated)
    print(f"=== {len(generated)} letters in {out_dir} — total exposure EUR {total:,.2f} ===")

    import subprocess
    import sys
    if sys.platform == "win32" and generated:
        subprocess.Popen(f'explorer "{out_dir}"')
    return generated
