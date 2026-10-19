"""Batch 02: synthetic regressions for cache policy checks and build publication."""
from copy import deepcopy
from datetime import datetime, timezone
import time
from types import SimpleNamespace

import pytest

import dashboard


NOW = datetime(2035, 6, 10, 12, tzinfo=timezone.utc)


def item(identity="a"):
    return {"id": f"synthetic:{identity}", "date_end": None, "status": None, "hidden_reason": None,
            "score": 80, "rank": 90}


def entry(rows, cfg, age=0):
    return dashboard._Kept(time.monotonic() - age, rows, "test-scan", dashboard._policy_fingerprint(cfg), [], [])


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch):
    for key in ("_LISTS", "_LISTS_USED", "_BUILD_LOCKS"):
        monkeypatch.setattr(dashboard, key, {})
    for key in ("_LISTS_ACTIVE", "_LISTS_BUILDING"):
        monkeypatch.setattr(dashboard, key, set())


@pytest.mark.parametrize("section,value", [
    ("filters", {"exclude_keywords": ["synthetic"]}),
    ("filters", {"weights": {"home": {"price": 1.5}}}),
    ("max_price", 25000), ("cash_on_hand", 25000),
    ("check_max_price", 25000),
])
def test_policy_change_does_not_serve_old_retained_list(monkeypatch, section, value):
    cfg = {"filters": {}, "max_price": 100000, "cash_on_hand": 100000}
    dashboard._LISTS["home"] = entry([item("old")], cfg)
    changed = {**cfg, section: value}
    calls = []
    monkeypatch.setitem(dashboard.app.config, "TESTING", False)
    monkeypatch.setattr(dashboard, "_config", lambda: changed)
    monkeypatch.setattr(dashboard, "_scan_busy", lambda: False)
    monkeypatch.setattr(dashboard, "_build_list", lambda mode, tag=None: calls.append(mode) or [item("new")])
    result = dashboard.all_listings("home")
    assert result[0]["id"] == "synthetic:new"
    assert calls == ["home"]


def test_unrelated_secret_update_keeps_retained_scoring(monkeypatch):
    cfg = {"filters": {}, "notifications": {"smtp_password": "test-only-old"}}
    rows = [item()]
    dashboard._LISTS["home"] = entry(rows, cfg)
    changed = {**cfg, "notifications": {"smtp_password": "test-only-new"}}
    monkeypatch.setitem(dashboard.app.config, "TESTING", False)
    monkeypatch.setattr(dashboard, "_config", lambda: changed)
    monkeypatch.setattr(dashboard, "_scan_busy", lambda: False)
    monkeypatch.setattr(dashboard, "_scan_finished_at", lambda: "test-scan")
    monkeypatch.setattr(dashboard, "_build_list", lambda *a, **kw: pytest.fail("Unexpected scoring rebuild"))
    assert dashboard.all_listings("home") is rows


def test_continually_changing_policy_is_not_published_or_returned(monkeypatch):
    counter = {"n": 0}
    calls = []
    old = entry([item("old")], {"filters": {}})
    dashboard._LISTS["home"] = old

    def config():
        counter["n"] += 1
        return {"filters": {"min_score": counter["n"]}}

    monkeypatch.setattr(dashboard, "_config", config)
    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    monkeypatch.setattr(dashboard, "get_db", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dashboard, "keep_scores_for", lambda modes: None)
    monkeypatch.setattr(dashboard, "load_listings", lambda conn, **kw: calls.append(kw) or [item("candidate")])
    with pytest.raises(dashboard.ListPolicyChanged):
        dashboard._build_list("home")
    assert len(calls) == 3
    assert dashboard._LISTS["home"] is old
    assert not dashboard._LISTS_ACTIVE
    assert not dashboard._LISTS_BUILDING


def test_policy_changed_during_indexing_is_rejected(monkeypatch):
    cfg = {"filters": {"min_score": 1}}
    calls = []
    original_index = dashboard._expiry_index

    def index(rows, built_at):
        cfg["filters"]["min_score"] += 1
        return original_index(rows, built_at)

    monkeypatch.setattr(dashboard, "_config", lambda: deepcopy(cfg))
    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    monkeypatch.setattr(dashboard, "get_db", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dashboard, "keep_scores_for", lambda modes: None)
    monkeypatch.setattr(dashboard, "load_listings", lambda conn, **kw: calls.append(kw) or [item()])
    monkeypatch.setattr(dashboard, "_scan_finished_at", lambda: "test-scan")
    monkeypatch.setattr(dashboard, "_expiry_index", index)
    with pytest.raises(dashboard.ListPolicyChanged):
        dashboard._build_list("home")
    assert len(calls) == 3
    assert "home" not in dashboard._LISTS


def test_stable_build_publishes_once(monkeypatch):
    cfg = {"filters": {}}
    calls = []
    monkeypatch.setattr(dashboard, "_config", lambda: deepcopy(cfg))
    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    monkeypatch.setattr(dashboard, "get_db", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dashboard, "keep_scores_for", lambda modes: None)
    monkeypatch.setattr(dashboard, "load_listings", lambda conn, **kw: calls.append(kw) or [item()])
    monkeypatch.setattr(dashboard, "_scan_finished_at", lambda: "test-scan")
    result = dashboard._build_list("home")
    assert result[0]["id"] == "synthetic:a"
    assert len(calls) == 1
    assert dashboard._LISTS["home"].policy == dashboard._policy_fingerprint(cfg)
    assert not dashboard._LISTS_ACTIVE
    assert not dashboard._LISTS_BUILDING


def test_api_exposes_retryable_failure_without_success_counts(db, monkeypatch):
    monkeypatch.setitem(dashboard.app.config, "TESTING", False)
    monkeypatch.setattr(dashboard, "_config", lambda: {
        "filters": {}, "max_price": 100000, "dashboard": {"host": "127.0.0.1", "port": 8050}})

    def fail(mode):
        raise dashboard.ListPolicyChanged("Settings changed repeatedly. Retry.")

    monkeypatch.setattr(dashboard, "all_listings", fail)
    response = dashboard.app.test_client().get("/api/listings?mode=home", base_url="http://127.0.0.1:8050")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["code"] == "list_policy_changed"
    assert payload["retryable"] is True
    assert "items" not in payload and "matched_total" not in payload
    assert response.headers["Retry-After"] == "1"
    assert response.headers["Cache-Control"] == "no-store"
