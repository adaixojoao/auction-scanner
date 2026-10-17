import json
import sqlite3

import pytest

import db as dbmod
from common import make_listing


LID = "eleiloes:history-fixture"


def observe(db, **fields):
    return dbmod.upsert_listing(
        db, make_listing("eleiloes", "history-fixture", **fields)
    )


def stored(db):
    return dict(db.execute("SELECT * FROM listings WHERE id = ?", (LID,)).fetchone())


def history(db, **kwargs):
    return dbmod.listing_changes(db, LID, **kwargs)


def test_initial_insert_is_not_a_change(db):
    assert observe(db, price=30000) == "inserted"
    assert history(db) == []
    assert db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1


def test_repeated_observation_is_idempotent(db):
    observe(db, title="Casa", price=30000)
    assert observe(db, title="Casa", price=30000) == "updated"
    assert history(db) == []


def test_price_change_preserves_existing_price_history(db):
    observe(db, price=30000)
    observe(db, price=24000)
    assert history(db)[0]["changes"] == {"price": {"before": 30000, "after": 24000}}
    assert [row[0] for row in db.execute(
        "SELECT price FROM price_history ORDER BY rowid"
    )] == [30000, 24000]


@pytest.mark.parametrize("field,before,after", [
    ("min_price", 20000, 22000),
    ("date_end", "2030-01-01T00:00:00+00:00", "2030-01-02T00:00:00+00:00"),
    ("description", "Casa habitável", "Casa em ruínas"),
    ("area_m2", 90, 80),
    ("concelho", "Guarda", "Trancoso"),
])
def test_material_field_changes_are_explained(db, field, before, after):
    observe(db, **{field: before})
    observe(db, **{field: after})
    assert history(db)[0]["changes"] == {field: {"before": before, "after": after}}


def test_one_event_groups_simultaneous_changes(db):
    observe(db, price=30000, min_price=25000)
    observe(db, price=28000, min_price=23000)
    assert len(history(db)) == 1
    assert set(history(db)[0]["changes"]) == {"price", "min_price"}


def test_thin_observation_does_not_claim_fields_were_removed(db):
    observe(db, description="Full text", min_price=25000)
    observe(db, price=30000)
    assert stored(db)["description"] == "Full text"
    assert stored(db)["min_price"] == 25000
    assert history(db)[0]["changes"] == {"price": {"before": None, "after": 30000}}


def test_shortened_text_is_not_logged_as_an_accepted_change(db):
    full = "A detailed description of the property and its surrounding land."
    observe(db, description=full)
    observe(db, description="A detailed description of the property...")
    assert stored(db)["description"] == full
    assert history(db) == []


def test_json_order_is_not_a_change(db):
    observe(db, raw_json='{"a": 1, "b": 2}')
    observe(db, raw_json='{"b": 2, "a": 1}')
    assert history(db) == []


def test_learned_keys_do_not_create_source_change_events(db):
    observe(db, raw_json={"source_field": "same", "geo": {"lat": 1}})
    observe(db, raw_json={"source_field": "same", "geo": {"lat": 2}})
    assert history(db) == []


def test_source_payload_change_is_retained_without_learned_data(db):
    observe(db, raw_json={"modalidade": "old", "geo": {"lat": 1}})
    observe(db, raw_json={"modalidade": "new"})
    assert history(db)[0]["changes"]["raw_json"] == {
        "before": {"modalidade": "old"}, "after": {"modalidade": "new"}
    }
    assert json.loads(stored(db)["raw_json"])["geo"] == {"lat": 1}


def test_malformed_source_payload_is_preserved(db):
    observe(db, raw_json="{malformed one")
    observe(db, raw_json="{malformed two")
    assert history(db)[0]["changes"]["raw_json"] == {
        "before": "{malformed one", "after": "{malformed two"
    }


def test_url_only_change_is_not_material_but_event_has_url_context(db):
    observe(db, price=30000, url="https://example.invalid/old")
    observe(db, url="https://example.invalid/new")
    assert history(db) == []
    observe(db, price=28000, url="https://example.invalid/latest")
    event = history(db)[0]
    assert event["previous_url"] == "https://example.invalid/new"
    assert event["current_url"] == "https://example.invalid/latest"
    assert event["source"] == "eleiloes"
    assert event["listing_id"] == LID
    assert event["observed_at"] == stored(db)["last_seen"]


def test_reversions_remain_distinct_and_pagination_is_stable(db):
    observe(db, price=30000)
    observe(db, price=24000)
    observe(db, price=30000)
    events = history(db)
    assert len(events) == 2
    assert events[0]["changes"]["price"] == {"before": 24000, "after": 30000}
    assert history(db, limit=1) == [events[0]]
    assert history(db, before_id=events[0]["id"]) == [events[1]]
    assert dbmod.listing_changes(db, "unknown:fixture") == []


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5, "10"])
def test_invalid_history_limit_is_rejected(db, limit):
    with pytest.raises(ValueError):
        history(db, limit=limit)


def test_history_reader_does_not_write(db):
    observe(db, price=30000)
    observe(db, price=24000)
    count = db.total_changes
    history(db)
    assert db.total_changes == count


def test_caller_rollback_reverts_listing_and_history(db):
    observe(db, price=30000)
    db.commit()
    observe(db, price=24000)
    assert db.in_transaction
    db.rollback()
    assert stored(db)["price"] == 30000
    assert history(db) == []
    assert db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1


def test_history_failure_rolls_back_upsert_but_preserves_prior_work(db):
    observe(db, price=30000)
    db.commit()
    db.execute("UPDATE listings SET user_note = 'fixture note' WHERE id = ?", (LID,))
    db.execute("""
        CREATE TEMP TRIGGER reject_history BEFORE INSERT ON listing_change_log
        BEGIN SELECT RAISE(ABORT, 'fixture history failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="fixture history failure"):
        observe(db, price=24000)
    assert stored(db)["price"] == 30000
    assert stored(db)["user_note"] == "fixture note"
    assert history(db) == []
    assert db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1


def test_history_survives_reconnect_without_duplicate_replay(tmp_path):
    path = str(tmp_path / "history.db")
    conn = dbmod.connect(path)
    try:
        observe(conn, price=30000)
        observe(conn, price=24000)
        conn.commit()
    finally:
        conn.close()
    conn = dbmod.connect(path)
    try:
        observe(conn, price=24000)
        conn.commit()
        assert len(history(conn)) == 1
    finally:
        conn.close()


def test_migration_preserves_old_rows_and_creates_no_fake_history(db):
    observe(db, price=30000)
    db.commit()
    snapshot = stored(db)
    # Simulate the immediately preceding v28 schema, not production data.
    db.execute("DROP TABLE listing_change_log")
    db.execute("PRAGMA user_version = 28")
    db.commit()
    dbmod.init_db(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
    assert stored(db) == snapshot
    assert history(db) == []
    dbmod.init_db(db)
    assert history(db) == []
