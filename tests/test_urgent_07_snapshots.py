import sqlite3
from datetime import datetime, timezone

import db as store


def test_snapshots_do_not_cross_database_connections(monkeypatch):
    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    store.invalidate_snapshot()
    calls = []
    monkeypatch.setattr(store, "load_listings", lambda conn, **kw: calls.append(conn) or [{"owner": id(conn)}])
    try:
        a = store.publication_snapshot(first, ttl=60)
        b = store.publication_snapshot(second, ttl=60)
        assert a != b
        assert calls == [first, second]
    finally:
        first.close()
        second.close()
        store.invalidate_snapshot()


def test_snapshot_consumers_cannot_mutate_each_other(monkeypatch):
    conn = sqlite3.connect(":memory:")
    store.invalidate_snapshot()
    calls = []
    monkeypatch.setattr(store, "load_listings", lambda *a, **k: calls.append(1) or [{"nested": {"score": 80}}])
    try:
        first = store.publication_snapshot(conn, ttl=60)
        first[0]["nested"]["score"] = 0
        second = store.publication_snapshot(conn, ttl=60)
        assert second[0]["nested"]["score"] == 80
        assert calls == [1]
    finally:
        conn.close()
        store.invalidate_snapshot()


def test_requested_time_is_part_of_snapshot_identity(monkeypatch):
    conn = sqlite3.connect(":memory:")
    store.invalidate_snapshot()
    calls = []
    monkeypatch.setattr(store, "load_listings", lambda *a, **k: calls.append(k["now"]) or [])
    try:
        one = datetime(2026, 10, 1, tzinfo=timezone.utc)
        two = datetime(2026, 10, 2, tzinfo=timezone.utc)
        store.publication_snapshot(conn, now=one, ttl=60)
        store.publication_snapshot(conn, now=two, ttl=60)
        assert calls == [one, two]
    finally:
        conn.close()
        store.invalidate_snapshot()


def test_mode_invalidation_only_removes_selected_goal(monkeypatch):
    conn = sqlite3.connect(":memory:")
    store.invalidate_snapshot()
    calls = []
    monkeypatch.setattr(store, "load_listings", lambda *a, **k: calls.append(k["mode"]) or [])
    try:
        store.publication_snapshot(conn, mode="home", ttl=60)
        store.publication_snapshot(conn, mode="land", ttl=60)
        store.invalidate_snapshot("home")
        store.publication_snapshot(conn, mode="home", ttl=60)
        store.publication_snapshot(conn, mode="land", ttl=60)
        assert calls == ["home", "land", "home"]
    finally:
        conn.close()
        store.invalidate_snapshot()
