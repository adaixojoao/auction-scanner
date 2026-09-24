"""outbox.py — sending letters and recording them, and information requests
the app prepares by itself.

One way to send: `email_letter()` e-mails a letter built by
letters.build_letter() with its PDF and records it in carta_log (`log_sent`).
The Offers page and the request queue both use it.

The request queue: after a scan, strong sales that take an information
request — and whose recipient's e-mail is known (the agente de execução on
e-leilões, the court on BOE, the seller's lawyer on licitor) — get the request
prepared and offered on Telegram with Send / Show letter / Skip. Nothing is
sent until you tap Send. Each sale is offered once.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from common import days_left, utcnow, utcnow_iso
from db import load_listings, set_listing_status

LOG = logging.getLogger("outbox")

REQUEST_DEFAULTS = {"enabled": True, "min_score": 75, "per_day": 5}
MIN_DAYS_LEFT = 5          # a request needs time for an answer before the sale ends


def _raw(item: dict) -> dict:
    try:
        return json.loads(item.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return {}


# ─── Sending and recording ──────────────────────────────────────────

def log_sent(db, item: dict, *, letter=None, bid: str = "", method: str, sent_to: str = "",
             notes: str = "") -> int:
    """Record a sent letter (or with letter=None an online bid) in carta_log,
    with its text exactly as sent."""
    from letters import parse_bid
    raw = _raw(item)
    is_offer = letter.is_offer if letter else True
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


def email_letter(db, cfg: dict, item: dict, letter, *, to: str = "", bid: str = "") -> tuple[str | None, int | None]:
    """E-mail `letter` with its PDF from Settings → E-mail and record it.
    Returns (error in plain words, None) or (None, carta_log id)."""
    from letters import letter_pdf
    from notifications import send_letter
    to = (to or letter.to_email or "").strip()
    error = send_letter(cfg.get("notifications", {}), to, letter.subject, letter.text,
                        letter_pdf(letter), letter.filename,
                        reply_to=cfg.get("proponente", {}).get("email", ""))
    if error:
        return error, None
    return None, log_sent(db, item, letter=letter, bid=bid, method="email", sent_to=to)


# ─── The request queue ──────────────────────────────────────────────

def request_settings(cfg: dict) -> dict:
    """auto_requests with defaults; an empty field on the Settings page is saved as null."""
    given = {k: v for k, v in (cfg.get("auto_requests") or {}).items() if v is not None}
    return {**REQUEST_DEFAULTS, **given}


def missing_for_requests(cfg: dict) -> list[str]:
    """What must be set up before requests can be prepared and sent."""
    missing = []
    p = cfg.get("proponente") or {}
    if not (p.get("nome") and p.get("email")):
        missing.append("your name and e-mail (Settings → Your details)")
    smtp = cfg.get("notifications") or {}
    if not all(smtp.get(k) for k in ("smtp_host", "smtp_user", "smtp_password")):
        missing.append("the e-mail account (Settings → E-mail)")
    tg = cfg.get("telegram") or {}
    if not (tg.get("enabled") and tg.get("token") and tg.get("chat_id")):
        missing.append("Telegram (Settings → Telegram alerts)")
    return missing


def info_letter_type(item: dict) -> str | None:
    """The sale's information-request letter, if it has one."""
    from letters import letter_types_for
    return next((t.key for t in letter_types_for(item) if not t.is_offer), None)


def request_candidates(db, cfg: dict, now=None) -> list[tuple[dict, object]]:
    """(listing, letter) pairs worth an information request now, best first."""
    from letters import build_letter
    rs = request_settings(cfg)
    already = {r[0] for r in db.execute("SELECT listing_id FROM letter_queue")}
    written = {r[0] for r in db.execute("SELECT listing_id FROM carta_log WHERE listing_id IS NOT NULL")}
    written_proc = {r[0] for r in db.execute("SELECT processo FROM carta_log WHERE processo IS NOT NULL")}
    now = now or utcnow()
    out = []
    for it in load_listings(db, filters=cfg.get("filters"), now=now):
        if (it["category"] != "imoveis" or it.get("status") == "dismissed"
                or it["score"] < rs["min_score"] or it["id"] in already or it["id"] in written):
            continue
        left = days_left(it.get("date_end"), now)
        if left is not None and left < MIN_DAYS_LEFT:
            continue
        ltype = info_letter_type(it)
        if not ltype:
            continue
        letter = build_letter(it, "", "", cfg.get("proponente") or {}, ltype)
        if not letter.to_email or (letter.processo and letter.processo in written_proc):
            continue
        out.append((it, letter))
    out.sort(key=lambda pair: -pair[0].get("rank", pair[0]["score"]))
    return out


