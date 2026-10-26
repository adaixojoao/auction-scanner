import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

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


def test_place_field_repairs_run_once_on_upgrade(tmp_path):
    """v13 rewrites the bad place values earlier scrapers left in an existing DB."""
    path = str(tmp_path / "places.db")
    conn = connect(path)
    conn.execute("PRAGMA user_version = 12")
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("servihabitat:1", "servihabitat", "ES", "1",
         "Casa en venta en C. Larga, 26, Campo De Peñaranda, El, Salamanca",
         None, "El", None),
        ("france:1", "france", "FR", "1",
         "22PlémetUne maison d'habitationde 162 m²", None, None, None),
        ("citius:1", "citius", "PT", "1", "Prédio", None, "lugar de Lage", None),
        ("fina:1", "fina", "HR", "1",
         "Nekretnina k.o. Oljasi, kč.br. 600", "Općinski sud u Zlataru", None, None),
    ]
    for lid, source, country, eid, title, district, concelho, freguesia in rows:
        conn.execute(
            "INSERT INTO listings (id, source, country, external_id, title, district, "
            "concelho, freguesia, first_seen, last_seen, is_new) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
            (lid, source, country, eid, title, district, concelho, freguesia, now, now))
    conn.commit()
    conn.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
    got = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, title, district, concelho FROM listings")}
    assert got["servihabitat:1"]["concelho"] == "El Campo De Peñaranda"
    assert got["france:1"]["concelho"] == "Plémet"
    assert got["france:1"]["title"].startswith("Une maison")
    assert got["citius:1"]["concelho"] is None
    assert got["fina:1"]["district"] is None
    assert got["fina:1"]["concelho"] == "Oljasi"
    conn.close()


def test_uf_parish_labels_are_rewritten_on_upgrade(tmp_path):
    path = str(tmp_path / "uf.db")
    conn = connect(path)
    conn.execute("PRAGMA user_version = 14")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO listings (id, source, country, external_id, title, freguesia, "
        "concelho, first_seen, last_seen, is_new) VALUES (?,?,?,?,?,?,?,?,?,0)",
        ("whitestar:1", "whitestar", "PT", "1", "Moradia",
         "U.F. BEJA (SALVADOR E SANTA MARIA DA FEIRA)", "Beja", now, now))
    conn.commit()
    conn.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
    assert conn.execute("SELECT freguesia FROM listings WHERE id='whitestar:1'").fetchone()[0] \
        == "Salvador E Santa Maria Da Feira"
    conn.close()


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


def test_only_scoring_files_invalidate_kept_scores():
    from db import _SCORE_CODE
    assert "scoring.py" in _SCORE_CODE and "costs.py" in _SCORE_CODE
    assert "dashboard.py" not in _SCORE_CODE and "app.py" not in _SCORE_CODE


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


def test_investment_mentions_homes_still_listed_in_the_district(db, add):
    for i in range(9):
        add("citius", f"supply{i}", title="Moradia T2", tipo="moradia", district="Guarda",
            concelho="Guarda", area_m2=90, price=40000, description="Em bom estado")
    rows = load_listings(db, filters={"min_score": 0}, mode="invest")
    assert len(rows) == 9
    assert all(any("8 other homes still listed" in r for r in it["reasons"]) for it in rows)


def test_occupancy_exclude_does_not_hide_an_investment_home(db, add):
    """A sitting tenant is income on Investment home, not a reason to hide the row."""
    add(external_id="o", title="Moradia T2", description="imóvel ocupado, arrendado a inquilino",
        price=20000, area_m2=80)
    filters = {"exclude_keywords": ["ocupado", "arrendado", "inquilino", "usufruto"]}
    home = {it["external_id"] for it in load_listings(db, filters=filters, mode="home")}
    invest = {it["external_id"]: it for it in load_listings(db, filters=filters, mode="invest")}
    assert "o" not in home
    assert "o" in invest
    assert any("tenant" in r.lower() or "occupied" in r.lower() for r in invest["o"]["reasons"])


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


