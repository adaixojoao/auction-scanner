"""
The app's web interface: Listings, Offers, Map, Sources and Settings.

Normally started by the desktop app (app.py), which opens it in its own
window. `python dashboard.py` still works and serves http://127.0.0.1:8050.

Scores, visibility and filters come from db.load_listings() — the same code
the report and the alerts use — so a listing scores the same everywhere.
Scraped text and URLs are untrusted: URLs are reduced to http(s) on the way
in (common.safe_url) and everything is escaped on the way out.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file

from common import COUNTRY_NAMES, FLAGS, lock_holder
from db import connect, hidden_category, load_listings, set_listing_status, source_health

HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["LAST_HEARTBEAT"] = None   # read by app.py to close with the window

NAV = [
    ("listings", "Listings", "/"),
    ("offers", "Offers", "/offers"),
    ("map", "Map", "/map"),
    ("sources", "Sources", "/sources"),
    ("settings", "Settings", "/settings"),
]

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


# ─── Local-app safety ────────────────────────────────────────────────
# The app listens on 127.0.0.1 only, but any web page open in the browser can
# still send requests to it. Refuse requests that name another host (DNS
# rebinding) and state changes coming from another origin (CSRF).

@app.before_request
def _guard_local():
    if app.config.get("TESTING"):
        return None
    dash = _config().get("dashboard", {})
    port = dash.get("port", 8050)
    if dash.get("host", "127.0.0.1") in ("127.0.0.1", "localhost"):
        if request.host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            abort(403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            abort(403)
    return None


@app.context_processor
def _layout_context():
    """Everything base.html needs, on every page."""
    offers_waiting = 0
    try:
        db = get_db()
        try:
            offers_waiting = db.execute(
                "SELECT COUNT(*) FROM listing_status WHERE status = 'shortlisted'").fetchone()[0]
        finally:
            db.close()
    except Exception:
        pass
    return {"nav": NAV, "flags": FLAGS, "offers_waiting": offers_waiting}


def _page(template: str, key: str, title: str, **extra):
    cfg = _config()
    return render_template(template, active=key, page_title=title,
                           countries=list(COUNTRY_NAMES.items()),
                           max_price=int(cfg.get("max_price", 50000)), **extra)


# ─── Pages ───────────────────────────────────────────────────────────

@app.route("/")
def listings_page():
    return _page("listings.html", "listings", "Listings")


@app.route("/offers")
def offers_page():
    p = _config().get("proponente", {})
    return _page("offers.html", "offers", "Offers",
                 proponente_ok=all(p.get(k) for k in ("nome", "nif", "morada")))


@app.route("/map")
def map_page():
    return _page("map.html", "map", "Map")


@app.route("/sources")
def sources_page():
    return _page("sources.html", "sources", "Sources")


@app.route("/settings")
def settings_page():
    return _page("settings.html", "settings", "Settings")


# Old addresses from before the pages were unified.
@app.route("/cartas-review")
def _old_cartas():
    return redirect("/offers")


@app.route("/health")
def _old_health():
    return redirect("/sources")


# ─── App plumbing ────────────────────────────────────────────────────

@app.route("/api/ping")
def api_ping():
    return jsonify({"app": "auction-scanner"})


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    app.config["LAST_HEARTBEAT"] = time.monotonic()
    return jsonify({"ok": True})


# ─── Listings ────────────────────────────────────────────────────────

def _public(item: dict) -> dict:
    """What the browser gets: no raw_json, a short description."""
    out = {k: v for k, v in item.items() if k != "raw_json"}
    if out.get("description"):
        out["description"] = out["description"][:300]
    out["hidden_category"] = hidden_category(item.get("hidden_reason"))
    return out


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
    status = args.get("status", "")
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
        loaded = load_listings(db, filters=_config().get("filters"), include_hidden=True,
                               where=" AND ".join(conditions), params=params)
        loaded = [it for it in loaded
                  if it["score"] >= min_score and (not properties_only or it["category"] == "imoveis")
                  and (not status or it["status"] == status)]
        hidden_counts = Counter(hidden_category(it["hidden_reason"]) for it in loaded
                                if it["hidden_reason"])
        if show_hidden:
            items = [it for it in loaded if it["hidden_reason"]]
        else:
            items = [it for it in loaded if not it["hidden_reason"]]

        items.sort(key=SORT_KEYS.get(sort, SORT_KEYS["score"]), reverse=(direction == "desc"))
        total = len(items)
        page_items = [_public(it) for it in items[(page - 1) * per_page:page * per_page]]
        visible = [it for it in loaded if not it["hidden_reason"]]
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
            "shortlisted": sum(1 for it in visible if it["status"] == "shortlisted"),
            "hidden": dict(hidden_counts),
            "sources_failing": sum(1 for h in health if h["state"] in ("error", "broken")),
        },
        "last_scrape": last_scrape,
    })


@app.route("/api/listings/status", methods=["POST"])
def api_listing_status():
    data = request.get_json(silent=True) or {}
    if not data.get("id"):
        return jsonify({"error": "id required"}), 400
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM listings WHERE id = ?", (data["id"],)).fetchone():
            return jsonify({"error": "no such listing"}), 404
        try:
            set_listing_status(db, data["id"], data.get("status"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    finally:
        db.close()
    return jsonify({"ok": True})


# ─── Scanning & sources ──────────────────────────────────────────────

@app.route("/api/scan", methods=["GET"])
def api_scan_status():
    from pipeline import scan_status
    db = get_db()
    try:
        return jsonify(scan_status(db))
    finally:
        db.close()


def _start_scan(countries=None, source_names=None):
    from pipeline import ScanBusy, run_scan

    def work():
        try:
            run_scan(countries=countries, source_names=source_names)
        except ScanBusy:
            pass
        except Exception:
            app.logger.exception("Scan failed")
    threading.Thread(target=work, name="scan", daemon=True).start()


@app.route("/api/scan", methods=["POST"])
def api_scan_start():
    from pipeline import LOCK_PATH, scan_status
    data = request.get_json(silent=True) or {}
    source = data.get("source")
    countries = data.get("countries")
    if source and source not in _registry():
        return jsonify({"error": f"unknown source {source}"}), 400
    if countries is not None and (not isinstance(countries, list)
                                  or any(c not in COUNTRY_NAMES for c in countries)):
        return jsonify({"error": "countries must be a list of country codes, or null"}), 400
    db = get_db()
    try:
        busy = scan_status(db)["running"]
    finally:
        db.close()
    if busy or lock_holder(LOCK_PATH):
        return jsonify({"error": "A scan is already running"}), 409
    _start_scan(countries=countries, source_names=[source] if source else None)
    return jsonify({"ok": True}), 202


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
        h["description"] = src.description if src else None
    return jsonify(health)


# ─── Offers ──────────────────────────────────────────────────────────

def _raw(item: dict) -> dict:
    try:
        return json.loads(item.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return {}


def _modalidade(item: dict, raw: dict) -> str:
    if item.get("source") == "eleiloes":
        return "LEILAO ELETRONICO"   # bids go in online, not by letter
    m = str(raw.get("modalidade") or "CARTA FECHADA").upper()
    if "NEGOCI" in m:
        return "NEGOCIACAO PARTICULAR"
    if "ADJUDIC" in m:
        return "ADJUDICACAO"
    return "CARTA FECHADA"


def _format_amount(value) -> str:
    from cartas import format_bid
    return format_bid(value) if value is not None else ""


def _offer_view(it: dict, key: str, offer: dict | None = None) -> dict:
    from cartas import classify_property, suggest_bid
    raw = _raw(it)
    area = it.get("area_m2") or 0
    cat = classify_property(it.get("title") or "", it.get("description") or "", area) or "IMOVEL"
    bid_val, _ = suggest_bid(cat, it.get("price"), area)
    return {
        "key": key,
        "id": it["id"],
        "country": it.get("country") or "PT",
        "title": it.get("title") or "",
        "description": (it.get("description") or "")[:400],
        "location": ", ".join(filter(None, [it.get("concelho"), it.get("district")])),
        "price": it.get("price"),
        "min_price": it.get("min_price"),
        "current_bid": it.get("current_bid"),
        "area_m2": area,
        "date_end": it.get("date_end"),
        "url": it.get("url") or "",
        "processo": str(raw.get("processo") or "").split(",")[0].strip(),
        "tribunal": raw.get("tribunal", ""),
        "modalidade": _modalidade(it, raw),
        "categoria": cat,
        "score": it["score"],
        "reasons": it["reasons"],
        "status": it.get("status"),
        "bid": bid_val,
        "agente_nome": raw.get("agente_nome", ""),
        "agente_email": raw.get("agente_email", ""),
        "agente_contacto": raw.get("agente_contacto", ""),
        "offer": ({"log_id": offer["id"], "bid": _format_amount(offer.get("bid_amount")),
                   "outcome": offer.get("outcome"), "sent_date": offer.get("sent_date")}
                  if offer else None),
    }


@app.route("/api/offers")
def api_offers():
    from cartas import classify_property

    db = get_db()
    try:
        items = load_listings(db, filters=_config().get("filters"), include_hidden=True)
        logs = [dict(r) for r in db.execute("SELECT * FROM carta_log ORDER BY created_at DESC, id DESC")]
    finally:
        db.close()
    by_id = {it["id"]: it for it in items}

    offered = {log["listing_id"] for log in logs if log["outcome"] != "cancelled"}
    review = []
    for it in items:
        if it["id"] in offered or it["category"] != "imoveis" or it["hidden_reason"]:
            continue
        # e-leilões is bid on online, so it only appears here if you shortlist it.
        candidate = (it["score"] >= 45 and it.get("source") != "eleiloes" and classify_property(
            it.get("title") or "", it.get("description") or "", it.get("area_m2") or 0) is not None)
        if it["status"] == "shortlisted" or candidate:
            review.append(_offer_view(it, it["id"]))
    review.sort(key=lambda c: (c["status"] != "shortlisted", -c["score"]))

    sent, closed = [], []
    for log in logs:
        it = by_id.get(log["listing_id"])
        if not it:
            continue
        view = _offer_view(it, f"log:{log['id']}", log)
        (sent if log["outcome"] == "pending" else closed).append(view)

    rejected = [_offer_view(it, it["id"]) for it in items
                if it["status"] == "dismissed" and it["category"] == "imoveis"]
    return jsonify({"review": review[:150], "sent": sent, "closed": closed, "rejected": rejected[:150]})


def _letter_for(listing_id: str, bid: str, bid_text: str):
    from cartas import build_letter
    db = get_db()
    try:
        found = load_listings(db, include_hidden=True, where="id = ?", params=(listing_id,))
    finally:
        db.close()
    if not found:
        return None, None
    item = found[0]
    return item, build_letter(item, bid, bid_text, _config().get("proponente", {}))


def bid_warning(item: dict, bid: str) -> str | None:
    """Offers below the announced minimum are normally refused in PT carta
    fechada / e-leilão sales (valor anunciado = 85% do valor base)."""
    from cartas import parse_bid
    kind = _modalidade(item, _raw(item))
    if (item.get("country") or "PT") != "PT" or kind not in ("CARTA FECHADA", "LEILAO ELETRONICO"):
        return None
    if not item.get("price"):
        return None
    minimum = item.get("min_price") or item["price"] * 0.85
    if (parse_bid(bid) or 0) >= minimum:
        return None
    return (f"This offer is below the minimum of EUR {minimum:,.0f} (85% of the base value), "
            "so it will normally not be accepted. Confirm the sale type with the agente de execução.")


@app.route("/api/offers/letter")
def api_offer_letter():
    bid = request.args.get("bid", "")
    item, letter = _letter_for(request.args.get("id", ""), bid, request.args.get("bid_text", ""))
    if not letter:
        return jsonify({"error": "no such listing"}), 404
    return jsonify({"text": letter.text, "subject": letter.subject, "to": letter.to_email,
                    "bid_text": letter.extra.get("bid_text", ""), "filename": letter.filename,
                    "warning": bid_warning(item, bid)})


@app.route("/api/offers/letter.pdf")
def api_offer_letter_pdf():
    from cartas import letter_pdf
    _item, letter = _letter_for(request.args.get("id", ""), request.args.get("bid", ""),
                                request.args.get("bid_text", ""))
    if not letter:
        abort(404)
    return Response(letter_pdf(letter), mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{letter.filename}"'})


@app.route("/api/offers/sent", methods=["POST"])
def api_offer_sent():
    from cartas import parse_bid
    data = request.get_json(silent=True) or {}
    item, letter = _letter_for(data.get("id", ""), data.get("bid", ""), data.get("bid_text", ""))
    if not letter:
        return jsonify({"error": "no such listing"}), 404
    db = get_db()
    try:
        cur = db.execute("""
            INSERT INTO carta_log (listing_id, processo, tribunal, country, sent_date, bid_amount,
                                   method, outcome, notes, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            item["id"], letter.processo, _raw(item).get("tribunal"), item.get("country") or "PT",
            datetime.now().strftime("%Y-%m-%d"), parse_bid(data.get("bid")),
            data.get("method") or ("online" if item.get("source") == "eleiloes" else "email"),
            "pending", data.get("notes", ""),
            datetime.now(timezone.utc).isoformat()))
        db.commit()
        log_id = cur.lastrowid
        if item.get("status") == "shortlisted":
            set_listing_status(db, item["id"], None)   # it is an offer now, not a shortlist entry
    finally:
        db.close()
    return jsonify({"ok": True, "log_id": log_id})


