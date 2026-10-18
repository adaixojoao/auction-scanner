"""Batch 3.1: cached lists follow deadlines and settings, every operational
offer route passes the same safeguards, note saves are acknowledged honestly,
and concurrent settings writes keep each other's changes. These run the
production cache (TESTING off), not the uncached test path."""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

import dashboard


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def production_cache(client, db, monkeypatch):
    """The kept-list path, with no scan running and a fixed 'scan finished' tag.
    TESTING is off, so requests come as the local app does (its host guard)."""
    real_open = client.open

    def local_open(*args, **kwargs):
        kwargs.setdefault("base_url", "http://127.0.0.1:8050")
        return real_open(*args, **kwargs)
    monkeypatch.setattr(client, "open", local_open)
    builds = []
    real = dashboard.load_listings

    def counting(*args, **kwargs):
        if not kwargs.get("where"):
            builds.append(kwargs.get("mode"))
        return real(*args, **kwargs)
    monkeypatch.setattr(dashboard, "load_listings", counting)
    monkeypatch.setattr(dashboard, "_scan_busy", lambda: False)
    monkeypatch.setattr(dashboard, "_scan_finished_at", lambda: "scan-1")
    monkeypatch.setattr(dashboard, "_rebuild_soon", lambda mode: None)
    dashboard._LISTS.clear()
    dashboard.app.config["TESTING"] = False
    yield builds
    dashboard.app.config["TESTING"] = True
    dashboard._LISTS.clear()


def _a_research_lead(monkeypatch):
    """Score eleiloes:lead as a priced research lead on Live and invest
    (the way tests/test_live_invest_shortlist.py does)."""
    import scoring
    real = scoring.score_detail

    def fake(item, now=None, targets=None, mode="home"):
        if mode == "both" and item["id"].endswith(":lead"):
            item["live_score"], item["invest_score"], item["research_only"] = 70.0, 30.0, False
            return 60.0, ["fixture lead"]
        return real(item, now=now, targets=targets, mode=mode)
    monkeypatch.setattr(scoring, "score_detail", fake)


def ids(data):
    return [it["id"] for it in data["items"]]


# ─── 1. Deadline-correct cached visibility ──────────────────────────────────

END = datetime(2030, 1, 5, 12, 0, tzinfo=timezone.utc)


def test_a_kept_list_hides_a_sale_once_its_exact_deadline_passes(client, add, db, production_cache, monkeypatch):
    add(external_id="exact", title="Moradia", price=9000, date_end=END.isoformat())
    add(external_id="dateonly", title="Moradia", price=9100, date_end="2030-01-05")
    add(external_id="open", title="Moradia", price=9200, date_end="2031-01-01T10:00:00+00:00")
    add(external_id="gone", title="Moradia", price=9300, date_end=END.isoformat())
    client.post("/api/listings/status", json={"id": "eleiloes:gone", "status": "dismissed", "reason": "other"})
    add(external_id="star", title="Moradia", price=9400, date_end=END.isoformat())
    client.post("/api/listings/status", json={"id": "eleiloes:star", "status": "shortlisted"})
    dashboard._LISTS.clear()
    first = client.get("/api/listings?per_page=100&mode=home").get_json()
    assert set(ids(first)) == {"eleiloes:exact", "eleiloes:dateonly", "eleiloes:open", "eleiloes:star"}
    assert production_cache == ["home"]
    kept_before = [dict(it) for it in dashboard._LISTS["home"].items]

    # One second after the exact instant: the instant has passed, the date-only
    # sale keeps its whole day (common.effective_end).
    monkeypatch.setattr(dashboard, "_now", lambda: END + timedelta(seconds=1))
    after = client.get("/api/listings?per_page=100&mode=home").get_json()
    assert set(ids(after)) == {"eleiloes:dateonly", "eleiloes:open"}
    assert after["matched_total"] == 2 and after["stats"]["hidden"]["expired"] == 2
    hidden = {it["id"]: it for it in client.get("/api/listings?per_page=100&mode=home&show_hidden=1").get_json()["items"]}
    assert hidden["eleiloes:exact"]["hidden_reason"] == "expired"
    assert hidden["eleiloes:star"]["hidden_reason"] == "expired"           # shortlisting does not extend a sale
    assert hidden["eleiloes:gone"]["hidden_reason"].startswith("dismissed")  # dismissal comes first

    monkeypatch.setattr(dashboard, "_now", lambda: datetime(2030, 1, 6, 0, 0, 1, tzinfo=timezone.utc))
    assert ids(client.get("/api/listings?per_page=100&mode=home").get_json()) == ["eleiloes:open"]
    assert production_cache == ["home"]                                    # no rebuild, no rescoring
    assert [dict(it) for it in dashboard._LISTS["home"].items] == kept_before   # the kept rows are untouched


