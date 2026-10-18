from datetime import datetime, timezone
import pytest
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
