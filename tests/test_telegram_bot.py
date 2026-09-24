import pytest

import telegram_alert
import telegram_bot
from db import get_kv, listing_statuses

CHAT = "5550001"
CFG = {"telegram": {"enabled": True, "token": "t", "chat_id": CHAT, "min_score": 60},
       "max_price": 100000, "filters": {}, "dashboard": {}}


@pytest.fixture
def api(monkeypatch):
    """Fake Bot API: records calls; getUpdates returns what the test queues."""
    calls, queue = [], []

    def fake(token, method, http_timeout=15, **params):
        calls.append((method, params))
        if method == "getUpdates":
            out, queue[:] = list(queue), []
            return out
        return True
    monkeypatch.setattr(telegram_bot, "_api", fake)
    sent = []
    monkeypatch.setattr(telegram_alert, "send_telegram",
                        lambda token, chat, msg, reply_markup=None: sent.append((msg, reply_markup)) or True)
    return calls, queue, sent


def tap(update_id, data, chat=CHAT, message_id=77):
    return {"update_id": update_id, "callback_query": {
        "id": f"cq{update_id}", "data": data,
        "message": {"message_id": message_id, "chat": {"id": int(chat)}}}}


def test_new_listing_alerts_carry_buttons(db, add, api):
    _, _, sent = api
    add(external_id="a", title="Moradia", price=20000)
    telegram_alert.alert_new_listings(db, CFG)
    buttons = sent[0][1]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["s|eleiloes:a", "d|eleiloes:a"]


def test_taps_shortlist_dismiss_and_undo(db, add, api):
    calls, queue, _ = api
    add(external_id="a", title="Moradia", price=20000)
    queue.append(tap(10, "s|eleiloes:a"))
    assert telegram_bot.poll_once(db, CFG) == 1
    assert listing_statuses(db) == {"eleiloes:a": "shortlisted"}
    edit = next(p for m, p in calls if m == "editMessageReplyMarkup")
    assert edit["message_id"] == 77 and edit["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "u|eleiloes:a"
    assert ("answerCallbackQuery", {"callback_query_id": "cq10", "text": "☆ Shortlisted"}) in calls

    queue.append(tap(11, "u|eleiloes:a"))
    telegram_bot.poll_once(db, CFG)
    assert listing_statuses(db) == {}
    queue.append(tap(12, "d|eleiloes:a"))
    telegram_bot.poll_once(db, CFG)
    assert listing_statuses(db) == {"eleiloes:a": "dismissed"}
    assert get_kv(db, telegram_bot.OFFSET_KEY) == "12"
    last_get = [p for m, p in calls if m == "getUpdates"][-1]
    assert last_get["offset"] == 12                     # asks only for what follows 11


def test_taps_from_another_chat_are_ignored(db, add, api):
    _, queue, _ = api
    add(external_id="a", title="Moradia", price=20000)
    queue.append(tap(20, "d|eleiloes:a", chat="999"))
    assert telegram_bot.poll_once(db, CFG) == 0
    assert listing_statuses(db) == {}


def test_long_ids_get_a_short_reference(db, add, api):
    calls, queue, _ = api
    long_id = "SUB-JA-2026-123456789012345678901234567890-LOTE-0001"
    add("spain", long_id, "ES", title="Vivienda", price=20000)
    kb = telegram_bot.listing_keyboard(db, f"spain:{long_id}")
    data = kb["inline_keyboard"][0][0]["callback_data"]
    assert len(data.encode()) <= 64 and data.startswith("s|#")
    queue.append(tap(30, data))
    telegram_bot.poll_once(db, CFG)
    assert listing_statuses(db) == {f"spain:{long_id}": "shortlisted"}


def test_top_command_sends_the_best_undecided(db, add, api):
    _, queue, sent = api
    add(external_id="a", title="Moradia", price=4000)
    add(external_id="b", title="Moradia", price=30000)
    add(external_id="c", title="Moradia", price=3000)
    from db import set_listing_status
    set_listing_status(db, "eleiloes:c", "dismissed")
    queue.append({"update_id": 40, "message": {"chat": {"id": int(CHAT)}, "text": "/top"}})
    assert telegram_bot.poll_once(db, CFG) == 1
    refs = [kb["inline_keyboard"][0][0]["callback_data"] for _, kb in sent]
    assert refs == ["s|eleiloes:a", "s|eleiloes:b"]     # best first, the dismissed one left out


def test_nothing_happens_without_telegram(db, api):
    calls, _, _ = api
    assert telegram_bot.poll_once(db, {"telegram": {"enabled": False}}) == 0
    assert calls == []
