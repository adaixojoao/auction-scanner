"""Listings from sources an ordinary scan no longer reads are hidden, never deleted."""
import pytest

import db as db_module
from common import make_listing
from db import load_listings, set_listing_status, upsert_listing


@pytest.fixture(autouse=True)
def _switched_off_is_real(monkeypatch):
    monkeypatch.setattr(db_module, "_OFF_SOURCES", None)


def _add(db, source, external_id):
    upsert_listing(db, make_listing(source, external_id, title="Casa", price=30000, description="long text"))
    return f"{source}:{external_id}"


def _read(db):
    return {r["id"] for r in load_listings(db, include_hidden=True)}


def test_switched_off_listing_is_not_read_but_is_kept(db):
    on, off = _add(db, "eleiloes", "1"), _add(db, "fotocasa", "1")
    assert _read(db) == {on}
    assert db.execute("SELECT COUNT(*) FROM listings WHERE id = ?", (off,)).fetchone()[0] == 1


def test_shortlisted_listing_stays_when_its_source_is_off(db):
    off = _add(db, "fotocasa", "1")
    set_listing_status(db, off, "shortlisted")
    assert _read(db) == {off}


def test_copy_of_a_switched_off_listing_is_no_longer_a_duplicate(db):
    copy, off = _add(db, "eleiloes", "1"), _add(db, "fotocasa", "1")
    db.execute("UPDATE listings SET duplicate_of = ? WHERE id = ?", (off, copy))
    item = next(r for r in load_listings(db, include_hidden=True) if r["id"] == copy)
    assert item["duplicate_of"] is None
    assert "duplicate" not in (item.get("hidden_reason") or "")
