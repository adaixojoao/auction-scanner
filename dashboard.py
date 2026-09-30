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
import logging
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

import geo
from common import COUNTRY_NAMES, FLAGS, make_session, price_to_pay, safe_url
from locks import lock_holder
from db import connect, hidden_category, load_listings, set_listing_status, source_health

HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["LAST_HEARTBEAT"] = None   # read by app.py to close with the window

NAV = [
    ("listings", "Listings", "/"),
    ("offers", "Offers", "/offers"),
    ("outcomes", "Outcomes", "/outcomes"),
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


@app.route("/outcomes")
def outcomes_page():
    return _page("outcomes.html", "outcomes", "Outcomes")


@app.route("/map")
def map_page():
    return _page("map.html", "map", "Map")


@app.route("/sources")
def sources_page():
    return _page("sources.html", "sources", "Sources")


@app.route("/settings")
def settings_page():
    import checklist
    from scoring import WEIGHTS
    cfg = _config()
    routes = [(route, label, [(it.key, it.label, it.key in checklist.blocking_keys(route, cfg)) for it in items])
              for route, (label, items) in checklist.TEMPLATES.items()]
    return _page("settings.html", "settings", "Settings", weights=list(WEIGHTS.items()), checklist_routes=routes)


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


def _climate_of(item: dict) -> dict:
    from scoring import climate_score
    return climate_score(item.get("climate"), item.get("kind"))


def _climate_matches(item: dict, grade: str, exact_only: bool) -> bool:
    from scoring import GRADE_ORDER
    c = _climate_of(item)
    if exact_only and c["confidence"] != "exact":
        return False
    if not grade:
        return True
    if grade in ("poor", "unknown"):
        return c["grade"] == grade
    return c["grade"] in GRADE_ORDER and GRADE_ORDER.index(c["grade"]) >= GRADE_ORDER.index(grade)


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
    climate_grade = args.get("climate", "")   # excellent / good / caution: at least; poor / unknown: exactly
    climate_exact = args.get("climate_exact", "0") == "1"
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
        if climate_grade or climate_exact:
            loaded = [it for it in loaded if _climate_matches(it, climate_grade, climate_exact)]
        hidden_counts = Counter(hidden_category(it["hidden_reason"]) for it in loaded
                                if it["hidden_reason"])
        if show_hidden:
            items = [it for it in loaded if it["hidden_reason"]]
        else:
            items = [it for it in loaded if not it["hidden_reason"]]

        # Only the best N (Settings → Show at most), then sorted as asked.
        found = len(items)
        cap = max(1, _num(_config().get("max_listings"), 100, int))
        items = sorted(items, key=SORT_KEYS["score"], reverse=True)[:cap]
        items.sort(key=SORT_KEYS.get(sort, SORT_KEYS["score"]), reverse=(direction == "desc"))
        total = len(items)
        page_items = [{**_public(it), "climate_grade": _climate_of(it)["grade"]}
                      for it in items[(page - 1) * per_page:page * per_page]]
        visible = [it for it in loaded if not it["hidden_reason"]]
        health = source_health(db, _registry())
        last_scrape = db.execute("SELECT MAX(timestamp) FROM scrape_log").fetchone()[0]
    finally:
        db.close()

    return jsonify({
        "items": page_items,
        "total": total,
        "found": found,
        "cap": cap,
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


@app.route("/api/listing")
def api_listing_detail():
    """Everything known about one listing, for the detail panel on Listings."""
    import costs
    import listing_info
    listing_id = request.args.get("id", "")
    db = get_db()
    try:
        found = load_listings(db, filters=_config().get("filters"), include_hidden=True,
                              where="id = ?", params=(listing_id,))
        if not found:
            return jsonify({"error": "no such listing"}), 404
        it = found[0]
        related = listing_info.related(db, it)
        results = listing_info.past_results(db, it)
        lots = listing_info.same_case_lots(db, it)
        history = geo.location_history(db, it["id"])
    finally:
        db.close()
    return jsonify({
        "id": it["id"], "title": it.get("title"), "source": it.get("source"),
        "url": safe_url(it.get("url")), "image": safe_url(it.get("image_url")),
        "description": (it.get("description") or "")[:4000],
        "score": it["score"], "rank": it.get("rank", it["score"]), "reasons": it.get("reasons") or [],
        "excellent": it.get("excellent"),
        "facts": listing_info.facts(it), "related": related, "same_case": lots, "past_results": results,
        "costs": costs.estimate(it),
        "bid_cap": _bid_cap(it),
        "climate": listing_info.climate_panel(it),
        "location": {**geo.location_confidence(it), "history": history, "country": it.get("country") or "PT"},
        "how_to_find": listing_info.how_to_find(it),
        "official": listing_info.official_records(it),
        "street_view": listing_info.street_view(it, (_config().get("maps") or {}).get("google_key", "")),
    })

@app.route("/api/listing/location", methods=["POST"])
def api_listing_location():
    """Verify a listing's position: coordinates, an address (OpenStreetMap) or a
    Spanish cadastral reference (the Catastro). Asks first ({"confirm"}) when
    the new position looks wrong; resend with "confirm": true to keep it."""
    import cadastre
    import climate
    data = request.get_json(silent=True) or {}
    method, text = data.get("method"), str(data.get("value") or "").strip()[:300]
    if method not in geo.VERIFY_METHODS or not text:
        return jsonify({"error": "method (coordinates / address / cadastre) and value required"}), 400
    db = get_db()
    try:
        found = load_listings(db, include_hidden=True, where="id = ?", params=(str(data.get("id") or ""),))
        if not found:
            return jsonify({"error": "no such listing"}), 404
        item = found[0]
        if method == "coordinates":
            ll = geo.parse_coordinates(text)
            if not ll:
                return jsonify({"error": "Paste the latitude and longitude, e.g. 38.7223, -9.1393, "
                                         "or a Google Maps link with @latitude,longitude in it."}), 400
            pos = {"lat": ll[0], "lon": ll[1], "precision": "verified"}
        elif method == "cadastre":
            if (item.get("country") or "PT") != "ES":
                return jsonify({"error": "Only Spanish cadastral references can be looked up here (the "
                                         "Catastro). Paste the coordinates from a map instead."}), 400
            rc = cadastre.reference_given(text)
            if not rc:
                return jsonify({"error": "That is not a Spanish cadastral reference "
                                         "(14 characters, e.g. 3589701UK6938N)."}), 400
            try:
                hit = cadastre.catastro_position(make_session(), rc)
            except Exception as e:  # noqa: BLE001 — say so rather than fail
                return jsonify({"error": f"The Catastro did not answer ({type(e).__name__}). Try again later."}), 502
            if not hit:
                return jsonify({"error": "The Catastro does not know that reference."}), 400
            pos, text = {"lat": hit["lat"], "lon": hit["lon"], "precision": "verified"}, rc
        else:
            try:
                pos = geo.lookup_address(make_session(), text, item.get("country"))
            except Exception as e:  # noqa: BLE001
                return jsonify({"error": f"OpenStreetMap did not answer ({type(e).__name__}). Try again later."}), 502
            if not pos:
                return jsonify({"error": "OpenStreetMap did not find that address. Try fewer words "
                                         "(street, village, town), or paste coordinates."}), 400
        towns = geo.town_index(db)
        doubts = geo.verify_doubts(item, pos, towns)
        if doubts and not data.get("confirm"):
            return jsonify({"confirm": " ".join(doubts) + " Keep it anyway?"})
        geo.verify_location(db, item, pos, method, text)
        if climate.available():
            climate.assess_pending(db, [item], towns, limit=1)      # the local layers: no network
    finally:
        db.close()
    return jsonify({"ok": True, "location": geo.location_confidence(item)})


@app.route("/api/listing/location/clear", methods=["POST"])
def api_listing_location_clear():
    """Go back to the scanner's own position (the history keeps yours)."""
    import climate
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        found = load_listings(db, include_hidden=True, where="id = ?", params=(str(data.get("id") or ""),))
        if not found:
            return jsonify({"error": "no such listing"}), 404
        item = found[0]
        if not geo.clear_location(db, item):
            return jsonify({"error": "this listing has no position of yours"}), 400
        if climate.available():
            climate.assess_pending(db, [item], geo.town_index(db), limit=1)
    finally:
        db.close()
    return jsonify({"ok": True, "location": geo.location_confidence(item)})


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


def _bid_cap(item: dict, cfg: dict | None = None) -> dict:
    import bidcap
    return bidcap.for_item(item, cfg or _config())


def _record_bid_cap(item: dict, log_id: int, bid: str) -> None:
    import bidcap
    from letters import parse_bid
    db = get_db()
    try:
        bidcap.record_offer(db, log_id, bidcap.for_item(item, _config()), parse_bid(bid))
    finally:
        db.close()


def _offer_view(it: dict, key: str, offer: dict | None = None, checks: dict | None = None,
                cfg: dict | None = None) -> dict:
    import checklist
    import listing_info
    from letters import bid_card, channel, classify_property, guidance, letter_types_for, place_of
    raw = _raw(it)
    area = it.get("area_m2") or 0
    types = [t.public(it) for t in letter_types_for(it)]
    first_offer = next((t for t in types if t["is_offer"]), None)
    ck = checklist.build(it, (checks or {}).get(it["id"], {}), cfg)
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
        "climate": listing_info.climate_panel(it),
        "location_check": geo.location_confidence(it),
        "location_gate": location_gate_applies(it),     # with /api/offers' location_gate_mode
        "checklist": {"summary": ck["summary"], "blocking_left": len(ck["blocking_left"]),
                      "concerns": len(ck["concerns"])},
        "bid_cap": _bid_cap(it, cfg),
        "status": it.get("status"),
        "bid": (first_offer or {}).get("suggested", ""),   # online-only sales: nothing to suggest
        "contact": _contact(it, raw),
        "visit": raw.get("visite") or raw.get("visitable") or "",
        "offer": ({"log_id": offer["id"], "bid": _format_amount(offer.get("bid_amount")),
                   "outcome": offer.get("outcome"), "sent_date": offer.get("sent_date"),
                   "letter_type": offer.get("letter_type"), "is_offer": bool(offer.get("is_offer", 1)),
                   "method": offer.get("method"), "sent_to": offer.get("sent_to") or "",
                   "letter_text": offer.get("letter_text") or "",        # exactly what was sent
                   "letter_subject": offer.get("letter_subject") or "",
                   "location_level": offer.get("location_level"),
                   "location_override": offer.get("location_override") or "",
                   "checklist_summary": offer.get("checklist_summary") or "",
                   "bid_cap_recommended": offer.get("bid_cap_recommended"),
                   "bid_cap_absolute": offer.get("bid_cap_absolute"),
                   "bid_cap_note": offer.get("bid_cap_note") or "",
                   "winning_bid": offer.get("winning_bid"),
                   "all_in_cost": offer.get("all_in_cost"),
                   "lost_reason": offer.get("lost_reason") or "",
                   "diligence_blocker": offer.get("diligence_blocker"),
                   "occupancy_found": offer.get("occupancy_found") or "",
                   "title_found": offer.get("title_found") or "",
                   "access_found": offer.get("access_found") or "",
                   "condition_after": offer.get("condition_after") or ""}
                  if offer else None),
    }


@app.route("/api/offers")
def api_offers():
    import checklist
    from letters import channel, classify_property

    db = get_db()
    try:
        items = load_listings(db, filters=_config().get("filters"), include_hidden=True)
        logs = [dict(r) for r in db.execute("SELECT * FROM carta_log ORDER BY created_at DESC, id DESC")]
        checks = checklist.stored_all(db)
    finally:
        db.close()
    by_id = {it["id"]: it for it in items}

    # Out of "To review": anything waiting for an answer, and anything already offered on.
    busy = {log["listing_id"] for log in logs
            if log["outcome"] == "pending" or (log.get("is_offer", 1) and log["outcome"] != "cancelled")}
    cfg = _config()
    min_score = (cfg.get("filters") or {}).get("min_score") or 45
    budget = cfg.get("max_price") or 0
    size = max(1, _num(cfg.get("max_listings"), 100, int))
    shortlisted, candidates = [], []
    for it in items:
        if it["id"] in busy or it["category"] != "imoveis":
            continue
        if it["status"] == "shortlisted":           # your picks are always here
            shortlisted.append(it)
            continue
        if it["hidden_reason"]:
            continue
        # Strong candidates: your minimum score and budget, sales where the offer is a
        # letter; online auctions and French court sales only if you shortlist them.
        pay = price_to_pay(it)
        if (it["score"] >= min_score and (not budget or pay <= budget) and channel(it) == "letter"
                and classify_property(it.get("title") or "", it.get("description") or "",
                                      it.get("area_m2") or 0) is not None):
            candidates.append(it)
    candidates.sort(key=lambda it: -it.get("rank", it["score"]))
    review = [_offer_view(it, it["id"], checks=checks, cfg=cfg) for it in
              sorted(shortlisted, key=lambda it: -it.get("rank", it["score"])) + candidates[:size]]

    sent, closed = [], []
    for log in logs:
        it = by_id.get(log["listing_id"])
        if not it:
            continue
        view = _offer_view(it, f"log:{log['id']}", log, checks=checks, cfg=cfg)
        (sent if log["outcome"] == "pending" else closed).append(view)

    rejected = [_offer_view(it, it["id"], checks=checks, cfg=cfg) for it in items
                if it["status"] == "dismissed" and it["category"] == "imoveis"]
    return jsonify({"review": review[:150], "sent": sent, "closed": closed, "rejected": rejected[:150],
                    "location_gate_mode": location_gate_mode()})


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
              notes: str = "", location_override: str | None = None) -> int:
    from outbox import log_sent
    db = get_db()
    try:
        return log_sent(db, item, letter=letter, bid=bid, method=method, sent_to=sent_to, notes=notes,
                        location_override=location_override)
    finally:
        db.close()


# ─── Offers: the location check (Settings → Location) ───────────────
# "warn" shows a warning on an offer for a listing placed only at its town or
# not at all; "block" also asks for your reason before the offer is sent or
# logged. Information requests are never checked: they are how you find out.
LOCATION_GATE_LEVELS = ("municipality", "unknown")
LOCATION_GATE_MODES = ("off", "warn", "block")
MIN_OVERRIDE_REASON = 5


def location_gate_mode() -> str:
    mode = (_config().get("location") or {}).get("gate")
    return mode if mode in LOCATION_GATE_MODES else "warn"


def location_gate_applies(item: dict) -> bool:
    return geo.location_confidence(item)["level"] in LOCATION_GATE_LEVELS


def _location_gate(item: dict, is_offer: bool, data: dict):
    """(error response or None, the reason to record)."""
    reason = str(data.get("location_override") or "").strip()[:500] or None
    if not is_offer or location_gate_mode() != "block" or not location_gate_applies(item):
        return None, reason if is_offer else None
    if not reason or len(reason) < MIN_OVERRIDE_REASON:
        label = geo.location_confidence(item)["label"]
        return (jsonify({"error": f"{label}: say why you are sending this offer anyway "
                                  "(Settings → Location asks for a reason).", "location_gate": True}), 409), None
    return None, reason


def _checklist_gate(item: dict, is_offer: bool, data: dict):
    """(error response or None, the checklist, the reason to record). An offer
    with blocking checklist items left needs your reason; a request does not."""
    import checklist
    db = get_db()
    try:
        ck = checklist.build(item, checklist.stored(db, item["id"]), _config())
    finally:
        db.close()
    if not is_offer or not ck["blocking_left"]:
        return None, ck, None
    reason = str(data.get("checklist_override") or "").strip()[:500]
    if len(reason) < checklist.MIN_OVERRIDE_REASON:
        return (jsonify({"error": f"Checklist: {ck['summary']} Say why you are sending this offer anyway.",
                         "checklist_gate": True}), 409), ck, None
    return None, ck, reason


def _record_checklist(item: dict, log_id: int, ck: dict, reason: str | None) -> None:
    import checklist
    db = get_db()
    try:
        checklist.record_offer(db, item["id"], log_id, ck["summary"], reason)
    finally:
        db.close()


@app.route("/api/checklist")
def api_checklist():
    import checklist
    item = _listing(request.args.get("id", ""))
    if not item:
        return jsonify({"error": "no such listing"}), 404
    db = get_db()
    try:
        out = checklist.build(item, checklist.stored(db, item["id"]), _config())
        out["history"] = checklist.history(db, item["id"])[:50]
    finally:
        db.close()
    out["statuses"] = [[k, checklist.STATUS_LABELS[k]] for k in checklist.STATUSES]
    return jsonify(out)


@app.route("/api/checklist", methods=["POST"])
def api_checklist_save():
    """Your status for one item. Only you set statuses: nothing else writes them."""
    import checklist
    data = request.get_json(silent=True) or {}
    item = _listing(str(data.get("id") or ""))
    if not item:
        return jsonify({"error": "no such listing"}), 404
    fields = {k: str(data.get(k) or "").strip() for k in ("notes", "reference", "checked_on", "verified_by")}
    if fields["checked_on"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["checked_on"]):
        return jsonify({"error": "checked_on must be a date (YYYY-MM-DD)"}), 400
    db = get_db()
    try:
        try:
            checklist.set_status(db, item, str(data.get("key") or ""), str(data.get("status") or ""), **fields)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        out = checklist.build(item, checklist.stored(db, item["id"]), _config())
    finally:
        db.close()
    return jsonify(out)


@app.route("/api/checklist.pdf")
def api_checklist_pdf():
    import checklist
    from letters import text_pdf
    item = _listing(request.args.get("id", ""))
    if not item:
        abort(404)
    db = get_db()
    try:
        ck = checklist.build(item, checklist.stored(db, item["id"]), _config())
    finally:
        db.close()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", item["id"])
    return _pdf_response(text_pdf(checklist.as_text(item, ck), ref=f"Ref: {item['id']}"), f"checklist_{safe}.pdf")


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
    is_offer = letter.is_offer if letter else True
    blocked, reason = _location_gate(item, is_offer, data)
    if blocked:
        return blocked
    blocked, ck, ck_reason = _checklist_gate(item, is_offer, data)
    if blocked:
        return blocked
    method = data.get("method") or ("online" if item.get("source") == "eleiloes" else "email")
    log_id = _log_sent(item, letter=letter, bid=bid, method=method,
                       sent_to=data.get("to", ""), notes=data.get("notes", ""), location_override=reason)
    if is_offer:
        _record_checklist(item, log_id, ck, ck_reason)
        _record_bid_cap(item, log_id, bid)
    return jsonify({"ok": True, "log_id": log_id})


@app.route("/api/offers/email", methods=["POST"])
def api_offer_email():
    """Send the letter from here by e-mail, with its PDF attached, and log it."""
    from outbox import email_letter
    data = request.get_json(silent=True) or {}
    item, letter = _letter_for(*_letter_args(data))
    if not item:
        return jsonify({"error": "no such listing"}), 404
    if not letter:
        return jsonify({"error": "no such letter for this listing"}), 400
    blocked, reason = _location_gate(item, letter.is_offer, data)
    if blocked:
        return blocked
    blocked, ck, ck_reason = _checklist_gate(item, letter.is_offer, data)
    if blocked:
        return blocked
    to = (data.get("to") or letter.to_email or "").strip()
    db = get_db()
    try:
        error, log_id = email_letter(db, _config(), item, letter, to=to, bid=data.get("bid", ""),
                                     location_override=reason)
    finally:
        db.close()
    if error:
        return jsonify({"error": error}), 400
    if letter.is_offer:
        _record_checklist(item, log_id, ck, ck_reason)
        _record_bid_cap(item, log_id, data.get("bid", ""))
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
    if "verdict" in result and (_config().get("climate") or {}).get("bid_guardrail") and data.get("id"):
        db = get_db()
        try:
            found = load_listings(db, include_hidden=True, where="id = ?", params=(str(data["id"]),))
        finally:
            db.close()
        if found:
            apply_climate_guardrail(result, _climate_of(found[0]))
    return jsonify(result), (502 if "verdict" not in result else 200)


def apply_climate_guardrail(result: dict, climate: dict) -> dict:
    """Settings → Climate: lower the AI's suggested and maximum bid by the
    climate grade's multiplier, at an exact position only. Amounts the user
    types are never touched; the original figures are kept beside."""
    from letters import parse_bid
    grade, m = climate.get("grade", "unknown"), climate.get("bid_multiplier", 1.0)
    if climate.get("confidence") != "exact":
        note = {"applied": False, "grade": grade,
                "text": "Climate guardrail not applied: the position or the climate data is not exact."}
    elif m >= 1:
        note = {"applied": False, "grade": grade, "text": f"Climate {grade}: no reduction."}
    else:
        was = {}
        for key in ("recommended_bid", "max_bid"):
            value = parse_bid(str(result.get(key) or ""))
            if value:
                was[key] = result[key]
                result[key] = _format_amount(round(value * m))
        note = {"applied": bool(was), "grade": grade, "multiplier": m, "was": was,
                "text": f"Climate {grade}: suggested bid and maximum lowered to {m:.0%} (Settings → Climate)."}
    result["climate_guardrail"] = note
    return result


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


@app.route("/api/analytics")
def api_analytics():
    """How your offers did (analytics.py). Local only; no personal fields."""
    import analytics
    db = get_db()
    try:
        return jsonify(analytics.summary(db))
    finally:
        db.close()


@app.route("/api/analytics.csv")
def api_analytics_csv():
    import analytics
    db = get_db()
    try:
        body = analytics.export_csv(db)
    finally:
        db.close()
    return Response(body, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=outcomes.csv"})


@app.route("/api/carta-log/<int:log_id>", methods=["PATCH"])
def update_carta_log(log_id):
    import analytics
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        row = db.execute("SELECT * FROM carta_log WHERE id=?", (log_id,)).fetchone()
        if row is None:
            return jsonify({"error": "not found"}), 404
        was = row["outcome"]
        if "outcome" in data:
            if data["outcome"] not in OUTCOMES:
                return jsonify({"error": f"outcome must be one of {', '.join(OUTCOMES)}"}), 400
            db.execute("UPDATE carta_log SET outcome=?, notes=? WHERE id=?",
                       (data["outcome"], data.get("notes", row["notes"] or ""), log_id))
            db.commit()
            row = db.execute("SELECT * FROM carta_log WHERE id=?", (log_id,)).fetchone()
        detail_keys = ("winning_bid", "all_in_cost", "lost_reason", "diligence_blocker",
                       "occupancy_found", "title_found", "access_found", "condition_after")
        if any(k in data for k in detail_keys):
            try:
                row = analytics.update_detail(db, log_id, data)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
    finally:
        db.close()

    if data.get("outcome") == "won" and was != "won":
        tg = _config().get("telegram", {})
        if tg.get("enabled"):
            from telegram_alert import alert_carta_won
            alert_carta_won(tg.get("token", ""), tg.get("chat_id", ""),
                            row["processo"] or row["listing_id"] or "?", row["bid_amount"] or 0, "won")
    return jsonify({"ok": True, "offer": {
        "log_id": row["id"], "outcome": row["outcome"],
        "winning_bid": row["winning_bid"], "all_in_cost": row["all_in_cost"],
        "lost_reason": row["lost_reason"] or "", "diligence_blocker": row["diligence_blocker"],
        "occupancy_found": row["occupancy_found"] or "", "title_found": row["title_found"] or "",
        "access_found": row["access_found"] or "", "condition_after": row["condition_after"] or "",
    }})


PROPONENTE_KEYS = ("nome", "nif", "morada", "email", "telefone", "localidade")


@app.route("/api/proponente")
def api_proponente():
    p = _config().get("proponente", {})
    return jsonify({k: p.get(k, "") for k in PROPONENTE_KEYS})


# ─── Settings ────────────────────────────────────────────────────────

# What the Settings page may change: section → allowed keys (None = a scalar).
EDITABLE = {
    "max_price": None,
    "max_listings": None,
    "filters": ("countries", "exclude_keywords", "min_score", "min_area_m2", "rural_min_m2",
                "rural_max_eur_m2", "weights"),
    "proponente": PROPONENTE_KEYS,
    "schedule": ("while_app_open", "pt_every_hours", "eu_every_hours"),
    "telegram": ("enabled", "token", "chat_id", "min_score", "deadline_min_score", "source_alerts",
                 "cut_min_pct", "cut_min_score"),
    "notifications": ("enabled", "smtp_host", "smtp_port", "smtp_user", "smtp_password",
                      "to_emails", "min_score"),
    "report": ("desktop_copy",),
    "updates": ("auto",),
    "auto_requests": ("enabled", "min_score", "per_day"),
    "maps": ("google_key",),
    "ai": ("provider", "ollama_url", "ollama_model", "anthropic_key", "photo_check", "photos_per_scan"),
    "backup": ("folder", "keep"),
    "climate": ("bid_guardrail",),
    "location": ("gate",),
    "checklist": ("blocking",),
    "bid_cap": ("max_all_in", "margin_pct", "contingency_pct", "rural_reserve_per_ha",
                "rural_reserve_fixed", "require_exact", "adviser_reserve_eur",
                "adviser_reserve_by_country"),
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
    weights = (changes.get("filters") or {}).get("weights")
    if weights is not None:
        from scoring import WEIGHTS
        if not isinstance(weights, dict) or any(k not in WEIGHTS or not isinstance(v, (int, float))
                                                or not 0 <= v <= 2 for k, v in weights.items()):
            return jsonify({"error": "weights: known names, each 0 to 2"}), 400
    if not isinstance((changes.get("climate") or {}).get("bid_guardrail", False), bool):
        return jsonify({"error": "climate.bid_guardrail must be true or false"}), 400
    blocking = (changes.get("checklist") or {}).get("blocking")
    if blocking is not None:
        from checklist import TEMPLATES
        if not isinstance(blocking, dict) or any(
                route not in TEMPLATES or not isinstance(keys, list)
                or not set(keys) <= {it.key for it in TEMPLATES[route][1]} for route, keys in blocking.items()):
            return jsonify({"error": "checklist.blocking: route → list of that route's item keys"}), 400
    if (changes.get("location") or {}).get("gate", "warn") not in LOCATION_GATE_MODES:
        return jsonify({"error": "location.gate must be off, warn or block"}), 400
    bid_cap = changes.get("bid_cap")
    if bid_cap is not None:
        nums = ("max_all_in", "margin_pct", "contingency_pct", "rural_reserve_per_ha",
                "rural_reserve_fixed", "adviser_reserve_eur")
        if any(k in bid_cap and (not isinstance(bid_cap[k], (int, float)) or bid_cap[k] < 0)
               for k in nums):
            return jsonify({"error": "bid_cap: amounts and percentages must be numbers ≥ 0"}), 400
        if "require_exact" in bid_cap and not isinstance(bid_cap["require_exact"], bool):
            return jsonify({"error": "bid_cap.require_exact must be true or false"}), 400
        by = bid_cap.get("adviser_reserve_by_country")
        if by is not None and (not isinstance(by, dict)
                               or any(not isinstance(v, (int, float)) or v < 0 for v in by.values())):
            return jsonify({"error": "bid_cap.adviser_reserve_by_country: country → amount ≥ 0"}), 400
    countries = (changes.get("filters") or {}).get("countries")
    if countries is not None and any(c not in COUNTRY_NAMES for c in countries):
        return jsonify({"error": "unknown country code"}), 400
    update_config(changes)
    return jsonify({"ok": True})


# ─── Accounts (accounts.py) ──────────────────────────────────────────
# Kept apart from /api/settings: the password goes one way only (in, encrypted
# with DPAPI), and the page only learns whether one is stored.

@app.route("/api/accounts", methods=["GET"])
def api_accounts_get():
    import accounts
    return jsonify(accounts.public_view(_config()))


@app.route("/api/accounts", methods=["POST"])
def api_accounts_save():
    import accounts
    from config import update_config
    data = request.get_json(silent=True) or {}
    site = data.get("site")
    if site not in accounts.LOGINS:
        return jsonify({"error": "unknown site"}), 400
    if data.get("forget"):
        update_config({"accounts": {site: {"username": "", "secret": ""}}})
        return jsonify({"ok": True})
    username = str(data.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username required"}), 400
    try:
        update_config(accounts.account_changes(site, username, data.get("password") or None))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/accounts/test", methods=["POST"])
def api_accounts_test():
    """Sign in once now, whatever the pause, and say how it went."""
    import accounts
    site = (request.get_json(silent=True) or {}).get("site")
    if site not in accounts.LOGINS:
        return jsonify({"error": "unknown site"}), 400
    creds = accounts.credentials(_config(), site)
    if not creds:
        return jsonify({"ok": False, "message": "No username and password stored."})
    try:
        accounts.LOGINS[site]["login"](make_session(timeout=30), *creds)
    except accounts.LoginNeedsCode:
        return jsonify({"ok": False, "message": "The site asked for an SMS code: the app cannot sign in on its own."})
    except accounts.LoginFailed as e:
        return jsonify({"ok": False, "message": str(e)})
    except Exception as e:  # noqa: BLE001: offline, site down
        return jsonify({"ok": False, "message": f"Could not reach the site ({type(e).__name__})."})
    return jsonify({"ok": True, "message": "Signed in."})


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


# ─── Updates ─────────────────────────────────────────────────────────

@app.route("/api/update", methods=["GET"])
def api_update_status():
    """This PC's version against GitHub master. ?check=1 asks GitHub (a few
    seconds); without it the last known state is shown."""
    import updater
    st = updater.status(fetch=request.args.get("check") == "1")
    st["last_update"] = updater.last_update()
    st["auto"] = (_config().get("updates") or {}).get("auto", True)
    return jsonify(st)


@app.route("/api/update/last")
def api_update_last():
    """What the last automatic update brought (cheap: no git), or null."""
    import updater
    return jsonify(updater.last_update())


@app.route("/api/backup", methods=["GET"])
def api_backup_status():
    """The backup folder, and the copies already in it (newest first)."""
    import glob
    cfg = _config().get("backup") or {}
    folder = (cfg.get("folder") or "").strip()
    out = {"folder": folder, "keep": cfg.get("keep") or 14, "copies": [], "error": None}
    if not folder:
        return jsonify(out)
    try:
        found = sorted(glob.glob(os.path.join(os.path.expanduser(folder), "auctions-*.db")), reverse=True)
        out["copies"] = [{"name": os.path.basename(f), "mb": round(os.path.getsize(f) / 1e6, 1),
                          "at": datetime.fromtimestamp(os.path.getmtime(f), timezone.utc).isoformat()}
                         for f in found[:5]]
        if not os.path.isdir(os.path.expanduser(folder)):
            out["error"] = "That folder does not exist (is the drive plugged in?)"
    except OSError as e:
        out["error"] = str(e)
    return jsonify(out)


@app.route("/api/backup", methods=["POST"])
def api_backup_now():
    """Copy the database to the backup folder now."""
    import updater
    cfg = _config().get("backup") or {}
    folder = (cfg.get("folder") or "").strip()
    if not folder:
        return jsonify({"error": "Set a backup folder first."}), 400
    try:
        made = updater.backup_database(folder=folder, keep=int(cfg.get("keep") or 14))
    except OSError as e:
        return jsonify({"error": f"Could not write to {folder}: {e}"}), 400
    if not made:
        return jsonify({"error": "There is no database to copy yet."}), 400
    return jsonify({"ok": True, "path": made, "mb": round(os.path.getsize(made) / 1e6, 1)})


def _scan_running() -> bool:
    from pipeline import scan_status
    db = get_db()
    try:
        return scan_status(db)["running"]
    finally:
        db.close()


@app.route("/api/update", methods=["POST"])
def api_update_apply():
    """Update now, then restart the app so the new version runs. During a scan
    the update waits for the scan to finish (202)."""
    import updater
    if _scan_running():
        if not app.config.get("UPDATE_QUEUED"):
            app.config["UPDATE_QUEUED"] = True
            threading.Thread(target=_update_after_scan, daemon=True).start()
        return jsonify({"queued": True}), 202
    st = updater.apply()
    if not st["ok"]:
        return jsonify({"error": st["reason"]}), 400
    restart = app.config.get("RESTART_APP")
    st["restart"] = bool(st["updated"] and restart)
    if st["restart"]:
        threading.Thread(target=_restart_soon, args=(restart,), daemon=True).start()
    return jsonify(st)


def _update_after_scan():
    import updater
    try:
        while _scan_running():
            time.sleep(10)
        st = updater.apply()
        restart = app.config.get("RESTART_APP")
        if st["updated"] and restart:
            _restart_soon(restart)
    finally:
        app.config["UPDATE_QUEUED"] = False


def _restart_soon(restart):
    """Start the new version and end this process, once the answer is out."""
    time.sleep(1.0)
    restart()
    logging.shutdown()
    os._exit(0)


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
