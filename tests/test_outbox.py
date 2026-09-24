"""Information requests prepared by the app and approved on Telegram (outbox.py)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import notifications
import outbox
import telegram_alert
import telegram_bot

CHAT = "5550001"
CFG = {
    "telegram": {"enabled": True, "token": "t", "chat_id": CHAT, "min_score": 60},
    "notifications": {"smtp_host": "smtp.example.com", "smtp_user": "me@example.com", "smtp_password": "x"},
    "proponente": {"nome": "Nome Exemplo", "email": "me@example.com", "nif": "000000000"},
    "auto_requests": {"enabled": True, "min_score": 75, "per_day": 5},
    "max_price": 100000, "filters": {}, "dashboard": {},
}
LATER = (datetime.now(timezone.utc) + timedelta(days=20)).replace(tzinfo=None).isoformat()


@pytest.fixture
def tg(monkeypatch):
    """Records Telegram messages and API calls; queue updates for poll_once."""
    sent, calls, queue = [], [], []
    monkeypatch.setattr(telegram_alert, "send_telegram",
                        lambda token, chat, msg, reply_markup=None: sent.append((msg, reply_markup)) or True)

    def api(token, method, http_timeout=15, **params):
        calls.append((method, params))
        if method == "getUpdates":
            out, queue[:] = list(queue), []
            return out
        return True
    monkeypatch.setattr(telegram_bot, "_api", api)
    return sent, calls, queue


@pytest.fixture
def mail(monkeypatch):
    out = []
    monkeypatch.setattr(notifications, "send_letter",
                        lambda smtp, to, subject, text, pdf, filename, reply_to="": out.append(
                            {"to": to, "subject": subject, "text": text, "pdf": pdf}) or None)
    return out


def sale(add, eid, email="agente@exemplo.pt", **kw):
    raw = {"referencia": eid, "processo": "123/24.0T8GRD", "agente_nome": "Agente Exemplo",
           "detail_checked": True, **({"agente_email": email} if email else {})}
    fields = dict(title="Moradia T3", price=4000, concelho="Guarda", date_end=LATER,
                  raw_json=json.dumps(raw), url=f"https://e-leiloes.pt/evento/{eid}")
    fields.update(kw)
    return add("eleiloes", eid, **fields)


def tap(update_id, data):
    return {"update_id": update_id, "callback_query": {
        "id": f"cq{update_id}", "data": data,
        "message": {"message_id": 9, "chat": {"id": int(CHAT)}}}}


def test_prepares_requests_only_where_it_can_be_sent(db, add, tg):
    sent, _, _ = tg
    sale(add, "LO1")                                           # the one to prepare
    sale(add, "LO2", email=None)                               # nobody to send it to
    sale(add, "LO3", price=90000, title="Loja")                # low score
    sale(add, "LO4", date_end=(datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat())
    sale(add, "LO5")
    from db import set_listing_status
    set_listing_status(db, "eleiloes:LO5", "dismissed")
    assert outbox.queue_requests(db, CFG) == 1
    msg, kb = sent[0]
    assert "Information request ready" in msg and "&lt;agente@exemplo.pt&gt;" in msg
    assert "Nothing is sent until you tap" in msg
    assert [b["callback_data"] for b in kb["inline_keyboard"][0]] == ["rs|eleiloes:LO1", "rv|eleiloes:LO1",
                                                                        "rk|eleiloes:LO1"]
    assert outbox.queue_requests(db, CFG) == 0                  # offered once


def test_needs_setup_and_respects_the_daily_limit(db, add, tg):
    for n in range(4):
        sale(add, f"LO{n}")
    no_smtp = {**CFG, "notifications": {}}
    assert outbox.queue_requests(db, no_smtp) == 0
    assert "the e-mail account (Settings → E-mail)" in outbox.missing_for_requests(no_smtp)
    off = {**CFG, "auto_requests": {"enabled": False}}
    assert outbox.queue_requests(db, off) == 0
    two = {**CFG, "auto_requests": {"per_day": 2, "min_score": None}}   # empty field: default
    assert outbox.queue_requests(db, two) == 2
    assert outbox.queue_requests(db, two) == 0


def test_send_tap_emails_the_request_and_records_it(db, add, tg, mail):
    sent, calls, queue = tg
    sale(add, "LO1")
    outbox.queue_requests(db, CFG)
    queue.append(tap(1, "rs|eleiloes:LO1"))
    telegram_bot.poll_once(db, CFG)
    assert len(mail) == 1 and mail[0]["to"] == "agente@exemplo.pt"
    assert "123/24.0T8GRD" in mail[0]["subject"] and mail[0]["pdf"].startswith(b"%PDF")
    log = db.execute("SELECT * FROM carta_log").fetchone()
    assert log["listing_id"] == "eleiloes:LO1" and log["is_offer"] == 0 and log["letter_type"] == "pt_info"
    assert log["sent_to"] == "agente@exemplo.pt" and log["letter_text"] == mail[0]["text"]
    assert db.execute("SELECT status FROM letter_queue").fetchone()[0] == "sent"
    relabel = [p for m, p in calls if m == "editMessageReplyMarkup"][-1]
    assert relabel["reply_markup"]["inline_keyboard"][0][0]["text"].startswith("✉ Sent to")
    queue.append(tap(2, "rs|eleiloes:LO1"))                    # a second tap sends nothing
    telegram_bot.poll_once(db, CFG)
    assert len(mail) == 1


def test_show_skip_and_a_failed_send_stays_waiting(db, add, tg, monkeypatch):
    sent, _, queue = tg
    sale(add, "LO1")
    sale(add, "LO2")
    outbox.queue_requests(db, CFG)
    queue.append(tap(1, "rv|eleiloes:LO1"))
    telegram_bot.poll_once(db, CFG)
    assert "<pre>" in sent[-1][0] and "Nome Exemplo" in sent[-1][0]

    monkeypatch.setattr(notifications, "send_letter", lambda *a, **k: "The e-mail could not be sent: timeout")
    queue.append(tap(2, "rs|eleiloes:LO1"))
    telegram_bot.poll_once(db, CFG)
    assert "Not sent: The e-mail could not be sent: timeout" in sent[-1][0]
    row = db.execute("SELECT status, note FROM letter_queue WHERE listing_id='eleiloes:LO1'").fetchone()
    assert row["status"] == "waiting" and "timeout" in row["note"]   # tap Send again later

    queue.append(tap(3, "rk|eleiloes:LO2"))
    telegram_bot.poll_once(db, CFG)
    assert db.execute("SELECT status FROM letter_queue WHERE listing_id='eleiloes:LO2'").fetchone()[0] == "skipped"
    assert outbox.send_queued(db, CFG, "eleiloes:LO2") == (False, "Already skipped.")


def test_a_sale_already_written_to_is_not_offered(db, add, tg):
    item = sale(add, "LO1")
    from outbox import log_sent
    log_sent(db, {**item, "status": None}, method="post")
    assert outbox.queue_requests(db, CFG) == 0
