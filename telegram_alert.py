"""telegram_alert.py — Telegram notifications: new high-score listings, price
cuts, offer deadlines, wins, and a weekly summary.

Messages use parse_mode=HTML, so every scraped string is escaped: one "&" in a
title used to make Telegram reject the whole message, silently.
"""
from __future__ import annotations

import html
import json
import logging

import requests

from common import FLAGS, days_left, effective_end, parse_dt, utcnow
from db import alerted_at, load_listings, mark_alerted, not_yet_alerted

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


def send_telegram(token: str, chat_id: str, message: str, reply_markup: dict | None = None) -> bool:
    """Send one HTML message; `reply_markup` adds buttons (telegram_bot.py handles the taps)."""
    if not token or not chat_id:
        return False
    if len(message) > MAX_MESSAGE:
        message = message[:MAX_MESSAGE - 20] + "\n…(truncated)"
    payload = {"chat_id": chat_id, "text": message,
               "parse_mode": "HTML", "disable_web_page_preview": False}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
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


def _cost_line(item: dict) -> str:
    """What it really costs: taxes, fees and, for a home, the work it needs."""
    import costs
    est = costs.estimate(item)
    if not est:
        return ""
    rent = est.get("rent")
    rent = f"\n\U0001f3e0 rents ~{_money(rent['monthly'])}/month \u2192 {rent['yield_pct']}% a year" if rent else ""
    if est["all_in"]:
        low, high = est["all_in"]["low"], est["all_in"]["high"]
        return f"\n\U0001f9fe {_money(low)}\u2013{high:,.0f} all-in (taxes, fees and the work)" + rent
    return f"\n\U0001f9fe {_money(est['total'])} to own it (taxes and fees in)" + rent


def format_listing(item: dict) -> str:
    flag = FLAGS.get(item.get("country") or "PT", "\U0001f30d")
    loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
    ends = (item.get("date_end") or "")[:10]
    return (
        f"{flag} <b>New opportunity — Score {item['score']:.0f}/100</b>\n\n"
        f"<b>{_esc((item.get('title') or '?')[:80])}</b>\n"
        f"\U0001f4cd {_esc(loc)}\n"
        f"\U0001f4b6 {_money(item.get('price'))}{f'  ·  Ends {_esc(ends)}' if ends else ''}"
        f"{_cost_line(item)}\n"
        f"\U0001f4cb {_esc((item.get('source') or '').upper())}\n"
        f"\U0001f3f7 {_esc(', '.join(item['reasons'][:3]))}\n\n"
        f"<a href=\"{_esc(item.get('url') or '')}\">View listing →</a>"
        + _citius_finder(item)
    )


def _citius_finder(item: dict) -> str:
    """Citius has no page per sale: say where to look (Listings → ⓘ has the steps)."""
    if item.get("source") != "citius":
        return ""
    from listing_info import case_number, raw_of
    court, proc = raw_of(item).get("tribunal"), case_number(item)
    if not (court or proc):
        return ""
    return ("\n\U0001f50e On Citius: Tribunal <b>" + _esc(court or "?") + "</b>, Imóvel, Em venda, "
            "Ignorar Datas → Pesquisar, then Ctrl+F <code>" + _esc(proc or "") + "</code>")


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
        from telegram_bot import listing_keyboard
        sent = [it["id"] for it in fresh
                if send_telegram(tg["token"], tg["chat_id"], format_listing(it),
                                 reply_markup=listing_keyboard(db, it["id"]))]
    else:
        ok = send_telegram(tg["token"], tg["chat_id"], format_digest(fresh[:15], len(fresh), cfg))
        sent = [it["id"] for it in fresh] if ok else []
    if sent:
        mark_alerted(db, CHANNEL, sent)
        LOG.info(f"Telegram: alerted {len(sent)} new listings")


# ─── Price cuts ──────────────────────────────────────────────────────
# A cut is what turns a listing anyone would ignore into one worth a letter,
# and it happens quietly between scans. Only the valor base counts: a bid
# going up is an auction working, not a discount.

