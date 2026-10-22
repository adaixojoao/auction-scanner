"""Alpha integrated: strict amounts, the durable outbox on the real e-mail route,
verified TLS, and the loopback-only dashboard. Synthetic data, no network."""
import json
import smtplib
import ssl

import pytest

import dashboard
import durable_outbox
import offer_safety
from local_guard import require_local_host


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000,
                        "proponente": {"nome": "Test Person", "nif": "123",
                                       "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"},
                        "notifications": {"smtp_host": "smtp.example.invalid", "smtp_port": 587,
                                          "smtp_user": "me@example.invalid", "smtp_password": "pw"}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


class Server:
    """A fake SMTP server whose behaviour each test sets."""
    sent, contexts, fail_at, refused = [], [], None, {}

    def __init__(self, host, port, timeout=None):
        if Server.fail_at == "connect":
            raise OSError("no route")

    def starttls(self, context=None):
        Server.contexts.append(context)

    def ehlo(self):
        pass

    def login(self, user, password):
        if Server.fail_at == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad password")

    def send_message(self, msg, to_addrs=None):
        if Server.fail_at == "data":
            raise smtplib.SMTPServerDisconnected("connection lost during DATA")
        if Server.fail_at == "refused":
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"no such user")})
        Server.sent.append((msg, to_addrs))
        return dict(Server.refused)

    def quit(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    Server.sent, Server.contexts, Server.fail_at, Server.refused = [], [], None, {}
    monkeypatch.setattr("smtplib.SMTP", Server)
    return Server


def _citius(add, external_id="c1"):
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada", "agente_email": "ae@solic.example"}
    add("citius", external_id, title="Moradia", price=30000, area_m2=120, raw_json=json.dumps(raw))
    return f"citius:{external_id}"


def _info(lid, op="op-00000001", **kw):
    return {"id": lid, "type": "pt_info", "to": "ae@solic.example", "operation_id": op, **kw}


def _count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_send_is_stored_before_it_goes_and_a_second_click_never_sends_again(client, db, add, smtp):
    lid = _citius(add)
    first = client.post("/api/offers/email", json=_info(lid))
    assert first.status_code == 200, first.get_json()
    out = first.get_json()
    assert out["state"] == "sent" and len(smtp.sent) == 1
    msg, to_addrs = smtp.sent[0]
    op = durable_outbox.get(db, out["operation_id"])
    assert msg["Message-ID"] == op["message_id"] and op["carta_log_id"] == out["log_id"]
    assert op["snapshot"]["to"] == "ae@solic.example" and op["snapshot"]["pdf_sha256"]
    assert "smtp_password" not in json.dumps(op["snapshot"]) and "pw" not in op["snapshot"].values()
    again = client.post("/api/offers/email", json=_info(lid))
    assert again.status_code == 200 and again.get_json()["log_id"] == out["log_id"]
    assert len(smtp.sent) == 1 and _count(db, "carta_log") == 1
    status = client.get(f"/api/offers/operations/{out['operation_id']}").get_json()
    assert status["state"] == "sent" and len(status["attempts"]) == 1 and "text" not in status


def test_tls_checks_the_certificate(client, add, smtp):
    client.post("/api/offers/email", json=_info(_citius(add)))
    [context] = smtp.contexts
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_a_known_failure_records_nothing_and_may_be_tried_again(client, db, add, smtp):
    lid = _citius(add)
    smtp.fail_at = "login"
    failed = client.post("/api/offers/email", json=_info(lid))
    assert failed.status_code == 400 and failed.get_json()["state"] == "failed"
    assert _count(db, "carta_log") == 0 and not smtp.sent
    smtp.fail_at = None
    retried = client.post("/api/offers/email", json=_info(lid))
    assert retried.status_code == 200 and len(smtp.sent) == 1
    assert _count(db, "operational_outbox") == 1 and _count(db, "operational_attempts") == 2


def test_a_send_that_breaks_during_data_is_never_repeated(client, db, add, smtp):
    lid = _citius(add)
    smtp.fail_at = "data"
    broke = client.post("/api/offers/email", json=_info(lid))
    body = broke.get_json()
    assert broke.status_code == 409 and body["uncertain"] and body["state"] == "delivery_unknown"
    assert "Sent folder" in body["error"]
    smtp.fail_at = None
    again = client.post("/api/offers/email", json=_info(lid))
    assert again.status_code == 409 and not smtp.sent and _count(db, "carta_log") == 0


def test_a_refused_recipient_is_a_failure_but_a_refused_copy_is_not(client, db, add, smtp):
    lid = _citius(add)
    smtp.fail_at = "refused"
    assert client.post("/api/offers/email", json=_info(lid)).get_json()["state"] == "failed"
    smtp.fail_at, smtp.refused = None, {"me@example.invalid": (550, b"copy refused")}
    ok = client.post("/api/offers/email", json=_info(lid, op="op-00000002"))
    assert ok.status_code == 200 and _count(db, "carta_log") == 1


def test_the_same_operation_with_other_content_needs_review(client, db, add, smtp):
    lid = _citius(add)
    smtp.fail_at = "login"
    client.post("/api/offers/email", json=_info(lid))
    smtp.fail_at = None
    changed = client.post("/api/offers/email", json=_info(lid, to="other@solic.example"))
    assert changed.status_code == 409 and changed.get_json()["state"] == "needs_review"
    assert not smtp.sent


def test_without_an_operation_id_identical_content_is_one_send(client, db, add, smtp):
    lid = _citius(add)
    body = {"id": lid, "type": "pt_info", "to": "ae@solic.example"}
    client.post("/api/offers/email", json=body)
    client.post("/api/offers/email", json=body)
    assert len(smtp.sent) == 1


def test_a_vague_amount_is_refused_before_anything_is_stored_or_sent(client, db, add, smtp):
    lid = _citius(add)
    for bad in ("EUR 26000", "26,000.00", "26 000", "-5"):
        r = client.post("/api/offers/email", json={"id": lid, "bid": bad, "to": "ae@solic.example",
                                                    "checklist_override": "the agent confirmed no charges"})
        assert r.status_code == 400 and r.get_json()["amount_invalid"], bad
        r = client.post("/api/offers/sent", json={"id": lid, "bid": bad,
                                                   "checklist_override": "the agent confirmed no charges"})
        assert r.status_code == 400 and r.get_json()["amount_invalid"], bad
    assert _count(db, "operational_outbox") == 0 and _count(db, "carta_log") == 0 and not smtp.sent


def test_an_offer_sends_with_its_scenario_and_stays_unapproved(client, db, add, smtp):
    lid = _citius(add)
    r = client.post("/api/offers/email", json={"id": lid, "bid": "26.000,00", "to": "ae@solic.example",
                                                "checklist_override": "the agent confirmed no charges",
                                                "operation_id": "op-offer-0001"})
    assert r.status_code == 200, r.get_json()
    scenario = r.get_json()["scenario"]
    assert scenario["status"] == "incomplete" and "obligations" in scenario["missing"]
    snap = durable_outbox.get(db, r.get_json()["operation_id"])["snapshot"]
    assert snap["kind"] == "binding_offer" and snap["bid_cents"] == 2_600_000
    assert not offer_safety.approve("binding_offer", None, {})["authorised_acquisition"]


def test_a_hand_edited_non_finite_cash_refuses_offers(client, add):
    import config
    lid = _citius(add)
    cfg = config.load_config()
    for bad in ("NaN", "inf", -5, True, "lots"):
        config.save_config({**cfg, "cash_on_hand": bad})
        assert offer_safety.cash_euros(config.load_config()) == 0
        r = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00",
                                                   "checklist_override": "the agent confirmed no charges"})
        assert r.status_code == 409 and r.get_json()["cash_gate"] == "unset"


