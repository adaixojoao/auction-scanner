"""Tests for Milestone D — evidence revision tracking (F09, F10, F16)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from db import get_evidence_revisions, init_db, record_evidence_revision


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ─── record_evidence_revision ────────────────────────────────────────────────

def test_first_write_returns_rev_1(db):
    rev = record_evidence_revision(db, "citius:a", "photo_check", {"condition": "good"})
    assert rev == 1


def test_same_content_is_idempotent(db):
    payload = {"condition": "good", "shows_house": True}
    record_evidence_revision(db, "citius:a", "photo_check", payload)
    rev2 = record_evidence_revision(db, "citius:a", "photo_check", payload)
    assert rev2 == 1


def test_changed_content_bumps_rev(db):
    record_evidence_revision(db, "citius:a", "photo_check", {"condition": "good"})
    rev2 = record_evidence_revision(db, "citius:a", "photo_check", {"condition": "some"})
    assert rev2 == 2


def test_multiple_bumps_accumulate(db):
    for i, cond in enumerate(["good", "some", "heavy", "unknown"], start=1):
        rev = record_evidence_revision(db, "citius:a", "photo_check", {"condition": cond})
        assert rev == i


def test_different_keys_are_independent(db):
    record_evidence_revision(db, "citius:a", "photo_check", {"x": 1})
    record_evidence_revision(db, "citius:a", "climate", {"flood_m": 0.0})
    rev_p = record_evidence_revision(db, "citius:a", "photo_check", {"x": 2})
    rev_c = record_evidence_revision(db, "citius:a", "climate", {"flood_m": 0.0})
    assert rev_p == 2  # changed
    assert rev_c == 1  # unchanged


def test_different_listings_are_independent(db):
    record_evidence_revision(db, "citius:a", "geo", {"lat": 1})
    record_evidence_revision(db, "citius:b", "geo", {"lat": 1})
    rev_a = record_evidence_revision(db, "citius:a", "geo", {"lat": 2})
    rev_b = record_evidence_revision(db, "citius:b", "geo", {"lat": 1})
    assert rev_a == 2
    assert rev_b == 1


# ─── get_evidence_revisions ───────────────────────────────────────────────────

def test_get_revisions_returns_all_keys(db):
    record_evidence_revision(db, "x:1", "photo_check", {"a": 1})
    record_evidence_revision(db, "x:1", "climate", {"b": 2})
    revs = get_evidence_revisions(db, "x:1")
    assert revs == {"photo_check": 1, "climate": 1}


def test_get_revisions_empty_for_unknown_listing(db):
    assert get_evidence_revisions(db, "unknown:0") == {}


def test_get_revisions_reflects_bumped_rev(db):
    record_evidence_revision(db, "x:1", "geo", {"lat": 1})
    record_evidence_revision(db, "x:1", "geo", {"lat": 2})
    assert get_evidence_revisions(db, "x:1")["geo"] == 2


# ─── tops.looked_at() — F09 fix ──────────────────────────────────────────────

def test_looked_at_false_when_no_photo_check():
    from tops import looked_at
    assert not looked_at({"raw_json": "{}"})


def test_looked_at_false_when_shows_house_false():
    from tops import looked_at
    rj = json.dumps({"photo_check": {"condition": "unknown", "shows_house": False, "confidence": "low", "notes": "map"}})
    assert not looked_at({"raw_json": rj})


def test_looked_at_false_when_shows_house_missing():
    from tops import looked_at
    # Old-format photo_check without shows_house key
    rj = json.dumps({"photo_check": {"condition": "good", "confidence": "high", "notes": "fine"}})
    assert not looked_at({"raw_json": rj})


def test_looked_at_true_when_shows_house_true():
    from tops import looked_at
    rj = json.dumps({"photo_check": {"condition": "good", "shows_house": True, "confidence": "high", "notes": "fine"}})
    assert looked_at({"raw_json": rj})


@pytest.mark.parametrize("confidence", ["low", None])
def test_looked_at_false_when_shows_house_true_but_not_confident(confidence):
    from tops import looked_at
    pc = {"condition": "good", "shows_house": True, "notes": "blurry"}
    if confidence:
        pc["confidence"] = confidence
    assert not looked_at({"raw_json": json.dumps({"photo_check": pc})})


# ─── needs_photo_check() — F09 re-queue ──────────────────────────────────────

def _make_photo_item(shows_house, has_photos=True):
    """Helper: a home item with controlled photo_check state."""
    pc = {"condition": "unknown", "confidence": "low", "notes": "map", "shows_house": shows_house}
    raw = {"photo_check": pc}
    if has_photos:
        raw["fotos"] = ["https://x.com/1.jpg"]
    return {
        "kind": "home",
        "raw_json": json.dumps(raw),
        "image_url": "https://x.com/cover.jpg" if has_photos else None,
    }


def test_needs_photo_check_false_when_shows_house_true():
    from photos import needs_photo_check
    assert not needs_photo_check(_make_photo_item(shows_house=True))


def test_needs_photo_check_true_when_shows_house_false():
    """shows_house=False means photos showed no building; re-queue if new photos arrive."""
    from photos import needs_photo_check
    # shows_house=False but has photos → needs re-check
    assert needs_photo_check(_make_photo_item(shows_house=False))


def test_needs_photo_check_false_when_no_photos_and_shows_house_false():
    """shows_house=False and no photos → nothing to check."""
    from photos import needs_photo_check
    assert not needs_photo_check(_make_photo_item(shows_house=False, has_photos=False))


def test_needs_photo_check_false_for_non_home():
    from photos import needs_photo_check
    item = {"kind": "land", "raw_json": "{}", "image_url": "https://x.com/cover.jpg"}
    assert not needs_photo_check(item)


# ─── evaluator evidence_revs in provenance — F16 ─────────────────────────────

def test_evaluator_includes_evidence_revs_in_provenance():
    from evaluator import DEFAULT_POLICY, evaluate
    from tests.test_evaluator import FULL_EVIDENCE

    ev = {**FULL_EVIDENCE, "_evidence_revs": {"photo_check": 3, "climate": 2}}
    dec = evaluate(ev, DEFAULT_POLICY)
    assert dec.provenance.get("evidence_revs") == {"photo_check": 3, "climate": 2}


def test_evaluator_evidence_revs_defaults_to_empty_dict():
    from evaluator import DEFAULT_POLICY, evaluate
    from tests.test_evaluator import FULL_EVIDENCE

    dec = evaluate(FULL_EVIDENCE, DEFAULT_POLICY)
    assert dec.provenance.get("evidence_revs") == {}
