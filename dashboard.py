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
import re
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
    "score": lambda x: x.get("rank", x.get("score", 0)),
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
    cfg = _config()
    p, smtp = cfg.get("proponente", {}), cfg.get("notifications", {})
    from db import FOLLOW_UP_DAYS
    return _page("offers.html", "offers", "Offers", follow_up_days=FOLLOW_UP_DAYS,
                 proponente_ok=all(p.get(k) for k in ("nome", "nif", "morada")),
                 smtp_ok=all(smtp.get(k) for k in ("smtp_host", "smtp_user", "smtp_password")))


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
    kind = args.get("kind", "")          # home / urban_plot / rural_plot / other
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
                  and (not status or it["status"] == status) and (not kind or it["kind"] == kind)]
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
            "homes": sum(1 for it in visible if it["kind"] == "home"),
            "urban_plots": sum(1 for it in visible if it["kind"] == "urban_plot"),
            "rural_plots": sum(1 for it in visible if it["kind"] == "rural_plot"),
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
    """The Portuguese sale type (for the 85% rule)."""
    if item.get("source") == "eleiloes":
        return "LEILAO ELETRONICO"   # bids go in online, not by letter
    m = str(raw.get("modalidade") or "CARTA FECHADA").upper()
    if "NEGOCI" in m:
        return "NEGOCIACAO PARTICULAR"
    if "ADJUDIC" in m:
        return "ADJUDICACAO"
    return "CARTA FECHADA"


def _sale_label(item: dict, raw: dict) -> str:
    """How this sale works, in two or three words, for the Offers list."""
    from letters import ES_SERVICER_SOURCES, PT_BANK_SOURCES, PT_COURT_SOURCES, channel
    src, ch = item.get("source"), channel(item)
    if ch == "lawyer":
        return "Court hearing (lawyer)"
    if ch == "hearing":
        return "Court hearing (in person)"
    if ch == "formal":
        return "Court sale (formal offer)"
    if ch == "online":
        return "Online auction"
    if src in PT_BANK_SOURCES or src in ES_SERVICER_SOURCES:
        return "Bank sale"
    if src in PT_COURT_SOURCES:
        return {"CARTA FECHADA": "Sealed bid", "NEGOCIACAO PARTICULAR": "Private negotiation",
                "ADJUDICACAO": "Adjudication"}[_modalidade(item, raw)]
    return "Offer by letter"


def _contact(item: dict, raw: dict) -> dict:
    """Who handles the sale, when the detail page said: agente de execução (PT),
    the court or agency (ES), the seller's lawyer (FR)."""
    for role, keys in (("Agente de execução", ("agente_nome", "agente_email", "agente_contacto")),
                       ("Court / authority", ("autoridad", "autoridad_email", "autoridad_telefono")),
                       ("Seller's lawyer", ("avocat_nom", "avocat_email", "avocat_tel"))):
        name, email, phone = (raw.get(k) or "" for k in keys)
        if name or email or phone:
            return {"role": role, "name": name, "email": email, "phone": phone}
    return {}


def _format_amount(value) -> str:
    from letters import format_bid
    return format_bid(value) if value is not None else ""


def _offer_view(it: dict, key: str, offer: dict | None = None) -> dict:
    from letters import bid_card, channel, classify_property, guidance, letter_types_for, place_of
    raw = _raw(it)
    area = it.get("area_m2") or 0
    types = [t.public(it) for t in letter_types_for(it)]
    first_offer = next((t for t in types if t["is_offer"]), None)
    return {
        "key": key,
        "id": it["id"],
        "source": it.get("source"),
        "country": it.get("country") or "PT",
        "title": it.get("title") or "",
        "description": (it.get("description") or "")[:400],
        "location": place_of(it),
        "price": it.get("price"),
        "min_price": it.get("min_price"),
        "current_bid": it.get("current_bid"),
        "area_m2": area,
        "date_end": it.get("date_end"),
        "url": it.get("url") or "",
        "processo": str(raw.get("processo") or raw.get("expediente") or "").split(",")[0].strip(),
        "tribunal": raw.get("tribunal") or raw.get("autoridad") or "",
        "modalidade": _modalidade(it, raw),
        "sale": _sale_label(it, raw),
        "channel": channel(it),
        "bid_card": bid_card(it),     # where the bid is made outside the app, to log it here
        "guidance": guidance(it),
        "letter_types": types,
        "categoria": classify_property(it.get("title") or "", it.get("description") or "", area) or "IMOVEL",
        "kind": it.get("kind"),
        "score": it["score"],
        "rank": it.get("rank", it["score"]),
        "reasons": it["reasons"],
        "status": it.get("status"),
        "bid": (first_offer or {}).get("suggested", ""),   # online-only sales: nothing to suggest
        "contact": _contact(it, raw),
        "visit": raw.get("visite") or raw.get("visitable") or "",
        "offer": ({"log_id": offer["id"], "bid": _format_amount(offer.get("bid_amount")),
                   "outcome": offer.get("outcome"), "sent_date": offer.get("sent_date"),
                   "letter_type": offer.get("letter_type"), "is_offer": bool(offer.get("is_offer", 1)),
                   "method": offer.get("method"), "sent_to": offer.get("sent_to") or "",
                   "letter_text": offer.get("letter_text") or "",        # exactly what was sent
                   "letter_subject": offer.get("letter_subject") or ""}
                  if offer else None),
    }