CUT_CHANNEL = "telegram-cut"      # alert_log: when the last cut was told about


def _price_history(db) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for lid, at, price in db.execute(
            "SELECT listing_id, observed_at, price FROM price_history "
            "WHERE price IS NOT NULL ORDER BY listing_id, observed_at"):
        out.setdefault(lid, []).append((at, price))
    return out


def price_cuts(db, cfg: dict, *, min_pct: float = 5, min_score: float = 60, now=None) -> list[dict]:
    """Visible listings whose price fell at its last change and that you have
    not been told about since. Each gains cut_from, cut_pct and cut_at.

    Your shortlist counts whatever it scores: you already said you want it."""
    history = _price_history(db)
    told = alerted_at(db, CUT_CHANNEL)
    out = []
    for it in load_listings(db, filters=cfg.get("filters"), now=now):
        rows = history.get(it["id"]) or []
        if len(rows) < 2:
            continue
        before, (at, after) = rows[-2][1], rows[-1]
        if not before or not after or after >= before:
            continue
        pct = (before - after) / before * 100
        if pct < min_pct or (it.get("status") != "shortlisted" and it["score"] < min_score):
            continue
        when = parse_dt(at)
        if when and told.get(it["id"]) and when <= told[it["id"]]:
            continue
        out.append({**it, "cut_from": before, "cut_pct": round(pct, 1), "cut_at": at})
    return sorted(out, key=lambda it: -it["cut_pct"])


def format_cut(item: dict) -> str:
    flag = FLAGS.get(item.get("country") or "PT", "\U0001f30d")
    loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
    ends = (item.get("date_end") or "")[:10]
    mine = "\u2b50 on your shortlist" if item.get("status") == "shortlisted" else f"Score {item['score']:.0f}/100"
    return (
        f"{flag} \U0001f4c9 <b>Price cut \u2014 {item['cut_pct']:.0f}% off</b>\n\n"
        f"<b>{_esc((item.get('title') or '?')[:80])}</b>\n"
        f"\U0001f4cd {_esc(loc)}\n"
        f"\U0001f4b6 <s>{_money(item['cut_from'])}</s> \u2192 <b>{_money(item.get('price'))}</b>"
        f"{f'  ·  Ends {_esc(ends)}' if ends else ''}"
        f"{_cost_line(item)}\n"
        f"\U0001f3f7 {_esc(mine)}\n\n"
        f"<a href=\"{_esc(item.get('url') or '')}\">View listing \u2192</a>"
        + _citius_finder(item)
    )


def alert_price_cuts(db, cfg: dict):
    """Tell me when something got cheaper, once per cut."""
    tg = _tg(cfg)
    if not tg:
        return
    cuts = price_cuts(db, cfg, min_pct=tg.get("cut_min_pct", 5),
                      min_score=tg.get("cut_min_score", 60))
    if not cuts:
        return
    from telegram_bot import listing_keyboard
    sent = [it["id"] for it in cuts[:MAX_INDIVIDUAL]
            if send_telegram(tg["token"], tg["chat_id"], format_cut(it),
                             reply_markup=listing_keyboard(db, it["id"]))]
    if sent:
        mark_alerted(db, CUT_CHANNEL, sent)
        LOG.info(f"Telegram: alerted {len(sent)} price cuts")


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


# ─── Reminders for your shortlist ───────────────────────────────────
# The morning digest covers every good sale ending soon. A listing you starred
# gets its own message, whatever its score: three days before (time to visit,
# to get the cheque) and on the last day. Each once.

REMINDER_CHANNEL = "telegram-remind"
REMINDERS = [(1, "last day"), (3, "3 days left")]      # (days before the end, label)
CAUTION_SHARE = 0.05       # Portuguese court sales: a cheque visado or bank guarantee of 5%


def _is_letter_sale(item: dict) -> bool:
    text = f"{item.get('description') or ''} {item.get('raw_json') or ''}".lower()
    return "carta fechada" in text or "negociação particular" in text or "negociacao particular" in text


