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
const FLAGS = {"PT":"&#x1F1F5;&#x1F1F9;","ES":"&#x1F1EA;&#x1F1F8;","FR":"&#x1F1EB;&#x1F1F7;","IT":"&#x1F1EE;&#x1F1F9;","HR":"&#x1F1ED;&#x1F1F7;","NL":"&#x1F1F3;&#x1F1F1;"};
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


def main():
    from config import load_config
    cfg = load_config()
    dash = cfg.get("dashboard", {})
    host = dash.get("host", "127.0.0.1")
    port = dash.get("port", 8050)
    print(f"\n  EU Auction Scanner Dashboard")
    print(f"  http://{host}:{port}\n")
    app.run(host=host, port=port, debug=True)


if __name__ == "__main__":
    main()
