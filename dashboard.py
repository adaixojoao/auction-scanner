"""
Web dashboard for browsing auction listings.
Run: python dashboard.py
Opens at http://127.0.0.1:8050
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, render_template_string, request, jsonify

DB_PATH = os.path.join(os.path.dirname(__file__), "auctions.db")

app = Flask(__name__)

COUNTRY_NAMES = {
    "PT": "Portugal", "ES": "Spain", "FR": "France",
    "IT": "Italy", "HR": "Croatia", "NL": "Netherlands",
    "DE": "Germany", "GR": "Greece", "BE": "Belgium",
    "RO": "Romania", "PL": "Poland", "CY": "Cyprus",
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EU Auction Scanner</title>
<style>
:root {
    --bg: #0f172a; --card: #1e293b; --accent: #3b82f6;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
    --text: #e2e8f0; --muted: #94a3b8; --border: #334155;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }

header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
header h1 { font-size: 24px; color: white; }
header h1 span { color: var(--accent); }

.stats { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
.stat-card {
    background: var(--card); border-radius: 12px; padding: 16px 24px;
    border: 1px solid var(--border); min-width: 140px;
}
.stat-card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
.stat-card .value { font-size: 28px; font-weight: 700; color: white; margin-top: 4px; }

.filters {
    background: var(--card); border-radius: 12px; padding: 16px 20px;
    border: 1px solid var(--border); margin-bottom: 24px;
    display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
}
.filters label { color: var(--muted); font-size: 13px; }
.filters select, .filters input {
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 12px; font-size: 13px;
}
.filters input[type=text] { width: 200px; }
.filters input[type=number] { width: 100px; }
.filters button {
    background: var(--accent); color: white; border: none; border-radius: 6px;
    padding: 8px 20px; cursor: pointer; font-size: 13px; font-weight: 600;
}
.filters button:hover { opacity: 0.9; }

.listing-table { width: 100%; border-collapse: collapse; }
.listing-table th {
    background: var(--card); color: var(--muted); font-size: 11px;
    text-transform: uppercase; letter-spacing: 1px; padding: 12px 10px;
    text-align: left; border-bottom: 2px solid var(--border);
    cursor: pointer; user-select: none;
}
.listing-table th:hover { color: var(--accent); }
.listing-table td {
    padding: 10px; border-bottom: 1px solid var(--border); font-size: 13px;
    vertical-align: top;
}
.listing-table tr:hover { background: rgba(59, 130, 246, 0.05); }

.score-badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-weight: 700; font-size: 13px; min-width: 32px; text-align: center;
}
.score-high { background: rgba(34,197,94,0.2); color: var(--green); }
.score-mid { background: rgba(234,179,8,0.2); color: var(--yellow); }
.score-low { background: rgba(148,163,184,0.2); color: var(--muted); }

.country-flag { font-size: 16px; margin-right: 4px; }
.price { font-weight: 600; color: white; }
.bid { color: var(--muted); }
.location { color: var(--muted); font-size: 12px; }
.flags { font-size: 11px; color: var(--muted); }
.new-badge { background: var(--accent); color: white; font-size: 10px; padding: 1px 6px; border-radius: 3px; margin-left: 4px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.title-cell { max-width: 300px; }
.title-cell a { color: var(--text); }

.pagination { display: flex; justify-content: center; gap: 8px; margin-top: 20px; }
.pagination button {
    background: var(--card); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 14px; cursor: pointer; font-size: 13px;
}
.pagination button.active { background: var(--accent); border-color: var(--accent); color: white; }
.pagination button:hover { border-color: var(--accent); }

.source-tag {
    display: inline-block; background: var(--bg); border: 1px solid var(--border);
    border-radius: 4px; padding: 1px 6px; font-size: 10px; color: var(--muted);
}

.empty { text-align: center; padding: 60px 20px; color: var(--muted); }

@media (max-width: 768px) {
    .filters { flex-direction: column; }
    .stats { flex-direction: column; }
    .listing-table { font-size: 12px; }
    .listing-table td, .listing-table th { padding: 6px 4px; }
}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1><span>EU</span> Auction Scanner</h1>
        <div style="color:var(--muted);font-size:13px" id="last-update"></div>
    </header>

    <div class="stats" id="stats"></div>

    <div class="filters">
        <div>
            <label>Country</label><br>
            <select id="f-country">
                <option value="">All</option>
                <option value="PT">Portugal</option>
                <option value="ES">Spain</option>
                <option value="FR">France</option>
                <option value="IT">Italy</option>
                <option value="HR">Croatia</option>
                <option value="NL">Netherlands</option>
                <option value="DE">Germany</option>
                <option value="GR">Greece</option>
                <option value="BE">Belgium</option>
                <option value="RO">Romania</option>
                <option value="PL">Poland</option>
                <option value="CY">Cyprus</option>
            </select>
        </div>
        <div>
            <label>Source</label><br>
            <select id="f-source"><option value="">All</option></select>
        </div>
        <div>
            <label>Max Price</label><br>
            <input type="number" id="f-maxprice" value="50000" step="5000">
        </div>
        <div>
            <label>Min Score</label><br>
            <input type="number" id="f-minscore" value="0" step="5" min="0" max="100">
        </div>
        <div>
            <label>Search</label><br>
            <input type="text" id="f-search" placeholder="title, location...">
        </div>
        <div>
            <label>Type</label><br>
            <select id="f-type"><option value="">All</option></select>
        </div>
        <div style="align-self:flex-end">
            <button onclick="loadListings()">Filter</button>
        </div>
    </div>

    <table class="listing-table">
        <thead>
            <tr>
                <th onclick="sortBy('score')">Score</th>
                <th onclick="sortBy('country')">Country</th>
                <th onclick="sortBy('source')">Source</th>
                <th onclick="sortBy('title')">Title</th>
                <th onclick="sortBy('price')">Price</th>
                <th onclick="sortBy('current_bid')">Bid</th>
                <th>Location</th>
                <th onclick="sortBy('date_end')">Ends</th>
                <th>Flags</th>
            </tr>
        </thead>
        <tbody id="tbody"></tbody>
    </table>

    <div class="pagination" id="pagination"></div>
</div>

<script>
const FLAGS = {"PT":"&#x1F1F5;&#x1F1F9;","ES":"&#x1F1EA;&#x1F1F8;","FR":"&#x1F1EB;&#x1F1F7;","IT":"&#x1F1EE;&#x1F1F9;","HR":"&#x1F1ED;&#x1F1F7;","NL":"&#x1F1F3;&#x1F1F1;","DE":"&#x1F1E9;&#x1F1EA;","GR":"&#x1F1EC;&#x1F1F7;","BE":"&#x1F1E7;&#x1F1EA;","RO":"&#x1F1F7;&#x1F1F4;","PL":"&#x1F1F5;&#x1F1F1;","CY":"&#x1F1E8;&#x1F1FE;"};
let currentSort = "score";
let currentDir = "desc";
let currentPage = 1;
const PAGE_SIZE = 50;

function sortBy(col) {
    if (currentSort === col) currentDir = currentDir === "desc" ? "asc" : "desc";
    else { currentSort = col; currentDir = "desc"; }
    loadListings();
}

function loadListings() {
    const params = new URLSearchParams({
        country: document.getElementById("f-country").value,
        source: document.getElementById("f-source").value,
        max_price: document.getElementById("f-maxprice").value,
        min_score: document.getElementById("f-minscore").value,
        search: document.getElementById("f-search").value,
        type: document.getElementById("f-type").value,
        sort: currentSort, dir: currentDir,
        page: currentPage, per_page: PAGE_SIZE,
    });
    fetch("/api/listings?" + params)
        .then(r => r.json())
        .then(data => {
            renderTable(data.items);
            renderStats(data.stats);
            renderPagination(data.total, data.page, data.per_page);
            document.getElementById("last-update").textContent =
                data.last_scrape ? "Last scrape: " + data.last_scrape : "";
        });
}

function renderTable(items) {
    const tbody = document.getElementById("tbody");
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty">No listings match your filters</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(it => {
        const sc = it.score;
        const cls = sc >= 70 ? "score-high" : sc >= 50 ? "score-mid" : "score-low";
        const price = it.price ? "€" + it.price.toLocaleString("de-DE", {maximumFractionDigits:0}) : "?";
        const bid = it.current_bid ? "€" + it.current_bid.toLocaleString("de-DE", {maximumFractionDigits:0}) : "-";
        const loc = [it.concelho, it.district].filter(Boolean).join(", ");
        const ends = it.date_end ? it.date_end.slice(0, 10) : "-";
        const flag = FLAGS[it.country] || "";
        const newBadge = it.is_new ? '<span class="new-badge">NEW</span>' : '';
        return `<tr>
            <td><span class="score-badge ${cls}">${sc}</span></td>
            <td><span class="country-flag">${flag}</span></td>
            <td><span class="source-tag">${it.source}</span></td>
            <td class="title-cell"><a href="${it.url || '#'}" target="_blank">${esc(it.title || '?')}</a>${newBadge}</td>
            <td class="price">${price}</td>
            <td class="bid">${bid}</td>
            <td class="location">${esc(loc)}</td>
            <td>${ends}</td>
            <td class="flags">${esc((it.reasons||[]).join(", "))}</td>
        </tr>`;
    }).join("");
}

function renderStats(s) {
    document.getElementById("stats").innerHTML = `
        <div class="stat-card"><div class="label">Total Listings</div><div class="value">${s.total}</div></div>
        <div class="stat-card"><div class="label">Properties</div><div class="value">${s.properties}</div></div>
        <div class="stat-card"><div class="label">Countries</div><div class="value">${s.countries}</div></div>
        <div class="stat-card"><div class="label">New Today</div><div class="value" style="color:var(--green)">${s.new_today}</div></div>
        <div class="stat-card"><div class="label">Score 70+</div><div class="value" style="color:var(--yellow)">${s.high_score}</div></div>
    `;
}

function renderPagination(total, page, perPage) {
    const pages = Math.ceil(total / perPage);
    if (pages <= 1) { document.getElementById("pagination").innerHTML = ""; return; }
    let html = "";
    for (let i = 1; i <= Math.min(pages, 10); i++) {
        html += `<button class="${i===page?'active':''}" onclick="goPage(${i})">${i}</button>`;
    }
    if (pages > 10) html += `<button disabled>... ${pages}</button>`;
    document.getElementById("pagination").innerHTML = html;
}

function goPage(p) { currentPage = p; loadListings(); }

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

// Load sources and types for filter dropdowns
fetch("/api/meta").then(r => r.json()).then(data => {
    const srcSel = document.getElementById("f-source");
    data.sources.forEach(s => { const o = document.createElement("option"); o.value = s; o.textContent = s; srcSel.appendChild(o); });
    const typeSel = document.getElementById("f-type");
    data.types.forEach(t => { const o = document.createElement("option"); o.value = t; o.textContent = t; typeSel.appendChild(o); });
});

// Handle Enter key in search
document.getElementById("f-search").addEventListener("keydown", e => { if (e.key === "Enter") loadListings(); });

loadListings();
</script>
</body>
</html>
"""


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _investment_score(item: dict) -> tuple[float, list[str]]:
    """Inline scoring (mirrors scraper.investment_score but avoids circular import)."""
    score = 50.0
    reasons = []
    title = (item.get("title") or "").lower()
    price = item.get("price") or 0
    bid = item.get("current_bid") or 0
    area = item.get("area_m2") or 0

    frac = ["1/2", "1/3", "1/4", "1/5", "1/6", "1/8", "1/12", "avos", "quota", "quinhão", "quinhao"]
    if any(p in title for p in frac):
        score -= 25; reasons.append("fractional share")
    if "usufruto" in title or "usufruct" in title:
        score -= 30; reasons.append("usufruct only")
    if "ruína" in title or "ruina" in title:
        score -= 10; reasons.append("ruins")
    if price and price < 500:
        score -= 15; reasons.append("suspiciously cheap")
    if bid and price and bid > price * 1.5:
        score -= 15; reasons.append(f"overbid {bid/price:.0%}")
    if any(w in title for w in ["parking", "garagem", "garage", "box"]):
        score -= 10; reasons.append("parking/storage")

    house = ["moradia", "apartamento", "vivienda", "appartement", "maison", "woonhuis", "casa"]
    if any(w in title for w in house):
        score += 15; reasons.append("full dwelling")
    if bid and price and bid < price * 0.7:
        score += min(20, (1 - bid / price) * 40); reasons.append(f"discount {1-bid/price:.0%}")
    elif not bid and price:
        score += 5; reasons.append("no bids yet")
    if area and area > 50:
        score += 5; reasons.append(f"{area:.0f}m²")
    if area and area > 100:
        score += 5

    urban = ["lisboa", "porto", "madrid", "barcelona", "paris", "amsterdam", "roma", "milano", "zagreb"]
    loc = " ".join(filter(None, [item.get("concelho", ""), item.get("district", "")])).lower()
    if any(c in f"{title} {loc}" for c in urban):
        score += 10; reasons.append("urban location")
    if price and 5000 <= price <= 40000 and any(w in title for w in house):
        score += 10; reasons.append("price sweet spot")

    if item.get("date_end"):
        try:
            end = datetime.fromisoformat(item["date_end"].replace("Z", "+00:00"))
            days = (end - datetime.now(timezone.utc)).days
            if 0 < days <= 7:
                score += 5; reasons.append(f"{days}d left")
        except (ValueError, TypeError):
            pass

    if item.get("source") == "citius" and (not price or price == 0):
        score += 15; reasons.append("no minimum (court sale)")

    return max(0, min(100, score)), reasons


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/meta")
def api_meta():
    db = get_db()
    sources = [r[0] for r in db.execute("SELECT DISTINCT source FROM listings ORDER BY source").fetchall()]
    types = [r[0] for r in db.execute("SELECT DISTINCT tipo FROM listings WHERE tipo IS NOT NULL ORDER BY tipo").fetchall()]
    db.close()
    return jsonify({"sources": sources, "types": types})


