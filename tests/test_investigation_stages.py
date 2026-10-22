"""Research stages and decisions (investigation_stages.py, db v32): append-only,
separate from shortlist/dismiss status, validated, conflict-safe, and never a
statement that a property is ready to buy. Synthetic data only."""
import logging
from datetime import date, datetime, timedelta, timezone

import pytest

import dashboard
import investigation_stages as stages


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def soon(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def home(add, eid="h", **fields):
    base = dict(title="Moradia T3 com quintal", price=60_000, tipo="moradia", district="Guarda", concelho="Guarda",
                date_end=soon(), url="https://www.e-leiloes.pt/leilao/x")
    return add(external_id=eid, **{**base, **fields})


def post(client, **body):
    body = {"id": "eleiloes:h", "stage": "screening", "reason_kind": "progress", "reason": "Looks worth a look",
            **body}
    return client.post("/api/investigation/stage", json=body)


# ─── Migration ─────────────────────────────────────────────────────────────

def test_a_v31_database_gains_the_empty_table_and_keeps_its_rows(tmp_path):
    import db as dbmod
    path = str(tmp_path / "v31.db")
    conn = dbmod.connect(path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO listings (id, source, country, external_id, title, first_seen, last_seen, is_new)"
                 " VALUES ('eleiloes:old', 'eleiloes', 'PT', 'old', 'Moradia', ?, ?, 0)", (now, now))
    conn.execute("DROP TABLE investigation_stages")
    conn.execute("PRAGMA user_version = 31")
    conn.commit()
    conn.close()
    conn = dbmod.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION >= 32
    assert conn.execute("SELECT COUNT(*) FROM investigation_stages").fetchone()[0] == 0
    assert conn.execute("SELECT title FROM listings WHERE id='eleiloes:old'").fetchone()[0] == "Moradia"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(investigation_stages)")}
    assert {"listing_id", "stage", "previous_stage", "reason", "reason_kind", "evidence_refs_json", "spend_amount",
            "spend_currency", "spend_date", "spend_category", "created_at"} <= cols
    conn.close()
    dbmod.connect(path).close()          # idempotent


# ─── Recording ─────────────────────────────────────────────────────────────

def test_stages_append_and_move_back_with_an_audit_trail(client, db, add):
    home(add)
    first = post(client, expected_previous="").get_json()["accepted"]
    assert (first["stage"], first["previous_stage"]) == ("screening", None)
    post(client, stage="documents_requested", expected_previous="screening")
    back = post(client, stage="screening", reason_kind="insufficient_evidence",
                reason="The registry answer did not cover the plot", expected_previous="documents_requested")
    data = back.get_json()
    assert back.status_code == 200 and data["accepted"]["previous_stage"] == "documents_requested"
    assert [r["stage"] for r in data["stage_history"]["history"]] == ["screening", "documents_requested", "screening"]
    assert db.execute("SELECT COUNT(*) FROM investigation_stages").fetchone()[0] == 3


def test_a_confirmed_issue_and_insufficient_evidence_stay_distinct(client, db, add):
    home(add)
    post(client, stage="rejected", reason_kind="confirmed_issue", reason="Registry shows a usufruct")
    post(client, stage="paused", reason_kind="insufficient_evidence", reason="No answer from the court yet")
    kinds = [r["reason_kind"] for r in stages.history(db, "eleiloes:h")]
    assert kinds == ["confirmed_issue", "insufficient_evidence"]


def test_a_stage_changes_no_status_checklist_or_verification(client, db, add):
    home(add)
    before = client.get("/api/investigation?id=eleiloes:h&mode=home").get_json()
    post(client, stage="decision_recorded", reason="Decided to bid")
    post(client, stage="rejected", reason_kind="confirmed_issue", reason="Not whole ownership")
    assert db.execute("SELECT COUNT(*) FROM listing_status").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM listing_status_history").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 0
    after = client.get("/api/investigation?id=eleiloes:h&mode=home").get_json()
    assert after["dimensions"] == before["dimensions"] and after["purchase_readiness_established"] is False
    assert after["stage_history"]["current"]["stage"] == "rejected"
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 1     # nothing dismissed or deleted


def test_a_concurrent_change_is_refused_and_writes_nothing(client, db, add):
    home(add)
    post(client, expected_previous="")
    stale = post(client, stage="evidence_reviewed", expected_previous="")   # another tab moved it on
    assert stale.status_code == 409 and stale.get_json()["current"] == "screening"
    assert db.execute("SELECT COUNT(*) FROM investigation_stages").fetchone()[0] == 1


@pytest.mark.parametrize("change, message", [
    ({"stage": "ready_to_buy"}, "unknown stage"),
    ({"reason_kind": "gut"}, "unknown reason kind"),
    ({"reason": "   "}, "a reason is required"),
    ({"reason": "x" * 1001}, "longer than"),
    ({"checklist_keys": ["no_such_item"]}, "unknown checklist item"),
    ({"checklist_keys": "registry"}, "must be a list"),
    ({"spend": {"amount": -5, "currency": "EUR", "date": "2026-01-02", "category": "registry"}}, "between 0"),
    ({"spend": {"amount": "nan", "currency": "EUR", "date": "2026-01-02", "category": "registry"}}, "between 0"),
    ({"spend": {"amount": True, "currency": "EUR", "date": "2026-01-02", "category": "registry"}}, "a number"),
    ({"spend": {"amount": 20_000_000, "currency": "EUR", "date": "2026-01-02", "category": "registry"}}, "between 0"),
    ({"spend": {"amount": 20, "currency": "euro", "date": "2026-01-02", "category": "registry"}}, "three-letter"),
    ({"spend": {"amount": 20, "currency": "EUR", "date": "02/01/2026", "category": "registry"}}, "a date"),
    ({"spend": {"amount": 20, "currency": "EUR",
                "date": (date.today() + timedelta(days=3)).isoformat(), "category": "registry"}}, "future"),
    ({"spend": {"amount": 20, "currency": "EUR", "date": "2026-01-02", "category": "bribes"}}, "category"),
    ({"spend": {"currency": "EUR"}}, "needs its amount"),
    ({"spend": "20"}, "must be an object"),
])
def test_malformed_bodies_are_refused_and_write_nothing(client, db, add, change, message):
    home(add)
    r = post(client, **change)
    assert r.status_code == 400 and message in r.get_json()["error"]
    assert db.execute("SELECT COUNT(*) FROM investigation_stages").fetchone()[0] == 0


def test_unknown_listings_and_non_objects_are_refused(client, db):
    assert post(client, id="nope:1").status_code == 404
    assert client.post("/api/investigation/stage", json=["screening"]).status_code == 400
    assert client.post("/api/investigation/stage", data="x", content_type="application/json").status_code == 400


def test_spending_is_kept_exactly_and_unknown_is_not_zero(client, db, add):
    home(add)
    post(client, stage="documents_requested", spend={"amount": "24.50", "currency": "eur", "date": "2026-10-01",
                                                     "category": "registry"})
    post(client, stage="evidence_reviewed", spend=None)
    h = client.get("/api/investigation?id=eleiloes:h&mode=home").get_json()["stage_history"]
    assert h["spend_totals"] == {"EUR": 24.5} and h["spend_entries"] == 1
    assert h["history"][1]["spend"]["amount"] is None


def test_evidence_references_are_this_listings_checklist_keys(client, db, add):
    home(add)
    r = post(client, checklist_keys=["registry", "charges", "registry"])
    assert r.status_code == 200 and r.get_json()["accepted"]["checklist_keys"] == ["charges", "registry"]


def test_a_stage_recorded_for_other_sale_facts_says_so(client, db, add):
    home(add)
    post(client, stage="professional_review", reason="Lawyer reviewed the notice")
    home(add, price=48_000, date_end=soon(60))       # a new sale event: price and deadline changed
    row = client.get("/api/investigation?id=eleiloes:h&mode=home").get_json()["stage_history"]["history"][0]
    assert set(row["facts_changed"]) == {"price", "deadline"}


def test_reasons_are_never_logged(client, db, add, caplog):
    home(add)
    with caplog.at_level(logging.DEBUG):
        post(client, reason="PRIVATE-REASON-42")
        post(client, stage="nonsense", reason="PRIVATE-REASON-43")
    assert "PRIVATE-REASON" not in caplog.text


# ─── Where stages show ─────────────────────────────────────────────────────

def test_the_brief_shows_stages_and_spending_but_not_the_reasons(client, db, add):
    home(add)
    post(client, stage="documents_requested", reason="PRIVATE-REASON [x](javascript:1)",
         spend={"amount": 30, "currency": "EUR", "date": "2026-10-01", "category": "registry"})
    body = client.get("/api/investigation/brief.md?id=eleiloes:h&mode=home").get_data(as_text=True)
    assert "Current: Documents requested" in body and "30.00 EUR" in body
    assert "PRIVATE-REASON" not in body and "javascript" not in body
    assert "unknown, not zero" in body


def test_the_queue_shows_and_filters_by_stage(client, db, add):
    home(add, eid="a")
    home(add, eid="b", title="Moradia T2 com pátio")
    home(add, eid="c", title="Moradia T4 grande")
    post(client, id="eleiloes:a", stage="screening")
    post(client, id="eleiloes:b", stage="rejected", reason_kind="confirmed_issue", reason="A share only")
    every = client.get("/api/investigation-queue?mode=home").get_json()
    by_id = {e["listing_id"]: e for e in every["items"]}
    assert by_id["eleiloes:a"]["stage"]["label"] == "Screening" and by_id["eleiloes:c"]["stage"] is None
    assert "reason" not in by_id["eleiloes:b"]["stage"]
    ids = lambda q: sorted(e["listing_id"] for e in client.get(f"/api/investigation-queue?mode=home&stage={q}")  # noqa: E731
                           .get_json()["items"])
    assert ids("screening") == ["eleiloes:a"]
    assert ids("none") == ["eleiloes:c"]
    assert ids("active") == ["eleiloes:a", "eleiloes:c"]
    assert ids("rejected") == ["eleiloes:b"]
    filtered = client.get("/api/investigation-queue?mode=home&stage=screening").get_json()
    assert filtered["history"] == []                  # filtered by stage is not "earlier research"
    assert client.get("/api/investigation-queue?mode=home&stage=ready").status_code == 400


def test_rows_carry_the_current_stage(client, db, add):
    home(add)
    post(client, stage="visit_warranted")
    item = next(it for it in client.get("/api/listings?mode=home").get_json()["items"] if it["id"] == "eleiloes:h")
    assert item["investigation"]["stage"] == {"stage": "visit_warranted", "label": "Visit warranted"}


def test_one_page_reads_stages_once(client, db, add, monkeypatch):
    for i in range(6):
        home(add, eid=f"q{i}")
    calls = []
    real = stages.current_for
    monkeypatch.setattr(stages, "current_for", lambda db, ids: calls.append(len(list(ids))) or real(db, ids))
    monkeypatch.setattr(stages, "history", lambda *a: pytest.fail("one history read per row"))
    client.get("/api/listings?mode=home&min_score=0")
    assert calls == [6]
