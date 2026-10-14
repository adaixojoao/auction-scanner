"""What state a house is in, from its photos (photos.py)."""
import json

import photos
from common import make_listing
from db import load_listings, upsert_listing
from scoring import condition, score_detail


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeClaude:
    """Stands in for anthropic.Anthropic(): records requests, answers JSON."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        return type("Resp", (), {"content": [_Block(json.dumps(self.answer))]})()


HOUSE = dict(title="Moradia T3 em Resende", price=20000, area_m2=100, concelho="Resende",
             image_url="https://e-leiloes.pt/api/img/1.jpg")


def test_the_photos_decide_when_the_text_says_nothing(db):
    upsert_listing(db, make_listing("eleiloes", "1", **HOUSE))
    upsert_listing(db, make_listing("eleiloes", "2", **{**HOUSE, "image_url": None}))      # no photos
    upsert_listing(db, make_listing("eleiloes", "3", **{**HOUSE, "description": "Moradia em bom estado"}))
    db.commit()
    before = {it["id"]: it["rank"] for it in load_listings(db, apply_min_score=False)}
    claude = FakeClaude({"condition": "heavy", "confidence": "high", "notes": "No roof, walls collapsing."})
    items = load_listings(db, apply_min_score=False)
    assert photos.check_pending(db, {}, items, looker=photos.ClaudeLooker(claude)) == 2        # not the one without photos
    image = claude.requests[0]["messages"][0]["content"][0]
    assert image == {"type": "image", "source": {"type": "url", "url": "https://e-leiloes.pt/api/img/1.jpg"}}
    assert claude.requests[0]["output_config"]["format"]["type"] == "json_schema"

    after = {it["id"]: it for it in load_listings(db, apply_min_score=False)}
    ruin = after["eleiloes:1"]
    assert "needs heavy work (ruin / full rebuild) (from the photos)" in ruin["reasons"]
    assert ruin["rank"] < before["eleiloes:1"]
    # What the text says wins over the photos.
    assert condition(after["eleiloes:3"]) == "good"
    # Each listing once, and a new scan's raw data keeps the answer.
    assert photos.check_pending(db, {}, load_listings(db, apply_min_score=False), looker=photos.ClaudeLooker(claude)) == 0
    upsert_listing(db, make_listing("eleiloes", "1", **HOUSE, raw_json={"valorBase": 20000}))
    db.commit()
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='eleiloes:1'").fetchone()[0])
    assert raw["valorBase"] == 20000 and raw["photo_check"]["condition"] == "heavy"


def test_an_unsure_answer_is_not_used():
    unsure = {"title": "Moradia", "raw_json": json.dumps(
        {"photo_check": {"condition": "good", "confidence": "low", "notes": "Only a map."}})}
    assert condition(unsure) == "unknown"
    sure = {**unsure, "raw_json": json.dumps({"photo_check": {"condition": "good", "confidence": "medium"}})}
    assert condition(sure) == "good"
    assert any(r.endswith("(from the photos)") for r in score_detail({**sure, "price": 9000, "area_m2": 90})[1])


def test_nothing_is_sent_without_a_key(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    upsert_listing(db, make_listing("eleiloes", "1", **HOUSE))
    db.commit()
    cfg = {"ai": {"provider": "anthropic", "anthropic_key": ""}}
    assert photos.check_pending(db, cfg, load_listings(db, apply_min_score=False)) == 0
    assert photos.check_pending(db, {"ai": {"photo_check": False}}, [], looker=photos.ClaudeLooker(FakeClaude({}))) == 0


def test_an_open_model_on_this_pc_looks_at_the_photos(db, fake_http):
    """Ollama: the photos are fetched here and sent as images; the answer is
    held to the same schema. Without Ollama running, nothing happens."""
    from conftest import FakeResponse
    calls = []

    def handler(method, url, kw):
        calls.append((method, url, kw))
        if url.endswith("/api/tags"):
            return FakeResponse(json_data={"models": [{"name": "qwen2.5vl:3b"}]})
        if url.endswith("/api/chat"):
            return FakeResponse(json_data={"message": {"content": json.dumps(
                {"condition": "some", "confidence": "medium", "notes": "Old kitchen, damp on a wall."})}})
        resp = FakeResponse("")
        resp.content = bytes([0xFF, 0xD8]) + b"jpeg"
        return resp
    session = fake_http(handler)
    looker = photos.OllamaLooker(session)
    assert looker.ready()
    upsert_listing(db, make_listing("eleiloes", "1", **HOUSE))
    db.commit()
    assert photos.check_pending(db, {}, load_listings(db, apply_min_score=False), looker=looker) == 1
    chat = next(kw for m, url, kw in calls if url.endswith("/api/chat"))
    assert chat["json"]["model"] == "qwen2.5vl:3b" and chat["json"]["format"] == photos.PHOTO_SCHEMA
    assert chat["json"]["messages"][0]["images"] == ["/9hqcGVn"]           # base64 of the fetched photo
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='eleiloes:1'").fetchone()[0])
    assert raw["photo_check"]["condition"] == "some" and raw["photo_check"]["by"] == "qwen2.5vl:3b"


def test_without_ollama_running_nothing_is_sent(monkeypatch):
    import common

    class Down:
        def get(self, *a, **k):
            raise ConnectionError("refused")
    monkeypatch.setattr(common, "make_session", lambda *a, **k: Down())
    assert photos.make_looker({"ai": {"provider": "ollama"}}) is None


def test_photo_urls_upgrade_http_and_read_more_gallery_keys():
    item = {
        "image_url": "http://example.com/cover.jpg",
        "raw_json": json.dumps({
            "immagini": [{"url": "//cdn.example.com/a.jpg"}, "https://cdn.example.com/b.jpg"],
            "gallery": ["https://cdn.example.com/cover.jpg"],
        }),
    }
    assert photos.photo_urls(item) == [
        "https://example.com/cover.jpg",
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
        "https://cdn.example.com/cover.jpg",
    ]


def test_photos_budget_catches_up_when_the_queue_is_long():
    few = [make_listing("eleiloes", str(i), **HOUSE) for i in range(3)]
    for row in few:
        row["kind"] = "home"
    many = [make_listing("eleiloes", str(i), **HOUSE) for i in range(photos.PHOTOS_CATCHUP_WHEN + 1)]
    for row in many:
        row["kind"] = "home"
    huge = [make_listing("eleiloes", str(i), **HOUSE) for i in range(photos.PHOTOS_CATCHUP_HARD_WHEN + 1)]
    for row in huge:
        row["kind"] = "home"
    assert photos.photos_budget(few, "ollama") == photos.PHOTOS_PER_SCAN["ollama"]
    assert photos.photos_budget(many, "ollama") == photos.PHOTOS_CATCHUP["ollama"]
    assert photos.photos_budget(many, "anthropic") == photos.PHOTOS_CATCHUP["anthropic"]
    assert photos.photos_budget(huge, "ollama") == photos.PHOTOS_CATCHUP_HARD["ollama"]
    assert photos.photos_budget(many, "ollama", configured=7) == 7


def test_check_pending_says_when_homes_wait_without_a_looker(db, monkeypatch, caplog):
    import logging
    upsert_listing(db, make_listing("eleiloes", "1", **HOUSE))
    db.commit()
    monkeypatch.setattr(photos, "make_looker", lambda cfg: None)
    with caplog.at_level(logging.INFO):
        assert photos.check_pending(db, {}, load_listings(db, apply_min_score=False)) == 0
    assert any("homes waiting" in r.message for r in caplog.records)


def test_a_looker_that_keeps_failing_is_given_up_on(db):
    for n in range(6):
        upsert_listing(db, make_listing("eleiloes", str(n), **HOUSE))
    db.commit()

    class DownLooker:
        name, calls = "ollama", 0

        def look(self, urls, prompt):
            DownLooker.calls += 1
            raise ConnectionError("Ollama went away")

    looker = DownLooker()
    assert photos.check_pending(db, {}, load_listings(db, apply_min_score=False), looker=looker) == 0
    assert DownLooker.calls == photos.PHOTO_FAILURES_IN_A_ROW      # not one 15-minute wait per home


def test_photos_are_shrunk_for_the_local_model():
    import io
    import pytest
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), "white").save(buf, "JPEG")
    small = Image.open(io.BytesIO(photos.shrink(buf.getvalue())))
    assert max(small.size) == photos.OLLAMA_PHOTO_SIDE
    assert photos.shrink(b"not an image") == b"not an image"


def test_photos_of_only_a_barn_are_flagged_and_not_used_for_condition():
    raw = json.dumps({"photo_check": {"condition": "heavy", "confidence": "high", "notes": "old barn",
                                      "photos": 1, "shows_house": False}})
    it = {**make_listing("eleiloes", "9", **HOUSE), "raw_json": raw}
    assert condition(it) == "unknown"
    assert "the photos don't show the house itself — ask the seller for photos" in score_detail(it)[1]


def test_exact_beach_homes_are_photographed_first(monkeypatch):
    sea = {"id": "a", "kind": "home", "rank": 10, "score": 10,
           "beach": {"km": 0.4, "approx": False}}
    inland = {"id": "b", "kind": "home", "rank": 90, "score": 90,
              "beach": {"km": 12.0, "approx": False}}

    def fake_load(db, filters=None, mode="home", **_):
        return [inland, sea] if mode == "home" else []

    monkeypatch.setattr("db.load_listings", fake_load)
    assert [it["id"] for it in photos.queue(None, {})][0] == "a"


def test_a_score_of_85_keeps_the_photos_and_a_lower_one_does_not(db):
    import io
    import pytest
    import listing_info
    Image = pytest.importorskip("PIL.Image")
    from common import make_listing
    upsert_listing(db, make_listing("eleiloes", "hi", **HOUSE))
    db.commit()
    item = next(it for it in load_listings(db, include_hidden=True, apply_min_score=False)
                if it["id"] == "eleiloes:hi")
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buf, "PNG")

    class Sess:
        def __init__(self):
            self.calls = 0

        def get(self, url, timeout=30):
            self.calls += 1
            return type("R", (), {"status_code": 200, "content": buf.getvalue(),
                                  "headers": {"content-type": "image/png"}})()

    low = {**item, "rank": 84}
    assert photos.keep_one(db, Sess(), low) is False
    assert photos.kept_path(db, item["id"], 0) is None
    session = Sess()
    high = {**item, "rank": 85}
    assert photos.keep_one(db, session, high) is True
    assert session.calls == 1
    path = photos.kept_path(db, item["id"], 0)
    assert path and open(path, "rb").read()[:2] == b"\xff\xd8"
    assert photos.kept_path(db, item["id"], 1) is None
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id=?", (item["id"],)).fetchone()[0])
    assert raw["kept_photos"] == ["0.jpg"]
    # Already saved: not fetched again. A later scrape does not drop the copies.
    assert photos.keep_one(db, Sess(), {**high, "raw_json": json.dumps(raw)}) is False
    upsert_listing(db, make_listing("eleiloes", "hi", **HOUSE, raw_json={"valorBase": 1}))
    db.commit()
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id=?", (item["id"],)).fetchone()[0])
    assert raw["kept_photos"] == ["0.jpg"] and raw["valorBase"] == 1
    pics = listing_info.gallery({**high, "raw_json": json.dumps(raw)})
    assert pics == ["/api/kept-photo?id=eleiloes%3Ahi&n=0"]
    assert any(f["label"] == "Photos kept" for f in listing_info.facts({**high, "raw_json": json.dumps(raw)}))


def test_the_open_pictures_can_show_a_ruin_but_not_a_sound_house(db, monkeypatch):
    """An obvious ruin at the building's pin counts. A standing outside does not:
    the inside was not in the picture. A town pin is not looked at. No key."""
    upsert_listing(db, make_listing("eleiloes", "out", **{**HOUSE, "image_url": None},
                                    raw_json={"lat": 41.06, "lon": -7.93}))
    db.commit()
    item = next(it for it in load_listings(db, include_hidden=True, apply_min_score=False)
                if it["id"] == "eleiloes:out")
    assert photos.needs_outside_check(item)
    assert not photos.needs_outside_check(
        {**item, "raw_json": json.dumps({"geo": {"lat": 41.0, "lon": -7.0, "precision": "municipality"}})})
    assert not photos.needs_outside_check(
        {**item, "raw_json": json.dumps({"geo": {"lat": 41.0, "lon": -7.0, "precision": "street"}})})

    class Look:
        name = "test"

        def look_images(self, images, prompt):
            return {"condition": "heavy", "confidence": "high", "notes": "collapsed roof",
                    "shows_house": True}

    monkeypatch.setattr(photos, "fetch_outside", lambda *a, **k: ([b"jpeg"], ["PNOA"]))
    assert photos.check_pending(db, {}, [item], looker=Look()) == 1
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='eleiloes:out'").fetchone()[0])
    assert raw["outside_check"]["condition"] == "heavy" and raw["outside_check"]["sources"] == ["PNOA"]
    judged = {**item, "raw_json": json.dumps(raw)}
    assert condition(judged) == "heavy"
    assert any("open aerial or street photo" in r for r in score_detail(judged)[1])
    raw["outside_check"] = {**raw["outside_check"], "condition": "standing", "notes": "outside standing"}
    standing = {**item, "raw_json": json.dumps(raw)}
    assert condition(standing) == "unknown"
    reasons = score_detail(standing)[1]
    assert any("outside is standing" in r and "inside was not seen" in r for r in reasons)
    assert not any(r.startswith("good condition") for r in reasons)
    raw["outside_check"] = {"condition": "heavy", "confidence": "low", "shows_house": True}
    assert condition({**item, "raw_json": json.dumps(raw)}) == "unknown"


def test_a_picture_server_that_fails_is_not_stored_as_no_ruin(db, monkeypatch):
    upsert_listing(db, make_listing("eleiloes", "out2", **{**HOUSE, "image_url": None},
                                    raw_json={"lat": 41.06, "lon": -7.93}))
    db.commit()
    item = next(it for it in load_listings(db, include_hidden=True, apply_min_score=False)
                if it["id"] == "eleiloes:out2")

    def refused(*_a, **_k):
        raise photos.OutsideUnavailable()

    monkeypatch.setattr(photos, "fetch_outside", refused)

    class Look:
        name = "test"

    assert photos.check_pending(db, {}, [item], looker=Look()) == 0
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='eleiloes:out2'").fetchone()[0])
    assert "outside_check" not in raw


def test_open_aerial_and_street_photos_need_no_key():
    import io
    import pytest
    Image = pytest.importorskip("PIL.Image")
    # The projection origin of the Portuguese grid is (0, 0). France and Spain
    # use a web-mercator square. Germany has no open aerial survey wired in.
    x, y = photos._pt_tm06(39.66825833333333, -8.133108333333334)
    assert abs(x) < 1 and abs(y) < 1
    pt, name = photos.aerial_url("PT", 39.66825833333333, -8.133108333333334)
    assert name == "DGT" and "ortos2025" in pt and "EPSG%3A3763" in pt
    fr, fr_name = photos.aerial_url("FR", 49.64, -1.64)
    assert fr_name == "IGN" and "data.geopf.fr" in fr and "ORTHOIMAGERY.ORTHOPHOTOS" in fr
    es, es_name = photos.aerial_url("ES", 37.39, -5.99)
    assert es_name == "PNOA" and "ign.es/wmts/pnoa-ma" in es and "TILEMATRIX=19" in es
    assert photos.aerial_url("DE", 48.1, 11.5) is None
    search = photos.street_search_url(41.06, -7.93)
    assert search.startswith("https://api.panoramax.xyz/api/search?") and "bbox=" in search
    near = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-7.9301, 41.0601]},
            "assets": {"sd": {"href": "https://panoramax.example/sd.jpg"}}}
    far = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-7.95, 41.08]},
           "assets": {"sd": {"href": "https://panoramax.example/far.jpg"}}}
    assert photos.nearest_street_href({"features": [far, near]}, 41.06, -7.93) == "https://panoramax.example/sd.jpg"
    assert photos.nearest_street_href({"features": [far]}, 41.06, -7.93) is None

    class Resp:
        def __init__(self, code, body, kind, payload=None):
            self.status_code, self.content = code, body
            self.headers = {"content-type": kind}
            self._payload = payload

        def json(self):
            return self._payload

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    png = buf.getvalue()

    class Sess:
        def get(self, url, timeout=30):
            if "panoramax" in url and "search" in url:
                return Resp(200, b"{}", "application/json", {"features": [near]})
            return Resp(200, png, "image/png")

    images, sources = photos.fetch_outside(Sess(), {"lat": 41.06, "lon": -7.93}, "ES")
    assert sources == ["Panoramax", "PNOA"] and len(images) == 2 and images[0][:2] == b"\xff\xd8"

    class Down:
        def get(self, url, timeout=30):
            return Resp(503, b"", "text/plain")

    try:
        photos.fetch_outside(Down(), {"lat": 41.06, "lon": -7.93}, "ES")
    except photos.OutsideUnavailable:
        pass
    else:
        raise AssertionError("a failed picture server should be tried again, not stored")