@app.route("/api/listings")
def api_listings():
    db = get_db()
    now = datetime.now(timezone.utc)

    country = request.args.get("country", "")
    source = request.args.get("source", "")
    max_price = float(request.args.get("max_price", 50000))
    min_score = float(request.args.get("min_score", 0))
    search = request.args.get("search", "").strip()
    tipo = request.args.get("type", "")
    sort = request.args.get("sort", "score")
    direction = request.args.get("dir", "desc")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    conditions = ["(date_end IS NULL OR date_end > ?)"]
    params = [now.isoformat()]

    if country:
        conditions.append("country = ?"); params.append(country)
    if source:
        conditions.append("source = ?"); params.append(source)
    if tipo:
        conditions.append("tipo = ?"); params.append(tipo)
    if max_price:
        conditions.append("(price <= ? OR price IS NULL)"); params.append(max_price)
    if search:
        conditions.append("(title LIKE ? OR concelho LIKE ? OR district LIKE ? OR description LIKE ?)")
        params.extend([f"%{search}%"] * 4)

    where = " AND ".join(conditions)

    rows = db.execute(f"SELECT * FROM listings WHERE {where}", params).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    items = []
    for r in rows:
        item = dict(zip(cols, r))
        sc, reasons = _investment_score(item)
        item["score"] = sc
        item["reasons"] = reasons
        if min_score and sc < min_score:
            continue
        items.append(item)

    # Sort
    if sort == "score":
        items.sort(key=lambda x: x.get("score", 0), reverse=(direction == "desc"))
    elif sort == "price":
        items.sort(key=lambda x: x.get("price") or 999999, reverse=(direction == "desc"))
    elif sort == "date_end":
        items.sort(key=lambda x: x.get("date_end") or "9999", reverse=(direction == "desc"))
    elif sort == "current_bid":
        items.sort(key=lambda x: x.get("current_bid") or 0, reverse=(direction == "desc"))
    elif sort in ("country", "source", "title"):
        items.sort(key=lambda x: (x.get(sort) or "").lower(), reverse=(direction == "desc"))

    total = len(items)
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]

    # Strip raw_json to save bandwidth
    for it in page_items:
        it.pop("raw_json", None)

    # Stats
    all_rows = db.execute("SELECT COUNT(*) FROM listings").fetchone()
    prop_count = db.execute(
        "SELECT COUNT(*) FROM listings WHERE tipo IN ('apartamento','moradia','imovel','inmueble','nekretnina','immobilier','immobile','vastgoed','terreno','apartamento/moradia')"
    ).fetchone()
    countries = db.execute("SELECT COUNT(DISTINCT country) FROM listings").fetchone()
    new_today = db.execute("SELECT COUNT(*) FROM listings WHERE is_new = 1").fetchone()
    high_score = sum(1 for it in items if it.get("score", 0) >= 70)

    last_scrape = db.execute("SELECT MAX(timestamp) FROM scrape_log").fetchone()

    db.close()

    return jsonify({
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "stats": {
            "total": all_rows[0],
            "properties": prop_count[0],
            "countries": countries[0],
            "new_today": new_today[0],
            "high_score": high_score,
        },
        "last_scrape": last_scrape[0] if last_scrape else None,
    })


