"""Milestone G — F05 extreme-discount investigation queue; F10 gallery-change requeue."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import scoring
from photos import _gallery_hash, needs_photo_check
from scoring import climate_score, score_detail

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

MILD = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1},
        "flood_m": 0.0, "fire_danger": {"high_days_2090": 0}}
SALE = json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"})


def _item(**kw):
    base = {"source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia",
            "area_m2": 80, "price": 30000, "description": "moradia em bom estado",
            "climate": dict(MILD), "raw_json": SALE}
    base.update(kw)
    return base


# ─── F05: extreme-discount investigation queue ───────────────────────────────

def test_extreme_discount_sets_flag_not_penalty(monkeypatch):
    """Listing >70% below local price gets investigation flag, not a point penalty (F05)."""
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "a city series"))
    item = _item(price=10000, area_m2=80)  # 10k all-in vs ~160k market — extreme gap
    base_score, _ = score_detail(item, mode="invest")
    assert item.get("extreme_discount") is True
    # No penalty: score equals the base with no extras (no discount points, no rent)
    inside = _item(price=55000, area_m2=80)  # 64% under — within trust threshold
    inside_score, _ = score_detail(inside, mode="invest")
    assert inside.get("extreme_discount") is None  # no flag when within threshold
    assert inside_score > base_score               # discount points earned when trustworthy


def test_extreme_discount_reason_says_check_why(monkeypatch):
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "a city series"))
    item = _item(price=10000, area_m2=80)
    _, why = score_detail(item, mode="invest")
    assert any("check why" in r for r in why)


def test_extreme_discount_drops_rent(monkeypatch):
    import prices
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "a city series"))
    monkeypatch.setattr(prices, "rent_per_m2", lambda *a, **k: (8.0, "town"))
    item = _item(price=10000, area_m2=80)
    _, why = score_detail(item, mode="invest")
    assert not any("a year net" in r for r in why)


def test_extreme_discount_does_not_affect_home_score(monkeypatch):
    """Extreme discount is an invest-mode concept — home score is unchanged."""
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "a city series"))
    item = _item(price=10000, area_m2=80)
    home_score, _ = score_detail(item, mode="home")
    assert home_score > 0
    assert "extreme_discount" not in item or item.get("extreme_discount") is None


def test_extreme_discount_visible_in_listing_blocked_from_load_best(db, add, monkeypatch):
    """Extreme-discount items remain visible in the listing tab (researchable/enrichable)
    but are blocked from finalist promotion via load_best (F05 gate)."""
    from db import forget_scores, load_best, load_listings
    # price=10000 area=80 local=2000 → mv=160k, all_in≈11k, gap=93% > 70% → extreme_discount
    add("eleiloes", "x1", title="Moradia T3", tipo="moradia",
        area_m2=80, price=10000, description="moradia em bom estado")
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "city series"))
    forget_scores(db)
    # Must still appear in the listing tab (for research and enrichment).
    visible = load_listings(db, apply_min_score=False, mode="invest", now=NOW)
    assert len(visible) == 1, "extreme-discount item must remain visible for research"
    assert visible[0].get("extreme_discount") is True
    assert visible[0].get("hidden_reason") is None
    # Must NOT appear in load_best (feeds reports, alerts, Offers shortlist).
    best = load_best(db, now=NOW, apply_min_score=False)
    ids = [it["id"] for it in best]
    assert visible[0]["id"] not in ids, "extreme-discount item must not reach finalist path"


def test_cleared_extreme_discount_reaches_load_best(db, add, monkeypatch):
    """After clear_extreme_discount(), the item graduates to the finalist path (F05 clearance)."""
    from db import clear_extreme_discount, forget_scores, load_best, load_listings, revoke_extreme_clearance
    add("eleiloes", "x2", title="Moradia T3", tipo="moradia",
        area_m2=80, price=10000, description="moradia em bom estado")
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "city series"))
    forget_scores(db)
    listing_id = "eleiloes:x2"
    # Before clearance: blocked from load_best
    best_before = {it["id"] for it in load_best(db, now=NOW, apply_min_score=False)}
    assert listing_id not in best_before, "should be blocked before clearance"
    # After clearance: passes the gate
    clear_extreme_discount(db, listing_id, note="price confirmed by court record")
    best_after = {it["id"] for it in load_best(db, now=NOW, apply_min_score=False)}
    assert listing_id in best_after, "cleared item must reach finalist path"
    # Still visible in listing tab with extreme_discount flag (unchanged)
    visible = load_listings(db, apply_min_score=False, mode="invest", now=NOW)
    item = next((it for it in visible if it["id"] == listing_id), None)
    assert item is not None and item.get("extreme_discount") is True
    # Revoking clearance re-blocks the item
    revoke_extreme_clearance(db, listing_id)
    best_revoked = {it["id"] for it in load_best(db, now=NOW, apply_min_score=False)}
    assert listing_id not in best_revoked, "revoked clearance must re-block the item"


def test_clearance_does_not_affect_unflagged_items(db, add, monkeypatch):
    """clear_extreme_discount on an item without the flag has no side effects (F05)."""
    from db import clear_extreme_discount, forget_scores, load_best
    add("eleiloes", "x3", title="Moradia T3", tipo="moradia",
        area_m2=80, price=55000, description="moradia em bom estado")
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "city series"))
    forget_scores(db)
    listing_id = "eleiloes:x3"
    # No extreme_discount flag (55k / 160k mv = 66% gap, under 70% threshold)
    clear_extreme_discount(db, listing_id, note="pre-emptive clearance — no-op")
    best = {it["id"] for it in load_best(db, now=NOW, apply_min_score=False)}
    assert listing_id in best, "non-flagged item must still reach finalist path"


# ─── F10: gallery-change requeue ─────────────────────────────────────────────

def _with_photo_check(urls, shows_house=True, gallery_hash_override=None):
    from photos import _gallery_hash
    urls_str = json.dumps({"fotos": urls, "precision": "sale"})
    pc = {"condition": "good", "confidence": "high", "notes": "", "photos": 2,
          "shows_house": shows_house,
          "gallery_hash": gallery_hash_override if gallery_hash_override is not None
          else _gallery_hash({"raw_json": urls_str}),
          "by": "test", "at": "2026-01-01T00:00:00"}
    return json.dumps({"fotos": urls, "photo_check": pc, "precision": "sale"})


def test_gallery_unchanged_does_not_requeue():
    urls = ["https://example.com/1.jpg", "https://example.com/2.jpg"]
    item = {"tipo": "moradia", "raw_json": _with_photo_check(urls)}
    assert needs_photo_check(item) is False


def test_gallery_grown_triggers_requeue():
    """When new photos appear since the last check, requeue (F10)."""
    old_urls = ["https://example.com/1.jpg"]
    new_urls = ["https://example.com/1.jpg", "https://example.com/2.jpg"]
    # Stored gallery_hash was computed on old_urls
    old_hash = _gallery_hash({"raw_json": json.dumps({"fotos": old_urls})})
    # Item now has new_urls but stored hash is from old_urls
    item = {"tipo": "moradia", "raw_json": _with_photo_check(new_urls, gallery_hash_override=old_hash)}
    assert needs_photo_check(item) is True


def test_shows_house_false_still_requeues():
    urls = ["https://example.com/1.jpg"]
    item = {"tipo": "moradia", "raw_json": _with_photo_check(urls, shows_house=False)}
    assert needs_photo_check(item) is True


def test_no_gallery_hash_in_old_check_does_not_requeue():
    """Old photo checks without gallery_hash are treated as complete (backward compat)."""
    urls = ["https://example.com/1.jpg"]
    pc = {"condition": "good", "confidence": "high", "notes": "",
          "photos": 1, "shows_house": True, "by": "test", "at": "2026-01-01T00:00:00"}
    rj = json.dumps({"fotos": urls, "photo_check": pc})
    item = {"tipo": "moradia", "raw_json": rj}
    assert needs_photo_check(item) is False


def test_check_photos_result_includes_gallery_hash(monkeypatch):
    """check_photos stores gallery_hash so future scans can detect new photos."""
    import photos

    class FakeLooker:
        name = "test"
        model = "test-model"
        def look(self, urls, prompt):
            return {"condition": "good", "confidence": "high",
                    "notes": "looks fine", "shows_house": True}

    urls = ["https://example.com/1.jpg", "https://example.com/2.jpg"]
    item = {"tipo": "moradia", "title": "Casa", "description": "casa",
            "raw_json": json.dumps({"fotos": urls})}
    result = photos.check_photos(FakeLooker(), item)
    assert result is not None
    assert "gallery_hash" in result
    assert result["gallery_hash"] == _gallery_hash(item)
