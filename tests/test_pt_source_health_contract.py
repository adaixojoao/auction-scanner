"""Fixture-backed source health; no live Portuguese-site requests."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

import db as store
import sources.pt as pt
from conftest import FakeResponse, FakeSession
from sources import Source, run_source


@pytest.fixture
def listing_only(monkeypatch):
    # This batch tests discovery, not the independently limited detail pass.
    monkeypatch.setattr(pt, "fetch_eleiloes_details", lambda *a, **k: 0)
    monkeypatch.setattr(pt.time, "sleep", lambda *_: None)


def _lot(number):
    return {"id": number, "tipoId": 1, "subtipoId": 2,
            "titulo": f"Fixture moradia {number}", "valorBase": 20000,
            "referencia": f"FIXTURE{number}"}


def _page(items, total):
    return FakeResponse(json_data={"list": items, "pagination": {"total": total}})


def _eleiloes(monkeypatch, handler):
    session = FakeSession(handler)
    monkeypatch.setattr(pt, "_eleiloes_session", lambda: session)
    return session


def _run(db, name, func):
    return run_source(db, Source(name, "PT", func), max_price=100000)


def _logged(db, name):
    return tuple(db.execute(
        "SELECT status, scope, pages_seen FROM scrape_log WHERE source=? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone())


def test_eleiloes_uses_actual_page_length_and_records_full_coverage(db, monkeypatch, listing_only):
    offsets = []

    def handler(method, url, kwargs):
        params = json.loads(parse_qs(urlsplit(url).query)["tableParams"][0])
        offset = params["first"]
        offsets.append(offset)
        assert params["rows"] == 100
        if offset == 0:
            return _page([_lot(1), _lot(2)], 3)
        assert offset == 2
        return _page([_lot(3)], 3)

    _eleiloes(monkeypatch, handler)
    result = _run(db, "eleiloes", pt.scrape_eleiloes)
    assert (result["count"], result["status"]) == (3, "ok")
    assert offsets == [0, 2]
    assert _logged(db, "eleiloes") == ("ok", "full", 2)
    assert db.execute("SELECT COUNT(*) FROM listings WHERE source='eleiloes'").fetchone()[0] == 3
    assert "eleiloes" in store.last_ok_by_source(db)


@pytest.mark.parametrize("response", [
    FakeResponse(status=503),
    FakeResponse(json_data={"unexpected": []}),
])
def test_eleiloes_first_page_failure_is_error_not_empty(db, monkeypatch, listing_only, response):
    _eleiloes(monkeypatch, lambda *a: response)
    result = _run(db, "eleiloes", pt.scrape_eleiloes)
    assert (result["count"], result["status"]) == (0, "error")
    assert result["message"]
    assert _logged(db, "eleiloes")[:2] == ("error", "partial")
    assert "eleiloes" not in store.last_ok_by_source(db)


@pytest.mark.parametrize("later_response, pages_seen", [
    (FakeResponse(status=503), 1),
    (FakeResponse(json_data={"unexpected": []}), 1),
    (_page([], 2), 2),
])
def test_eleiloes_incomplete_run_keeps_rows_without_advancing_freshness(
        db, monkeypatch, listing_only, later_response, pages_seen):
    def handler(method, url, kwargs):
        offset = json.loads(parse_qs(urlsplit(url).query)["tableParams"][0])["first"]
        return _page([_lot(1)], 2) if offset == 0 else later_response

    _eleiloes(monkeypatch, handler)
    result = _run(db, "eleiloes", pt.scrape_eleiloes)
    assert (result["count"], result["status"]) == (1, "ok")
    assert _logged(db, "eleiloes") == ("ok", "partial", pages_seen)
    assert db.execute("SELECT COUNT(*) FROM listings WHERE source='eleiloes'").fetchone()[0] == 1
    assert "eleiloes" not in store.last_ok_by_source(db)


def test_eleiloes_valid_empty_response_is_not_a_failure(db, monkeypatch, listing_only):
    _eleiloes(monkeypatch, lambda *a: _page([], 0))
    result = _run(db, "eleiloes", pt.scrape_eleiloes)
    assert (result["count"], result["status"]) == (0, "empty")
    assert result["message"] is None
    assert _logged(db, "eleiloes") == ("empty", "full", 1)
    assert "eleiloes" not in store.last_ok_by_source(db)


@pytest.mark.parametrize("response", [
    FakeResponse(status=503),
    FakeResponse(text="<html><body>Fixture unexpected layout</body></html>"),
])
def test_citius_startup_failure_is_error_not_empty(db, monkeypatch, response):
    session = FakeSession(lambda *a: response)
    monkeypatch.setattr(pt, "make_session", lambda *a, **k: session)
    result = _run(db, "citius", pt.scrape_citius)
    assert (result["count"], result["status"]) == (0, "error")
    assert result["message"]
    assert _logged(db, "citius")[:2] == ("error", "partial")
    assert "citius" not in store.last_ok_by_source(db)
