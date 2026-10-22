"""Beta wired into the app: link-check recovery and owned-key writes, the
NULL-safe counts, the v34 migration and the shared hazard check."""
import json
from datetime import datetime, timedelta, timezone

import confirm
import db as dbmod


class Resp:
    def __init__(self, status, text, url):
        self.status_code, self.text, self.url = status, text, url


class Session:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, timeout=20):
        self.asked.append(url)
        return self.pages[url]


def _mark(db, lid, state, days_ago):
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id=?", (lid,)).fetchone()[0] or "{}")
    raw["link_check"] = {"availability": state, "gone": True, "why": "reservado", "at": at}
    db.execute("UPDATE listings SET raw_json=? WHERE id=?", (json.dumps(raw), lid))
    db.commit()


def test_a_reserved_mark_is_rechecked_and_can_come_back(db, add, monkeypatch):
    monkeypatch.setattr(confirm, "PAUSE_S", 0)
    old = add("eleiloes", "R1", title="Moradia", price=5000, url="https://example.invalid/r1")
    new = add("eleiloes", "R2", title="Moradia", price=5000, url="https://example.invalid/r2")
    _mark(db, old["id"], "reserved", 10)
    _mark(db, new["id"], "reserved", 1)                   # checked yesterday: not yet
    page = "<html><head><title>Moradia T3</title></head><body>" + "text " * 100 + "</body></html>"
    s = Session({"https://example.invalid/r1": Resp(200, page, "https://example.invalid/r1")})
    confirm.check_top(db, {"filters": {"min_score": 0}}, s)
    assert s.asked == ["https://example.invalid/r1"]
    row = dict(db.execute("SELECT * FROM listings WHERE id=?", (old["id"],)).fetchone())
    assert confirm.gone_reason(row) is None                # no longer hidden as reserved
    assert confirm.availability_state(row) == "unknown"    # but not called available
    check = json.loads(row["raw_json"])["link_check"]
    assert check["basis"] == "generic_page" and check["parser"] == confirm.PARSER_VERSION
    assert check["requested_url"] == check["final_url"] and check["redirected"] is False


def test_the_link_check_keeps_data_saved_since_the_copy_was_loaded(db, add):
    item = add("eleiloes", "K1", title="Moradia", price=5000, url="https://example.invalid/k1")
    stale = dict(db.execute("SELECT * FROM listings WHERE id=?", (item["id"],)).fetchone())
    raw = json.loads(stale["raw_json"] or "{}")
    raw["detail_checked"] = True                           # a detail fetch saved meanwhile
    db.execute("UPDATE listings SET raw_json=? WHERE id=?", (json.dumps(raw), item["id"]))
    db.commit()
    confirm._store(db, stale, "withdrawn", "page gone", requested="https://example.invalid/k1",
                   final="https://example.invalid/home")
    saved = json.loads(db.execute("SELECT raw_json FROM listings WHERE id=?", (item["id"],)).fetchone()[0])
    assert saved["detail_checked"] is True and saved["link_check"]["availability"] == "withdrawn"
    assert saved["link_check"]["redirected"] is True


def test_a_letter_without_a_listing_does_not_empty_the_unchecked_count(db, add, monkeypatch):
    monkeypatch.setattr(dbmod, "_work_limits", lambda: (1000.0, 0.0))
    add("eleiloes", "U1", title="Moradia", price=50000)
    db.execute("INSERT INTO carta_log (listing_id, is_offer, created_at) VALUES (NULL, 1, '2026-01-01')")
    db.commit()
    assert dbmod.unchecked_over_cap(db)[0] == 1


def test_v34_adds_the_evidence_tables_and_keeps_letters(tmp_path):
    path = str(tmp_path / "old.db")
    conn = dbmod.connect(path)
    conn.execute("INSERT INTO carta_log (listing_id, is_offer, created_at, letter_text)"
                 " VALUES ('x:1', 1, '2026-01-01', 'Exact letter')")
    conn.execute("DROP TABLE evidence_current")
    conn.execute("DROP TABLE evidence_observations")
    conn.execute("PRAGMA user_version = 33")
    conn.commit()
    conn.close()
    conn = dbmod.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION == 34
    assert conn.execute("SELECT letter_text FROM carta_log").fetchone()[0] == "Exact letter"
    assert conn.execute("SELECT COUNT(*) FROM evidence_current").fetchone()[0] == 0
    conn.close()


