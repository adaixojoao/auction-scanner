"""
Web dashboard for browsing auction listings.
Run: python dashboard.py
Opens at http://127.0.0.1:8050  (also /cartas-review, /map, /health)

Scores, visibility and filters come from db.load_listings() — the same code
the report and the alerts use — so a listing scores the same everywhere.
Scraped text and URLs are untrusted: URLs are reduced to http(s) on the way
in (common.safe_url) and everything is escaped on the way out.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

from common import COUNTRY_NAMES, FLAGS
from db import connect, hidden_category, load_listings, source_health

HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

SORT_KEYS = {
    "score": lambda x: x.get("score", 0),
    "price": lambda x: x.get("price") if x.get("price") is not None else 1e12,
    "date_end": lambda x: x.get("date_end") or "9999",
    "current_bid": lambda x: x.get("current_bid") or 0,
    "first_seen": lambda x: x.get("first_seen") or "",
    "country": lambda x: (x.get("country") or "").lower(),
    "source": lambda x: (x.get("source") or "").lower(),
    "title": lambda x: (x.get("title") or "").lower(),
}

_STYLE = """
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
nav a { margin-left: 16px; font-size: 13px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.muted { color: var(--muted); font-size: 13px; }
table { width: 100%; border-collapse: collapse; }
th {
    background: var(--card); color: var(--muted); font-size: 11px;
    text-transform: uppercase; letter-spacing: 1px; padding: 12px 10px;
    text-align: left; border-bottom: 2px solid var(--border);
}
td { padding: 10px; border-bottom: 1px solid var(--border); font-size: 13px; vertical-align: top; }
"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EU Auction Scanner</title>
<style>
""" + _STYLE + """
.stats { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
.stat-card {
    background: var(--card); border-radius: 12px; padding: 16px 24px;
    border: 1px solid var(--border); min-width: 140px;
}
.stat-card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
.stat-card .value { font-size: 28px; font-weight: 700; color: white; margin-top: 4px; }
.stat-card .value a { color: inherit; }

.filters {
    background: var(--card); border-radius: 12px; padding: 16px 20px;
    margin-bottom: 20px; display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end;
    border: 1px solid var(--border);
}
.filters label { display: block; color: var(--muted); font-size: 11px; margin-bottom: 4px; text-transform: uppercase; }
.filters select, .filters input {
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 12px; font-size: 13px;
}
.filters input[type=text] { width: 200px; }
.filters input[type=number] { width: 100px; }
.filters .check { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; padding-bottom: 8px; }
.filters button {
    background: var(--accent); color: white; border: none; border-radius: 6px;
    padding: 8px 20px; cursor: pointer; font-size: 13px; font-weight: 600;
}
.listing-table th { cursor: pointer; user-select: none; }
.listing-table th:hover { color: var(--accent); }
.listing-table tr:hover { background: rgba(59, 130, 246, 0.05); }
.listing-table tr.hidden-row { opacity: 0.45; }

.score-badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-weight: 700; font-size: 13px; min-width: 32px; text-align: center;
}
.score-high { background: rgba(34,197,94,0.2); color: var(--green); }
.score-mid { background: rgba(234,179,8,0.2); color: var(--yellow); }
.score-low { background: rgba(148,163,184,0.2); color: var(--muted); }
.country-flag { font-size: 16px; margin-right: 4px; }
.price { font-weight: 600; color: white; white-space: nowrap; }
.bid, .location, .flags { color: var(--muted); }
.location { font-size: 12px; }
.flags { font-size: 11px; }
.badge { font-size: 10px; padding: 1px 6px; border-radius: 3px; margin-left: 4px; white-space: nowrap; }
.new-badge { background: var(--accent); color: white; }
.drop-badge { background: rgba(34,197,94,0.2); color: var(--green); }
.hidden-badge { background: rgba(239,68,68,0.15); color: var(--red); }
.title-cell { max-width: 320px; }
.title-cell a { color: var(--text); }
.pagination { display: flex; justify-content: center; gap: 8px; margin-top: 20px; flex-wrap: wrap; }
.pagination button {
    background: var(--card); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 14px; cursor: pointer; font-size: 13px;
}
.pagination button.active { background: var(--accent); border-color: var(--accent); color: white; }
.pagination button:disabled { opacity: 0.4; cursor: default; }
.source-tag {
    display: inline-block; background: var(--bg); border: 1px solid var(--border);
    border-radius: 4px; padding: 1px 6px; font-size: 10px; color: var(--muted);
}
.empty { text-align: center; padding: 60px 20px; color: var(--muted); }
@media (max-width: 768px) {
    .filters { flex-direction: column; align-items: stretch; }
    .stats { flex-direction: column; }
    td, th { padding: 6px 4px; }
}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1><span>EU</span> Auction Scanner</h1>
        <div>
            <span class="muted" id="last-update"></span>
            <nav style="display:inline">
                <a href="/cartas-review">Cartas</a><a href="/map">Map</a><a href="/health">Source health</a>
            </nav>
        </div>
    </header>

    <div class="stats" id="stats"></div>

    <div class="filters">
        <div>
            <label>Country</label>
            <select id="f-country">
                <option value="">All</option>
                {% for code, name in countries %}<option value="{{ code }}">{{ name }}</option>{% endfor %}
            </select>
        </div>
        <div><label>Source</label><select id="f-source"><option value="">All</option></select></div>
        <div><label>Max Price</label><input type="number" id="f-maxprice" value="{{ max_price }}" step="5000"></div>
        <div><label>Min Score</label><input type="number" id="f-minscore" value="0" step="5" min="0" max="100"></div>
        <div><label>Search</label><input type="text" id="f-search" placeholder="title, location..."></div>
        <div><label>Type</label><select id="f-type"><option value="">All</option></select></div>
        <div class="check"><input type="checkbox" id="f-properties" checked><label for="f-properties" style="margin:0">Property only</label></div>
        <div class="check"><input type="checkbox" id="f-hidden"><label for="f-hidden" style="margin:0">Show hidden</label></div>
        <div><button onclick="currentPage=1;loadListings()">Filter</button></div>
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
                <th>Why</th>
            </tr>
        </thead>
        <tbody id="tbody"></tbody>
    </table>
    <div class="pagination" id="pagination"></div>
</div>

<script>
const FLAGS = {{ flags|tojson }};
let currentSort = "score", currentDir = "desc", currentPage = 1;
const PAGE_SIZE = 50;

function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function money(v) { return v ? "€" + Number(v).toLocaleString("de-DE", {maximumFractionDigits: 0}) : "?"; }

function sortBy(col) {
    if (currentSort === col) currentDir = currentDir === "desc" ? "asc" : "desc";
    else { currentSort = col; currentDir = col === "date_end" || col === "price" ? "asc" : "desc"; }
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
        properties: document.getElementById("f-properties").checked ? 1 : 0,
        show_hidden: document.getElementById("f-hidden").checked ? 1 : 0,
        sort: currentSort, dir: currentDir, page: currentPage, per_page: PAGE_SIZE,
    });
    fetch("/api/listings?" + params)
        .then(r => r.json())
        .then(data => {
            renderTable(data.items);
            renderStats(data.stats);
            renderPagination(data.total, data.page, data.per_page);
            document.getElementById("last-update").textContent =
                data.last_scrape ? "Last scrape: " + data.last_scrape.slice(0, 16).replace("T", " ") + " UTC" : "";
        });
}

