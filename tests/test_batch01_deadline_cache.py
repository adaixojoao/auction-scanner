"""Batch 01: deadline overlays, build returns, and production-cache API reads.

Synthetic records only. HTTP reads use Flask's in-process client. Existing
conftest fixtures isolate files/databases and block outbound network calls.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import time
from types import SimpleNamespace

import pytest

import dashboard
from common import effective_end
from db import research_selection


UTC = timezone.utc
BASE = datetime(2035, 6, 10, 12, 0, tzinfo=UTC)


def record(identity="a", deadline=None, reason=None, status=None):
    return {
        "id": f"synthetic:{identity}", "source": "synthetic", "country": "PT",
        "title": f"Synthetic home {identity}", "description": "Synthetic data",
        "tipo": "moradia", "category": "imoveis", "kind": "home",
        "price": 10000, "current_bid": None, "date_end": deadline,
        "status": status, "hidden_reason": reason, "score": 80, "rank": 90,
        "is_recent": False, "reasons": ["Synthetic reason"],
        "invest_score": 80, "live_score": 80,
    }


def kept(rows, built_at=BASE, policy="test-policy", tag="test-scan"):
    ends, positions = dashboard._expiry_index(rows, built_at)
    return dashboard._Kept(time.monotonic(), rows, tag, policy, ends, positions)


@pytest.fixture(autouse=True)
def private_caches(monkeypatch):
    for name in ("_LISTS", "_LISTS_USED", "_BUILD_LOCKS"):
        monkeypatch.setattr(dashboard, name, {})
    for name in ("_LISTS_ACTIVE", "_LISTS_BUILDING"):
        monkeypatch.setattr(dashboard, name, set())


@pytest.mark.parametrize("offset,expected", [(-1, None), (0, "expired"), (1, "expired")])
def test_exact_deadline_boundary(offset, expected):
    end = BASE + timedelta(minutes=1)
    rows = [record(deadline=end.isoformat())]
    original = deepcopy(rows)
    entry = kept(rows)
    result = dashboard._with_current_expiry(entry, end + timedelta(seconds=offset))
    assert result[0]["hidden_reason"] == expected
    assert rows == original
    assert result[0]["score"] == original[0]["score"]
    assert result[0]["reasons"] == original[0]["reasons"]


@pytest.mark.parametrize("reason,status", [
    ("dismissed: other", "dismissed"), (None, "dismissed"),
    ("duplicate of synthetic:other", None), ("score 0 < filters.min_score 1", None),
    ("cash over budget", None), ("keyword 'test'", None),
    (None, "shortlisted"),
])
def test_precedence_and_copy_on_change(reason, status):
    end = BASE + timedelta(minutes=1)
    rows = [record(deadline=end.isoformat(), reason=reason, status=status)]
    entry = kept(rows)
    result = dashboard._with_current_expiry(entry, end)
    if status == "dismissed":
        assert result is rows
        assert result[0]["hidden_reason"] == reason
    else:
        assert result is not rows
        assert result[0] is not rows[0]
        assert result[0]["hidden_reason"] == "expired"
    assert rows[0]["hidden_reason"] == reason


@pytest.mark.parametrize("deadline", [None, "", "not a date", "9999-12-31", "9999-12-31T12:00:00Z"])
def test_unknown_deadline_policy_is_preserved(deadline):
    rows = [record(deadline=deadline)]
    entry = kept(rows)
    assert dashboard._with_current_expiry(entry, BASE + timedelta(days=2)) is rows


@pytest.mark.parametrize("deadline", ["2035-06-10", "2035-06-10T00:00:00"])
def test_date_only_and_legacy_midnight_reuse_effective_end(deadline):
    end = effective_end(deadline)
    assert end is not None
    rows = [record(deadline=deadline)]
    entry = kept(rows, end - timedelta(hours=1))
    assert dashboard._with_current_expiry(entry, end - timedelta(microseconds=1))[0]["hidden_reason"] is None
    assert dashboard._with_current_expiry(entry, end)[0]["hidden_reason"] == "expired"


@pytest.mark.parametrize("deadline", [
    "2035-06-11T00:00:00Z", "2035-06-11T00:00:00+02:00",
    "2035-06-10T12:01:00", "2035-06-10T13:01:00+01:00",
])
def test_exact_and_legacy_naive_instants_follow_existing_parser(deadline):
    end = effective_end(deadline)
    assert end is not None
    rows = [record(deadline=deadline)]
    entry = kept(rows, end - timedelta(minutes=1))
    assert dashboard._with_current_expiry(entry, end)[0]["hidden_reason"] == "expired"


def test_expired_candidate_is_removed_from_research():
    end = BASE + timedelta(minutes=1)
    rows = [record(deadline=end.isoformat())]
    assert research_selection(rows)[0]
    effective = dashboard._with_current_expiry(kept(rows), end)
    assert research_selection(effective) == ([], [])
    assert rows[0]["hidden_reason"] is None


def test_index_sort_and_unaffected_record_identity():
    late = BASE + timedelta(minutes=2)
    early = BASE + timedelta(minutes=1)
    rows = [record("late", late.isoformat()), record("early", early.isoformat()), record("undated")]
    entry = kept(rows)
    assert entry.at == [1, 0]
    effective = dashboard._with_current_expiry(entry, early)
    assert effective[0] is rows[0]
    assert effective[1]["hidden_reason"] == "expired"
    assert effective[2] is rows[2]


def test_request_local_clock_does_not_leak_to_another_request():
    from flask import g
    with dashboard.app.test_request_context("/api/listings"):
        g.listings_now = BASE
        assert dashboard._now() == BASE
    with dashboard.app.test_request_context("/api/listings"):
        before = datetime.now(UTC)
        actual = dashboard._now()
        after = datetime.now(UTC)
        assert before <= actual <= after


@pytest.mark.parametrize("changing_policy", [False, True])
def test_build_return_is_overlaid_even_if_build_crosses_deadline(monkeypatch, changing_policy):
    clock = {"now": BASE}
    end = BASE + timedelta(seconds=1)
    calls = []
    config_calls = {"n": 0}
    rows = [record(deadline=end.isoformat())]

    def config():
        config_calls["n"] += 1
        return {"filters": {}, "marker": config_calls["n"] if changing_policy else 0}

    def loader(conn, **kwargs):
        calls.append(kwargs["now"])
        # Every attempt begins with a future deadline; it passes while scoring.
        rows[0]["date_end"] = (kwargs["now"] + timedelta(seconds=1)).isoformat()
        clock["now"] = kwargs["now"] + timedelta(seconds=2)
        return deepcopy(rows)

    monkeypatch.setattr(dashboard, "_now", lambda: clock["now"])
    monkeypatch.setattr(dashboard, "_config", config)
    monkeypatch.setattr(dashboard, "_policy_fingerprint", lambda cfg: str(cfg["marker"]))
    monkeypatch.setattr(dashboard, "get_db", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dashboard, "load_listings", loader)
    monkeypatch.setattr(dashboard, "keep_scores_for", lambda modes: None)
    monkeypatch.setattr(dashboard, "_scan_finished_at", lambda: "test-scan")
    if changing_policy:
        # Batch 02: a list whose settings never held still is refused, not shown.
        with pytest.raises(dashboard.ListPolicyChanged):
            dashboard._build_list("home")
        assert "home" not in dashboard._LISTS
    else:
        result = dashboard._build_list("home")
        assert result[0]["hidden_reason"] == "expired"
        assert dashboard._LISTS["home"].items[0]["hidden_reason"] is None
    assert len(calls) == (3 if changing_policy else 1)
    assert not dashboard._LISTS_ACTIVE
    assert not dashboard._LISTS_BUILDING


@pytest.fixture
def cached_api(db, monkeypatch):
    cfg = {
        "max_price": 100000, "invest_max_price": 100000,
        "land_max_price": 100000, "check_max_price": 100000,
        "filters": {}, "cash_on_hand": 1000000, "max_listings": 3,
        "dashboard": {"host": "127.0.0.1", "port": 8050},
    }
    clock = {"now": BASE}
    monkeypatch.setitem(dashboard.app.config, "TESTING", False)
    monkeypatch.setattr(dashboard, "_config", lambda: cfg)
    monkeypatch.setattr(dashboard, "_now", lambda: clock["now"])
    monkeypatch.setattr(dashboard, "_scan_busy", lambda: False)
    monkeypatch.setattr(dashboard, "_scan_finished_at", lambda: "test-scan")
    monkeypatch.setattr(dashboard, "_rebuild_soon", lambda mode: pytest.fail("Unexpected rebuild"))
    monkeypatch.setattr(dashboard, "load_listings", lambda *a, **kw: pytest.fail("Unexpected loader call"))
    monkeypatch.setattr(dashboard, "source_health", lambda *a, **kw: [])
    monkeypatch.setattr(dashboard, "_registry", lambda: {})
    monkeypatch.setattr(dashboard, "unchecked_over_cap", lambda conn: (0, 100000))
    # Isolate membership/count tests from unrelated presentation calculations.
    monkeypatch.setattr(dashboard, "_presented", lambda conn, rows: [dict(row) for row in rows])
    client = dashboard.app.test_client()

    def install(rows, mode="home"):
        dashboard._LISTS[mode] = kept(rows, policy=dashboard._policy_fingerprint(cfg))

    def get(**query):
        response = client.get(
            "/api/listings", query_string={"mode": "home", "sort": "price", "dir": "asc", **query},
            base_url="http://127.0.0.1:8050",
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        return response.get_json()

    return SimpleNamespace(clock=clock, install=install, get=get, client=client, cfg=cfg)


def test_production_cached_api_counts_pages_and_hidden_state(cached_api):
    end = BASE + timedelta(minutes=1)
    rows = [record(str(i), end.isoformat() if i < 3 else None) for i in range(4)]
    cached_api.install(rows)
    before = cached_api.get(per_page=2, page=2)
    assert (before["matched_total"], before["display_total"], before["effective_page"]) == (4, 3, 2)
    cached_api.clock["now"] = end
    after = cached_api.get(per_page=2, page=2)
    assert (after["matched_total"], after["display_total"], after["effective_page"]) == (1, 1, 1)
    assert [row["id"] for row in after["items"]] == ["synthetic:3"]
    assert after["stats"]["hidden"] == {"expired": 3}
    hidden = cached_api.get(show_hidden=1, per_page=10)
    assert hidden["matched_total"] == 3
    assert all(row["hidden_reason"] == "expired" for row in hidden["items"])
    assert all(row["hidden_reason"] is None for row in rows)


def test_production_api_empty_results_have_page_one(cached_api):
    end = BASE + timedelta(minutes=1)
    cached_api.install([record(deadline=end.isoformat())])
    cached_api.clock["now"] = end
    result = cached_api.get(page=99)
    assert (result["matched_total"], result["display_total"], result["effective_page"], result["items"]) == (0, 0, 1, [])


def test_production_api_dismissal_precedence(cached_api):
    end = BASE + timedelta(minutes=1)
    cached_api.install([record(deadline=end.isoformat(), reason="dismissed: other", status="dismissed")])
    cached_api.clock["now"] = end
    result = cached_api.get(show_hidden=1)
    assert result["items"][0]["hidden_reason"] == "dismissed: other"
    assert result["stats"]["hidden"] == {"dismissed": 1}


def test_production_api_research_excludes_newly_expired_rows(cached_api):
    end = BASE + timedelta(minutes=1)
    cached_api.install([record(deadline=end.isoformat())], mode="both")
    cached_api.clock["now"] = end
    result = cached_api.get(mode="both")
    assert result["research"]["valued"] == []
    assert result["research"]["unvalued"] == []


def test_production_api_guard_still_refuses_foreign_host(cached_api):
    response = cached_api.client.get("/api/listings", base_url="http://evil.example:8050")
    assert response.status_code == 403


def test_slim_hydration_uses_captured_clock(monkeypatch):
    from flask import g
    calls = []

    def loader(conn, **kwargs):
        calls.append(kwargs)
        return [{"id": "synthetic:a", "hidden_reason": "expired"}]

    monkeypatch.setattr(dashboard, "_config", lambda: {"filters": {}})
    monkeypatch.setattr(dashboard, "load_listings", loader)
    with dashboard.app.test_request_context("/api/listings"):
        g.listings_now = BASE
        result = dashboard._full_rows(None, [{"id": "synthetic:a", "_slim": True}], "home")
    assert calls[0]["now"] == BASE
    assert calls[0]["params"] == ("synthetic:a",)
    assert result[0]["hidden_reason"] == "expired"
