"""Germany and west of it by default: other countries are neither scanned nor
scored unless filters.countries names them ([] is every country)."""
import config
import pipeline
from db import load_listings, set_listing_status
from scoring import WESTERN_CONTINENTAL_COUNTRIES


def test_the_default_countries_are_western_continental_europe():
    assert set(config.DEFAULTS["filters"]["countries"]) == WESTERN_CONTINENTAL_COUNTRIES


def test_other_countries_are_not_read_even_with_hidden_ones(db, add):
    add("eleiloes", "pt", country="PT", title="Moradia", price=20000)
    add("fina", "hr", country="HR", title="Kuća", price=20000)
    filters = {"countries": ["PT", "DE"]}
    got = {it["id"] for it in load_listings(db, filters=filters, include_hidden=True, apply_min_score=False)}
    assert got == {"eleiloes:pt"}
    every = {it["id"] for it in load_listings(db, filters={"countries": []}, include_hidden=True,
                                              apply_min_score=False)}
    assert every == {"eleiloes:pt", "fina:hr"}


def test_a_shortlisted_listing_abroad_stays(db, add):
    add("fina", "hr", country="HR", title="Kuća", price=20000)
    set_listing_status(db, "fina:hr", "shortlisted")
    got = {it["id"] for it in load_listings(db, filters={"countries": ["PT"]}, include_hidden=True,
                                            apply_min_score=False)}
    assert got == {"fina:hr"}


def test_a_scan_covers_only_the_chosen_countries(db, monkeypatch):
    ran = []
    monkeypatch.setattr("sources.run_source", lambda db, s, **kw: ran.append(s.country) or {"count": 0, "status": "empty"})
    cfg = {**config.DEFAULTS, "filters": {"countries": ["PT", "DE"]}}
    pipeline.run_scan(cfg=cfg, report=False, alerts=False, db=db)
    assert ran and set(ran) <= {"PT", "DE"}
    ran.clear()
    pipeline.run_scan(["HR", "DE"], cfg=cfg, report=False, alerts=False, db=db)
    assert set(ran) == {"DE"}