function renderTable(items) {
    const tbody = document.getElementById("tbody");
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty">No listings match your filters</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(it => {
        const sc = Math.round(it.score);
        const cls = sc >= 70 ? "score-high" : sc >= 50 ? "score-mid" : "score-low";
        const loc = [it.concelho, it.district].filter(Boolean).join(", ");
        const title = it.url
            ? `<a href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title || "?")}</a>`
            : esc(it.title || "?");
        const badges = (it.is_recent ? '<span class="badge new-badge">NEW</span>' : "")
            + (it.price_drop_pct ? `<span class="badge drop-badge">↓${Math.round(it.price_drop_pct)}%</span>` : "")
            + (it.hidden_reason ? `<span class="badge hidden-badge" title="${esc(it.hidden_reason)}">hidden: ${esc(it.hidden_category)}</span>` : "");
        return `<tr class="${it.hidden_reason ? "hidden-row" : ""}">
            <td><span class="score-badge ${cls}">${sc}</span></td>
            <td><span class="country-flag" title="${esc(it.country)}">${FLAGS[it.country] || esc(it.country)}</span></td>
            <td><span class="source-tag">${esc(it.source)}</span></td>
            <td class="title-cell">${title}${badges}</td>
            <td class="price">${money(it.price)}</td>
            <td class="bid">${it.current_bid ? money(it.current_bid) : "-"}</td>
            <td class="location">${esc(loc)}</td>
            <td>${esc(it.date_end ? it.date_end.slice(0, 10) : "-")}</td>
            <td class="flags">${esc((it.reasons || []).join(", "))}</td>
        </tr>`;
    }).join("");
}

