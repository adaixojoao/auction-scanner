"""telegram_alert.py — Instant Telegram notifications for high-score listings and deadlines."""
import logging
import requests

LOG = logging.getLogger("telegram")

FLAGS = {"PT": "\U0001f1f5\U0001f1f9", "ES": "\U0001f1ea\U0001f1f8",
         "FR": "\U0001f1eb\U0001f1f7", "DE": "\U0001f1e9\U0001f1ea",
         "IT": "\U0001f1ee\U0001f1f9", "NL": "\U0001f1f3\U0001f1f1",
         "HR": "\U0001f1ed\U0001f1f7", "GR": "\U0001f1ec\U0001f1f7",
         "BE": "\U0001f1e7\U0001f1ea", "RO": "\U0001f1f7\U0001f1f4",
         "PL": "\U0001f1f5\U0001f1f1", "CY": "\U0001f1e8\U0001f1fe"}


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10)
        return resp.status_code == 200
    except Exception as e:
        LOG.error(f"Telegram send failed: {e}")
        return False


def alert_new_listings(db, cfg: dict, score_fn):
    tg = cfg.get("telegram", {})
    if not tg.get("enabled"): return
    token   = tg.get("token", "")
    chat_id = tg.get("chat_id", "")
    min_sc  = tg.get("min_score", 75)
    if not token or not chat_id: return

    cols = [d[1] for d in db.execute("PRAGMA table_info(listings)").fetchall()]
    rows = db.execute("SELECT * FROM listings WHERE is_new=1").fetchall()

    sent = 0
    for r in rows:
        item = dict(zip(cols, r))
        sc, reasons = score_fn(item)
        if sc < min_sc: continue
        country = item.get("country", "PT")
        flag    = FLAGS.get(country, "\U0001f30d")
        price   = f"€{item['price']:,.0f}" if item.get("price") else "?"
        loc     = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
        ends    = (item.get("date_end") or "")[:10]
        msg = (
            f"{flag} <b>New opportunity — Score {sc:.0f}/100</b>\n\n"
            f"<b>{(item.get('title') or '?')[:80]}</b>\n"
            f"\U0001f4cd {loc}\n"
            f"\U0001f4b6 {price}{f'  ·  Ends {ends}' if ends else ''}\n"
            f"\U0001f4cb {item.get('source', '').upper()}\n"
            f"\U0001f3f7 {', '.join(reasons[:3])}\n\n"
            f"<a href='{item.get('url', '')}'>View listing →</a>"
        )
        if send_telegram(token, chat_id, msg):
            sent += 1
    if sent:
        LOG.info(f"Telegram: sent {sent} new listing alerts")


def alert_carta_deadlines(db, cfg: dict, score_fn):
    from datetime import datetime, timezone, timedelta
    import json

    tg = cfg.get("telegram", {})
    if not tg.get("enabled"): return
    token   = tg.get("token", "")
    chat_id = tg.get("chat_id", "")
    if not token or not chat_id: return

    now = datetime.now(timezone.utc)
    sent_procs = {r[0] for r in db.execute(
        "SELECT processo FROM carta_log WHERE outcome NOT IN ('expired','cancelled')"
    ).fetchall()}

    cols = [d[1] for d in db.execute("PRAGMA table_info(listings)").fetchall()]
    rows = db.execute("""
        SELECT * FROM listings
        WHERE date_end IS NOT NULL AND date_end > ? AND date_end < ?
    """, (now.isoformat(), (now + timedelta(days=4)).isoformat())).fetchall()

    for r in rows:
        item = dict(zip(cols, r))
        raw  = json.loads(item.get("raw_json") or "{}")
        proc = raw.get("processo", item.get("external_id", ""))
        if proc in sent_procs: continue

        end     = datetime.fromisoformat(item["date_end"].replace("Z", "+00:00"))
        hours   = int((end - now).total_seconds() / 3600)
        days    = (end - now).days
        country = item.get("country", "PT")
        flag    = FLAGS.get(country, "\U0001f30d")
        urgency = "\U0001f6a8 URGENT" if days <= 1 else "⚠️ DEADLINE SOON"

        msg = (
            f"{urgency} {flag} — Offer not yet sent!\n\n"
            f"<b>{(item.get('title') or '?')[:60]}</b>\n"
            f"⏰ {hours}h remaining\n"
            f"\U0001f4b6 €{item.get('price', 0):,.0f}\n"
            f"\U0001f4cd {item.get('concelho', '')} {item.get('district', '')}\n"
            f"\U0001f3db {item.get('source', '').upper()}\n\n"
            f"Open dashboard:\nhttp://127.0.0.1:8050/cartas-review"
        )
        send_telegram(token, chat_id, msg)

    db.execute("""
        UPDATE carta_log SET outcome='expired'
        WHERE outcome='pending'
        AND listing_id IN (SELECT id FROM listings WHERE date_end < ?)
    """, (now.isoformat(),))
    db.commit()


def alert_carta_won(token: str, chat_id: str, processo: str, bid: float, estado: str):
    send_telegram(token, chat_id,
        f"\U0001f389 <b>WON!</b>\n\nProcess: {processo}\nBid: €{bid:,.0f}\nStatus: {estado}")


def alert_weekly_summary(token: str, chat_id: str, stats: dict):
    msg = (
        f"\U0001f4ca <b>Weekly Summary</b>\n\n"
        f"\U0001f195 New listings: {stats.get('new', 0)}\n"
        f"\U0001f4e8 Cartas sent: {stats.get('sent', 0)}\n"
        f"\U0001f3c6 Won: {stats.get('won', 0)}\n"
        f"⏳ Pending: {stats.get('pending', 0)}\n"
        f"\U0001f4b6 Total exposure: €{stats.get('exposure', 0):,.0f}\n\n"
        f"Dashboard: http://127.0.0.1:8050"
    )
    send_telegram(token, chat_id, msg)
