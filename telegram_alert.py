"""Send Telegram alerts for high-scoring new listings."""

import logging
import requests

LOG = logging.getLogger("telegram")


def send_telegram(token: str, chat_id: str, message: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=10)


def alert_new_listings(db, cfg: dict, score_fn):
    tg = cfg.get("telegram", {})
    if not tg.get("enabled"):
        return
    token = tg.get("token", "")
    chat_id = tg.get("chat_id", "")
    min_sc = tg.get("min_score", 75)
    if not token or not chat_id:
        return

    cols = [d[1] for d in db.execute("PRAGMA table_info(listings)").fetchall()]
    rows = db.execute(
        "SELECT * FROM listings WHERE is_new=1 AND country='PT'"
    ).fetchall()

    sent = 0
    for r in rows:
        item = dict(zip(cols, r))
        sc, reasons = score_fn(item)
        if sc < min_sc:
            continue
        price = f"€{item['price']:,.0f}" if item.get("price") else "?"
        loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
        msg = (
            f"<b>Nova oportunidade -- Score {sc:.0f}/100</b>\n\n"
            f"<b>{(item.get('title') or '?')[:80]}</b>\n"
            f"{loc}\n"
            f"{price} | {item.get('source', '').upper()}\n"
            f"{', '.join(reasons[:3])}\n\n"
            f"<a href='{item.get('url', '')}'>Ver listagem</a>"
        )
        try:
            send_telegram(token, chat_id, msg)
            sent += 1
        except Exception as e:
            LOG.error(f"Telegram send failed: {e}")

    if sent:
        LOG.info(f"Telegram: sent {sent} alerts")
