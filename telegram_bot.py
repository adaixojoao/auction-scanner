"""telegram_bot.py — act on alerts from Telegram.

New-listing alerts carry buttons (☆ Shortlist, ✕ Dismiss). Taps come back as
Telegram "updates", which this module fetches with getUpdates (no webhook: the
app runs on a PC, not a server):

- while the app is open, app.py polls continuously, so a tap works in seconds;
- when it is closed, every scheduler tick (the background task) reads them.

A tap changes the listing exactly as the app's ☆ / ✕ do (db.set_listing_status),
and the buttons become "↩ Undo". Only the configured chat may act: anyone can
find a bot and press its buttons, so everything else is ignored.

Information requests prepared by outbox.py come with ✉ Send / 📄 Show letter /
✕ Skip. Send e-mails that request (outbox.send_queued); nothing is sent otherwise.

Commands: /top (the best listings not yet decided, with buttons), /help.
"""
from __future__ import annotations

import logging

import requests

from common import stable_id
from db import get_kv, load_listings, set_kv, set_listing_status

LOG = logging.getLogger("telegram")

OFFSET_KEY = "telegram_update_offset"
TOP_COUNT = 5
# callback_data may hold at most 64 bytes; longer listing IDs get a short reference.
_MAX_ID_IN_BUTTON = 56

ACTIONS = {"s": ("shortlisted", "☆ Shortlisted"), "d": ("dismissed", "✕ Dismissed"), "u": (None, "↩ Undone")}

HELP = ("Tap <b>☆ Shortlist</b> or <b>✕ Dismiss</b> under an alert; <b>↩ Undo</b> reverses it.\n"
        "/top — the best listings you have not decided on yet\n"
        "/help — this message")


# ─── Buttons ────────────────────────────────────────────────────────

def _ref(db, listing_id: str) -> str:
    if len(listing_id.encode()) <= _MAX_ID_IN_BUTTON:
        return listing_id
    ref = "#" + stable_id(listing_id)
    set_kv(db, f"tgref:{ref}", listing_id)
    return ref


def _listing_id(db, ref: str) -> str | None:
    return get_kv(db, f"tgref:{ref}") if ref.startswith("#") else ref


def listing_keyboard(db, listing_id: str) -> dict:
    ref = _ref(db, listing_id)
    return {"inline_keyboard": [[
        {"text": "☆ Shortlist", "callback_data": f"s|{ref}"},
        {"text": "✕ Dismiss", "callback_data": f"d|{ref}"},
    ]]}


def request_keyboard(db, listing_id: str) -> dict:
    ref = _ref(db, listing_id)
    return {"inline_keyboard": [[
        {"text": "✉ Send", "callback_data": f"rs|{ref}"},
        {"text": "📄 Show letter", "callback_data": f"rv|{ref}"},
        {"text": "✕ Skip", "callback_data": f"rk|{ref}"},
    ]]}


def _label_keyboard(text: str) -> dict:
    """A single, inert button that says what happened."""
    return {"inline_keyboard": [[{"text": text, "callback_data": "noop|"}]]}


def undo_keyboard(db, listing_id: str, done: str) -> dict:
    return {"inline_keyboard": [[
        {"text": f"{done} · ↩ Undo", "callback_data": f"u|{_ref(db, listing_id)}"},
    ]]}


# ─── Telegram API ───────────────────────────────────────────────────

def _api(token: str, method: str, http_timeout: float = 15, **params):
    """Call one Bot API method; the result, or None on any failure."""
    params = {k: v for k, v in params.items() if v is not None}
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             json=params, timeout=http_timeout)
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — offline must not break the app
        LOG.debug(f"Telegram {method} failed: {e}")
        return None
    if not data.get("ok"):
        LOG.debug(f"Telegram {method} refused: {data.get('description')}")
        return None
    return data.get("result")


# ─── Handling updates ───────────────────────────────────────────────

def _is_owner(chat_id, cfg_chat_id) -> bool:
    return chat_id is not None and str(chat_id) == str(cfg_chat_id).strip()


def handle_callback(db, cfg: dict, tg: dict, cq: dict) -> str | None:
    """One button tap. Returns what was done (for logs and tests), or None."""
    msg = cq.get("message") or {}
    if not _is_owner((msg.get("chat") or {}).get("id"), tg["chat_id"]):
        LOG.warning("Telegram: ignored a button tap from another chat")
        return None
    action, _, ref = (cq.get("data") or "").partition("|")
    if action == "noop":
        _api(tg["token"], "answerCallbackQuery", callback_query_id=cq.get("id"))
        return None
    listing_id = _listing_id(db, ref) if ref else None
    if action in REQUEST_ACTIONS and listing_id:
        return _handle_request(db, cfg, tg, cq, action, listing_id)
    if action not in ACTIONS or not listing_id:
        _api(tg["token"], "answerCallbackQuery", callback_query_id=cq.get("id"), text="Unknown button")
        return None
    if not db.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,)).fetchone():
        _api(tg["token"], "answerCallbackQuery", callback_query_id=cq.get("id"),
             text="That listing is no longer in the app")
        return None

    status, done = ACTIONS[action]
    set_listing_status(db, listing_id, status)
    # A tap handled after a long pause (app closed) may be too old to answer;
    # the changed buttons below still show that it worked.
    _api(tg["token"], "answerCallbackQuery", callback_query_id=cq.get("id"), text=done)
    markup = listing_keyboard(db, listing_id) if status is None else undo_keyboard(db, listing_id, done)
    _api(tg["token"], "editMessageReplyMarkup", chat_id=msg["chat"]["id"],
         message_id=msg.get("message_id"), reply_markup=markup)
    LOG.info(f"Telegram: {listing_id} → {status or 'no decision'}")
    return f"{listing_id}:{status or 'cleared'}"