@app.route("/api/offers")
def api_offers():
    from letters import channel, classify_property

    db = get_db()
    try:
        items = load_listings(db, filters=_config().get("filters"), include_hidden=True)
        logs = [dict(r) for r in db.execute("SELECT * FROM carta_log ORDER BY created_at DESC, id DESC")]
    finally:
        db.close()
    by_id = {it["id"]: it for it in items}

    # Out of "To review": anything waiting for an answer, and anything already offered on.
    busy = {log["listing_id"] for log in logs
            if log["outcome"] == "pending" or (log.get("is_offer", 1) and log["outcome"] != "cancelled")}
    review = []
    for it in items:
        if it["id"] in busy or it["category"] != "imoveis" or it["hidden_reason"]:
            continue
        # Strong candidates are sales where the offer is a letter; online auctions and
        # French court sales only appear here if you shortlist them.
        candidate = (it["score"] >= 45 and channel(it) == "letter" and classify_property(
            it.get("title") or "", it.get("description") or "", it.get("area_m2") or 0) is not None)
        if it["status"] == "shortlisted" or candidate:
            review.append(_offer_view(it, it["id"]))
    review.sort(key=lambda c: (c["status"] != "shortlisted", -c["rank"]))

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


def _listing(listing_id: str) -> dict | None:
    db = get_db()
    try:
        found = load_listings(db, filters=_config().get("filters"), include_hidden=True,
                              where="id = ?", params=(listing_id,))
    finally:
        db.close()
    return found[0] if found else None


def _letter_for(listing_id: str, bid: str, bid_text: str, letter_type: str | None = None,
                text: str | None = None):
    """(listing, letter). The letter is None when the listing does not exist or
    `letter_type` is not one of its letters. `text` is the user's edited version."""
    from letters import build_letter, get_type
    item = _listing(listing_id)
    if not item or (letter_type and not get_type(letter_type, item)):
        return item, None
    letter = build_letter(item, bid, bid_text, _config().get("proponente", {}), letter_type or None)
    return item, letter.with_text(text)


def bid_warning(item: dict, bid: str, letter_type: str | None = None) -> str | None:
    """Amounts that cannot work: below 85% of the valor base in Portuguese court
    sales by sealed bid or e-leilão, a French maximum below the mise à prix, an
    Italian offer below the offerta minima, a German bid below the ZVG limits."""
    from letters import DE_COURT_SOURCES, IT_COURT_SOURCES, PT_COURT_SOURCES, parse_bid
    price, value = item.get("price"), parse_bid(bid) or 0
    if not price:
        return None
    if (item.get("source") in PT_COURT_SOURCES | {"eleiloes"}
            and letter_type in (None, "pt_carta_fechada", "online")
            and _modalidade(item, _raw(item)) in ("CARTA FECHADA", "LEILAO ELETRONICO")):
        minimum = item.get("min_price") or price * 0.85
        if value < minimum:
            return (f"This offer is below the minimum of EUR {minimum:,.0f} (85% of the base value), "
                    "so it will normally not be accepted. Confirm the sale type with the agente de execução.")
    if letter_type == "fr_mandat" and value < price:
        return (f"Bidding starts at the mise à prix of EUR {price:,.0f}, so a maximum below it "
                "cannot win. Final prices are usually well above it.")
    if letter_type == "online" and item.get("source") in IT_COURT_SOURCES and value < price * 0.75:
        return (f"Below the offerta minima of EUR {price * 0.75:,.0f} (75% of the base price): "
                "such an offer is not admissible.")
    if letter_type == "online" and item.get("source") in DE_COURT_SOURCES and value < price * 0.7:
        return (f"Below 70% of the Verkehrswert (EUR {price * 0.7:,.0f}): at the first hearing the creditor "
                f"can ask for the sale to be refused, and bids under EUR {price * 0.5:,.0f} (half) are "
                "refused outright. At a later hearing these limits no longer apply.")
    return None