@app.route("/cartas-review")
def cartas_review():
    path = os.path.join(os.path.dirname(__file__), "carta_review.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/api/cartas-candidates")
def api_cartas_candidates():
    from scoring import score as _score_fn, categorize as _categorize
    from cartas import classify_property, suggest_bid

    db = get_db()
    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    rows = db.execute(
        "SELECT * FROM listings "
        "WHERE country='PT' AND source='citius' "
        "AND (date_end IS NULL OR date_end > datetime('now'))"
    ).fetchall()
    db.close()

    items = [dict(zip(cols, r)) for r in rows]
    candidates = []

    for it in items:
        if _categorize(it) != "imoveis":
            continue
        sc, reasons = _score_fn(it)
        if sc < 45:
            continue

        title = it.get("title") or ""
        desc = it.get("description") or ""
        area = it.get("area_m2") or 0
        cat = classify_property(title, desc, area)
        if cat is None:
            continue

        bid_val, bid_text = suggest_bid(cat, it.get("price"), area)

        raw = {}
        if it.get("raw_json"):
            try:
                raw = json.loads(it["raw_json"])
            except Exception:
                pass

        modalidade = raw.get("modalidade", "CARTA FECHADA").upper()
        if "NEGOCI" in modalidade:
            modalidade = "NEGOCIACAO PARTICULAR"
        elif "ADJUDIC" in modalidade:
            modalidade = "ADJUDICACAO"
        else:
            modalidade = "CARTA FECHADA"

        candidates.append({
            "id": it.get("id") or str(hash(it.get("url", ""))),
            "title": title,
            "description": desc[:300],
            "location": ", ".join(filter(None, [it.get("concelho", ""), it.get("district", "")])),
            "price": it.get("price"),
            "current_bid": it.get("current_bid"),
            "area_m2": area,
            "date_end": it.get("date_end"),
            "url": it.get("url", ""),
            "processo": raw.get("processo", ""),
            "tribunal": raw.get("tribunal", ""),
            "modalidade": modalidade,
            "categoria": cat,
            "score": sc,
            "reasons": reasons,
            "bid": bid_val,
            "bidText": bid_text,
            "agente_nome": raw.get("agente_nome", ""),
            "agente_email": raw.get("agente_email", ""),
            "agente_contacto": raw.get("agente_contacto", ""),
        })

    candidates.sort(key=lambda x: -x["score"])
    return jsonify(candidates[:100])