def test_a_stale_listing_whose_page_still_opens_stays_unconfirmed(db, add):
    """2026-10-26: off its source's list, but the page opened afterwards did not
    say sold or deleted, so it stays shown and is said to be unconfirmed. A
    page that says sold hides it for that."""
    import json
    add(external_id="up", title="Moradia", price=10000)
    add(external_id="sold", title="Moradia", price=10000)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    later = (old + timedelta(days=5)).isoformat()
    db.execute("UPDATE listings SET last_seen=?", (old.isoformat(),))
    for ext, state in (("up", "unknown"), ("sold", "withdrawn")):
        db.execute("UPDATE listings SET raw_json=? WHERE external_id=?",
                   (json.dumps({"link_check": {"availability": state, "at": later,
                                               "gone": state == "withdrawn", "why": "vendido"}}), ext))
    db.commit()
    record_scrape(db, "eleiloes", count=1, status="ok")
    shown = {it["external_id"]: it for it in load_listings(db, include_hidden=True)}
    assert shown["up"]["hidden_reason"] is None and shown["up"]["unconfirmed_since"]
    assert shown["sold"]["hidden_reason"].startswith("gone")


def test_duplicates_are_flagged_not_deleted(db, add):
    add("eleiloes", "a", title="Moradia", price=20000, area_m2=100, concelho="Guarda",
        description="rich", district="Guarda")
    add("imobancos", "b", title="Moradia", price=20200, area_m2=102, concelho="guarda")
    add("bpi", "c", title="Moradia", price=90000, area_m2=100, concelho="Guarda")
    # speculative price+area match → possible_match (not suppressive duplicate_of)
    assert mark_duplicates(db) == 0
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3
    assert db.execute("SELECT duplicate_of FROM listings WHERE duplicate_of IS NOT NULL").fetchone() is None
    pm = db.execute("SELECT possible_match FROM listings WHERE id='imobancos:b'").fetchone()["possible_match"]
    assert pm == "eleiloes:a"
    assert "imobancos:b" in {it["id"] for it in load_listings(db)}
    # re-running is stable
    assert mark_duplicates(db) == 0


def test_a_portal_relisting_is_a_duplicate_but_court_twin_lots_are_not(db, add):
    add("fotocasa", "1", "ES", title="Vivienda en Ferrol", price=19000, area_m2=87, concelho="Ferrol")
    add("fotocasa", "2", "ES", title="Vivienda en Ferrol", price=19000, area_m2=87, concelho="Ferrol")
    add("citius", "x", title="Fracção A", price=19000, area_m2=87, concelho="Guarda")
    add("citius", "y", title="Fracção B", price=19000, area_m2=87, concelho="Guarda")
    # Equal price and size on one portal is similarity, not identity: a hint only.
    assert mark_duplicates(db) == 0
    assert db.execute("SELECT possible_match FROM listings WHERE id='fotocasa:2'").fetchone()[0] == "fotocasa:1"
    # With the same street address the re-post is the same house and is hidden.
    db.execute("UPDATE listings SET raw_json=? WHERE id IN ('fotocasa:1','fotocasa:2')",
               ('{"address": "Calle Real 5"}',))
    db.commit()
    assert mark_duplicates(db) == 1
    flagged = db.execute("SELECT id FROM listings WHERE duplicate_of IS NOT NULL").fetchone()[0]
    assert flagged.startswith("fotocasa:")


def test_flats_of_one_building_are_hinted_toward_the_cheapest(db, add):
    # Three floors of one Cortenova building, one court case: one choice is a hint.
    t = "Piena Proprietà. In Comune Amministrativo di CORTENOVA (LC),"
    add("pvp_giustizia", "1", "IT", title=t, price=27717, area_m2=425, concelho="Cortenova")
    add("pvp_giustizia", "2", "IT", title=t, price=27675, area_m2=425, concelho="Cortenova")
    add("pvp_giustizia", "3", "IT", title=t, price=28835, area_m2=425, concelho="Cortenova")
    add("pvp_giustizia", "4", "IT", title=t, price=60000, area_m2=425, concelho="Cortenova")
    # Same title, price and size, no unit identity: similarity only → hints.
    assert mark_duplicates(db) == 0
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 4
    hints = {r[0]: r[1] for r in db.execute("SELECT id, possible_match FROM listings")}
    assert hints["pvp_giustizia:1"] == hints["pvp_giustizia:3"] == "pvp_giustizia:2"
    assert hints["pvp_giustizia:4"] is None