function renderStats(s) {
    const hidden = Object.entries(s.hidden || {}).map(([k, v]) => `${v} ${k}`).join(", ");
    document.getElementById("stats").innerHTML = `
        <div class="stat-card"><div class="label">Matching</div><div class="value">${s.total}</div></div>
        <div class="stat-card"><div class="label">Properties</div><div class="value">${s.properties}</div></div>
        <div class="stat-card"><div class="label">Countries</div><div class="value">${s.countries}</div></div>
        <div class="stat-card"><div class="label">New (24h)</div><div class="value" style="color:var(--green)">${s.new_today}</div></div>
        <div class="stat-card"><div class="label">Score 70+</div><div class="value" style="color:var(--yellow)">${s.high_score}</div></div>
        <div class="stat-card"><div class="label">Sources failing</div><div class="value" style="color:${s.sources_failing ? "var(--red)" : "var(--green)"}"><a href="/health">${s.sources_failing}</a></div></div>
        ${hidden ? `<div class="stat-card"><div class="label">Hidden</div><div class="muted" style="margin-top:8px">${esc(hidden)}</div></div>` : ""}
    `;
}

function renderPagination(total, page, perPage) {
    const pages = Math.ceil(total / perPage);
    const el = document.getElementById("pagination");
    if (pages <= 1) { el.innerHTML = ""; return; }
    const first = Math.max(1, Math.min(page - 4, pages - 9)), last = Math.min(pages, first + 9);
    let html = `<button ${page === 1 ? "disabled" : ""} onclick="goPage(${page - 1})">‹</button>`;
    for (let i = first; i <= last; i++) html += `<button class="${i === page ? "active" : ""}" onclick="goPage(${i})">${i}</button>`;
    html += `<button ${page === pages ? "disabled" : ""} onclick="goPage(${page + 1})">›</button>`;
    el.innerHTML = html + `<span class="muted" style="align-self:center">${pages} pages</span>`;
}

function goPage(p) { currentPage = p; loadListings(); window.scrollTo(0, 0); }

fetch("/api/meta").then(r => r.json()).then(data => {
    const add = (id, values) => values.forEach(v => {
        const o = document.createElement("option"); o.value = v; o.textContent = v;
        document.getElementById(id).appendChild(o);
    });
    add("f-source", data.sources);
    add("f-type", data.types);
});
document.getElementById("f-search").addEventListener("keydown", e => { if (e.key === "Enter") { currentPage = 1; loadListings(); } });
loadListings();
</script>
</body>
</html>
"""

HEALTH_TEMPLATE = """
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Source health — Auction Scanner</title>
<style>""" + _STYLE + """
.state { font-weight: 700; font-size: 12px; }
.ok { color: var(--green); } .error, .broken { color: var(--red); }
.never-worked, .never-run { color: var(--yellow); }
td.msg { color: var(--muted); font-size: 12px; max-width: 420px; }
</style></head>
<body><div class="container">
<header><h1><span>Source</span> health</h1><nav><a href="/">Listings</a><a href="/cartas-review">Cartas</a></nav></header>
<p class="muted" style="margin-bottom:16px">
  <b>broken</b> = worked before, now returns nothing (usually the site changed its layout).
  <b>never worked</b> = has never returned a listing (selectors not confirmed against the live site).
  Run one source with <code>python scraper.py --source NAME</code> to see its log.
