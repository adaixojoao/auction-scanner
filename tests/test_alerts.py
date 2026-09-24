import json
from datetime import datetime, timedelta, timezone

import pytest

import notifications
import telegram_alert

CFG = {"telegram": {"enabled": True, "token": "t", "chat_id": "c", "min_score": 60},
       "max_price": 100000, "filters": {}, "dashboard": {}}


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(telegram_alert, "send_telegram",
                        lambda token, chat, msg: messages.append(msg) or True)
    return messages


def test_new_listing_alert_escapes_html_and_is_sent_once(db, add, sent):
    add(external_id="a", title="Moradia & Terreno <T3>", price=20000, url="https://x.pt/?a=1&b=2")
    telegram_alert.alert_new_listings(db, CFG)
    assert len(sent) == 1
    assert "Moradia &amp; Terreno &lt;T3&gt;" in sent[0]
    assert 'href="https://x.pt/?a=1&amp;b=2"' in sent[0]
    telegram_alert.alert_new_listings(db, CFG)
    assert len(sent) == 1   # alert_log: never twice


def test_many_new_listings_become_one_digest(db, add, sent):
    for i in range(12):
        add(external_id=f"m{i}", title=f"Moradia {i}", price=20000)
    telegram_alert.alert_new_listings(db, CFG)
    assert len(sent) == 1 and "12 new listings" in sent[0]


def test_failed_send_is_retried_next_run(db, add, monkeypatch):
    add(external_id="a", title="Moradia", price=20000)
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda *a: False)
    telegram_alert.alert_new_listings(db, CFG)
    calls = []
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda *a: calls.append(a) or True)
    telegram_alert.alert_new_listings(db, CFG)
    assert len(calls) == 1


def test_deadlines_with_naive_dates_and_carta_log(db, add, sent):
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat()
    add("citius", "p1", title="Moradia em Moura", price=20000, date_end=soon,
        raw_json=json.dumps({"processo": "1/20.0T, Juízo"}))
    add("citius", "p2", title="Moradia em Beja", price=20000, date_end=soon,
        raw_json=json.dumps({"processo": "2/20.0T"}))
    db.execute("INSERT INTO carta_log (listing_id, processo, created_at, outcome) "
               "VALUES ('x', '1/20.0T', '2026-01-01', 'pending')")
    db.commit()
    telegram_alert.alert_carta_deadlines(db, CFG)   # used to raise on naive dates
    assert len(sent) == 1
    assert "Beja" in sent[0] and "Moura" not in sent[0]


def test_pending_cartas_expire_when_sale_ends(db, add):
    add("citius", "p1", title="Moradia", price=20000, date_end="2000-01-01T10:00:00")
    db.execute("INSERT INTO carta_log (listing_id, created_at, outcome) VALUES ('citius:p1','2000-01-01','pending')")
    db.commit()
    assert telegram_alert.expire_pending_cartas(db) == 1
    assert db.execute("SELECT outcome FROM carta_log").fetchone()[0] == "expired"


def test_long_messages_are_truncated(monkeypatch):
    posted = {}

    class R:
        status_code = 200
        text = ""

    monkeypatch.setattr(telegram_alert.requests, "post",
                        lambda url, json, timeout: posted.update(json) or R())
    assert telegram_alert.send_telegram("t", "c", "x" * 10000)
    assert len(posted["text"]) <= telegram_alert.MAX_MESSAGE


def test_email_alerts_only_new_and_escaped(db, add, monkeypatch):
    mails = []
    monkeypatch.setattr(notifications, "_send_email",
                        lambda cfg, subject, body, plain: mails.append((subject, body)) or True)
    add(external_id="a", title="Casa <b>grande</b>", price=20000)
    cfg = {"enabled": True, "to_emails": ["me@x.pt"], "min_score": 50, "send_on": "new"}
    notifications.send_alerts(db, cfg, max_price=100000)
    notifications.send_alerts(db, cfg, max_price=100000)
    assert len(mails) == 1
    assert "Casa &lt;b&gt;grande&lt;/b&gt;" in mails[0][1]
