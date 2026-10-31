"""Traps found checking eight "good" photo checks by hand (2026-10-31): a
misspelt "à rénover", a home that is comfortable only "après rénovation", and
a single front photo taken for a sound house."""
import json

import scoring


def _home(text, photo_check=None):
    raw = {"photo_check": photo_check} if photo_check else {}
    return {"title": "Maison", "description": text, "tipo": "maison", "country": "FR",
            "raw_json": json.dumps(raw)}


def test_a_misspelt_a_renover_is_a_renovation():
    assert scoring.condition(_home("Maison à rénovée à Villiers, 3 chambres")) == "heavy"


def test_comfort_after_renovation_needs_work():
    assert scoring.condition(_home("Ce bien, après rénovation, vous apportera le confort")) == "some"


def test_a_done_renovation_is_still_good():
    assert scoring.condition(_home("Maison rénovée en 2013, y compris toiture")) == "good"


def _seen(condition, photos):
    return {"condition": condition, "confidence": "high", "notes": "", "photos": photos, "shows_house": True}


def test_one_photo_cannot_make_a_silent_home_good():
    assert scoring.condition(_home("Moradia T0 para venda", _seen("good", 1))) == "unknown"
    assert scoring.condition(_home("Moradia T0 para venda", _seen("good", 2))) == "good"


def test_one_photo_can_still_show_damage():
    assert scoring.condition(_home("Moradia T0 para venda", _seen("heavy", 1))) == "heavy"
    assert scoring.condition(_home("Moradia T0 para venda", _seen("some", 1))) == "some"
