"""telegram_alert.py — Telegram notifications: new high-score listings, offer
deadlines, wins, and a weekly summary.

Messages use parse_mode=HTML, so every scraped string is escaped: one "&" in a
title used to make Telegram reject the whole message, silently.
"""
from __future__ import annotations

import html
import json
import logging

import requests

from common import FLAGS, days_left, effective_end, utcnow
from db import load_listings, mark_alerted, not_yet_alerted

LOG = logging.getLogger("telegram")

CHANNEL = "telegram"
MAX_MESSAGE = 4096
# More new listings than this in one run → one digest message instead of a flood.
MAX_INDIVIDUAL = 5


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def _money(v) -> str:
    return f"€{v:,.0f}" if v else "?"


FOLLOW_UP_CHANNEL = "telegram-followup"   # alert_log channel: each unanswered letter is mentioned once


def _dashboard_url(cfg: dict, path: str = "") -> str:
    d = cfg.get("dashboard", {})
    return f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8050)}{path}"


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    if not token or not chat_id:
        return False
    if len(message) > MAX_MESSAGE:
        message = message[:MAX_MESSAGE - 20] + "\n…(truncated)"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10)
        if resp.status_code != 200:
            LOG.error(f"Telegram rejected message ({resp.status_code}): {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        LOG.error(f"Telegram send failed: {e}")
        return False


def _tg(cfg: dict) -> dict | None:
    tg = cfg.get("telegram", {})
    if not tg.get("enabled") or not tg.get("token") or not tg.get("chat_id"):
        return None
    return tg


def format_listing(item: dict) -> str:
    flag = FLAGS.get(item.get("country") or "PT", "\U0001f30d")
    loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
    ends = (item.get("date_end") or "")[:10]
    return (
        f"{flag} <b>New opportunity — Score {item['score']:.0f}/100</b>\n\n"
        f"<b>{_esc((item.get('title') or '?')[:80])}</b>\n"
        f"\U0001f4cd {_esc(loc)}\n"
        f"\U0001f4b6 {_money(item.get('price'))}{f'  ·  Ends {_esc(ends)}' if ends else ''}\n"
        f"\U0001f4cb {_esc((item.get('source') or '').upper())}\n"
        f"\U0001f3f7 {_esc(', '.join(item['reasons'][:3]))}\n\n"
        f"<a href=\"{_esc(item.get('url') or '')}\">View listing →</a>"
    )


def format_digest(items: list[dict], total: int, cfg: dict) -> str:
    lines = [f"\U0001f514 <b>{total} new listings scoring "
             f"{cfg.get('telegram', {}).get('min_score', 75)}+</b>\n"]
    for it in items:
        flag = FLAGS.get(it.get("country") or "PT", "")
        link = (f"<a href=\"{_esc(it['url'])}\">{_esc((it.get('title') or '?')[:55])}</a>"
                if it.get("url") else _esc((it.get("title") or "?")[:55]))
        lines.append(f"{flag} <b>{it['score']:.0f}</b> · {_money(it.get('price'))} · {link}")
    if total > len(items):
        lines.append(f"\n…and {total - len(items)} more.")
    lines.append(f"\nDashboard: {_dashboard_url(cfg)}")
    return "\n".join(lines)


def alert_new_listings(db, cfg: dict, score_fn=None):
    """Alert listings scoring >= telegram.min_score that were never alerted.
    `score_fn` is ignored (kept for old callers)."""
    tg = _tg(cfg)
    if not tg:
        return
    min_sc = tg.get("min_score", 75)
    max_price = cfg.get("max_price", 100000)

    candidates = [it for it in load_listings(db, filters=cfg.get("filters"))
                  if it["score"] >= min_sc and (it.get("price") or 0) <= max_price]
    fresh_ids = not_yet_alerted(db, CHANNEL, [it["id"] for it in candidates])
    fresh = sorted((it for it in candidates if it["id"] in fresh_ids), key=lambda it: -it.get("rank", it["score"]))
    if not fresh:
        return

    if len(fresh) <= MAX_INDIVIDUAL:
        sent = [it["id"] for it in fresh if send_telegram(tg["token"], tg["chat_id"], format_listing(it))]
    else:
        ok = send_telegram(tg["token"], tg["chat_id"], format_digest(fresh[:15], len(fresh), cfg))
        sent = [it["id"] for it in fresh] if ok else []
    if sent:
        mark_alerted(db, CHANNEL, sent)
        LOG.info(f"Telegram: alerted {len(sent)} new listings")


def _sent_processes(db) -> tuple[set, set]:
    procs, ids = set(), set()
    for r in db.execute("SELECT processo, listing_id FROM carta_log "
                        "WHERE outcome NOT IN ('expired','cancelled') AND is_offer = 1"):
        if r[0]:
            procs.add(r[0].split(",")[0].strip())
        if r[1]:
            ids.add(r[1])
    return procs, ids


def upcoming_deadlines(db, cfg: dict, *, within_days: float = 4, min_score: float = 60, now=None):
    """Property listings ending soon that have no offer in carta_log yet, most urgent first."""
    now = now or utcnow()
    procs, ids = _sent_processes(db)
    out = []
    for it in load_listings(db, filters=cfg.get("filters"), now=now):
        left = days_left(it.get("date_end"), now)
        if left is None or not (0 < left <= within_days):
            continue
        if it["category"] != "imoveis" or it["score"] < min_score or it["id"] in ids:
            continue
        raw = json.loads(it.get("raw_json") or "{}") if it.get("raw_json") else {}
        proc = str(raw.get("processo") or it.get("external_id") or "").split(",")[0].strip()
        if proc in procs:
            continue
        it["hours_left"] = left * 24
        out.append(it)
    out.sort(key=lambda it: it["hours_left"])
    return out


def expire_pending_cartas(db, now=None) -> int:
    """Mark pending carta_log rows 'expired' once their listing's sale has ended."""
    now = now or utcnow()
    expired = []
    for r in db.execute("""
            SELECT c.id, l.date_end FROM carta_log c JOIN listings l ON l.id = c.listing_id
            WHERE c.outcome = 'pending' AND l.date_end IS NOT NULL"""):
        end = effective_end(r[1])
        if end and end <= now:
            expired.append((r[0],))
    db.executemany("UPDATE carta_log SET outcome='expired' WHERE id=?", expired)
    db.commit()
    return len(expired)


def alert_carta_deadlines(db, cfg: dict, score_fn=None):
    """One message: sales ending in the next 4 days with no offer sent, and
    letters still unanswered after db.FOLLOW_UP_DAYS (each mentioned once)."""
    from db import FOLLOW_UP_DAYS, awaiting_reply, mark_alerted, not_yet_alerted

    now = utcnow()
    expire_pending_cartas(db, now)
    tg = _tg(cfg)
    if not tg:
        return
    items = upcoming_deadlines(db, cfg, min_score=tg.get("deadline_min_score", 60), now=now)
    waiting = awaiting_reply(db, now=now)
    fresh = not_yet_alerted(db, FOLLOW_UP_CHANNEL, [f"carta_log:{r['id']}" for r in waiting])
    waiting = [r for r in waiting if f"carta_log:{r['id']}" in fresh]
    if not items and not waiting:
        return

    lines = []
    if items:
        lines.append(f"⏰ <b>{len(items)} sale(s) ending within 4 days — no offer sent</b>\n")
    for it in items[:12]:
        urgency = "\U0001f6a8" if it["hours_left"] <= 24 else "⚠️"
        flag = FLAGS.get(it.get("country") or "PT", "")
        loc = ", ".join(filter(None, [it.get("concelho"), it.get("district")]))
        title = _esc((it.get("title") or "?")[:60])
        link = f"<a href=\"{_esc(it['url'])}\">{title}</a>" if it.get("url") else title
        lines.append(f"{urgency} {flag} <b>{it['hours_left']:.0f}h</b> · score {it['score']:.0f} · "
                     f"{_money(it.get('price'))}\n    {link}\n    \U0001f4cd {_esc(loc)}")
    if len(items) > 12:
        lines.append(f"\n…and {len(items) - 12} more.")
    if waiting:
        lines.append(f"\n\U0001f4ed <b>{len(waiting)} letter(s) unanswered after {FOLLOW_UP_DAYS}+ days</b> "
                     "— time for a call or a reminder:")
        for r in waiting[:12]:
            to = f" to {_esc(r['sent_to'])}" if r.get("sent_to") else ""
            lines.append(f"• {_esc(r.get('processo') or r.get('listing_id') or '?')} — sent "
                         f"{_esc(r.get('sent_date'))}{to}")
    lines.append(f"\nOffers: {_dashboard_url(cfg, '/offers')}")
    if send_telegram(tg["token"], tg["chat_id"], "\n".join(lines)) and waiting:
        mark_alerted(db, FOLLOW_UP_CHANNEL, [f"carta_log:{r['id']}" for r in waiting])


def alert_carta_won(token: str, chat_id: str, processo: str, bid: float, estado: str):
    send_telegram(token, chat_id,
                  f"\U0001f389 <b>WON!</b>\n\nProcess: {_esc(processo)}\n"
                  f"Bid: €{bid:,.0f}\nStatus: {_esc(estado)}")


def alert_weekly_summary(token: str, chat_id: str, stats: dict, health: list[dict] | None = None,
                         dashboard_url: str = "http://127.0.0.1:8050"):
    msg = (
        f"\U0001f4ca <b>Weekly Summary</b>\n\n"
        f"\U0001f195 New listings: {stats.get('new', 0)}\n"
        f"\U0001f4e8 Cartas sent: {stats.get('sent', 0)}\n"
        f"\U0001f3c6 Won (all time): {stats.get('won', 0)}\n"
        f"⏳ Pending: {stats.get('pending', 0)}\n"
        f"\U0001f4b6 Total exposure: €{stats.get('exposure', 0):,.0f}\n"
    )
    failing = [h for h in (health or []) if h["state"] in ("error", "broken")]
    if failing:
        msg += f"\n⚠️ <b>{len(failing)} source(s) failing</b>: " + \
               ", ".join(_esc(h["source"]) for h in failing[:15]) + "\n"
    msg += f"\nDashboard: {dashboard_url}"
    send_telegram(token, chat_id, msg)