def test_duplicates_deduped_when_one_side_has_no_area(db, add):
    # Citius scrapes often have no area_m2; the same property on e-leilões has one.
    # Price+área match (area treated as match when one side is None) → possible_match hint.
    add("eleiloes", "a", title="Moradia", price=20000, area_m2=100, concelho="Guarda")
    add("citius",   "b", title="Moradia", price=20000, area_m2=None, concelho="Guarda")
    assert mark_duplicates(db) == 0
    assert db.execute("SELECT duplicate_of FROM listings WHERE duplicate_of IS NOT NULL").fetchone() is None
    pm = db.execute("SELECT possible_match FROM listings WHERE id='citius:b'").fetchone()["possible_match"]
    assert pm == "eleiloes:a"


def test_a_listing_with_no_size_does_not_stop_the_cross_site_check(db, add):
    # Two portals, prices €3,000 apart: the cross-site check ran and crashed on the missing size.
    add("licitor", "a", "FR", title="Maison", price=20000, area_m2=None, concelho="Vierzon")
    add("bienici", "b", "FR", title="Maison à Vierzon", price=23000, area_m2=90, concelho="Vierzon")
    assert mark_duplicates(db) == 0
    # Two lots of one court sale, neither with a size: not twins, and no crash.
    add("licitor", "c", "FR", title="Une maison", description="Maison à vendre", price=10000, area_m2=None,
        concelho="Bourges")
    add("licitor", "d", "FR", title="Une maison", description="Maison à vendre", price=10500, area_m2=None,
        concelho="Bourges")
    assert mark_duplicates(db) == 0


def test_two_houses_in_one_village_are_not_twins(db, add):
    add("fotocasa", "a", "ES", title="Casa en Castropodame", price=22000, area_m2=158, concelho="Castropodame",
        description="Amplia casa en el centro de Calamocos para reforma integral")
    add("fotocasa", "b", "ES", title="Casa en Castropodame", price=20000, area_m2=147, concelho="Castropodame",
        description="Ubicada en una zona con encanto, esta casa adosada de dos plantas")
    assert mark_duplicates(db) == 0


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


def test_the_same_house_at_a_different_price_on_another_portal(db):
    import json
    from common import make_listing
    from db import mark_duplicates, upsert_listing
    geo_a = json.dumps({"geo": {"lat": 43.042, "lon": -9.2107, "precision": "street"}})
    geo_b = json.dumps({"geo": {"lat": 43.037, "lon": -9.2422, "precision": "street"}})        # 2.6 km off
    upsert_listing(db, make_listing("fotocasa", "1", "ES", title="Casa en Muxía", price=16950, area_m2=150,
                                    concelho="Muxía", raw_json=geo_b))
    upsert_listing(db, make_listing("pisos", "2", "ES", title="Casa rústica en Vilachán", price=15000, area_m2=150,
                                    concelho="Muxía", raw_json=geo_a))
    upsert_listing(db, make_listing("pisos", "3", "ES", title="Casa en Muxía", price=15500, area_m2=90,
                                    concelho="Muxía", raw_json=geo_a))                          # another size
    db.commit()
    # geo/phrase match is circumstantial — two different addresses can satisfy it
    # so it is a non-suppressive hint, not a confirmed duplicate
    assert mark_duplicates(db) == 0
    assert db.execute("SELECT duplicate_of FROM listings WHERE duplicate_of IS NOT NULL").fetchone() is None
    pm = db.execute("SELECT possible_match FROM listings WHERE id='fotocasa:1'").fetchone()["possible_match"]
    assert pm == "pisos:2"           # hint: the closer address is the reference


