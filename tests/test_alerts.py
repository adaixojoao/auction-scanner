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
                        lambda token, chat, msg, **k: messages.append(msg) or True)
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
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda *a, **k: False)
    telegram_alert.alert_new_listings(db, CFG)
    calls = []
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda *a, **k: calls.append(a) or True)
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


def test_unanswered_letters_are_followed_up_once(db, add, sent):
    old = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y-%m-%d")
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    rows = [("citius:a", "1/20.0T", old, "email", "ae@solic.pt", "pending"),
            ("citius:b", "2/20.0T", recent, "email", None, "pending"),     # too recent
            ("eleiloes:c", None, old, "online", None, "pending"),          # online bid: no reply expected
            ("citius:d", "4/20.0T", old, "post", None, "answered")]        # already answered
    db.executemany("INSERT INTO carta_log (listing_id, processo, sent_date, method, sent_to, outcome, created_at) "
                   "VALUES (?,?,?,?,?,?, '2026-01-01')", rows)
    db.commit()
    telegram_alert.alert_carta_deadlines(db, CFG)
    assert len(sent) == 1 and "1 letter(s) unanswered" in sent[0]
    assert "1/20.0T" in sent[0] and "ae@solic.pt" in sent[0] and "2/20.0T" not in sent[0]
    assert "/offers" in sent[0]
    telegram_alert.alert_carta_deadlines(db, CFG)
    assert len(sent) == 1          # mentioned once, not every morning


# ─── Broken-source alarm ────────────────────────────────────────────

def _runs(db, source, *outcomes):
    """Record scrape runs oldest first: a number is a good run, "e"/0 a failed one."""
    from db import record_scrape
    for n, out in enumerate(outcomes):
        ts = f"2026-09-{10 + n:02d}T08:00:00"
        if out == "e":
            record_scrape(db, source, count=0, status="error", message="HTTP 404 Not Found from x.pt", timestamp=ts)
        else:
            record_scrape(db, source, count=out, status="ok" if out else "empty", timestamp=ts)


def test_source_alarm_after_two_failures_and_only_once(db, sent):
    _runs(db, "cgd", 50, "e")
    assert telegram_alert.alert_source_failures(db, CFG, ["cgd"]) == 0    # one blip is not a breakage
    _runs(db, "cgd", 50, "e", "e")
    assert telegram_alert.alert_source_failures(db, CFG, ["cgd"]) == 1
    assert "stopped working" in sent[0] and "<b>cgd</b> — HTTP 404 Not Found from x.pt" in sent[0]
    assert "last worked 2026-09-10" in sent[0]
    assert telegram_alert.alert_source_failures(db, CFG, ["cgd"]) == 0    # not every scan
    assert len(sent) == 1


def test_source_alarm_says_when_it_works_again_and_rearms(db, sent):
    _runs(db, "santander", 8, 0, 0)                                       # broken: finds nothing
    telegram_alert.alert_source_failures(db, CFG, ["santander"])
    assert "finds nothing" in sent[-1]
    _runs(db, "santander", 8, 0, 0, 12)
    telegram_alert.alert_source_failures(db, CFG, ["santander"])
    assert "Working again: <b>santander</b> (12 listings)" in sent[-1]
    telegram_alert.alert_source_failures(db, CFG, ["santander"])
    assert len(sent) == 2                                                 # "back" once
    _runs(db, "santander", 8, 0, 0, 12, 0, 0)                             # breaks again later
    telegram_alert.alert_source_failures(db, CFG, ["santander"])
    assert len(sent) == 3 and "stopped working" in sent[-1]


def test_source_alarm_only_for_this_scan_and_can_be_off(db, sent, monkeypatch):
    _runs(db, "haya", "e", "e")
    assert telegram_alert.alert_source_failures(db, CFG, ["cgd"]) == 0     # not scanned now
    off = {**CFG, "telegram": {**CFG["telegram"], "source_alerts": False}}
    assert telegram_alert.alert_source_failures(db, off, ["haya"]) == 0
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda *a, **k: False)
    assert telegram_alert.alert_source_failures(db, CFG, ["haya"]) == 0    # send failed…
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda t, c, m, **k: sent.append(m) or True)
    assert telegram_alert.alert_source_failures(db, CFG, ["haya"]) == 1    # …so it is retried
    assert "has not worked yet" in sent[-1]


# ─── Price cuts ──────────────────────────────────────────────────────

def house(price, **over):
    return dict(external_id="p1", title="Moradia T3", tipo="moradia", area_m2=110, price=price, **over)