REQUEST_ACTIONS = {"rs", "rv", "rk"}


def _handle_request(db, cfg: dict, tg: dict, cq: dict, action: str, listing_id: str) -> str | None:
    import outbox
    from telegram_alert import _esc, send_telegram
    msg = cq["message"]

    def answer(text):
        _api(tg["token"], "answerCallbackQuery", callback_query_id=cq.get("id"), text=text[:190])

    def relabel(markup):
        _api(tg["token"], "editMessageReplyMarkup", chat_id=msg["chat"]["id"],
             message_id=msg.get("message_id"), reply_markup=markup)

    if action == "rv":
        _item, letter = outbox.queued_letter(db, cfg, listing_id)
        if not letter:
            answer("That request is no longer available")
            return None
        body = letter.text if len(letter.text) <= 3300 else letter.text[:3300] + "\n…(the PDF has it all)"
        send_telegram(tg["token"], tg["chat_id"],       # cut the text, not the HTML: a cut tag is refused
                      f"<b>{_esc(letter.subject)}</b>\nTo: {_esc(letter.to_email)}\n\n"
                      f"<pre>{_esc(body)}</pre>")
        answer("Letter below")
        return f"{listing_id}:shown"
    if action == "rk":
        if outbox.skip_queued(db, listing_id):
            relabel(_label_keyboard("✕ Skipped"))
        answer("Skipped")
        return f"{listing_id}:skipped"
    sent, text = outbox.send_queued(db, cfg, listing_id)
    answer(text)
    if sent:
        relabel(_label_keyboard(f"✉ {text}"))
    else:
        send_telegram(tg["token"], tg["chat_id"], f"⚠️ Not sent: {_esc(text)}")
    LOG.info(f"Telegram: request for {listing_id} → {text}")
    return f"{listing_id}:{'sent' if sent else 'not sent'}"


def send_top(db, cfg: dict, tg: dict) -> int:
    from telegram_alert import format_listing, send_telegram
    items = [it for it in load_listings(db, filters=cfg.get("filters"))
             if it["category"] == "imoveis" and not it.get("status")]
    items.sort(key=lambda it: -it.get("rank", it["score"]))
    if not items:
        send_telegram(tg["token"], tg["chat_id"], "Nothing new to decide on. ✔")
        return 0
    for it in items[:TOP_COUNT]:
        send_telegram(tg["token"], tg["chat_id"], format_listing(it), reply_markup=listing_keyboard(db, it["id"]))
    return min(len(items), TOP_COUNT)


def handle_message(db, cfg: dict, tg: dict, message: dict) -> str | None:
    if not _is_owner((message.get("chat") or {}).get("id"), tg["chat_id"]):
        return None
    command = (message.get("text") or "").strip().split("@")[0].split(" ")[0].lower()
    from telegram_alert import send_telegram
    if command == "/top":
        send_top(db, cfg, tg)
        return "top"
    if command in ("/help", "/start"):
        send_telegram(tg["token"], tg["chat_id"], HELP)
        return "help"
    return None


def poll_once(db, cfg: dict, *, wait: int = 0) -> int:
    """Fetch and handle pending updates. `wait` > 0 long-polls (the app's
    listener); 0 returns at once (a scheduler tick). Returns how many were handled."""
    from telegram_alert import _tg
    tg = _tg(cfg)
    if not tg:
        return 0
    offset = int(get_kv(db, OFFSET_KEY, "0") or 0)
    updates = _api(tg["token"], "getUpdates", http_timeout=wait + 10,
                   offset=offset + 1 if offset else None, timeout=wait or None,
                   allowed_updates=["callback_query", "message"])
    if not updates:
        return 0
    handled = 0
    for up in updates:
        # Remember the offset first: an update that crashes the handler must not
        # be fetched (and crash it) forever.
        set_kv(db, OFFSET_KEY, str(up["update_id"]))
        try:
            if "callback_query" in up:
                handled += handle_callback(db, cfg, tg, up["callback_query"]) is not None
            elif "message" in up:
                handled += handle_message(db, cfg, tg, up["message"]) is not None
        except Exception:  # noqa: BLE001
            LOG.exception("Telegram update failed")
    return handled
