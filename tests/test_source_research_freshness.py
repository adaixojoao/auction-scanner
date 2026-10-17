import sqlite3

import pytest

import db as dbmod
from common import make_listing


LID = "eleiloes:freshness-fixture"


def observe(db, **fields):
    return dbmod.upsert_listing(
        db, make_listing("eleiloes", "freshness-fixture", **fields)
    )


def rev(db):
    return dbmod.get_evidence_revisions(db, LID)["listing_source"]


def rev_sum(db):
    return db.execute(
        "SELECT COALESCE(SUM(rev), 0) FROM evidence_revisions WHERE listing_id = ?",
        (LID,),
    ).fetchone()[0]


def save_decision(db, value):
    db.execute(
        "INSERT OR REPLACE INTO evaluator_decisions"
        " (listing_id, state, requirements_json, provenance_json, evidence_rev_sum, evaluated_at)"
        " VALUES (?, 'research', '[]', '{}', ?, '2030-01-01T00:00:00+00:00')",
        (LID, value),
    )
    db.commit()


def test_insert_and_replay_have_stable_source_revision(db):
    observe(db, title="Casa", price=30000)
    assert rev(db) == 1
    observe(db, title="Casa", price=30000)
    assert rev(db) == 1


@pytest.mark.parametrize("field,before,after", [
    ("price", 30000, 24000),
    ("min_price", 25000, 22000),
    ("date_end", "2030-01-01", "2030-01-02"),
    ("description", "Casa habitável", "Casa em ruínas"),
    ("area_m2", 90, 80),
    ("image_url", "https://example.invalid/old.jpg", "https://example.invalid/new.jpg"),
])
def test_accepted_source_change_invalidates_default_decision_read(db, field, before, after):
    observe(db, **{field: before})
    save_decision(db, rev_sum(db))
    assert dbmod.load_decision(db, LID) is not None
    observe(db, **{field: after})
    assert rev(db) == 2
    assert dbmod.load_decision(db, LID) is None
    audit = dbmod.load_decision(db, LID, allow_stale=True)
    assert audit["stale"] is True
    assert audit["current_evidence_rev_sum"] == rev_sum(db)


def test_stale_caller_sum_cannot_bypass_database_freshness(db):
    observe(db, price=30000)
    old = rev_sum(db)
    save_decision(db, old)
    observe(db, price=24000)
    assert dbmod.load_decision(db, LID, ev_rev_sum=old) is None


def test_caller_sum_must_match_current_database(db):
    observe(db, price=30000)
    save_decision(db, rev_sum(db))
    assert dbmod.load_decision(db, LID, ev_rev_sum=rev_sum(db)) is not None
    assert dbmod.load_decision(db, LID, ev_rev_sum=rev_sum(db) + 1) is None


def test_unknown_legacy_decision_sum_is_stale(db):
    save_decision(db, None)
    assert dbmod.load_decision(db, LID) is None
    assert dbmod.load_decision(db, LID, allow_stale=True)["stale"]


def test_zero_revision_sum_can_be_fresh(db):
    save_decision(db, 0)
    assert not dbmod.load_decision(db, LID)["stale"]


def test_reverification_restores_freshness_without_clearing_evidence(db):
    observe(db, price=30000)
    save_decision(db, rev_sum(db))
    observe(db, price=24000)
    assert dbmod.load_decision(db, LID) is None
    save_decision(db, rev_sum(db))
    assert not dbmod.load_decision(db, LID)["stale"]
    assert rev(db) == 2


def test_changed_source_excludes_clearance_without_deleting_it(db):
    observe(db, price=30000)
    dbmod.clear_extreme_discount(db, LID, note="fixture review", evidence_rev_sum=rev_sum(db))
    assert dbmod._extreme_clearances(db, {LID}, {LID: rev_sum(db)}) == {LID}
    observe(db, price=24000)
    assert dbmod._extreme_clearances(db, {LID}, {LID: rev_sum(db)}) == set()
    assert db.execute(
        "SELECT note FROM investigation_clearances WHERE listing_id = ?", (LID,)
    ).fetchone()[0] == "fixture review"


def test_thin_and_cut_copy_observations_do_not_change_source_revision(db):
    full = "A detailed description of the property and its surrounding land."
    observe(db, description=full, price=30000)
    observe(db)
    observe(db, description="A detailed description of the property...")
    assert rev(db) == 1


def test_json_order_and_learned_payload_do_not_change_source_revision(db):
    observe(db, raw_json={"a": 1, "b": 2, "geo": {"lat": 1}})
    observe(db, raw_json={"b": 2, "a": 1, "geo": {"lat": 2}})
    assert rev(db) == 1


def test_source_method_payload_change_bumps_revision(db):
    observe(db, raw_json={"modalidade": "old"})
    observe(db, raw_json={"modalidade": "new"})
    assert rev(db) == 2


def test_url_only_change_does_not_invalidate_source_snapshot(db):
    observe(db, url="https://example.invalid/old")
    observe(db, url="https://example.invalid/new")
    assert rev(db) == 1


def test_other_listing_is_unaffected(db):
    observe(db, price=30000)
    other = make_listing("eleiloes", "independent-fixture", price=40000)
    dbmod.upsert_listing(db, other)
    observe(db, price=24000)
    assert dbmod.get_evidence_revisions(db, other["id"])["listing_source"] == 1


def test_existing_unbaselined_row_invalidates_legacy_decision_on_observation(db):
    # Simulate a pre-4C row without deleting production history.
    dbmod._upsert_listing_without_change_log(
        db, make_listing("eleiloes", "freshness-fixture", price=30000)
    )
    save_decision(db, 0)
    assert dbmod.load_decision(db, LID) is not None
    observe(db, price=30000)
    assert rev(db) == 1
    assert dbmod.load_decision(db, LID) is None


def test_reader_preserves_stale_row_and_does_not_write(db):
    observe(db, price=30000)
    save_decision(db, rev_sum(db))
    observe(db, price=24000)
    changes = db.total_changes
    assert dbmod.load_decision(db, LID) is None
    dbmod.load_decision(db, LID, allow_stale=True)
    assert db.total_changes == changes
    assert db.execute(
        "SELECT COUNT(*) FROM evaluator_decisions WHERE listing_id = ?", (LID,)
    ).fetchone()[0] == 1


def test_caller_rollback_restores_listing_journal_and_revision(db):
    observe(db, price=30000)
    db.commit()
    observe(db, price=24000)
    assert rev(db) == 2
    db.rollback()
    assert rev(db) == 1
    assert dbmod.listing_changes(db, LID) == []
    assert db.execute("SELECT price FROM listings WHERE id = ?", (LID,)).fetchone()[0] == 30000


def test_revision_failure_rolls_back_source_and_history(db):
    observe(db, price=30000)
    db.commit()
    db.execute("""
        CREATE TEMP TRIGGER reject_source_revision BEFORE UPDATE ON evidence_revisions
        WHEN NEW.evidence_key = 'listing_source'
        BEGIN SELECT RAISE(ABORT, 'fixture revision failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="fixture revision failure"):
        observe(db, price=24000)
    assert rev(db) == 1
    assert dbmod.listing_changes(db, LID) == []
    assert db.execute("SELECT price FROM listings WHERE id = ?", (LID,)).fetchone()[0] == 30000


def test_existing_independent_evidence_revision_still_invalidates_decision(db):
    observe(db, price=30000)
    save_decision(db, rev_sum(db))
    dbmod.record_evidence_revision(db, LID, "climate", {"fixture_grade": "unknown"})
    assert dbmod.load_decision(db, LID) is None
