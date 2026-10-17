import sqlite3

import pytest

import checklist
import db as dbmod
from common import make_listing


@pytest.fixture
def listing(db):
    item = make_listing("eleiloes", "freshness-fixture", title="Moradia", price=30000,
                        area_m2=90, date_end="2030-01-01", raw_json={"notice": "fixture"})
    dbmod.upsert_listing(db, item)
    db.commit()
    return item


def result(db, listing):
    return checklist.build(listing, checklist.stored(db, listing["id"]), {})


def entry(db, listing, key):
    return next(x for x in result(db, listing)["items"] if x["key"] == key)


def change(db, listing, **fields):
    row = dict(listing)
    row.update(fields)
    dbmod.upsert_listing(db, row)
    db.commit()


@pytest.mark.parametrize("status", ["verified", "not_applicable"])
def test_completed_status_is_preserved_but_recheck_blocks(db, listing, status):
    checklist.set_status(db, listing, "registry", status, reference="fixture document")
    assert entry(db, listing, "registry")["status"] == status
    change(db, listing, description="Changed lot description")
    effective = entry(db, listing, "registry")
    assert effective["status"] == "requested"
    assert effective["needs_recheck"]
    assert effective["recorded_status"] == status
    assert "registry" in result(db, listing)["blocking_left"]
    saved = db.execute("SELECT status, reference FROM checklist_items WHERE listing_id = ?",
                       (listing["id"],)).fetchone()
    assert saved["status"] == status
    assert saved["reference"] == "fixture document"


def test_unchanged_replay_keeps_completion(db, listing):
    checklist.set_status(db, listing, "registry", "verified")
    change(db, listing)
    assert not entry(db, listing, "registry")["needs_recheck"]


def test_top_level_price_change_targets_cost_item(db, listing):
    for key in ("registry", "all_in"):
        checklist.set_status(db, listing, key, "verified")
    change(db, listing, price=24000)
    assert not entry(db, listing, "registry")["needs_recheck"]
    assert entry(db, listing, "all_in")["needs_recheck"]


def test_deadline_change_targets_terms(db, listing):
    for key in ("registry", "terms"):
        checklist.set_status(db, listing, key, "verified")
    change(db, listing, date_end="2030-02-01")
    assert entry(db, listing, "terms")["needs_recheck"]
    assert not entry(db, listing, "registry")["needs_recheck"]


def test_account_preparation_survives_description_change(db, listing):
    checklist.set_status(db, listing, "account", "verified")
    change(db, listing, description="Changed source wording")
    assert not entry(db, listing, "account")["needs_recheck"]


def test_legacy_completion_is_not_falsely_bound(db, listing):
    db.execute("""
        INSERT INTO checklist_items (listing_id, item_key, status, updated_at)
        VALUES (?, 'registry', 'verified', '2020-01-01')
    """, (listing["id"],))
    db.commit()
    assert entry(db, listing, "registry")["needs_recheck"]


def test_explicit_reconfirmation_uses_persisted_not_stale_caller_inputs(db, listing):
    checklist.set_status(db, listing, "registry", "verified")
    change(db, listing, description="Changed persisted context")
    assert entry(db, listing, "registry")["needs_recheck"]
    checklist.set_status(db, listing, "registry", "verified")
    assert not entry(db, listing, "registry")["needs_recheck"]
    logs = checklist.history(db, listing["id"])
    assert logs[0]["old_status"] == "verified"
    assert logs[0]["new_status"] == "verified"
    assert logs[0]["evidence_binding"] != logs[1]["evidence_binding"]


def test_single_bulk_reads_agree_without_writing(db, listing):
    checklist.set_status(db, listing, "registry", "verified")
    change(db, listing, description="Changed")
    changes = db.total_changes
    single = checklist.stored(db, listing["id"])
    assert checklist.stored_all(db)[listing["id"]] == single
    assert db.total_changes == changes


def test_concern_remains_recorded_after_source_change(db, listing):
    checklist.set_status(db, listing, "registry", "concern", notes="fixture concern")
    change(db, listing, description="Changed")
    assert entry(db, listing, "registry")["status"] == "concern"


def test_fresh_checklist_export_explains_recheck(db, listing):
    checklist.set_status(db, listing, "registry", "verified")
    change(db, listing, description="Changed")
    assert "Needs rechecking; recorded status: verified" in checklist.as_text(
        listing, result(db, listing)
    )