def test_a_price_cut_is_alerted_once_and_again_when_it_falls_further(db, add, sent):
    add(**house(40000))
    telegram_alert.alert_price_cuts(db, CFG)
    assert sent == []                      # one price so far: nothing was cut

    add(**house(32000))
    telegram_alert.alert_price_cuts(db, CFG)
    assert len(sent) == 1 and "Price cut" in sent[0] and "20% off" in sent[0]
    assert "€40,000" in sent[0] and "€32,000" in sent[0]

    telegram_alert.alert_price_cuts(db, CFG)
    assert len(sent) == 1                  # the same cut is never repeated

    add(**house(20000))
    telegram_alert.alert_price_cuts(db, CFG)
    assert len(sent) == 2 and "€20,000" in sent[1]


def test_a_rising_bid_is_not_a_price_cut(db, add, sent):
    add(**house(40000))
    add(**house(40000, current_bid=45000))
    telegram_alert.alert_price_cuts(db, CFG)
    assert sent == []


def test_a_cut_too_small_to_matter_is_not_alerted(db, add, sent):
    add(**house(40000))
    add(**house(39000))                    # 2.5%
    telegram_alert.alert_price_cuts(db, CFG)
    assert sent == []


def test_the_shortlist_counts_whatever_it_scores(db, add, sent):
    from db import set_listing_status
    shop = dict(source="eleiloes", external_id="s2", title="Loja", tipo="loja/escritorio")
    add(**shop, price=40000)
    add(**shop, price=20000)
    telegram_alert.alert_price_cuts(db, CFG)
    assert sent == []                      # a shop scores under the threshold

    set_listing_status(db, "eleiloes:s2", "shortlisted")
    telegram_alert.alert_price_cuts(db, CFG)
    assert len(sent) == 1 and "on your shortlist" in sent[0]


def test_price_cuts_are_not_sent_without_telegram(db, add, sent):
    add(**house(40000))
    add(**house(20000))
    telegram_alert.alert_price_cuts(db, {**CFG, "telegram": {"enabled": False}})
    assert sent == []


def test_a_starred_sale_gets_its_own_reminders(db, add, sent):
    """Three days before and on the last day, each once, whatever the score,
    with the 85% floor and the 5% cheque for a Portuguese sealed-offer sale."""
    now = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
    add("citius", "r1", title="Moradia em Resende", price=20000, date_end="2026-09-27T14:30:00",
        description="Modalidade: Venda mediante proposta em carta fechada")
    add("citius", "r2", title="Moradia não marcada", price=20000, date_end="2026-09-27T14:30:00")
    from db import set_listing_status
    set_listing_status(db, "citius:r1", "shortlisted")
    assert telegram_alert.alert_shortlist_reminders(db, CFG, now=now) == 1
    assert "3 days left" in sent[0] and "Resende" in sent[0]
    assert "€17,000" in sent[0] and "€1,000" in sent[0]          # 85% and the 5% cheque
    assert telegram_alert.alert_shortlist_reminders(db, CFG, now=now) == 0      # once
    assert telegram_alert.alert_shortlist_reminders(db, CFG, now=now + timedelta(days=1, hours=6)) == 1
    assert "last day" in sent[1]
    assert telegram_alert.alert_shortlist_reminders(db, CFG, now=now + timedelta(days=5)) == 0   # ended


def test_a_top_sale_gets_a_last_call_in_its_final_day(db, add, sent):
    """Not starred, but good: one message in the last 24 hours, with how to bid
    on the Portal das Finanças. Starred and dismissed sales are left alone."""
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    add("financas", "0361.2014.526", title="Moradia T3 com terreno", tipo="moradia", area_m2=110,
        price=7199, date_end="2026-09-29T10:00:00", description="Modalidade: Leilão Eletrónico")
    add("eleiloes", "far", title="Moradia T3", tipo="moradia", area_m2=110, price=9000,
        date_end="2026-10-05T10:00:00")
    add("eleiloes", "starred", title="Moradia T3", tipo="moradia", area_m2=110, price=9000,
        date_end="2026-09-29T09:00:00")
    from db import set_listing_status
    set_listing_status(db, "eleiloes:starred", "shortlisted")
    assert telegram_alert.alert_last_calls(db, CFG, now=now) == 1
    assert "last day" in sent[0] and "22h" in sent[0] and "score" in sent[0]
    assert "Portal das Finanças" in sent[0] and "€7,199" in sent[0]
    assert "cheque" not in sent[0]                           # the court's 5% rule is not the tax office's
    assert telegram_alert.alert_last_calls(db, CFG, now=now + timedelta(hours=3)) == 0   # once
    assert telegram_alert.alert_last_calls(db, {**CFG, "telegram": {"enabled": False}}, now=now) == 0