@app.route("/api/analyze-property", methods=["POST"])
def api_analyze_property():
    from analysis import analyze_property
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    result = analyze_property(data)
    return jsonify(result), (502 if "verdict" not in result else 200)


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


OUTCOMES = ("pending", "won", "lost", "cancelled", "expired")


@app.route("/api/carta-log/<int:log_id>", methods=["PATCH"])
def update_carta_log(log_id):
    data = request.get_json(silent=True) or {}
    if data.get("outcome") not in OUTCOMES:
        return jsonify({"error": f"outcome must be one of {', '.join(OUTCOMES)}"}), 400
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


@app.route("/api/proponente")
def api_proponente():
    p = _config().get("proponente", {})
    return jsonify({k: p.get(k, "") for k in ("nome", "nif", "morada", "email", "localidade")})


# ─── Settings ────────────────────────────────────────────────────────

# What the Settings page may change: section → allowed keys (None = a scalar).
EDITABLE = {
    "max_price": None,
    "filters": ("countries", "exclude_keywords", "min_score", "min_area_m2"),
    "proponente": ("nome", "nif", "morada", "email", "localidade"),
    "schedule": ("while_app_open", "pt_every_hours", "eu_every_hours"),
    "telegram": ("enabled", "token", "chat_id", "min_score", "deadline_min_score"),
    "notifications": ("enabled", "smtp_host", "smtp_port", "smtp_user", "smtp_password",
                      "to_emails", "min_score"),
    "report": ("desktop_copy",),
}


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    cfg = _config()
    out = {}
    for section, keys in EDITABLE.items():
        if keys is None:
            out[section] = cfg.get(section)
        else:
            out[section] = {k: (cfg.get(section) or {}).get(k) for k in keys}
    return jsonify(out)


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    from config import update_config
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object required"}), 400
    changes = {}
    for section, keys in EDITABLE.items():
        if section not in data:
            continue
        if keys is None:
            if data[section] is not None:
                changes[section] = data[section]
            continue
        if not isinstance(data[section], dict):
            return jsonify({"error": f"{section} must be an object"}), 400
        vals = {k: v for k, v in data[section].items() if k in keys and v is not None}
        if vals:
            changes[section] = vals
    countries = (changes.get("filters") or {}).get("countries")
    if countries is not None and any(c not in COUNTRY_NAMES for c in countries):
        return jsonify({"error": "unknown country code"}), 400
    update_config(changes)
    return jsonify({"ok": True})