</p>
<table>
<tr><th>Source</th><th>Country</th><th>State</th><th>Last run (UTC)</th><th>Last count</th>
<th>Runs without results</th><th>Last OK</th><th>Stored</th><th>Last message</th></tr>
{% for h in health %}
<tr>
  <td>{{ h.source }}</td><td>{{ h.country }}</td>
  <td class="state {{ h.state|replace(' ', '-') }}">{{ h.state }}</td>
  <td>{{ (h.last_run or '-')[:16]|replace('T', ' ') }}</td>
  <td>{{ h.last_count if h.last_count is not none else '-' }}</td>
  <td>{{ h.failing_runs }}</td>
  <td>{{ (h.last_ok or '-')[:10] }}</td>
  <td>{{ h.listings }}</td>
  <td class="msg">{{ h.last_message or '' }}</td>
</tr>
{% endfor %}
</table>
</div></body></html>
"""


def get_db():
    return connect()


def _config() -> dict:
    from config import load_config
    return load_config()


def _registry():
    from sources import load_all
    return load_all()


def _num(value, default, cast=float):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _public(item: dict) -> dict:
    """What the browser gets: no raw_json, a short description."""
    out = {k: v for k, v in item.items() if k != "raw_json"}
    if out.get("description"):
        out["description"] = out["description"][:300]
    out["hidden_category"] = hidden_category(item.get("hidden_reason"))
    return out


@app.route("/")
def index():
    cfg = _config()
    return render_template_string(
        HTML_TEMPLATE,
        countries=list(COUNTRY_NAMES.items()),
        flags=FLAGS,
        max_price=int(cfg.get("max_price", 50000)),
    )


@app.route("/api/meta")
def api_meta():
    db = get_db()
    try:
        sources = [r[0] for r in db.execute("SELECT DISTINCT source FROM listings ORDER BY source")]
        types = [r[0] for r in db.execute(
            "SELECT DISTINCT tipo FROM listings WHERE tipo IS NOT NULL ORDER BY tipo")]
    finally:
        db.close()
    return jsonify({"sources": sources, "types": types})


@app.route("/api/listings")
def api_listings():
    args = request.args
    country = args.get("country", "")
    source = args.get("source", "")
    max_price = _num(args.get("max_price"), 0)
    min_score = _num(args.get("min_score"), 0)
    search = args.get("search", "").strip()
    tipo = args.get("type", "")
    properties_only = args.get("properties", "0") == "1"
    show_hidden = args.get("show_hidden", "0") == "1"
    sort = args.get("sort", "score")
    direction = args.get("dir", "desc")
    page = max(1, _num(args.get("page"), 1, int))
    per_page = min(1000, max(1, _num(args.get("per_page"), 50, int)))

    conditions, params = [], []
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
    if not show_hidden:
        # Cheap pre-filter; load_listings() applies the exact end-of-day rule.
        conditions.append("(date_end IS NULL OR date_end >= date('now', '-1 day'))")

    db = get_db()
    try:
        cfg = _config()
        loaded = load_listings(db, filters=cfg.get("filters"), include_hidden=True,
                               where=" AND ".join(conditions), params=params)
        loaded = [it for it in loaded
                  if it["score"] >= min_score and (not properties_only or it["category"] == "imoveis")]
        hidden_counts = Counter(hidden_category(it["hidden_reason"]) for it in loaded
                                if it["hidden_reason"])
        items = loaded if show_hidden else [it for it in loaded if not it["hidden_reason"]]

        key = SORT_KEYS.get(sort, SORT_KEYS["score"])
        items.sort(key=key, reverse=(direction == "desc"))

        total = len(items)
        page_items = [_public(it) for it in items[(page - 1) * per_page:page * per_page]]
        visible = [it for it in items if not it["hidden_reason"]]
        health = source_health(db, _registry())
        last_scrape = db.execute("SELECT MAX(timestamp) FROM scrape_log").fetchone()[0]
    finally:
        db.close()

    return jsonify({
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "stats": {
            "total": total,
            "properties": sum(1 for it in visible if it["category"] == "imoveis"),
            "countries": len({it.get("country") for it in visible}),
            "new_today": sum(1 for it in visible if it["is_recent"]),
            "high_score": sum(1 for it in visible if it["score"] >= 70),
            "hidden": dict(hidden_counts),
            "sources_failing": sum(1 for h in health if h["state"] in ("error", "broken")),
        },
        "last_scrape": last_scrape,
    })


@app.route("/api/health")
def api_health():
    db = get_db()
    try:
        registry = _registry()
        health = source_health(db, registry)
    finally:
        db.close()
    for h in health:
        src = registry.get(h["source"])
        h["country"] = src.country if src else None
        h["default"] = src.default if src else None
    return jsonify(health)


@app.route("/health")
def health_page():
    db = get_db()
    try:
        registry = _registry()
        health = source_health(db, registry)
    finally:
        db.close()
    for h in health:
        src = registry.get(h["source"])
        h["country"] = src.country if src else "?"
    return render_template_string(HEALTH_TEMPLATE, health=health)


@app.route("/cartas-review")
def cartas_review():
    with open(os.path.join(HERE, "carta_review.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.route("/api/proponente")
def api_proponente():
    """Who signs the cartas — from config.json, so it lives in one place."""
    p = _config().get("proponente", {})
    return jsonify({k: p.get(k, "") for k in ("nome", "nif", "morada", "email", "localidade")})


@app.route("/api/cartas-candidates")
def api_cartas_candidates():
    from cartas import classify_property, suggest_bid

    country = request.args.get("country", "")
    db = get_db()
    try:
        items = load_listings(db, filters=_config().get("filters"),
                              where="country = ?" if country else "",
                              params=(country,) if country else ())
    finally:
        db.close()

    candidates = []
    for it in items:
        if it["category"] != "imoveis" or it["score"] < 45:
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
            except (ValueError, TypeError):
                pass
        modalidade = str(raw.get("modalidade") or "CARTA FECHADA").upper()
        if it.get("source") == "eleiloes":
            modalidade = "LEILAO ELETRONICO"   # bids go in online, not by letter
        elif "NEGOCI" in modalidade:
            modalidade = "NEGOCIACAO PARTICULAR"
        elif "ADJUDIC" in modalidade:
            modalidade = "ADJUDICACAO"
        else:
            modalidade = "CARTA FECHADA"

        candidates.append({
            "id": it["id"],
            "country": it.get("country") or "PT",
            "title": title,
            "description": desc[:300],
            "location": ", ".join(filter(None, [it.get("concelho"), it.get("district")])),
            "price": it.get("price"),
            "min_price": it.get("min_price"),
            "current_bid": it.get("current_bid"),
            "area_m2": area,
            "date_end": it.get("date_end"),
            "url": it.get("url") or "",
            "processo": raw.get("processo", ""),
            "tribunal": raw.get("tribunal", ""),
            "modalidade": modalidade,
            "categoria": cat,
            "score": it["score"],
            "reasons": it["reasons"],
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

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400

    COUNTRY_CONTEXT = {
        "PT": "Portugal. Venda judicial via Citius. Carta fechada = sealed bid. Risco: dívidas de IMI transferem para comprador.",
        "ES": "Spain. Subastas judiciales via BOE. Minimum bid 50-75% of appraised value. Risk: occupants with legal protection.",
        "FR": "France. Enchères judiciaires via licitor.com. Buyer pays ~8% notary fees. Risk: occupants with droit au maintien.",
        "DE": "Germany. Zwangsversteigerung. No minimum bid by law. Risk: Grundschuld not cleared.",
        "IT": "Italy. Vendita giudiziaria via pvp.giustizia.it. Starting bid 25% below appraisal. Risk: occupants, condominium debts.",
        "NL": "Netherlands. Executieveiling. No minimum bid. 2% transfer tax. Risk: hidden defects, no warranty.",
        "HR": "Croatia. Forced sale via FINA/e-oglasna. Starts 75% market, drops to 50% second round. Risk: unclear title.",
        "GR": "Greece. Electronic auction via eauction.gr. Starting bid 2/3 of appraisal. Risk: ENFIA tax debts transfer.",
        "BE": "Belgium. Notary auction via biddit.be. Legally binding bid. Risk: structural defects, no warranty.",
        "RO": "Romania. ANAF tax seizure. Risk: multiple creditors, unclear priority.",
        "PL": "Poland. Bailiff auction. First: 3/4 appraised, second: 1/2. Risk: occupants, mortgage not cleared.",
        "CY": "Cyprus. Forced sale via DLS. Risk: title deeds not issued, occupants.",
    }
    country = data.get("country", "PT")
    ctx = COUNTRY_CONTEXT.get(country, "European judicial auction.")

    prompt = f"""You are an expert in European judicial property auctions with 20 years of experience.
