"""Tests for Milestone E — typed availability states, scan scope, sale-state (F12-F14)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from confirm import (
    AVAILABILITY_STATES,
    SALE_STATES,
    availability_state,
    classify_response,
    gone_reason,
)
from db import init_db, last_ok_by_source, record_scrape


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ─── classify_response — F12 typed availability states ───────────────────────

def test_404_is_withdrawn():
    state, why = classify_response(404, "")
    assert state == "withdrawn"
    assert why


def test_410_is_withdrawn():
    state, why = classify_response(410, "")
    assert state == "withdrawn"


def test_403_is_blocked():
    state, why = classify_response(403, "some content here")
    assert state == "blocked"


def test_500_is_error():
    state, why = classify_response(500, "")
    assert state == "error"


def test_network_failure_is_error():
    state, why = classify_response(0, "")
    assert state == "error"


def test_clean_page_is_available():
    html = "<html><head><title>Moradia T3</title></head><body>" + "text " * 100 + "</body></html>"
    state, why = classify_response(200, html)
    assert state == "available"
    assert why is None


def test_sold_word_in_title_is_withdrawn():
    html = "<html><head><title>Vendido — casa rural</title></head><body>" + "x " * 100 + "</body></html>"
    state, why = classify_response(200, html)
    assert state == "withdrawn"
    assert "vendido" in (why or "").lower()


def test_reserved_word_in_title_is_reserved():
    html = "<html><head><title>Reservado — piso centro</title></head><body>" + "x " * 100 + "</body></html>"
    state, why = classify_response(200, html)
    assert state == "reserved"


def test_thin_body_is_unknown():
    state, why = classify_response(200, "<html><body>short</body></html>")
    assert state == "unknown"


def test_all_availability_states_are_valid_strings():
    assert all(isinstance(s, str) for s in AVAILABILITY_STATES)
    assert "available" in AVAILABILITY_STATES
    assert "withdrawn" in AVAILABILITY_STATES
    assert "reserved" in AVAILABILITY_STATES
    assert "blocked" in AVAILABILITY_STATES
    assert "error" in AVAILABILITY_STATES
    assert "unknown" in AVAILABILITY_STATES


# ─── gone_reason / availability_state — legacy compat ────────────────────────

def _item_with_check(availability=None, gone=None, why=None):
    check = {"at": "2026-01-01T00:00:00"}
    if availability is not None:
        check["availability"] = availability
    if gone is not None:
        check["gone"] = gone
    if why is not None:
        check["why"] = why
    return {"raw_json": json.dumps({"link_check": check})}


def test_gone_reason_returns_none_when_available():
    assert gone_reason(_item_with_check(availability="available")) is None


def test_gone_reason_returns_why_for_withdrawn():
    item = _item_with_check(availability="withdrawn", why="page gone")
    assert gone_reason(item) == "page gone"


def test_gone_reason_returns_state_for_reserved():
    item = _item_with_check(availability="reserved", why="reservado")
    assert gone_reason(item) == "reservado"


def test_gone_reason_reads_legacy_gone_true():
    item = _item_with_check(gone=True, why="vendido")
    assert gone_reason(item) == "vendido"


def test_gone_reason_returns_none_for_legacy_gone_false():
    assert gone_reason(_item_with_check(gone=False)) is None


def test_availability_state_returns_unknown_when_not_checked():
    assert availability_state({"raw_json": "{}"}) == "unknown"


def test_availability_state_returns_typed_value():
    item = _item_with_check(availability="blocked")
    assert availability_state(item) == "blocked"


def test_availability_state_maps_legacy_gone_true_to_withdrawn():
    item = _item_with_check(gone=True, why="page gone")
    assert availability_state(item) == "withdrawn"


# ─── scan scope in scrape_log — F13 ──────────────────────────────────────────

def test_full_scope_run_advances_last_ok(db):
    record_scrape(db, "citius", count=10, status="ok", scope="full")
    assert "citius" in last_ok_by_source(db)


def test_partial_scope_run_does_not_advance_last_ok(db):
    record_scrape(db, "citius", count=5, status="ok", scope="partial")
    assert "citius" not in last_ok_by_source(db)


def test_full_run_after_partial_advances_last_ok(db):
    record_scrape(db, "citius", count=5, status="ok", scope="partial")
    record_scrape(db, "citius", count=20, status="ok", scope="full")
    assert "citius" in last_ok_by_source(db)


def test_null_scope_treated_as_full_for_compat(db):
    """Old rows without scope column behave as before (treated as full)."""
    record_scrape(db, "leilosoc", count=15, status="ok")  # scope=None
    assert "leilosoc" in last_ok_by_source(db)


def test_pages_seen_is_stored(db):
    record_scrape(db, "citius", count=10, status="ok", scope="full", pages_seen=4)
    row = db.execute("SELECT pages_seen, scope FROM scrape_log WHERE source='citius'").fetchone()
    assert row["pages_seen"] == 4
    assert row["scope"] == "full"


# ─── sale_state constants — F14 ──────────────────────────────────────────────

def test_all_sale_states_are_valid_strings():
    assert "available" in SALE_STATES
    assert "observed_bid" in SALE_STATES
    assert "confirmed_bid" in SALE_STATES
    assert "accepted_award" in SALE_STATES
    assert "completed_purchase" in SALE_STATES
    assert "withdrawn" in SALE_STATES