def test_recording_the_send_is_atomic_with_the_accepted_state(client, db, add, smtp, monkeypatch):
    import outbox
    lid = _citius(add)

    real = outbox.log_sent

    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(outbox, "log_sent", broken)
    with pytest.raises(RuntimeError):
        client.post("/api/offers/email", json=_info(lid))
    op = db.execute("SELECT state, carta_log_id FROM operational_outbox").fetchone()
    assert tuple(op) == ("sending", None) and _count(db, "carta_log") == 0
    assert len(smtp.sent) == 1        # accepted, so it stays for reconciliation: never resent
    monkeypatch.setattr(outbox, "log_sent", real)
    assert client.post("/api/offers/email", json=_info(lid)).status_code == 409


def test_the_migration_adds_the_outbox_and_keeps_old_offers(tmp_path):
    import sqlite3
    import db as dbmod
    path = str(tmp_path / "v32.db")
    conn = sqlite3.connect(path)
    for v in range(1, 33):
        dbmod._MIGRATIONS[v](conn)
        conn.execute(f"PRAGMA user_version = {v}")
    conn.execute("INSERT INTO carta_log (listing_id, sent_date, method, outcome, created_at, is_offer) "
                 "VALUES ('citius:old', '2026-01-01', 'email', 'pending', '2026-01-01T00:00:00', 1)")
    conn.commit()
    conn.close()
    conn = dbmod.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION >= 33
    assert conn.execute("SELECT COUNT(*) FROM operational_outbox").fetchone()[0] == 0
    assert conn.execute("SELECT listing_id FROM carta_log").fetchone()[0] == "citius:old"
    conn.close()
    dbmod.connect(path).close()               # a second start changes nothing


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_hosts_are_allowed(host):
    assert require_local_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "", None, "::"])
def test_any_other_host_refuses_to_start(host):
    with pytest.raises(ValueError):
        require_local_host(host)


def test_the_cli_dashboard_refuses_a_public_host(monkeypatch):
    monkeypatch.setattr(dashboard, "_config", lambda: {"dashboard": {"host": "0.0.0.0"}})
    monkeypatch.setattr(dashboard.app, "run", lambda **k: pytest.fail("must not listen"))
    with pytest.raises(ValueError):
        dashboard.main()