def test_scores_are_kept_but_a_changed_listing_or_weight_is_scored_again(db):
    from common import make_listing
    from db import load_listings, upsert_listing
    upsert_listing(db, make_listing("eleiloes", "1", title="Moradia T3", price=20000, area_m2=120,
                                    description="Casa em bom estado"))
    db.commit()
    first = load_listings(db, apply_min_score=False)[0]
    again = load_listings(db, apply_min_score=False)[0]
    assert again["score"] == first["score"] and again["reasons"] == first["reasons"]
    lighter = load_listings(db, filters={"weights": {"price": 0}}, apply_min_score=False)[0]
    assert lighter["rank"] < first["rank"]                     # a new weight (Settings) is scored again
    db.execute("UPDATE listings SET description = 'Casa em ruínas, sem telhado' WHERE id = 'eleiloes:1'")
    db.commit()
    ruin = load_listings(db, apply_min_score=False)[0]
    assert ruin["score"] < first["score"]                      # the changed row was scored again


def test_a_scan_that_only_sees_a_listing_again_keeps_its_score(db, monkeypatch):
    import db as dbmod
    from common import make_listing
    from db import load_listings, upsert_listing
    upsert_listing(db, make_listing("eleiloes", "1", title="Moradia T3", price=20000, area_m2=120))
    db.commit()
    load_listings(db, apply_min_score=False)
    calls = []
    real = dbmod._score_one
    monkeypatch.setattr(dbmod, "_score_one", lambda *a, **k: calls.append(1) or real(*a, **k))
    db.execute("UPDATE listings SET last_seen = '2030-01-01T00:00:00+00:00' WHERE id = 'eleiloes:1'")
    db.commit()
    load_listings(db, apply_min_score=False)
    assert calls == []


def test_live_and_invest_is_the_average_of_both_against_the_best(db):
    """Owner's choice (2026-10-26): each raw score is divided by the best shown
    candidate's, so the best home to live in and the best investment are each
    100; the score is their average times the listing's charges. A list of one
    listing is scaled against the best kept from the full list."""
    from db import _normalize_both
    a = {"id": "a", "live_rank": 80.0, "invest_rank": 20.0, "both_factor": 1.0, "hidden_reason": None}
    b = {"id": "b", "live_rank": 40.0, "invest_rank": 40.0, "both_factor": 0.5, "hidden_reason": None}
    gone = {"id": "c", "live_rank": 500.0, "invest_rank": 500.0, "both_factor": 1.0, "hidden_reason": "stale"}
    out = _normalize_both(db, [a, b, gone], full=True, min_score=0, include_hidden=True)
    assert a["rank"] == 75.0                                   # (80/80 + 20/40) / 2
    assert a["score"] == 75.0                                  # shown as is, not squeezed again
    assert b["rank"] == 37.5                                   # (40/80 + 40/40) / 2 × 0.5
    assert len(out) == 3                                       # a hidden one does not set the scale
    one = {"id": "a", "live_rank": 80.0, "invest_rank": 20.0, "both_factor": 1.0, "hidden_reason": None}
    _normalize_both(db, [one], full=False, min_score=0, include_hidden=False)
    assert one["rank"] == 75.0                                 # the same scale, not its own
    low = _normalize_both(db, [dict(b)], full=False, min_score=40, include_hidden=False)
    assert low == []                                           # under the minimum once scaled


def test_a_near_miss_stays_flagged_and_does_not_set_the_scale(db):
    from db import _normalize_both
    a = {"id": "a", "live_rank": 50.0, "invest_rank": 50.0, "both_factor": 1.0, "hidden_reason": None}
    near = {"id": "n", "live_rank": 200.0, "invest_rank": 200.0, "both_factor": 1.0, "hidden_reason": None,
            "near_miss": "cash €70,000 all-in is over the €60,000 you can lose"}
    _normalize_both(db, [a, near], full=True, min_score=0, include_hidden=False)
    assert a["rank"] == 100.0                                  # the near miss is not the best
    assert near["rank"] == 100.0                               # capped at the scale, still shown


def test_a_long_load_logs_how_far_it_is(caplog, monkeypatch):
    import logging

    import db as dbmod
    monkeypatch.setattr(dbmod, "PROGRESS_EVERY", 2)
    progress = dbmod._Progress("land", 4)
    with caplog.at_level(logging.INFO, logger="auction-scanner"):
        for _ in range(4):
            progress.step()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Scores land")]
    assert lines[0].startswith("Scores land: 2 of 4 (50%)") and "min left" in lines[0]
    assert len(lines) == 2


