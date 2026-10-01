"""Source validation harness (Module 7): catalog, blocked status, fixtures,
maintenance priorities and the Validate API."""
from __future__ import annotations

import json

import pytest

import dashboard
import source_validation
from db import record_scrape, source_health
from sources import REGISTRY, SourceUnavailable, load_all, run_source


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "proponente": {"nome": "Test Person", "nif": "123",
                                                      "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_catalog_covers_every_registered_source():
    load_all()
    missing = [n for n in REGISTRY if n not in source_validation.CATALOG]
    assert not missing, f"add CATALOG entries for {missing}"
    for name, meta in source_validation.CATALOG.items():
        assert meta["kind"] in source_validation.KINDS, name
        assert meta["access"] in source_validation.ACCESS, name


def test_source_unavailable_is_blocked_not_error(db):
    load_all()
    result = run_source(db, REGISTRY["novobanco"], max_price=100000)
    assert result["status"] == "blocked"
    assert "no longer exists" in result["message"]
    health = {h["source"]: h for h in source_health(db, REGISTRY)}
    assert health["novobanco"]["state"] == "blocked"


def test_sareb_bot_wall_is_blocked(db, fake_http):
    from conftest import FakeResponse
    wall = '<html><head><script src="/_Incapsula_Resource?SWJIYLWA=1"></script></head></html>'
    fake_http(lambda m, url, kw: FakeResponse(wall))
    result = run_source(db, REGISTRY["sareb"], max_price=100000)
    assert result["status"] == "blocked" and "Incapsula" in result["message"]


def test_health_transitions_include_blocked(db):
    record_scrape(db, "good", count=5, status="ok")
    record_scrape(db, "wall", count=0, status="blocked", message="bot check")
    record_scrape(db, "was_good", count=3, status="ok", timestamp="2026-01-01T00:00:00+00:00")
    record_scrape(db, "was_good", count=0, status="empty")
    health = {h["source"]: h for h in source_health(db, known_sources=["wall", "good", "was_good"])}
    assert health["good"]["state"] == "ok"
    assert health["wall"]["state"] == "blocked"
    assert health["was_good"]["state"] == "broken"


def test_enrich_health_adds_catalog_and_detail(db):
    load_all()
    record_scrape(db, "eleiloes", count=2, status="ok")
    row = next(h for h in source_health(db, REGISTRY) if h["source"] == "eleiloes")
    enriched = source_validation.enrich_health_row(row, REGISTRY["eleiloes"])
    assert enriched["kind"] == "official" and enriched["access"] == "public_api"
    assert enriched["has_fixture"] is True
    assert "listings" in enriched["detail"].lower() or enriched["detail"]


def test_eleiloes_fixture(db, fake_http):
    result = source_validation.run_fixture(db, "eleiloes", fake_http=fake_http)
    assert result["ok"], result
    assert result["count"] == 2
    assert "eleiloes:101" in result["ids"]
    # Vehicle at €900 must not be kept (reject_prices / not property).
    assert "eleiloes:103" not in result["ids"]
    assert not result["fee_prices_found"]


def test_fixture_rejects_fee_as_price(db, fake_http, tmp_path, monkeypatch):
    """A fixture that keeps a deposit amount as price must fail the check."""
    from conftest import FakeResponse
    from common import make_listing
    from db import upsert_listing
    from sources import Source

    monkeypatch.setattr(source_validation, "_FIXTURES_DIR", tmp_path)
    (tmp_path / "toy.json").write_text(json.dumps({
        "source": "toy",
        "expect_count": 1,
        "reject_prices": [500],
        "responses": [{"match": "example", "file": "toy_page.json"}],
    }), encoding="utf-8")
    (tmp_path / "toy_page.json").write_text("{}", encoding="utf-8")

    def scrape_toy(db, max_price=100000, **_kw):
        upsert_listing(db, make_listing("toy", "1", "PT", title="X", price=500,
                                        url="https://example.com/1"))
        return 1

    reg = {"toy": Source("toy", "PT", scrape_toy)}
    fake_http(lambda m, u, kw: FakeResponse("{}", json_data={}))
    result = source_validation.run_fixture(db, "toy", fake_http=fake_http, registry=reg)
    assert result["ok"] is False and result["fee_prices_found"]


def test_maintenance_prioritises_useful_but_degraded(db, add):
    load_all()
    # A source that used to yield listings, then broke.
    add(source="bcp", external_id="1", title="Casa", price=20000, country="PT")
    record_scrape(db, "bcp", count=5, status="ok", timestamp="2026-01-01T00:00:00+00:00")
    record_scrape(db, "bcp", count=0, status="empty")
    # Expected blocked with no history ranks lower.
    record_scrape(db, "novobanco", count=0, status="blocked", message="gone")
    rows = source_validation.maintenance_report(db, REGISTRY)
    by = {r["source"]: r for r in rows}
    assert "bcp" in by and by["bcp"]["state"] == "broken"
    assert by["bcp"]["priority"] > by.get("novobanco", {"priority": -1})["priority"]


def test_validate_api_blocked_skips_live(client, db):
    load_all()
    r = client.post("/api/sources/novobanco/validate", json={"live": True})
    assert r.status_code == 200
    body = r.get_json()
    assert body["live"]["skipped"] is True
    assert body["meta"]["access"] == "blocked"


def test_validate_api_unknown_source(client):
    assert client.post("/api/sources/nope/validate", json={}).status_code == 400


def test_maintenance_api(client, db):
    data = client.get("/api/sources/maintenance").get_json()
    assert "priorities" in data and "generated_at" in data


def test_classify_scrape_status():
    assert source_validation.classify_scrape_status(None, 3) == "ok"
    assert source_validation.classify_scrape_status(None, 0) == "empty"
    assert source_validation.classify_scrape_status(SourceUnavailable("wall"), 0) == "blocked"
    assert source_validation.classify_scrape_status(RuntimeError("x"), 0) == "error"


def test_health_api_includes_catalog(client, db):
    health = {h["source"]: h for h in client.get("/api/health").get_json()}
    assert health["eleiloes"]["kind"] == "official"
    assert health["eleiloes"]["access"] == "public_api"
    assert health["eleiloes"]["has_fixture"] is True
    assert health["sareb"]["access"] == "blocked"