def _task_installed() -> bool:
    from scheduler import TASK_NAME
    try:
        return subprocess.run(["schtasks", "/query", "/tn", TASK_NAME],
                              capture_output=True).returncode == 0
    except OSError:
        return False


@app.route("/api/background", methods=["GET"])
def api_background():
    supported = sys.platform == "win32"
    return jsonify({"supported": supported, "installed": supported and _task_installed()})


@app.route("/api/background", methods=["POST"])
def api_background_set():
    if sys.platform != "win32":
        return jsonify({"error": "The background task is only available on Windows"}), 400
    import scheduler
    if (request.get_json(silent=True) or {}).get("enabled"):
        scheduler.install_task()
    else:
        scheduler.remove_task()
    return jsonify({"ok": True, "installed": _task_installed()})


# ─── Exports ─────────────────────────────────────────────────────────

@app.route("/export/report.<ext>")
def export_report(ext):
    if ext not in ("md", "docx", "pdf"):
        abort(404)
    from pipeline import build_report
    cfg = _config()
    cfg = {**cfg, "report": {**cfg.get("report", {}), "desktop_copy": False}}
    db = get_db()
    try:
        md_path = build_report(db, cfg)
    finally:
        db.close()
    path = os.path.join(os.path.dirname(md_path), f"report.{ext}")
    if not os.path.exists(path):
        return jsonify({"error": f"report.{ext} could not be generated "
                                 "(is python-docx / fpdf2 installed?)"}), 500
    stamp = datetime.now().strftime("%Y-%m-%d")
    return send_file(path, as_attachment=True, download_name=f"Auction-Report-{stamp}.{ext}")


def main():
    cfg = _config()
    dash = cfg.get("dashboard", {})
    host = dash.get("host", "127.0.0.1")
    port = dash.get("port", 8050)
    print(f"\n  Auction Scanner — http://{host}:{port}")
    print("  (the desktop app, app.py, opens this in its own window)\n")
    # Flask's debugger executes code typed into the browser; never on by default.
    app.run(host=host, port=port, debug=bool(dash.get("debug", False)))


if __name__ == "__main__":
    main()
