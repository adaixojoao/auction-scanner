import json
from datetime import datetime, timezone

import pytest

import db as store
import evaluator as ev


NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


def check(checked="2030-01-01T11:00:00+00:00", expiry="2030-01-01T13:00:00+00:00", status="available"):
    return ev._req_sale_availability({
        "sale_availability": status, "sale_availability_checked_at": checked,
        "sale_availability_expires_at": expiry, "_evaluation_now": NOW,
    }, {})


def test_current_explicit_window_passes():
    r = check()
    assert r.outcome == ev.PASS
    assert r.expires_at == datetime(2030, 1, 1, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize("expiry", [None, "bad", "2030-01-01T13:00:00", "2030-01-01T10:00:00+00:00"])
def test_missing_or_invalid_expiry_cannot_confirm_available(expiry):
    assert check(expiry=expiry).outcome == ev.UNKNOWN


def test_at_expiry_is_already_unresolved():
    assert check(expiry="2030-01-01T12:00:00+00:00").code == "DATA_AVAILABILITY_EXPIRED"


def test_future_check_is_not_current_evidence():
    assert check(checked="2030-01-01T12:30:00+00:00").outcome == ev.UNKNOWN


@pytest.mark.parametrize("status", ["withdrawn", "ended", "reserved"])
def test_adverse_state_is_not_rescued_by_timing(status):
    assert check(checked=None, expiry=None, status=status).outcome == ev.FAIL


def test_failed_check_stays_unknown():
    assert check(status="blocked").outcome == ev.UNKNOWN


def test_public_evaluator_uses_its_actual_now_without_mutating_input():
    evidence = {
        "sale_availability": "available",
        "sale_availability_checked_at": "2030-01-01T11:00:00+00:00",
        "sale_availability_expires_at": "2030-01-01T13:00:00+00:00",
        "_evaluation_now": "1900-01-01T00:00:00+00:00",
    }
    before = dict(evidence)
    decision = ev.evaluate(evidence, now=NOW)
    r = next(r for r in decision.requirements if r.key == "sale_availability")
    assert r.outcome == ev.PASS
    assert evidence == before


# ─── The scan's link check as availability evidence, and stored-decision expiry ───

def _checked(state="available", at="2030-01-01T11:00:00+00:00", **item):
    return {"id": "x:1", "raw_json": json.dumps({"link_check": {"availability": state, "at": at}}), **item}


def test_link_check_gives_state_and_check_time_but_no_expiry_without_a_window():
    got = store._availability_evidence(_checked())
    assert got == {"sale_availability": "available",
                   "sale_availability_checked_at": "2030-01-01T11:00:00+00:00",
                   "sale_availability_expires_at": None}
    ev_ = {**got, "_evaluation_now": NOW}
    assert ev._req_sale_availability(ev_, {}).code == "DATA_AVAILABILITY_TIMING_INVALID"


def test_the_owners_window_sets_the_expiry():
    got = store._availability_evidence(_checked(), valid_hours=2)
    assert got["sale_availability_expires_at"] == "2030-01-01T13:00:00+00:00"
    assert ev._req_sale_availability({**got, "_evaluation_now": NOW}, {}).outcome == ev.PASS
    late = datetime(2030, 1, 1, 14, tzinfo=timezone.utc)
    assert ev._req_sale_availability({**got, "_evaluation_now": late}, {}).code == "DATA_AVAILABILITY_EXPIRED"


@pytest.mark.parametrize("hours", [0, -1, float("nan"), float("inf"), True, "24"])
def test_an_unset_or_invalid_window_sets_no_expiry(hours):
    assert store._availability_evidence(_checked(), valid_hours=hours)["sale_availability_expires_at"] is None


def test_a_sold_page_fails_without_any_window():
    got = store._availability_evidence(_checked("withdrawn"))
    assert ev._req_sale_availability({**got, "_evaluation_now": NOW}, {}).outcome == ev.FAIL


@pytest.mark.parametrize("at", [None, "bad", "2030-01-01T11:00:00"])
def test_a_check_without_a_usable_time_gives_no_times(at):
    got = store._availability_evidence(_checked(at=at), valid_hours=24)
    assert got["sale_availability"] == "available"
    assert got["sale_availability_checked_at"] is None and got["sale_availability_expires_at"] is None


def test_explicit_item_fields_win_and_no_check_changes_nothing():
    explicit = _checked(sale_availability="reserved")
    assert store._availability_evidence(explicit, 24)["sale_availability"] == "reserved"
    assert store._availability_evidence({"id": "x:2", "raw_json": "{}"}, 24)["sale_availability"] is None


def _store_decision(db, expires_at):
    req = ev.Requirement(key="sale_availability", outcome=ev.PASS, code="PROC_AVAILABILITY_OK",
                         message="ok", expires_at=expires_at)
    decision = ev.Decision(state="research", requirements=[req],
                           provenance={"evaluated_at": "2030-01-01T11:00:00+00:00"})
    store._persist_decision(db, "x:1", decision, 0)


def test_a_stored_decision_is_stale_once_its_evidence_expires(db):
    _store_decision(db, datetime(2030, 1, 1, 13, tzinfo=timezone.utc))
    fresh = store.load_decision(db, "x:1", now=NOW)
    assert fresh is not None and fresh["expired"] is False
    assert fresh["requirements"][0]["expires_at"] == "2030-01-01T13:00:00+00:00"
    at_expiry = datetime(2030, 1, 1, 13, tzinfo=timezone.utc)
    assert store.load_decision(db, "x:1", now=at_expiry) is None
    audit = store.load_decision(db, "x:1", now=at_expiry, allow_stale=True)
    assert audit["stale"] is True and audit["expired"] is True


def test_a_decision_without_expiry_is_unaffected(db):
    _store_decision(db, None)
    assert store.load_decision(db, "x:1", now=NOW)["expired"] is False


@pytest.mark.parametrize("hours,code", [(24, 200), (0, 200), (-1, 400), (True, 400), ("24", 400)])
def test_the_window_setting_is_validated(db, hours, code):
    import config
    import dashboard
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    r = dashboard.app.test_client().post("/api/settings", json={"availability_valid_hours": hours})
    assert r.status_code == code
    if code == 200:
        assert config.load_config()["availability_valid_hours"] == hours