@app.route("/api/analyze-property", methods=["POST"])
def api_analyze_property():
    try:
        import anthropic
    except ImportError:
        return jsonify({"error": "anthropic not installed. Run: pip install anthropic"}), 500

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    prompt = f"""És um especialista em imóveis portugueses e leilões judiciais com 20 anos de experiência.
O comprador quer adquirir imóveis significativamente abaixo do valor de mercado para fins caritativos.

DADOS DO IMÓVEL:
- Título: {data.get('title','')}
- Localização: {data.get('location','')}
- Área: {data.get('area_m2','')} m²
- Valor base (VB): €{data.get('price','')}
- Lance atual: €{data.get('current_bid','Sem lances')}
- Modalidade: {data.get('modalidade','')}
- Categoria: {data.get('categoria','')}
- Prazo: {data.get('date_end','')}
- Tribunal: {data.get('tribunal','')}
- Processo: {data.get('processo','')}
- Descrição completa: {data.get('description','')}
- Score automático: {data.get('score','')}/100
- Razões do score: {', '.join(data.get('reasons',[]))}
- Proposta sugerida: EUR {data.get('bid','')}

VERIFICA ESPECIFICAMENTE:
1. A descrição contém "direito de superfície", "aforamento", "bem indiviso", "herança", "compropriedade", "usufruto"? Se sim, é um RED FLAG.
2. O número do processo parece antigo (ex: /2015, /2016)? Processos antigos podem ter complicações acumuladas.
3. A área e o preço fazem sentido para a localização? Calcula €/m² e compara com o mercado local.
4. Para uma moradia/apartamento: estima custo de renovação básico (€100-200/m² para obras ligeiras, €300-500/m² para obras pesadas).
5. Vale a pena visitar antes de licitar, ou é seguro licitar sem visita?
6. Se for carta fechada: qual é o bid máximo que faz sentido dado o risco?

Responde APENAS com este JSON (sem texto adicional):
{{
  "veredicto": "COMPRAR" | "INVESTIGAR" | "PASSAR",
  "confianca": 1-10,
  "resumo": "Uma frase direta e honesta sobre esta oportunidade",
  "pontos_positivos": ["máximo 3 pontos"],
  "riscos": ["máximo 3 riscos, sendo honesto sobre o que não sabes"],
  "red_flags": ["flags legais ou estruturais críticos — vazio se nenhum"],
  "preco_mercado_estimado": null,
  "desconto_estimado_pct": null,
  "custo_renovacao_estimado": null,
  "bid_recomendado": "valor em formato 1.000,00",
  "bid_maximo": "valor máximo absoluto em formato 1.000,00",
  "bid_justificacao": "Uma frase explicando o bid recomendado",
  "visitar_antes": true,
  "proximos_passos": ["3 passos concretos e accionáveis"]
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        import re as _re
        m = _re.search(r'\{.*\}', text, _re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            result = {"veredicto": "INVESTIGAR", "resumo": text, "confianca": 5,
                      "pontos_positivos": [], "riscos": [], "bid_recomendado": data.get('bid', ''),
                      "bid_justificacao": "", "proximos_passos": []}
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/carta-log", methods=["GET"])
def get_carta_log():
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM carta_log ORDER BY created_at DESC").fetchall()
        cols = [d[0] for d in db.execute("SELECT * FROM carta_log LIMIT 0").description]
    except Exception:
        db.close()
        return jsonify([])
    db.close()
    return jsonify([dict(zip(cols, r)) for r in rows])


@app.route("/api/carta-log", methods=["POST"])
def add_carta_log():
    data = request.get_json()
    db = get_db()
    db.execute("""
        INSERT INTO carta_log
        (listing_id, processo, tribunal, sent_date, bid_amount, method, outcome, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        data.get("listing_id"), data.get("processo"), data.get("tribunal"),
        data.get("sent_date"), data.get("bid_amount"), data.get("method", "email"),
        data.get("outcome", "pending"), data.get("notes", ""),
        datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/carta-log/<int:log_id>", methods=["PATCH"])
def update_carta_log(log_id):
    data = request.get_json()
    db = get_db()
    if "outcome" in data:
        db.execute("UPDATE carta_log SET outcome=?, notes=? WHERE id=?",
                   (data["outcome"], data.get("notes", ""), log_id))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/map")
def map_view():
    return """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Auction Scanner Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>body{margin:0}#map{height:100vh}</style>
</head><body>
<div id="map"></div>
<script>
const map=L.map('map').setView([39.5,-8.0],7);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {attribution:'OpenStreetMap'}).addTo(map);
const C={"Lisboa":[38.717,-9.139],"Porto":[41.157,-8.629],"Guarda":[40.537,-7.267],
"Viseu":[40.661,-7.909],"Coimbra":[40.211,-8.429],"Aveiro":[40.644,-8.645],
"Braga":[41.545,-8.426],"Faro":[37.014,-7.935],"Evora":[38.571,-7.909],
"Beja":[38.015,-7.864],"Castelo Branco":[39.820,-7.491],
"Portalegre":[39.287,-7.428],"Santarem":[39.236,-8.685],
"Setubal":[38.524,-8.893],"Leiria":[39.744,-8.807],
"Viana do Castelo":[41.694,-8.834],"Vila Real":[41.300,-7.745],
"Braganca":[41.806,-6.757]};
fetch('/api/listings?country=PT&max_price=100000&per_page=500')
.then(r=>r.json())
.then(data=>{
  data.items.forEach(it=>{
    const loc=it.concelho||it.district||"";
    let coords=null;
    for(const[key,c]of Object.entries(C)){if(loc.toLowerCase().includes(key.toLowerCase())){coords=c;break;}}
    if(!coords)return;
    const col=it.score>=85?"#22c55e":it.score>=65?"#eab308":"#94a3b8";
    L.circleMarker(coords,{radius:8,fillColor:col,color:"#fff",weight:1,fillOpacity:0.85}).addTo(map)
    .bindPopup("<b>"+it.title+"</b><br>Score: "+it.score+" | "+(it.price?"\\u20ac"+it.price.toLocaleString():"?")+"<br>"+loc+"<br><a href='"+(it.url||"#")+"' target='_blank'>Ver</a>");
  });
});
</script></body></html>"""


def main():
    from config import load_config
    cfg = load_config()
    dash = cfg.get("dashboard", {})
    host = dash.get("host", "127.0.0.1")
    port = dash.get("port", 8050)
    print(f"\n  EU Auction Scanner Dashboard")
    print(f"  http://{host}:{port}")
    print(f"  http://{host}:{port}/cartas-review")
    print(f"  http://{host}:{port}/map\n")
    app.run(host=host, port=port, debug=True)


if __name__ == "__main__":
    main()