def _queued_today(db) -> int:
    today = utcnow_iso()[:10]
    return db.execute("SELECT COUNT(*) FROM letter_queue WHERE substr(created_at, 1, 10) = ?",
                      (today,)).fetchone()[0]


def request_message(item: dict, letter) -> str:
    from telegram_alert import _esc, _money
    raw = _raw(item)
    who = raw.get("agente_nome") or raw.get("autoridad") or raw.get("avocat_nom") or ""
    ends = (item.get("date_end") or "")[:10]
    return (
        "✉️ <b>Information request ready</b>\n\n"
        f"<b>{_esc((item.get('title') or '?')[:80])}</b>\n"
        f"💶 {_money(item.get('price'))} · score {item['score']:.0f}"
        f"{f' · ends {_esc(ends)}' if ends else ''}\n"
        f"To: {_esc(who + ' ' if who else '')}&lt;{_esc(letter.to_email)}&gt;\n"
        f"Subject: {_esc(letter.subject)}\n\n"
        "It asks whether the sale is still on, the deadline, deposit, charges, "
        "occupancy and visits. Nothing is sent until you tap <b>Send</b>."
    )


def queue_requests(db, cfg: dict) -> int:
    """Prepare new requests and offer them on Telegram. Returns how many."""
    rs = request_settings(cfg)
    if not rs["enabled"] or missing_for_requests(cfg):
        return 0
    room = max(0, int(rs["per_day"]) - _queued_today(db))
    if not room:
        return 0
    from telegram_alert import send_telegram
    from telegram_bot import request_keyboard
    tg = cfg["telegram"]
    queued = 0
    for item, letter in request_candidates(db, cfg)[:room]:
        if not send_telegram(tg["token"], tg["chat_id"], request_message(item, letter),
                             reply_markup=request_keyboard(db, item["id"])):
            break                                          # Telegram down: try after the next scan
        db.execute("INSERT OR IGNORE INTO letter_queue (listing_id, letter_type, to_email, status, created_at) "
                   "VALUES (?,?,?,?,?)", (item["id"], letter.type_key, letter.to_email, "waiting", utcnow_iso()))
        db.commit()
        queued += 1
    if queued:
        LOG.info(f"Prepared {queued} information request(s) for approval on Telegram")
    return queued


def _queued(db, listing_id: str):
    return db.execute("SELECT * FROM letter_queue WHERE listing_id = ?", (listing_id,)).fetchone()


def _decide(db, listing_id: str, status: str, note: str | None = None):
    db.execute("UPDATE letter_queue SET status = ?, note = ?, decided_at = ? WHERE listing_id = ?",
               (status, note, utcnow_iso(), listing_id))
    db.commit()


def queued_letter(db, cfg: dict, listing_id: str):
    """(listing, letter) for a queued request, rebuilt with today's details."""
    from letters import build_letter
    row = _queued(db, listing_id)
    found = load_listings(db, filters=cfg.get("filters"), include_hidden=True,
                          where="id = ?", params=(listing_id,))
    if not row or not found:
        return None, None
    return found[0], build_letter(found[0], "", "", cfg.get("proponente") or {}, row["letter_type"])


def send_queued(db, cfg: dict, listing_id: str) -> tuple[bool, str]:
    """Send one waiting request (you tapped Send). Returns (sent, message)."""
    row = _queued(db, listing_id)
    if not row:
        return False, "This request is no longer in the queue."
    if row["status"] != "waiting":
        return False, f"Already {row['status']}."
    item, letter = queued_letter(db, cfg, listing_id)
    if not letter:
        return False, "That listing is no longer in the app."
    error, _log_id = email_letter(db, cfg, item, letter, to=row["to_email"] or "")
    if error:
        # Stays waiting: tap Send again once the problem (often the mail server) is fixed.
        db.execute("UPDATE letter_queue SET note = ? WHERE listing_id = ?", (error, listing_id))
        db.commit()
        return False, error
    _decide(db, listing_id, "sent", letter.to_email)
    return True, f"Sent to {row['to_email'] or letter.to_email}"


def skip_queued(db, listing_id: str) -> bool:
    row = _queued(db, listing_id)
    if not row or row["status"] != "waiting":
        return False
    _decide(db, listing_id, "skipped")
    return True