def shortlist_reminders(db, cfg: dict, now=None) -> list[tuple[dict, str, str]]:
    """(listing, key, label) for starred listings that reach a reminder point
    and have not had that reminder."""
    now = now or utcnow()
    due = []
    for it in load_listings(db, filters=cfg.get("filters"), now=now, include_hidden=True, apply_min_score=False):
        if it.get("status") != "shortlisted":
            continue
        left = days_left(it.get("date_end"), now)
        if left is None or left <= 0:
            continue
        for days, label in REMINDERS:
            if left <= days:
                due.append((it, f"{it['id']}@{days}d", label))
                break
    told = not_yet_alerted(db, REMINDER_CHANNEL, [key for _, key, _ in due])
    return [d for d in due if d[1] in told]


def format_reminder(item: dict, label: str, hours: float, why: str = "your shortlist") -> str:
    import costs
    from common import price_to_pay
    pay = price_to_pay(item)
    lines = [f"⏰ <b>{_esc(label)} — {hours:.0f}h</b> · {_esc(why)}",
             f"<b>{_esc((item.get('title') or '?')[:80])}</b>",
             f"💶 {_money(pay)}  ·  ends {_esc((item.get('date_end') or '')[:16].replace('T', ' '))}"]
    if item.get("source") == "financas":
        lines.append(f"🏛 Bid or offer on the Portal das Finanças with your account"
                     + (f" — at least {_money(item['price'])} (the base value)." if item.get("price") else "."))
    elif (item.get("country") or "PT") == "PT" and _is_letter_sale(item) and item.get("price"):
        lines.append(f"✍️ Sealed offer: at least {_money(0.85 * item['price'])} (85%), with a cheque "
                     f"visado or bank guarantee of {_money(CAUTION_SHARE * item['price'])} (5%) to the court.")
    est = costs.estimate(item)
    if est:
        lines.append(f"🧾 {_money(est['total'])} to own it (taxes and fees in)")
    if item.get("url"):
        lines.append(f"<a href=\"{_esc(item['url'])}\">View listing →</a>")
    return "\n".join(lines) + _citius_finder(item)


def alert_shortlist_reminders(db, cfg: dict, now=None) -> int:
    tg = _tg(cfg)
    if not tg:
        return 0
    now = now or utcnow()
    sent = []
    for it, key, label in shortlist_reminders(db, cfg, now):
        hours = days_left(it.get("date_end"), now) * 24
        if send_telegram(tg["token"], tg["chat_id"], format_reminder(it, label, hours)):
            sent.append(key)
    if sent:
        mark_alerted(db, REMINDER_CHANNEL, sent)
        LOG.info(f"Telegram: {len(sent)} shortlist reminders")
    return len(sent)


# ─── Last call for the best sales ───────────────────────────────────
# The morning digest lists what ends within 4 days; a top sale you have not
# starred (or dismissed) still gets one message of its own in its last 24
# hours, so a closing time like "29/9 às 10:00" is not missed. Scans run every
# couple of hours, so it arrives with time to act.

LAST_CALL_CHANNEL = "telegram-lastcall"
LAST_CALL_HOURS = 24


def last_calls(db, cfg: dict, now=None) -> list[dict]:
    now = now or utcnow()
    tg = _tg(cfg) or {}
    floor = tg.get("lastcall_min_score", tg.get("min_score", 75))
    _, offered = _sent_processes(db)
    due = []
    for it in load_listings(db, filters=cfg.get("filters"), now=now):
        left = days_left(it.get("date_end"), now)
        if left is None or not (0 < left * 24 <= LAST_CALL_HOURS):
            continue
        if it.get("status") in ("shortlisted", "dismissed") or it["id"] in offered:
            continue
        if it["category"] != "imoveis" or it["score"] < floor:
            continue
        it["hours_left"] = left * 24
        due.append(it)
    told = not_yet_alerted(db, LAST_CALL_CHANNEL, [it["id"] for it in due])
    return sorted((it for it in due if it["id"] in told), key=lambda it: it["hours_left"])


