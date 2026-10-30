"""A scan's lookups ask for the same goal lists again and again; inside
db.reuse_lists() the second read is a copy of the first (2026-10-30)."""
import db as dbmod
from db import load_listings, reuse_lists


def test_a_repeated_read_is_reused_and_each_caller_gets_its_own_rows(db, add, monkeypatch):
    add("eleiloes", "a", title="Moradia", price=20000)
    reads = []
    real = dbmod._load_listings
    monkeypatch.setattr(dbmod, "_load_listings", lambda *a, **k: reads.append(k.get("mode")) or real(*a, **k))
    with reuse_lists():
        first = load_listings(db, filters={}, apply_min_score=False)
        first[0]["best_rank"] = 99                     # enrich_order writes on its rows
        second = load_listings(db, filters={}, apply_min_score=False)
        load_listings(db, filters={}, apply_min_score=False, mode="invest")
        load_listings(db, filters={}, apply_min_score=False, refresh=True)
    assert reads == ["home", "invest", "home"]       # the refresh read afresh
    assert "best_rank" not in second[0] and second[0]["id"] == first[0]["id"]


def test_outside_the_block_every_read_is_fresh(db, add, monkeypatch):
    add("eleiloes", "a", title="Moradia", price=20000)
    reads = []
    real = dbmod._load_listings
    monkeypatch.setattr(dbmod, "_load_listings", lambda *a, **k: reads.append(1) or real(*a, **k))
    load_listings(db, filters={}, apply_min_score=False)
    load_listings(db, filters={}, apply_min_score=False)
    assert len(reads) == 2 and dbmod._REUSE[0] is None