def _counting_scores(monkeypatch):
    import db as dbmod
    rescored = []
    real = dbmod._score_one
    monkeypatch.setattr(dbmod, "_score_one", lambda item, *a, **kw: (rescored.append(item["id"]), real(item, *a, **kw))[1])
    return rescored


def _add_town(db, country, name, lat, lon):
    from geo import town_key
    db.execute("INSERT OR REPLACE INTO places (key, country, name, lat, lon, checked_at) VALUES (?, ?, ?, ?, ?, ?)",
               (town_key(country, name), country, name, lat, lon, "2026-10-25T00:00:00+00:00"))
    db.commit()


def test_a_scan_rescores_only_the_listings_whose_shared_entries_changed(db, monkeypatch):
    """After a scan (refresh=True) a listing is scored again only when an entry
    of the shared tables its score read changed (2026-10-25: a scan used to
    score all 135,000 again, an hour, because any new town or price moved the
    tables' counts)."""
    from common import make_listing
    from db import load_listings, upsert_listing
    for n, place in ((1, "Lamego"), (2, "Penela")):
        upsert_listing(db, make_listing("eleiloes", str(n), title="Moradia T3", price=20000 + n, area_m2=120,
                                        concelho=place, district="Viseu" if n == 1 else "Coimbra"))
    db.commit()
    _add_town(db, "PT", "Porto", 41.15, -8.61)                 # the town index is not empty
    first = {it["id"]: it["score"] for it in load_listings(db, apply_min_score=False, include_hidden=True)}
    rescored = _counting_scores(monkeypatch)

    # The scan finds a listing elsewhere and a town nobody here is in.
    upsert_listing(db, make_listing("spain", "9", country="ES", title="Casa", price=15000, area_m2=90,
                                    concelho="Pravia", district="Asturias"))
    db.commit()
    _add_town(db, "ES", "Pravia", 43.49, -6.11)
    again = {it["id"]: it["score"] for it in load_listings(db, apply_min_score=False, include_hidden=True,
                                                           refresh=True)}
    assert rescored == ["spain:9"]                             # only the new listing
    assert {k: again[k] for k in first} == first

    # Lamego is placed on the map: the one listing that reads that town is scored again.
    rescored.clear()
    _add_town(db, "PT", "Lamego", 41.10, -7.81)
    load_listings(db, apply_min_score=False, include_hidden=True, refresh=True)
    assert rescored == ["eleiloes:1"]


def test_a_new_day_rescores_only_the_listings_the_date_counts_for(db, monkeypatch):
    """A closing date or a court case makes a score depend on the day; a portal
    listing with neither scores the same tomorrow (2026-10-26: every midnight
    used to score all 135,000 again)."""
    from datetime import datetime, timedelta, timezone
    from common import make_listing
    from db import load_listings, upsert_listing
    day = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
    upsert_listing(db, make_listing("imovirtual", "1", title="Moradia T3", price=20000, area_m2=120))
    upsert_listing(db, make_listing("eleiloes", "2", title="Moradia T3", price=20000, area_m2=120,
                                    date_end="2026-11-20T10:00:00"))
    db.commit()
    load_listings(db, apply_min_score=False, include_hidden=True, now=day)
    rescored = _counting_scores(monkeypatch)
    load_listings(db, apply_min_score=False, include_hidden=True, now=day + timedelta(hours=5))
    assert rescored == []                                      # the same day: nothing
    load_listings(db, apply_min_score=False, include_hidden=True, now=day + timedelta(days=1))
    assert rescored == ["eleiloes:2"]                          # the next day: the sale with a date