def test_an_empty_fire_reading_caps_the_climate_grade():
    import scoring
    base = {"flood_m": 0, "water_km": 5}
    full = scoring.climate_score({**base, "fire_danger": {"high_days_2090": 0}})
    empty = scoring.climate_score({**base, "fire_danger": {}})
    text = scoring.climate_score({**base, "flood_m": float("nan"), "fire_danger": {"high_days_2090": 0}})
    assert full["confidence"] != "hazard_data_incomplete"
    assert empty["confidence"] == text["confidence"] == "hazard_data_incomplete"
    assert any("fire danger layer missing" in r for r in empty["reasons"])
    assert any("flood layer missing" in r for r in text["reasons"])


def _home(gallery, check):
    return {"title": "Moradia T3", "tipo": "moradia", "kind": "home",
            "raw_json": json.dumps({"photos": gallery, "photo_check": check})}


def test_photo_attribution_is_true_false_or_unknown(monkeypatch):
    import photos

    class Looker:
        name, model = "fake", "fake-vision"

        def __init__(self, answer):
            self.answer = answer

        def look(self, urls, prompt):
            return self.answer
    gallery = [f"https://example.invalid/{i}.jpg" for i in range(5)]
    item = {"title": "Moradia", "raw_json": json.dumps({"photos": gallery})}
    monkeypatch.setattr(photos, "photo_urls", lambda it: list(gallery))
    said = {"condition": "good", "confidence": "high", "notes": ""}
    assert photos.check_photos(Looker({**said, "shows_house": True}), item)["shows_house"] is True
    assert photos.check_photos(Looker({**said, "shows_house": False}), item)["shows_house"] is False
    unsaid = photos.check_photos(Looker(said), item)
    assert unsaid["shows_house"] is None
    assert unsaid["gallery_order"] and unsaid["selected_inputs"]


def test_an_unknown_photo_answer_is_asked_again_later_not_every_scan(monkeypatch):
    import photos
    gallery = ["https://example.invalid/a.jpg", "https://example.invalid/b.jpg"]
    monkeypatch.setattr(photos, "photo_urls", lambda it: list(gallery))
    h = photos._gallery_hash({})
    fresh = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    assert not photos.needs_photo_check(_home(gallery, {"shows_house": None, "gallery_hash": h, "at": fresh}))
    assert photos.needs_photo_check(_home(gallery, {"shows_house": None, "gallery_hash": h, "at": old}))
    assert not photos.needs_photo_check(_home(gallery, {"shows_house": True, "gallery_hash": h, "at": old}))
    assert photos.needs_photo_check(_home(gallery, {"shows_house": False, "gallery_hash": h, "at": fresh}))
    assert photos.needs_photo_check(_home(gallery, {"shows_house": True, "gallery_hash": "other", "at": fresh}))


def test_scoring_and_the_evaluator_read_hazards_the_same_way():
    from collection_contracts import hazard_metrics
    assert hazard_metrics({"flood_m": 0, "fire_danger": {"rcp45_2079-2098": 5}}) == (0.0, 5.0)
    assert hazard_metrics({"flood_m": 0, "fire_danger": {"high_days_2090": "x", "rcp45_2079-2098": 5}}) == (0.0, None)
    assert hazard_metrics({"flood_m": True, "fire_danger": {}, "fire_danger_days": 3}) == (None, 3.0)


def test_a_letter_without_a_stored_listing_stays_in_the_history(db):
    import dashboard
    db.execute("INSERT INTO carta_log (listing_id, processo, is_offer, outcome, sent_date, created_at, letter_text)"
               " VALUES (NULL, '123/24.0T8GRD', 1, 'pending', '2026-01-02', '2026-01-02', 'Exact letter')")
    db.execute("INSERT INTO carta_log (listing_id, is_offer, outcome, sent_date, created_at)"
               " VALUES ('gone:1', 1, 'lost', '2025-05-01', '2025-05-01')")
    db.commit()
    dashboard.app.config["TESTING"] = True
    body = dashboard.app.test_client().get("/api/offers").get_json()
    sent = [v for v in body["sent"] if v.get("historical_only")]
    closed = [v for v in body["closed"] if v.get("historical_only")]
    assert len(sent) == 1 and len(closed) == 1
    assert sent[0]["offer"]["letter_text"] == "Exact letter" and sent[0]["processo"] == "123/24.0T8GRD"
    assert sent[0]["price"] is None and sent[0]["area_m2"] is None   # unknown, not zero
