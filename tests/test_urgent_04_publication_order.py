from contextlib import nullcontext

import pytest

import pipeline


def prepare_fake_scan(db, monkeypatch, successful_rescore=True):
    events = []
    def spy(name, result=None):
        def run(*args, **kwargs):
            events.append(name)
            return result
        return run

    monkeypatch.setattr(pipeline, "scan_lock", nullcontext)
    monkeypatch.setattr(pipeline, "_below_other_programs", nullcontext)
    monkeypatch.setattr(pipeline, "configure_http", lambda *a: None)
    monkeypatch.setattr(pipeline, "make_session", lambda *a, **k: object())
    monkeypatch.setattr("sources.load_all", lambda: {})
    monkeypatch.setattr("sources.sources_for", lambda *a, **k: [])
    monkeypatch.setattr("db.mark_duplicates", spy("duplicates", 0))
    monkeypatch.setattr("links.link_court_sales", spy("court-links", 0))
    monkeypatch.setattr(pipeline, "build_report", spy("report", "test-report.md"))
    monkeypatch.setattr(pipeline, "send_alerts", spy("alerts"))
    monkeypatch.setattr("telegram_alert.alert_source_failures", spy("source-alerts"))
    monkeypatch.setattr("outbox.queue_requests", spy("requests", 0))
    monkeypatch.setattr("outcomes.record_results", spy("results"))
    monkeypatch.setattr(pipeline, "enrich_order", lambda *a, **k: [])
    monkeypatch.setattr("geo.locate_towns", spy("towns"))
    monkeypatch.setattr("geo.town_index", lambda *a, **k: {})
    monkeypatch.setattr("geo.geocode_pending", spy("geocode"))
    monkeypatch.setattr("cadastre.locate_pending", spy("cadastre"))
    monkeypatch.setattr("geo.check_water_pending", spy("water"))
    monkeypatch.setattr("outline.check_pending", spy("outline"))
    monkeypatch.setattr("climate.assess_pending", spy("climate"))
    monkeypatch.setattr(pipeline, "_plots_for_site_check", lambda *a: [])
    monkeypatch.setattr("site_check.check_pending", spy("site"))
    monkeypatch.setattr("photos.queue", lambda *a: [])
    monkeypatch.setattr("photos.check_pending", spy("photos"))
    monkeypatch.setattr("confirm.check_top", spy("availability"))
    monkeypatch.setattr(pipeline, "rescore", spy("rescore", successful_rescore))
    monkeypatch.setattr("photos.keep_high", spy("keep-photos"))
    monkeypatch.setattr("dashboard.refresh_cached_lists", spy("refresh"))
    return events


def test_publication_consumes_post_enrichment_rescore(db, monkeypatch):
    events = prepare_fake_scan(db, monkeypatch)
    pipeline.run_scan(db=db, cfg={"max_price": 100000}, report=True, alerts=True)
    for before in ("geocode", "cadastre", "climate", "site", "photos", "availability"):
        assert events.index(before) < events.index("rescore") < events.index("report")
    assert events.index("report") < events.index("alerts") < events.index("requests")
    assert events.count("rescore") == 1


def test_failed_rescore_does_not_publish(db, monkeypatch):
    events = prepare_fake_scan(db, monkeypatch, successful_rescore=False)
    with pytest.raises(RuntimeError, match="rescoring failed"):
        pipeline.run_scan(db=db, cfg={"max_price": 100000}, report=True, alerts=True)
    assert "report" not in events
    assert "alerts" not in events
    assert "requests" not in events
    assert db.execute("SELECT running FROM scan_state WHERE id=1").fetchone()[0] == 0


def test_disabled_publication_remains_disabled(db, monkeypatch):
    events = prepare_fake_scan(db, monkeypatch)
    pipeline.run_scan(db=db, cfg={"max_price": 100000}, report=False, alerts=False)
    assert "availability" in events
    assert "report" not in events
    assert "alerts" not in events
    assert "requests" not in events