The buyer wants to acquire properties significantly below market value for charitable purposes.
They are based in Portugal but buy across the EU.

COUNTRY CONTEXT: {ctx}

PROPERTY DATA:
- Title: {data.get('title','')}
- Country: {country}
- Location: {data.get('location','')}
- Area: {data.get('area_m2','')} m²
- Base value: €{data.get('price','')}
- Current bid: €{data.get('current_bid','No bids')}
- Sale type: {data.get('modalidade','')}
- Category: {data.get('categoria','')}
- Deadline: {data.get('date_end','')}
- Court/Agent: {data.get('tribunal','')}
- Case number: {data.get('processo','')}
- Description: {data.get('description','')}
- Auto score: {data.get('score','')}/100
- Score reasons: {', '.join(data.get('reasons',[]))}
- Suggested bid: EUR {data.get('bid','')}

CHECK SPECIFICALLY:
1. Red flags in description (occupants, tax debts, unclear title, usufruct, fractional ownership)?
2. Is the case number old (pre-2020)? Old cases accumulate complications.
3. Does price/m² make sense for the location? Compare with local market.
4. Estimate renovation cost if dwelling (€100-200/m² light, €300-500/m² heavy).
5. Can this be bid on remotely from Portugal, or is physical presence required?
6. What is the realistic all-in cost (bid + taxes + fees + renovation)?
7. Is the suggested bid at or above the legal minimum for this sale type? Say so if not.