MAX_LETTER = 20_000   # characters; an edited letter is sent back to the server


def _letter_args(src) -> tuple[str, str, str, str, str]:
    """(listing id, amount, amount in words, letter type, edited text) from a query or JSON body."""
    return (src.get("id", ""), src.get("bid", ""), src.get("bid_text", ""), src.get("type") or "",
            (src.get("text") or "")[:MAX_LETTER])


@app.route("/api/offers/letter")
def api_offer_letter():
    listing_id, bid, bid_text, ltype, _text = _letter_args(request.args)
    item, letter = _letter_for(listing_id, bid, bid_text, ltype)
    if not item:
        return jsonify({"error": "no such listing"}), 404
    if not letter:
        return jsonify({"error": f"no letter of type {ltype!r} for this listing"}), 400
    return jsonify({"text": letter.text, "subject": letter.subject, "to": letter.to_email,
                    "bid_text": letter.extra.get("bid_text", ""), "filename": letter.filename,
                    "type": letter.type_key, "is_offer": letter.is_offer,
                    "warning": bid_warning(item, bid, letter.type_key) if letter.is_offer else None})


@app.route("/api/offers/warning")
def api_offer_warning():
    item = _listing(request.args.get("id", ""))
    if not item:
        return jsonify({"error": "no such listing"}), 404
    return jsonify({"warning": bid_warning(item, request.args.get("bid", ""), request.args.get("type") or None)})


def _pdf_response(data: bytes, filename: str) -> Response:
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/offers/letter.pdf", methods=["GET", "POST"])
def api_offer_letter_pdf():
    """The letter as PDF. POST (JSON) when it carries the user's edited text."""
    from letters import letter_pdf
    src = (request.get_json(silent=True) or {}) if request.method == "POST" else request.args
    _item, letter = _letter_for(*_letter_args(src))
    if not letter:
        abort(404)
    return _pdf_response(letter_pdf(letter), letter.filename)