def test_kept_scores_from_before_reads_were_noted_are_scored_once_more(db, monkeypatch):
    """A score kept with the old inputs (a JSON list of counts) has no record
    of what it read: a refresh scores it again, and after that it is kept."""
    import db as dbmod
    from common import make_listing
    from db import load_listings, upsert_listing
    upsert_listing(db, make_listing("eleiloes", "1", title="Moradia T3", price=20000, area_m2=120))
    db.commit()
    load_listings(db, apply_min_score=False)
    key, data, _ = dbmod._SCORED["home"]["eleiloes:1"]
    dbmod._SCORED["home"]["eleiloes:1"] = (key, data, "[3, 0, 0, \"\", 0]")
    rescored = _counting_scores(monkeypatch)
    load_listings(db, apply_min_score=False, refresh=True)
    load_listings(db, apply_min_score=False, refresh=True)
    assert rescored == ["eleiloes:1"]


def test_noted_reads_survive_tuple_keys_and_whole_country_reads():
    from db import _NONEMPTY, _reads_record, _reads_unchanged
    tables = {"floor_market": {("PT", "viseu", "house", 3): (110, 7)},
              "towns": {"PT:lamego": {"name": "Lamego"}, "ES:pravia": {"name": "Pravia"}}}
    log = {("floor_market", ("PT", "viseu", "house", 3)), ("floor_market", _NONEMPTY),
           ("towns", ("*", "PT"))}
    record = _reads_record(log, tables, {})
    assert _reads_unchanged(record, tables, {})
    tables["towns"]["ES:gijon"] = {"name": "Gijón"}             # another country's town
    assert _reads_unchanged(record, tables, {})
    tables["towns"]["PT:penela"] = {"name": "Penela"}           # one of the country's
    assert not _reads_unchanged(record, tables, {})
    assert not _reads_unchanged("[1, 2]", tables, {})