Respond ONLY with this JSON (no other text):
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
        m = re.search(r'\{.*\}', text, re.DOTALL)
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
        rows = [dict(r) for r in db.execute("SELECT * FROM carta_log ORDER BY created_at DESC")]
    finally:
        db.close()
    return jsonify(rows)


@app.route("/api/carta-log", methods=["POST"])
def add_carta_log():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not (data.get("listing_id") or data.get("processo")):
        return jsonify({"error": "listing_id or processo required"}), 400
    db = get_db()
    try:
        cur = db.execute("""
            INSERT INTO carta_log
            (listing_id, processo, tribunal, country, sent_date, bid_amount, method, outcome, notes, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("listing_id"), data.get("processo"), data.get("tribunal"),
            data.get("country") or "PT",
            data.get("sent_date"), _num(data.get("bid_amount"), None), data.get("method", "email"),
            data.get("outcome", "pending"), data.get("notes", ""),
            datetime.now(timezone.utc).isoformat(),
        ))
        db.commit()
        new_id = cur.lastrowid
    finally:
        db.close()
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/carta-log/<int:log_id>", methods=["PATCH"])
def update_carta_log(log_id):
    data = request.get_json(silent=True) or {}
    if "outcome" not in data:
        return jsonify({"error": "outcome required"}), 400
    db = get_db()
    try:
        row = db.execute("SELECT * FROM carta_log WHERE id=?", (log_id,)).fetchone()
        if row is None:
            return jsonify({"error": "not found"}), 404
        db.execute("UPDATE carta_log SET outcome=?, notes=? WHERE id=?",
                   (data["outcome"], data.get("notes", row["notes"] or ""), log_id))
        db.commit()
    finally:
        db.close()

    if data["outcome"] == "won" and row["outcome"] != "won":
        tg = _config().get("telegram", {})
        if tg.get("enabled"):
            from telegram_alert import alert_carta_won
            alert_carta_won(tg.get("token", ""), tg.get("chat_id", ""),
                            row["processo"] or row["listing_id"] or "?", row["bid_amount"] or 0, "won")
    return jsonify({"ok": True})


MAP_TEMPLATE = """<!DOCTYPE html>
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
"Braga":[41.545,-8.426],"Faro":[37.014,-7.935],"Evora":[38.571,-7.909],"Évora":[38.571,-7.909],
"Beja":[38.015,-7.864],"Castelo Branco":[39.820,-7.491],
"Portalegre":[39.287,-7.428],"Santarem":[39.236,-8.685],"Santarém":[39.236,-8.685],
"Setubal":[38.524,-8.893],"Setúbal":[38.524,-8.893],"Leiria":[39.744,-8.807],
"Viana do Castelo":[41.694,-8.834],"Vila Real":[41.300,-7.745],
"Braganca":[41.806,-6.757],"Bragança":[41.806,-6.757]};
function esc(s){return String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");}
fetch('/api/listings?country=PT&properties=1&max_price={{ max_price }}&per_page=1000')
.then(r=>r.json())
.then(data=>{
  data.items.forEach(it=>{
    // District first: it is what the coordinates are for.
    const loc=[it.district,it.concelho].filter(Boolean).join(" ");
    let coords=null;
    for(const[key,c]of Object.entries(C)){if(loc.toLowerCase().includes(key.toLowerCase())){coords=c;break;}}
    if(!coords)return;
    const jitter=()=>(Math.random()-0.5)*0.08;
    const col=it.score>=85?"#22c55e":it.score>=65?"#eab308":"#94a3b8";
    L.circleMarker([coords[0]+jitter(),coords[1]+jitter()],{radius:8,fillColor:col,color:"#fff",weight:1,fillOpacity:0.85}).addTo(map)
    .bindPopup("<b>"+esc(it.title)+"</b><br>Score: "+Math.round(it.score)+" | "+(it.price?"\\u20ac"+it.price.toLocaleString():"?")+"<br>"+esc(loc)+
      (it.url?"<br><a href='"+esc(it.url)+"' target='_blank' rel='noopener noreferrer'>Ver</a>":""));
  });
});
</script></body></html>"""


@app.route("/map")
def map_view():
    return render_template_string(MAP_TEMPLATE, max_price=int(_config().get("max_price", 100000)))


def main():
    cfg = _config()
    dash = cfg.get("dashboard", {})
    host = dash.get("host", "127.0.0.1")
    port = dash.get("port", 8050)
    print("\n  EU Auction Scanner Dashboard")
    print(f"  http://{host}:{port}")
    print(f"  http://{host}:{port}/cartas-review")
    print(f"  http://{host}:{port}/map")
    print(f"  http://{host}:{port}/health\n")
    # Flask's debugger executes code typed into the browser; never on by default.
    app.run(host=host, port=port, debug=bool(dash.get("debug", False)))


if __name__ == "__main__":
    main()
