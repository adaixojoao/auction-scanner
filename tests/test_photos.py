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
    assert photos.check_pending(db, {}, items, client=claude) == 2        # not the one without photos
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
    assert photos.check_pending(db, {}, load_listings(db, apply_min_score=False), client=claude) == 0
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
    assert photos.check_pending(db, {"ai": {"anthropic_key": ""}}, load_listings(db, apply_min_score=False)) == 0
    assert photos.check_pending(db, {"ai": {"photo_check": False}}, [], client=FakeClaude({})) == 0