@app.route("/api/offers/log/<int:log_id>.pdf")
def api_offer_log_pdf(log_id):
    """The letter as it was sent. Rows logged before letters were stored are rebuilt."""
    from letters import letter_pdf, text_pdf
    db = get_db()
    try:
        row = db.execute("SELECT * FROM carta_log WHERE id = ?", (log_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        abort(404)
    if row["letter_text"]:
        return _pdf_response(text_pdf(row["letter_text"], ref=f"Ref: {row['processo'] or row['listing_id']}"),
                             row["letter_filename"] or f"letter_{log_id}.pdf")
    ltype = row["letter_type"] if row["letter_type"] != "online" else None
    _item, letter = _letter_for(row["listing_id"] or "", _format_amount(row["bid_amount"]), "", ltype)
    if not letter:
        abort(404)
    return _pdf_response(letter_pdf(letter), letter.filename)


def _log_sent(item: dict, *, letter=None, bid: str = "", method: str, sent_to: str = "",
              notes: str = "") -> int:
    from letters import parse_bid
    raw = _raw(item)
    is_offer = letter.is_offer if letter else True
    db = get_db()
    try:
        cur = db.execute("""
            INSERT INTO carta_log (listing_id, processo, tribunal, country, sent_date, bid_amount,
                                   method, outcome, notes, created_at, letter_type, is_offer, sent_to,
                                   letter_text, letter_subject, letter_filename)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            item["id"], letter.processo if letter else str(raw.get("processo") or "").split(",")[0].strip(),
            raw.get("tribunal") or raw.get("autoridad"), item.get("country") or "PT",
            datetime.now().strftime("%Y-%m-%d"), parse_bid(bid) if is_offer else None,
            method, "pending", notes, datetime.now(timezone.utc).isoformat(),
            letter.type_key if letter else "online", int(is_offer), sent_to or None,
            letter.text if letter else None, letter.subject if letter else None,
            letter.filename if letter else None))
        db.commit()
        if is_offer and item.get("status") == "shortlisted":
            set_listing_status(db, item["id"], None)   # it is an offer now, not a shortlist entry
        return cur.lastrowid
    finally:
        db.close()


@app.route("/api/offers/sent", methods=["POST"])
def api_offer_sent():
    """Record a letter you sent yourself (post, your own e-mail, your lawyer),
    or with method "online" a bid you placed on an auction site."""
    data = request.get_json(silent=True) or {}
    listing_id, bid, bid_text, ltype, text = _letter_args(data)
    if data.get("method") == "online":
        item, letter = _listing(listing_id), None
        if not item:
            return jsonify({"error": "no such listing"}), 404
    else:
        item, letter = _letter_for(listing_id, bid, bid_text, ltype, text)
        if not item:
            return jsonify({"error": "no such listing"}), 404
        if not letter:
            return jsonify({"error": f"no letter of type {ltype!r} for this listing"}), 400
    method = data.get("method") or ("online" if item.get("source") == "eleiloes" else "email")
    log_id = _log_sent(item, letter=letter, bid=bid, method=method,
                       sent_to=data.get("to", ""), notes=data.get("notes", ""))
    return jsonify({"ok": True, "log_id": log_id})


@app.route("/api/offers/email", methods=["POST"])
def api_offer_email():
    """Send the letter from here by e-mail, with its PDF attached, and log it."""
    from letters import letter_pdf
    from notifications import send_letter
    data = request.get_json(silent=True) or {}
    item, letter = _letter_for(*_letter_args(data))
    if not item:
        return jsonify({"error": "no such listing"}), 404
    if not letter:
        return jsonify({"error": "no such letter for this listing"}), 400
    to = (data.get("to") or letter.to_email or "").strip()
    cfg = _config()
    error = send_letter(cfg.get("notifications", {}), to, letter.subject, letter.text,
                        letter_pdf(letter), letter.filename,
                        reply_to=cfg.get("proponente", {}).get("email", ""))
    if error:
        return jsonify({"error": error}), 400
    log_id = _log_sent(item, letter=letter, bid=data.get("bid", ""), method="email", sent_to=to)
    return jsonify({"ok": True, "log_id": log_id, "to": to})


def _calendar_event(it: dict):
    from ics_export import WHAT, event
    from letters import channel, guidance, place_of
    raw = _raw(it)
    contact = _contact(it, raw)
    notes = [guidance(it)]
    if contact:
        notes.append(f"{contact['role']}: " + " · ".join(v for v in (contact["name"], contact["phone"],
                                                                       contact["email"]) if v))
    if raw.get("visite") or raw.get("visitable"):
        notes.append(f"Visits: {raw.get('visite') or raw.get('visitable')}")
    notes.append(f"Listing: {it.get('url') or it['id']}")
    return event(it, what=WHAT[channel(it)], description="\n\n".join(notes),
                 location=raw.get("tribunal") or place_of(it))


@app.route("/api/offers/calendar.ics")
def api_offers_calendar():
    """Sale dates as a calendar file: one listing (?id=), or everything you are
    working on: shortlisted listings and offers waiting for an answer."""
    from common import effective_end, utcnow
    from ics_export import calendar
    listing_id = request.args.get("id")
    filters = _config().get("filters")
    db = get_db()
    try:
        if listing_id:
            items = load_listings(db, filters=filters, include_hidden=True, where="id = ?",
                                  params=(listing_id,))
        else:
            pending = {r[0] for r in db.execute(
                "SELECT listing_id FROM carta_log WHERE outcome = 'pending' AND listing_id IS NOT NULL")}
            items = [it for it in load_listings(db, filters=filters, include_hidden=True)
                     if it["status"] == "shortlisted" or it["id"] in pending]
    finally:
        db.close()
    now = utcnow()
    events = [ev for it in items
              if (listing_id or (effective_end(it.get("date_end")) or now) > now)
              and (ev := _calendar_event(it))]
    if listing_id and not events:
        return jsonify({"error": "this listing has no sale date"}), 404
    name = (f"sale-{re.sub(r'[^A-Za-z0-9.-]+', '-', listing_id)}.ics" if listing_id
            else "auction-deadlines.ics")
    return Response(calendar(events), mimetype="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.route("/api/analyze-property", methods=["POST"])
def api_analyze_property():
    from analysis import analyze_property
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "No data"}), 400
    filters = _config().get("filters") or {}
    data = {**data, "targets": {k: filters.get(k) for k in ("rural_min_m2", "rural_max_eur_m2")}}
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


OUTCOMES = ("pending", "won", "lost", "cancelled", "expired", "answered")


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


PROPONENTE_KEYS = ("nome", "nif", "morada", "email", "telefone", "localidade")


@app.route("/api/proponente")
def api_proponente():
    p = _config().get("proponente", {})
    return jsonify({k: p.get(k, "") for k in PROPONENTE_KEYS})


# ─── Settings ────────────────────────────────────────────────────────

# What the Settings page may change: section → allowed keys (None = a scalar).
EDITABLE = {
    "max_price": None,
    "filters": ("countries", "exclude_keywords", "min_score", "min_area_m2", "rural_min_m2",
                "rural_max_eur_m2"),
    "proponente": PROPONENTE_KEYS,
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
