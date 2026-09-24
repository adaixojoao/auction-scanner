import sqlite3
from datetime import datetime, timedelta, timezone

import db as dbmod
from db import (connect, load_listings, mark_duplicates, record_scrape, source_health,
                upsert_listing)
from common import make_listing

LEGACY_SCHEMA = """
CREATE TABLE listings (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
    title TEXT, description TEXT, tipo TEXT, area_m2 REAL, price REAL, current_bid REAL,
    min_price REAL, district TEXT, concelho TEXT, freguesia TEXT, url TEXT, image_url TEXT,
    date_end TEXT, raw_json TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    is_new INTEGER DEFAULT 1
);
CREATE TABLE scrape_log (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
    timestamp TEXT NOT NULL, count INTEGER, status TEXT, message TEXT);
CREATE TABLE carta_log (id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id TEXT, processo TEXT,
    tribunal TEXT, sent_date TEXT, bid_amount REAL, method TEXT DEFAULT 'email',
    outcome TEXT DEFAULT 'pending', notes TEXT, created_at TEXT NOT NULL);
INSERT INTO listings VALUES ('eleiloes:1','eleiloes','1','Moradia',NULL,'moradia',NULL,20000,NULL,
    NULL,NULL,NULL,NULL,'javascript:alert(1)',NULL,NULL,NULL,'2026-09-01','2026-09-20',0);
INSERT INTO scrape_log (source,timestamp,count,status) VALUES ('eleiloes','2026-09-20T10:00:00+00:00',5,'ok');
"""


def test_legacy_database_migrates_in_place(tmp_path):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    raw.commit()
    raw.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    assert {"country", "duplicate_of"} <= cols
    assert "country" in {r[1] for r in conn.execute("PRAGMA table_info(carta_log)")}
    # existing listings are baselined so the upgrade does not re-alert them
    assert conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1
    conn.close()
    # idempotent
    connect(path).close()


def test_legacy_bad_url_is_sanitised_on_read(tmp_path):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    raw.close()
    conn = connect(path)
    items = load_listings(conn, include_hidden=True)
    assert items[0]["url"] is None


def test_upsert_keeps_enriched_fields_and_tracks_price(db):
    row = make_listing("eleiloes", "1", title="Casa", price=30000, description="long text")
    assert upsert_listing(db, row) == "inserted"
    thin = make_listing("eleiloes", "1", title="Casa", price=24000)
    assert upsert_listing(db, thin) == "updated"
    got = db.execute("SELECT * FROM listings WHERE id='eleiloes:1'").fetchone()
    assert got["description"] == "long text" and got["price"] == 24000 and got["is_new"] == 0
    prices = [r[0] for r in db.execute("SELECT price FROM price_history ORDER BY observed_at")]
    assert prices == [30000, 24000]
    item = load_listings(db)[0]
    assert item["price_drop_pct"] == 20.0
    assert any("price cut 20%" in r for r in item["reasons"])


def test_visibility_rules(db, add):
    now = datetime.now(timezone.utc)
    add(external_id="live", title="Moradia", price=10000,
        date_end=(now + timedelta(days=5)).isoformat())
    add(external_id="gone", title="Moradia", price=10000,
        date_end=(now - timedelta(days=1)).isoformat())
    add(external_id="today", title="Moradia", price=10000, date_end=now.strftime("%Y-%m-%dT00:00:00"))
    add(external_id="usuf", title="Moradia", description="direito de usufruto", price=10000)

    filters = {"exclude_keywords": ["usufruto"]}
    visible = {it["external_id"] for it in load_listings(db, filters=filters)}
    assert visible == {"live", "today"}

    everything = {it["external_id"]: it["hidden_reason"]
                  for it in load_listings(db, filters=filters, include_hidden=True)}
    assert everything["gone"] == "expired"
    assert everything["usuf"].startswith("keyword")
    # filters never delete
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 4


def test_vacant_listing_not_hidden_by_occupancy_filter(db, add):
    add(external_id="v", title="Moradia", description="imóvel desocupado", price=10000)
    add(external_id="o", title="Moradia", description="imóvel ocupado", price=10000)
    visible = {it["external_id"] for it in load_listings(db, filters={"exclude_keywords": ["ocupado"]})}
    assert visible == {"v"}


def test_min_score_filter(db, add):
    add(external_id="frac", title="1/2 de prédio", price=10000)
    add(external_id="good", title="Moradia", price=10000)
    visible = {it["external_id"] for it in load_listings(db, filters={"min_score": 45})}
    assert visible == {"good"}


def test_stale_listings_hidden_after_source_moves_on(db, add):
    add(external_id="old", title="Moradia", price=10000)
    add(external_id="fresh", title="Moradia", price=10000)
    old_seen = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    db.execute("UPDATE listings SET last_seen=? WHERE external_id='old'", (old_seen,))
    db.commit()
    # No successful scrape recorded yet: nothing can be called stale.
    assert len(load_listings(db)) == 2
    record_scrape(db, "eleiloes", count=1, status="ok")
    visible = {it["external_id"] for it in load_listings(db)}
    assert visible == {"fresh"}


def test_duplicates_are_flagged_not_deleted(db, add):
    add("eleiloes", "a", title="Moradia", price=20000, area_m2=100, concelho="Guarda",
        description="rich", district="Guarda")
    add("imobancos", "b", title="Moradia", price=20200, area_m2=102, concelho="guarda")
    add("bpi", "c", title="Moradia", price=90000, area_m2=100, concelho="Guarda")
    assert mark_duplicates(db) == 1
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3
    dup = db.execute("SELECT id, duplicate_of FROM listings WHERE duplicate_of IS NOT NULL").fetchone()
    assert tuple(dup) == ("imobancos:b", "eleiloes:a")
    assert "imobancos:b" not in {it["id"] for it in load_listings(db)}
    # re-running is stable
    assert mark_duplicates(db) == 1


def test_source_health_states(db):
    record_scrape(db, "good", count=5, status="ok")
    record_scrape(db, "was_good", count=3, status="ok", timestamp="2026-01-01T00:00:00+00:00")
    record_scrape(db, "was_good", count=0, status="empty")
    record_scrape(db, "never", count=0, status="empty")
    record_scrape(db, "boom", count=0, status="error", message="ConnectionError: x")
    health = {h["source"]: h for h in source_health(db, known_sources=["unrun"])}
    assert health["good"]["state"] == "ok"
    assert health["was_good"]["state"] == "broken" and health["was_good"]["failing_runs"] == 1
    assert health["never"]["state"] == "never worked"
    assert health["boom"]["state"] == "error" and "ConnectionError" in health["boom"]["last_message"]
    assert health["unrun"]["state"] == "never run"
