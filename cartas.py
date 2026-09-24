"""
Generate proposal letters (cartas) for active Citius judicial sales.
Checks live status on Citius, filters for quality properties, and generates PDFs.
"""

import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("cartas")

CITIUS_URL = "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx"

RUSTICO_KEYWORDS = [
    "mato", "pinhal", "pastagem", "cultura arvense", "sequeiro",
    "oliveir", "vinha", "eucalipt", "sobreir", "pasto",
]
CASA_KEYWORDS = [
    "casa", "habitação", "habitacao", "moradia", "apartamento", "andar",
    "assoalhada", "r/c", "res-do-chao", "rés-do-chão", "prédio urbano",
    "predio urbano", "fração autónoma", "fracao autonoma",
]
TERRENO_CONSTRUCAO_KEYWORDS = [
    "construção urbana", "construcao urbana", "lote", "urbaniz",
]

MIN_HERDADE_M2 = 5000


def _safe_latin1(text: str) -> str:
    if not text:
        return ""
    replacements = {
        'ã': 'a', 'õ': 'o', 'ç': 'c', 'é': 'e',
        'ê': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'á': 'a', 'à': 'a', 'ô': 'o', 'â': 'a',
        'Ã': 'A', 'Ç': 'C', 'É': 'E', 'Í': 'I',
        'Ó': 'O', 'Ú': 'U', 'Ô': 'O',
        'º': 'o', 'ª': 'a',
        '–': '-', '—': '-', '“': '"', '”': '"',
        '‘': "'", '’': "'", '…': '...', '²': '2',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode('latin-1', errors='replace').decode('latin-1')


def classify_property(title: str, description: str, area_m2: float) -> str | None:
    combined = (title + " " + description).lower()
    area = area_m2 or 0

    is_casa = any(k in combined for k in CASA_KEYWORDS)
    is_terreno = any(k in combined for k in TERRENO_CONSTRUCAO_KEYWORDS)
    is_rustico_small = any(k in combined for k in RUSTICO_KEYWORDS) and area < MIN_HERDADE_M2
    is_herdade = area >= MIN_HERDADE_M2

    if is_casa:
        return "CASA"
    if is_terreno:
        return "TERRENO_CONSTRUCAO"
    if is_herdade:
        return "HERDADE"
    if is_rustico_small:
        return None
    return "IMOVEL"


def suggest_bid(category: str, price: float | None, area_m2: float | None) -> tuple[str, str]:
    if category == "TERRENO_CONSTRUCAO":
        return "5.000,00", "cinco mil euros"
    if category == "CASA":
        if price and price > 5000:
            return "4.000,00", "quatro mil euros"
        return "2.500,00", "dois mil e quinhentos euros"
    if category == "HERDADE":
        if area_m2 and area_m2 > 10000:
            return "1.500,00", "mil e quinhentos euros"
        return "1.000,00", "mil euros"
    return "1.000,00", "mil euros"


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
) -> list[dict]:
    """Find top properties, check if active, generate PDF letters.

    Args:
        proponente: {"nome": ..., "nif": ..., "morada": ..., "email": ...}

    Returns:
        List of generated carta info dicts.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        LOG.error("fpdf2 not installed. Run: pip install fpdf2")
        return []

    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    rows = db.execute(
        "SELECT * FROM listings "
        "WHERE country='PT' AND source='citius' "
        "AND (date_end IS NULL OR date_end > datetime('now'))"
    ).fetchall()
    items = [dict(zip(cols, r)) for r in rows]

    # Score and classify
    candidates = []
    for it in items:
        if categorize_fn(it) != "imoveis":
            continue
        sc, reasons = score_fn(it)
        if sc == 0:
            continue

        bid = it.get("current_bid") or 0
        if bid > 10000:
            continue

        title = it.get("title") or ""
        desc = it.get("description") or ""
        area = it.get("area_m2") or 0

        cat = classify_property(title, desc, area)
        if cat is None:
            continue

        candidates.append((it, sc, reasons, cat))

    candidates.sort(key=lambda x: -x[1])
    candidates = candidates[:top_n * 2]

    if not candidates:
        LOG.info("No quality properties found for cartas")
        return []

    # Build process -> tribunal map for active check
    proc_trib = {}
    proc_data = {}
    for it, sc, reasons, cat in candidates:
        raw = {}
        if it.get("raw_json"):
            try:
                raw = json.loads(it["raw_json"])
            except Exception:
                pass
        processo = raw.get("processo", "")
        proc_key = processo.split(",")[0].strip() if processo else ""
        tribunal = raw.get("tribunal", "")
        if proc_key and tribunal:
            proc_trib[proc_key] = tribunal
            proc_data[proc_key] = (it, sc, reasons, cat, raw)

    LOG.info(f"Checking {len(proc_trib)} processes on Citius...")
    print(f"\nVerificando {len(proc_trib)} processos no Citius...\n")

    estados = check_citius_active(proc_trib)

    active_procs = []
    for proc, estado in estados.items():
        is_active = "em venda" in estado.lower()
        marker = "OK" if is_active else "XX"
        cat = proc_data[proc][3] if proc in proc_data else "?"
        print(f"  [{marker}] {proc} — {estado} ({cat})")
        if is_active:
            active_procs.append(proc)

    print(f"\n{len(active_procs)} de {len(estados)} ativas\n")

    if not active_procs:
        print("Nenhuma ativa. Nao foram geradas cartas.")
        return []

    # Generate PDFs
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("carta_") and f.endswith(".pdf"):
            os.remove(os.path.join(out_dir, f))

    nome = proponente["nome"]
    nif = proponente["nif"]
    morada = proponente["morada"]
    email = proponente.get("email", "")

    from datetime import datetime
    today = datetime.now().strftime("%d de %B de %Y").replace(
        "January", "janeiro").replace("February", "fevereiro").replace(
        "March", "marco").replace("April", "abril").replace(
        "May", "maio").replace("June", "junho").replace(
        "July", "julho").replace("August", "agosto").replace(
        "September", "setembro").replace("October", "outubro").replace(
        "November", "novembro").replace("December", "dezembro")

    generated = []
    count = 0

    for proc in active_procs:
        if proc not in proc_data:
            continue
        it, sc, reasons, cat, raw = proc_data[proc]
        valor, valor_texto = suggest_bid(cat, it.get("price"), it.get("area_m2"))

        count += 1
        processo = raw.get("processo", "")
        tribunal = raw.get("tribunal", "")
        modalidade = raw.get("modalidade", "")
        title_short = (it.get("title") or "Imovel")[:80]
        loc = ", ".join(filter(None, [it.get("concelho", ""), it.get("district", "")]))
        area_str = f"{it['area_m2']:.0f} m2" if it.get("area_m2") else "area nao especificada"

        is_negociacao = "negociação particular" in modalidade.lower() or "negociacao particular" in _safe_latin1(modalidade).lower()
        is_adjudicacao = "adjudica" in modalidade.lower()

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=25)

        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, _safe_latin1(nome), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 5, f"NIF: {nif}", new_x="LMARGIN", new_y="NEXT")
        pdf.multi_cell(0, 5, _safe_latin1(morada))
        pdf.ln(8)
        pdf.cell(0, 5, f"Guarda, {_safe_latin1(today)}", new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(6)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 5, "Exmo(a). Sr(a). Juiz / Agente de Execucao", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 5, _safe_latin1(tribunal), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)

        proc_key = processo.split(",")[0].strip()
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 5, f"Assunto: Proposta de Aquisicao - Processo {_safe_latin1(proc_key)}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)
        pdf.set_font("Helvetica", size=10)

        s = _safe_latin1
        dados_bloco = (
            f"Dados do proponente:\n"
            f"   Nome: {s(nome)}\n"
            f"   NIF: {nif}\n"
            f"   Morada: {s(morada)}\n"
            f"   Email: {email}"
        )

        if is_negociacao:
            body = (
                f"Exmo(a). Sr(a),\n\n"
                f"Venho por este meio manifestar o meu interesse na aquisicao do imovel "
                f"em venda por negociacao particular no ambito do processo acima referido.\n\n"
                f"Descricao do bem: {s(title_short)}\n"
                f"Localizacao: {s(loc)}\n"
                f"Area: {s(area_str)}\n\n"
                f"Apresento a seguinte proposta de aquisicao:\n\n"
                f"   Valor: EUR {valor} ({valor_texto})\n\n"
                f"{dados_bloco}\n\n"
                f"Solicito que me informem sobre:\n"
                f"   1. Os procedimentos necessarios para formalizar a proposta;\n"
                f"   2. Se e necessario deposito de caucao e respetivo montante;\n"
                f"   3. O contacto direto do encarregado da venda.\n\n"
                f"Encontro-me disponivel para qualquer esclarecimento adicional "
                f"e para deslocacao ao imovel para visita.\n\n"
                f"Com os melhores cumprimentos,\n\n\n\n"
                f"{s(nome)}\n"
                f"NIF: {nif}"
            )
        elif is_adjudicacao:
            body = (
                f"Exmo(a). Sr(a),\n\n"
                f"Venho por este meio manifestar o meu interesse na aquisicao do imovel "
                f"no ambito do processo acima referido, atualmente em fase de adjudicacao.\n\n"
                f"Descricao do bem: {s(title_short)}\n"
                f"Localizacao: {s(loc)}\n"
                f"Area: {s(area_str)}\n\n"
                f"Caso ainda seja possivel apresentar proposta, ofereco:\n\n"
                f"   Valor: EUR {valor} ({valor_texto})\n\n"
                f"{dados_bloco}\n\n"
                f"Solicito informacao sobre o estado atual da venda e se ainda e "
                f"possivel apresentar proposta.\n\n"
                f"Com os melhores cumprimentos,\n\n\n\n"
                f"{s(nome)}\n"
                f"NIF: {nif}"
            )
        else:
            body = (
                f"Exmo(a). Sr(a),\n\n"
                f"Venho por este meio apresentar proposta de aquisicao do imovel "
                f"em venda mediante proposta em carta fechada no ambito do processo "
                f"acima referido.\n\n"
                f"Descricao do bem: {s(title_short)}\n"
                f"Localizacao: {s(loc)}\n"
                f"Area: {s(area_str)}\n\n"
                f"PROPOSTA DE AQUISICAO:\n\n"
                f"   Proponente: {s(nome)}\n"
                f"   NIF: {nif}\n"
                f"   Morada: {s(morada)}\n"
                f"   Email: {email}\n"
                f"   Valor da proposta: EUR {valor} ({valor_texto})\n\n"
                f"Solicito igualmente informacao sobre:\n"
                f"   1. O prazo limite para entrega de propostas;\n"
                f"   2. Se e necessario juntar cheque visado de caucao e montante;\n"
                f"   3. O local e horario para entrega de propostas;\n"
                f"   4. A data prevista para abertura das propostas.\n\n"
                f"Encontro-me disponivel para qualquer esclarecimento adicional.\n\n"
                f"Com os melhores cumprimentos,\n\n\n\n"
                f"{s(nome)}\n"
                f"NIF: {nif}"
            )

        pdf.multi_cell(0, 5, body)
        pdf.ln(10)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 4, f"Ref: {s(processo)} | {cat}", new_x="LMARGIN", new_y="NEXT")

        filename = f"carta_{count:02d}_{proc_key.replace('/', '-')}_{valor.replace('.', '').replace(',', '_')}EUR.pdf"
        filepath = os.path.join(out_dir, filename)
        pdf.output(filepath)

        mod_label = "NEG.PARTICULAR" if is_negociacao else "ADJUDICACAO" if is_adjudicacao else "CARTA FECHADA"
        print(f"  [{count}] EUR {valor} | {mod_label} | {cat}")
        print(f"       {s(title_short)[:60]}")
        print(f"       {s(loc)}")
        print(f"       -> {filename}")
        print()

        generated.append({
            "processo": proc_key,
            "tribunal": tribunal,
            "modalidade": mod_label,
            "valor": valor,
            "categoria": cat,
            "filepath": filepath,
        })

    total = sum(
        float(g["valor"].replace(".", "").replace(",", "."))
        for g in generated
    )
    print(f"=== {count} cartas geradas em {out_dir} ===")
    print(f"Exposicao total: EUR {total:,.2f}")

    return generated