def test_log_failure_does_not_partially_save_completion(db, listing):
    db.execute("""
        CREATE TEMP TRIGGER reject_checklist_log BEFORE INSERT ON checklist_log
        BEGIN SELECT RAISE(ABORT, 'fixture log failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="fixture log failure"):
        checklist.set_status(db, listing, "registry", "verified")
    assert db.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 0


def test_migration_preserves_legacy_status_and_is_idempotent(db):
    # Temporary test DB only: simulate the preceding checklist tables.
    db.execute("DROP TABLE checklist_items")
    db.execute("DROP TABLE checklist_log")
    dbmod._migrate_v10(db)
    db.execute("""
        INSERT INTO checklist_items (listing_id, item_key, status, updated_at)
        VALUES ('fixture:legacy', 'registry', 'verified', '2020-01-01')
    """)
    db.execute("PRAGMA user_version = 29")
    db.commit()
    dbmod.init_db(db)
    dbmod.init_db(db)
    row = db.execute("SELECT status, evidence_binding FROM checklist_items").fetchone()
    assert row["status"] == "verified"
    assert row["evidence_binding"] is None


def test_json_object_key_order_is_not_material(db, listing):
    change(db, listing, raw_json='{"a": 1, "b": 2}')
    checklist.set_status(db, listing, "registry", "verified")
    change(db, listing, raw_json='{"b": 2, "a": 1}')
    assert not entry(db, listing, "registry")["needs_recheck"]


# ─── Endpoint integration (section 10 acceptance checks) ─────────────

@pytest.fixture
def client(db, monkeypatch):
    import config
    import dashboard
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000,
                        "proponente": {"nome": "Test Person", "nif": "123",
                                       "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _court_lot(add, **fields):
    import json
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada", "agente_email": "ae@solic.pt"}
    base = dict(title="Moradia", price=30000, area_m2=120, raw_json=json.dumps(raw))
    base.update(fields)
    add("citius", "fresh1", **base)
    return "citius:fresh1"


def test_stale_blocking_completion_reenters_the_offer_gate(client, db, add):
    lid = _court_lot(add)
    for key in ("registry", "charges"):
        assert client.post("/api/checklist", json={"id": lid, "key": key, "status": "verified"}).status_code == 200
    assert client.get(f"/api/checklist?id={lid}").get_json()["blocking_left"] == []

    _court_lot(add, description="The lot now includes a garage")
    fresh = client.get(f"/api/checklist?id={lid}").get_json()
    assert sorted(fresh["blocking_left"]) == ["charges", "registry"]
    assert all(x["needs_recheck"] for x in fresh["items"] if x["key"] in ("registry", "charges"))
    view = client.get("/api/offers").get_json()["review"][0]
    assert view["checklist"]["blocking_left"] == 2          # bulk Offers agrees with the fresh GET

    refused = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00"})
    assert refused.status_code == 409 and refused.get_json()["checklist_gate"]
    assert client.post("/api/offers/sent", json={"id": lid, "type": "pt_info"}).status_code == 200

    sent = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00",
                                                 "checklist_override": "lawyer rechecked by phone"})
    assert sent.status_code == 200
    assert any(h["action"] == "override" for h in checklist.history(db, lid))
    # An override is logged, but it is not a re-verification.
    assert sorted(client.get(f"/api/checklist?id={lid}").get_json()["blocking_left"]) == ["charges", "registry"]


def test_binding_and_staleness_survive_a_reconnect(db, listing):
    checklist.set_status(db, listing, "registry", "verified")
    fresh = dbmod.connect(dbmod.DB_PATH)
    try:
        assert not next(x for x in checklist.build(listing, checklist.stored(fresh, listing["id"]), {})["items"]
                        if x["key"] == "registry")["needs_recheck"]
    finally:
        fresh.close()
    change(db, listing, description="Changed")
    fresh = dbmod.connect(dbmod.DB_PATH)
    try:
        assert checklist.stored(fresh, listing["id"])["registry"]["needs_recheck"]
    finally:
        fresh.close()


def test_photo_and_location_changes_target_their_items_only(db, listing):
    for key in ("visit", "location", "account"):
        checklist.set_status(db, listing, key, "verified")
    change(db, listing, image_url="https://example.invalid/new.jpg")
    assert entry(db, listing, "visit")["needs_recheck"]
    assert not entry(db, listing, "location")["needs_recheck"]
    assert not entry(db, listing, "account")["needs_recheck"]