def test_a_goal_not_kept_in_memory_is_read_back_from_the_scores_file(db, monkeypatch):
    """The app keeps only the goals it has open (dashboard.MAX_LISTS). Another
    goal's scores are read back from disk, never scored again, and let go after."""
    import db as dbmod
    from common import make_listing
    from db import keep_scores_for, load_listings, upsert_listing
    for n in range(3):
        upsert_listing(db, make_listing("eleiloes", str(n), title="Moradia T3", price=20000 + n, area_m2=120,
                                        description="Casa com terreno e poço"))
    db.commit()

    def scores(**kw):
        return {it["id"]: (it["score"], it["reasons"])
                for it in load_listings(db, apply_min_score=False, mode="land", **kw)}

    first = scores()
    keep_scores_for({"home"})
    assert "land" not in dbmod._SCORED                         # let go at once
    calls = []
    real = dbmod._score_one
    monkeypatch.setattr(dbmod, "_score_one", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert scores() == first and calls == []                   # read back, not scored again
    assert "land" not in dbmod._SCORED                         # and let go after the call
    one = scores(where="id = ?", params=("eleiloes:1",))
    assert one == {"eleiloes:1": first["eleiloes:1"]} and calls == []
    load_listings(db, apply_min_score=False, mode="home")
    assert "home" in dbmod._SCORED                             # a goal on screen stays


def test_the_five_wishes_are_not_kept_with_the_scores(db):
    import db as dbmod
    from common import make_listing
    from db import load_listings, upsert_listing
    upsert_listing(db, make_listing("eleiloes", "1", title="Moradia T3", price=20000, area_m2=160))
    db.commit()
    item = load_listings(db, apply_min_score=False)[0]
    assert "wishes" not in item
    assert "wishes" not in dbmod._SCORED["home"]["eleiloes:1"][1]


def test_a_hidden_listing_kept_slim_is_the_same_listing_read_in_full(db):
    from common import make_listing
    from db import _SLIM, load_listings, upsert_listing
    upsert_listing(db, make_listing("eleiloes", "1", title="Moradia T3", price=20000, area_m2=120,
                                    description="Casa em bom estado"))
    upsert_listing(db, make_listing("eleiloes", "2", title="Garagem", price=900, area_m2=12))
    db.commit()
    full = {it["id"]: it for it in load_listings(db, include_hidden=True, filters={"min_score": 99})}
    slim = {it["id"]: it for it in load_listings(db, include_hidden=True, filters={"min_score": 99},
                                                 slim_hidden=True)}
    assert all(it["hidden_reason"] for it in full.values())
    for lid, it in slim.items():
        assert it["_slim"] and set(it) <= set(_SLIM) | {"_slim"}
        assert {k: v for k, v in it.items() if k != "_slim"} == {k: full[lid][k] for k in _SLIM if k in full[lid]}
    # Read again by its id, from the kept score, it is the listing a full list gives from the kept scores
    # (a list scored just now also carries the shared land_market table).
    from_kept = {it["id"]: it for it in load_listings(db, include_hidden=True, filters={"min_score": 99})}
    again = load_listings(db, include_hidden=True, filters={"min_score": 99}, where="id = ?", params=("eleiloes:2",))
    assert again[0] == from_kept["eleiloes:2"]


def test_evidence_revision_busts_score_cache(db, monkeypatch):
    """An evidence update (location, hazard, etc.) invalidates the score cache (F11)."""
    import db as dbmod
    from common import make_listing
    from db import load_listings, record_evidence_revision, upsert_listing
    upsert_listing(db, make_listing("eleiloes", "1", title="Moradia T3", price=20000, area_m2=120,
                                    description="Casa em bom estado"))
    db.commit()
    load_listings(db, apply_min_score=False)
    first_key = dbmod._SCORED["home"]["eleiloes:1"][0]
    # Loading again with nothing changed reuses the kept score.
    load_listings(db, apply_min_score=False)
    assert dbmod._SCORED["home"]["eleiloes:1"][0] == first_key
    # Simulate an evidence update (e.g. climate or location revision)
    record_evidence_revision(db, "eleiloes:1", "climate", {"grade": "good", "version": 1})
    db.commit()
    # Load again without forgetting the kept scores: the rev sum alone must
    # change the cache key, so the listing is scored again with the new evidence.
    rescored = []
    real = dbmod._score_one
    monkeypatch.setattr(dbmod, "_score_one", lambda item, *a, **kw: (rescored.append(item["id"]), real(item, *a, **kw)))
    load_listings(db, apply_min_score=False)
    assert rescored == ["eleiloes:1"], "evidence revision must re-score the listing"
    assert dbmod._SCORED["home"]["eleiloes:1"][0] != first_key, "evidence revision must change the score cache key"


# ─── Task 7: anomaly clearance bound to evidence revisions ────────────────────

def test_clearance_without_rev_sum_is_stale_after_migration(db, add, monkeypatch):
    """A pre-v27 clearance (evidence_rev_sum=NULL) is treated as stale by load_best (Task 7)."""
    import scoring
    monkeypatch.setattr(scoring, "local_price", lambda item: (500_000.0, "test series"))
    from db import clear_extreme_discount, load_best
    # price=30000 on a 80m² property is ~92% below 500k/80m²=6250/m² — triggers extreme_discount
    add(external_id="disc1", title="Moradia T3", price=30000, area_m2=80)
    listing_id = "eleiloes:disc1"
    clear_extreme_discount(db, listing_id, note="checked", evidence_rev_sum=None)
    result = load_best(db)
    ids = {it["id"] for it in result}
    assert listing_id not in ids, "clearance with NULL rev_sum must not grant finalist access"


def test_clearances_read_for_more_ids_than_sqlite_variables(db):
    """A scan asks about every listing at once (2026-10-24: ~80,000), past
    SQLite's variable limit; the clearance read must not bind one per id."""
    from db import _extreme_clearances, clear_extreme_discount
    clear_extreme_discount(db, "eleiloes:kept", note="checked", evidence_rev_sum=0)
    ids = {f"x:{i}" for i in range(40_000)} | {"eleiloes:kept"}
    assert _extreme_clearances(db, ids, ev_rev_sums={}) == {"eleiloes:kept"}
    assert _extreme_clearances(db, {"x:1"}) == set()


def test_clearance_with_matching_rev_sum_passes(db, add, monkeypatch):
    """A clearance whose evidence_rev_sum matches the current sum reaches load_best (Task 7)."""
    import scoring
    monkeypatch.setattr(scoring, "local_price", lambda item: (500_000.0, "test series"))
    from db import clear_extreme_discount, load_best, record_evidence_revision
    add(external_id="disc2", title="Moradia T3 em bom estado", price=30000, area_m2=80)
    listing_id = "eleiloes:disc2"
    record_evidence_revision(db, listing_id, "location", {"precision": "sale"})
    db.commit()
    rev_sum = db.execute(
        "SELECT SUM(rev) FROM evidence_revisions WHERE listing_id=?", (listing_id,)
    ).fetchone()[0] or 0
    clear_extreme_discount(db, listing_id, note="verified ownership", evidence_rev_sum=rev_sum)
    result = load_best(db)
    ids = {it["id"] for it in result}
    assert listing_id in ids, "clearance with matching rev_sum must allow finalist access"


def test_clearance_revoked_when_evidence_changes(db, add, monkeypatch):
    """After an evidence update, the clearance is revoked and the item is blocked again (Task 7)."""
    import scoring
    monkeypatch.setattr(scoring, "local_price", lambda item: (500_000.0, "test series"))
    from db import clear_extreme_discount, load_best, record_evidence_revision
    add(external_id="disc3", title="Moradia grande em bom estado", price=30000, area_m2=80)
    listing_id = "eleiloes:disc3"
    record_evidence_revision(db, listing_id, "location", {"precision": "sale"})
    db.commit()
    rev_sum = db.execute(
        "SELECT SUM(rev) FROM evidence_revisions WHERE listing_id=?", (listing_id,)
    ).fetchone()[0] or 0
    clear_extreme_discount(db, listing_id, note="ok", evidence_rev_sum=rev_sum)
    assert any(it["id"] == listing_id for it in load_best(db))
    record_evidence_revision(db, listing_id, "hazard", {"flood_m": 0.5})
    db.commit()
    result_after = load_best(db)
    ids_after = {it["id"] for it in result_after}
    assert listing_id not in ids_after, "stale clearance must not count after evidence change"
    row = db.execute(
        "SELECT * FROM investigation_clearances WHERE listing_id=?", (listing_id,)
    ).fetchone()
    assert row is not None, "a stale clearance is excluded, never deleted by a reader"


def test_anomaly_investigated_uses_clearance_not_note(db, add, monkeypatch):
    """_shadow_evaluate sets anomaly_investigated from investigation_clearances, not investigation_note (Task 7)."""
    import scoring
    monkeypatch.setattr(scoring, "local_price", lambda item: (500_000.0, "test series"))
    from db import clear_extreme_discount, load_listings, record_evidence_revision
    add(external_id="disc4", title="Moradia T4 bom estado", price=30000, area_m2=80)
    listing_id = "eleiloes:disc4"
    items = load_listings(db, apply_min_score=False)
    it = next(i for i in items if i["id"] == listing_id)
    assert not it.get("investigation_cleared"), "not cleared yet"
    record_evidence_revision(db, listing_id, "identity", {"confirmed": True})
    db.commit()
    rev_sum = db.execute(
        "SELECT SUM(rev) FROM evidence_revisions WHERE listing_id=?", (listing_id,)
    ).fetchone()[0] or 0
    clear_extreme_discount(db, listing_id, note="owner verified", evidence_rev_sum=rev_sum)
    items = load_listings(db, apply_min_score=False)
    it2 = next(i for i in items if i["id"] == listing_id)
    assert it2.get("investigation_cleared"), "cleared listing must have investigation_cleared=True"


def test_status_history_is_append_only_and_survives_clearing(db):
    from db import listing_status_history, set_listing_status
    set_listing_status(db, "eleiloes:s1", "dismissed", "occupied")
    set_listing_status(db, "eleiloes:s1", None)                         # Undo to an open listing
    assert db.execute("SELECT 1 FROM listing_status WHERE listing_id='eleiloes:s1'").fetchone() is None
    assert [(h["status"], h["reason"]) for h in listing_status_history(db, "eleiloes:s1")] == [
        ("dismissed", "occupied"), (None, None)]
    with pytest.raises(ValueError):
        set_listing_status(db, "eleiloes:s1", "sold")
    assert len(listing_status_history(db, "eleiloes:s1")) == 2         # a refused change is not recorded