def test_research_candidates_drop_an_expired_sale_without_a_rebuild(client, add, production_cache, monkeypatch):
    import db as store
    _a_research_lead(monkeypatch)
    add("eleiloes", "lead", title="Moradia T3", price=22000, area_m2=90, description="moradia em bom estado",
        url="https://example.test/lead", date_end=END.isoformat(),
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    dashboard._LISTS.clear()
    research = lambda data: {c["id"] for g in ("valued", "unvalued") for c in (data["research"] or {}).get(g, [])}
    first = client.get("/api/listings?per_page=100&mode=both&min_score=0").get_json()
    assert "eleiloes:lead" in research(first)
    monkeypatch.setattr(dashboard, "_now", lambda: END + timedelta(seconds=1))
    later = client.get("/api/listings?per_page=100&mode=both&min_score=0").get_json()
    assert "eleiloes:lead" not in research(later) and "eleiloes:lead" not in ids(later)
    assert production_cache == ["both"]
    assert store.research_eligible({"hidden_reason": "expired"}) is False


def test_the_expiry_overlay_copies_only_changed_rows():
    rows = [{"id": "a", "date_end": END.isoformat(), "hidden_reason": None},
            {"id": "b", "date_end": END.isoformat(), "hidden_reason": "dismissed: other"},
            {"id": "c", "date_end": None, "hidden_reason": None}]
    ends, at = dashboard._expiry_index(rows, END - timedelta(days=1))
    kept = dashboard._Kept(0, rows, "t", "p", ends, at)
    assert dashboard._with_current_expiry(kept, END - timedelta(seconds=1)) is rows
    out = dashboard._with_current_expiry(kept, END)
    assert out[0] == {**rows[0], "hidden_reason": "expired"} and out[0] is not rows[0]
    assert out[1] is rows[1] and out[2] is rows[2] and rows[0]["hidden_reason"] is None


# ─── 2. Settings-aware kept lists ───────────────────────────────────────────

def test_kept_lists_follow_every_list_setting_but_not_unrelated_ones(client, add, production_cache):
    import config
    add(external_id="a", title="Moradia junto ao rio", price=9000)
    add(external_id="b", title="Moradia", price=40000)
    dashboard._LISTS.clear()
    get = lambda: set(ids(client.get("/api/listings?per_page=100&mode=home").get_json()))
    assert get() == {"eleiloes:a", "eleiloes:b"} and len(production_cache) == 1

    client.post("/api/settings", json={"notifications": {"smtp_host": "mail.example.invalid"}})
    client.post("/api/settings", json={"telegram": {"chat_id": "123"}})
    assert get() == {"eleiloes:a", "eleiloes:b"} and len(production_cache) == 1   # no rescoring for these

    client.post("/api/settings", json={"filters": {"exclude_keywords": ["rio"]}})
    assert get() == {"eleiloes:b"} and len(production_cache) == 2
    client.post("/api/settings", json={"max_price": 30000})                         # the goal budget
    assert get() == set() and len(production_cache) == 3                           # b is over it now
    client.post("/api/settings", json={"cash_on_hand": 5000})
    get()
    assert len(production_cache) == 4
    client.post("/api/settings", json={"filters": {"weights": {"home": {"price": 1.5}}}})
    get()
    assert len(production_cache) == 5
    # Another process writing config.json directly is noticed on the next read.
    config.update_config({"filters": {"exclude_keywords": []}, "cash_on_hand": 10_000_000, "max_price": 100000})
    assert get() == {"eleiloes:a", "eleiloes:b"} and len(production_cache) == 6


def test_a_build_overtaken_by_new_settings_is_not_published(client, add, production_cache, monkeypatch):
    import config
    add(external_id="a", title="Moradia", price=9000)
    dashboard._LISTS.clear()
    real = dashboard.load_listings
    calls = []

    def changing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:                          # settings saved mid-build
            config.update_config({"filters": {"exclude_keywords": ["moradia"]}})
        return real(*args, **kwargs)
    monkeypatch.setattr(dashboard, "load_listings", changing)
    data = client.get("/api/listings?per_page=100&mode=home").get_json()
    assert len(calls) == 2 and ids(data) == []        # built again under the new settings
    assert dashboard._LISTS["home"].policy == dashboard._policy_fingerprint(config.load_config())


def test_the_fingerprint_ignores_secrets_and_delivery_settings():
    base = {"filters": {"min_score": 10}, "cash_on_hand": 5}
    fp = dashboard._policy_fingerprint(base)
    for extra in ({"ai": {"anthropic_key": "sk-secret"}}, {"notifications": {"smtp_password": "x"}},
                  {"proponente": {"nome": "Test"}}, {"max_listings": 500}, {"accounts": {"x": {}}}):
        assert dashboard._policy_fingerprint({**base, **extra}) == fp
    assert dashboard._policy_fingerprint({**base, "ai": {"photo_check": True}}) != fp
    assert dashboard._policy_fingerprint({**base, "brand_new_setting": 1}) != fp
    assert "sk-secret" not in fp and len(fp) == 16


# ─── 3. Shared operational offer gates ──────────────────────────────────────

def _offer_listing(add):
    add("citius", "o1", title="Moradia", price=30000,
        raw_json=json.dumps({"processo": "1/20.0T", "modalidade": "carta fechada"}))
    return "citius:o1"


def _rows(db):
    return [dict(r) for r in db.execute("SELECT * FROM carta_log ORDER BY id")]


def test_historical_records_are_kept_as_told_and_marked_unchecked(client, db):
    r = client.post("/api/carta-log", json={"listing_id": "citius:unknown", "processo": "9/99.0T",
                                             "bid_amount": 99_000_000})
    assert r.status_code == 200 and r.get_json()["purpose"] == "historical"
    row = _rows(db)[-1]
    assert row["checklist_summary"] == dashboard.HISTORICAL_SUMMARY and row["cash_override"] is None
    assert client.post("/api/carta-log", json={"processo": "9/99.0T"}).status_code == 200


@pytest.mark.parametrize("body,code", [
    ([1, 2], 400), ({"purpose": "maybe", "processo": "x"}, 400), ({"listing_id": 5}, 400),
    ({"processo": "x", "bid_amount": "NaN"}, 400), ({"processo": "x", "bid_amount": -5}, 400),
    ({"processo": "x", "bid_amount": True}, 400), ({"processo": "x", "is_offer": "yes"}, 400),
    ({"purpose": "operational", "listing_id": "citius:missing", "bid_amount": 100}, 404),
    ({"purpose": "operational", "processo": "1/20.0T"}, 404),
])
def test_the_legacy_route_validates_its_body(client, db, body, code):
    assert client.post("/api/carta-log", json=body).status_code == code
    assert _rows(db) == []


def test_operational_records_pass_the_same_gates_as_sent_offers(client, db, add):
    import config
    lid = _offer_listing(add)
    body = {"purpose": "operational", "listing_id": lid, "processo": "1/20.0T", "bid_amount": 26000}
    r = client.post("/api/carta-log", json=body)
    assert r.status_code == 409 and r.get_json().get("checklist_gate")
    body["checklist_override"] = "Checked with my lawyer by phone"
    config.update_config({"cash_on_hand": 0})
    r = client.post("/api/carta-log", json=body)
    assert r.status_code == 409 and r.get_json()["cash_gate"] == "unset"
    config.update_config({"cash_on_hand": 1000})
    r = client.post("/api/carta-log", json=body)
    assert r.status_code == 409 and r.get_json()["cash_gate"] is True
    assert _rows(db) == []                                         # nothing written while refused
    body["cash_override"] = "Family loan agreed in writing"
    r = client.post("/api/carta-log", json=body)
    assert r.status_code == 200 and r.get_json()["purpose"] == "operational"
    row = _rows(db)[-1]
    assert row["cash_override"] == "Family loan agreed in writing"
    assert row["checklist_summary"] and row["checklist_summary"] != dashboard.HISTORICAL_SUMMARY
    assert row["location_level"]
    override = db.execute("SELECT reason FROM checklist_log WHERE carta_log_id = ? AND action = 'override'",
                          (row["id"],)).fetchone()
    assert override and override[0] == "Checked with my lawyer by phone"


def test_an_operational_information_request_is_not_gated(client, db, add):
    import config
    config.update_config({"cash_on_hand": 0})
    lid = _offer_listing(add)
    r = client.post("/api/carta-log", json={"purpose": "operational", "listing_id": lid, "is_offer": False,
                                             "bid_amount": 26000})
    assert r.status_code == 200
    row = _rows(db)[-1]
    assert row["is_offer"] == 0 and row["bid_amount"] is None


def test_sent_offers_still_refuse_without_cash(client, db, add):
    import config
    config.update_config({"cash_on_hand": 0})
    lid = _offer_listing(add)
    r = client.post("/api/offers/sent", json={"id": lid, "bid": "26000", "type": "pt_carta_fechada",
                                               "checklist_override": "Checked with my lawyer by phone"})
    assert r.status_code == 409 and _rows(db) == []


# ─── 6. Honest note acknowledgement ─────────────────────────────────────────

@pytest.mark.parametrize("body,code", [
    ([1], 400), ("text", 400), ({"note": "x"}, 400), ({"id": "", "note": "x"}, 400),
    ({"id": ["eleiloes:n1"], "note": "x"}, 400), ({"id": "eleiloes:n1", "note": ["x"]}, 400),
    ({"id": "eleiloes:n1", "note": None}, 400), ({"id": "eleiloes:n1", "note": 5}, 400),
    ({"id": "eleiloes:missing", "note": "x"}, 404),
])
def test_a_note_save_is_refused_rather_than_claimed(client, db, add, body, code):
    add(external_id="n1", title="Moradia", price=9000)
    r = client.post("/api/listing/note", json=body)
    assert r.status_code == code and "ok" not in r.get_json()
    assert db.execute("SELECT user_note FROM listings WHERE id='eleiloes:n1'").fetchone()[0] is None


def test_the_acknowledged_length_counts_as_the_browser_does(client, db, add):
    add(external_id="n1", title="Moradia", price=9000)
    text = "🏠 casa  \n"                        # the emoji is two UTF-16 units, as JavaScript counts
    assert client.post("/api/listing/note", json={"id": "eleiloes:n1", "note": text}).get_json()["length"] == 10
    assert client.post("/api/listing/note", json={"id": "eleiloes:n1", "note": "🏠" * 1001}).status_code == 400


# ─── 7. Coordinated configuration writes ────────────────────────────────────

def test_concurrent_thread_updates_keep_every_change(db):
    import config
    config.save_config({})
    errors = []

    def write(i):
        try:
            for j in range(5):
                config.update_config({f"key_{i}": j, "shared": {f"part_{i}": j}})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    user = config.load_user_config()
    assert not errors
    assert all(user[f"key_{i}"] == 4 for i in range(8))
    assert user["shared"] == {f"part_{i}": 4 for i in range(8)}
    folder = os.path.dirname(config.CONFIG_PATH)
    assert not [n for n in os.listdir(folder) if n.startswith(".config-")]


def test_concurrent_process_updates_keep_every_change(db, tmp_path):
    import config
    config.save_config({"kept": True})
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = ("import sys, config; config.CONFIG_PATH = sys.argv[1]\n"
              "for j in range(10): config.update_config({'proc_' + sys.argv[2]: j})\n")
    procs = [subprocess.Popen([sys.executable, "-c", script, config.CONFIG_PATH, str(i)], cwd=repo)
             for i in range(4)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    user = config.load_user_config()
    assert user["kept"] is True and all(user[f"proc_{i}"] == 9 for i in range(4))


def test_a_failed_write_keeps_the_last_good_file(db, monkeypatch):
    import config
    config.save_config({"cash_on_hand": 123, "notifications": {"smtp_password": "secret-value"}})
    before = open(config.CONFIG_PATH, encoding="utf-8").read()

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(config.os, "replace", broken)
    with pytest.raises(OSError) as err:
        config.update_config({"cash_on_hand": 999})
    assert "secret-value" not in str(err.value)
    assert open(config.CONFIG_PATH, encoding="utf-8").read() == before
    folder = os.path.dirname(config.CONFIG_PATH)
    assert not [n for n in os.listdir(folder) if n.startswith(".config-")]
    monkeypatch.undo()
    assert config.update_config({"cash_on_hand": 5})["cash_on_hand"] == 5    # the lock was let go


# ─── 4. Detail freshness tokens ─────────────────────────────────────────────

def test_rows_carry_a_detail_token_that_changes_with_evidence_and_status(client, db, add):
    import db as store
    add(external_id="t1", title="Moradia", price=9000)
    token = lambda: client.get("/api/listings?per_page=100&mode=home").get_json()["items"][0]["detail_token"]
    first = token()
    assert first == token()
    store.record_evidence_revision(db, "eleiloes:t1", "location", {"lat": 40.5, "lon": -7.2})
    db.commit()
    second = token()
    assert second != first
    client.post("/api/listings/status", json={"id": "eleiloes:t1", "status": "shortlisted"})
    assert token() != second
    db.execute("UPDATE listings SET current_bid = 9500 WHERE id = 'eleiloes:t1'")
    db.commit()
    assert len(token()) == 12


# ─── 8. Documentation and coverage ──────────────────────────────────────────

def test_the_sale_method_filter_says_what_it_covers(client):
    html = client.get("/").get_data(as_text=True)
    assert 'aria-describedby="f-sale-method-help"' in html
    assert "Portuguese and Dutch labels only" in html