def alert_last_calls(db, cfg: dict, now=None) -> int:
    tg = _tg(cfg)
    if not tg:
        return 0
    sent = []
    for it in last_calls(db, cfg, now)[:5]:
        if send_telegram(tg["token"], tg["chat_id"],
                         format_reminder(it, "last day", it["hours_left"], why=f"score {it['score']:.0f}")):
            sent.append(it["id"])
    if sent:
        mark_alerted(db, LAST_CALL_CHANNEL, sent)
        LOG.info(f"Telegram: {len(sent)} last-call messages")
    return len(sent)


# ─── Broken-source alarm ────────────────────────────────────────────
# A site that stops working used to show only on the Sources page and in the
# weekly summary. Now the scan that sees it fail a second time in a row says so
# (one blip is not a breakage), once per breakage: the alert_log key carries the
# last good run, so a source that recovers and breaks again is reported again.

SOURCE_CHANNEL = "telegram-source"
SOURCE_RECOVERED_CHANNEL = "telegram-source-ok"
SOURCE_FAILS_BEFORE_ALARM = 2


def _source_key(h: dict) -> str:
    return f"src:{h['source']}@{h['last_ok'] or 'never'}"


def alert_source_failures(db, cfg: dict, scanned: list[str]) -> int:
    """Tell Telegram about sources from this scan that broke, and ones that
    work again after an alarm. Returns how many sources were mentioned."""
    tg = _tg(cfg)
    if not tg or not tg.get("source_alerts", True) or not scanned:
        return 0
    from db import source_health
    health = {h["source"]: h for h in source_health(db) if h["source"] in set(scanned)}

    failing = [h for h in health.values()
               if h["state"] in ("error", "broken") and h["failing_runs"] >= SOURCE_FAILS_BEFORE_ALARM]
    fresh_keys = not_yet_alerted(db, SOURCE_CHANNEL, [_source_key(h) for h in failing])
    new_fail = [h for h in failing if _source_key(h) in fresh_keys]

    # Recovered: working now, and alerted about since their previous good run.
    recovered = []
    for h in health.values():
        if h["state"] != "ok":
            continue
        alerted = [r[0] for r in db.execute(
            "SELECT listing_id FROM alert_log WHERE channel = ? AND listing_id LIKE ?",
            (SOURCE_CHANNEL, f"src:{h['source']}@%"))]
        pending = not_yet_alerted(db, SOURCE_RECOVERED_CHANNEL, alerted)
        if pending:
            recovered.append((h, pending))

    lines = []
    if new_fail:
        lines.append(f"⚠️ <b>{len(new_fail)} source(s) stopped working</b>\n")
        for h in sorted(new_fail, key=lambda h: h["source"]):
            since = f"last worked {h['last_ok'][:10]}" if h["last_ok"] else "has not worked yet"
            why = h["last_message"] or ("finds nothing" if h["state"] == "broken" else "error")
            lines.append(f"• <b>{_esc(h['source'])}</b> — {_esc(why)} "
                         f"({h['failing_runs']} runs, {since})")
        lines.append(f"\nSources page: {_dashboard_url(cfg, '/sources')}")
    if recovered:
        if lines:
            lines.append("")
        lines.append("✅ Working again: " + ", ".join(
            f"<b>{_esc(h['source'])}</b> ({h['last_count']} listings)" for h, _ in recovered))
    if not lines:
        return 0
    if not send_telegram(tg["token"], tg["chat_id"], "\n".join(lines)):
        return 0                                   # retried after the next scan
    if new_fail:
        mark_alerted(db, SOURCE_CHANNEL, [_source_key(h) for h in new_fail])
    for _, keys in recovered:
        mark_alerted(db, SOURCE_RECOVERED_CHANNEL, keys)
    LOG.info(f"Telegram: source alarm for {[h['source'] for h in new_fail]}, "
             f"recovered {[h['source'] for h, _ in recovered]}")
    return len(new_fail) + len(recovered)


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
