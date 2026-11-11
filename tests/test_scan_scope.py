"""A scrape that stopped early must not make the listings it never reached
look gone. The shared scrapers say how far they got (ScanCount.scope), and
only a full scrape moves the freshness clock that hides stale listings."""
from datetime import datetime, timedelta, timezone

import pytest

import db as store
import sources._cards as cards
import sources._market as market
from conftest import FakeResponse, FakeSession
from sources import ScanCount, Source, run_source
from sources._cards import CardSite, scrape_cards

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _cards_page(start, n):
    return "".join(f'<div class="card"><a href="/imovel/{i}">Moradia {i}</a><h3>Moradia {i}</h3>'
                   f'<span>20.000 €</span></div>' for i in range(start, start + n))


def _site(**kw):
    return CardSite(source="cardtest", country="PT", base="https://t.example", path="/l",
                    card_selector="div.card", min_cards=3, delay=0, **kw)


def _serve(monkeypatch, module, handler):
    session = FakeSession(handler)
    monkeypatch.setattr(module, "make_session", lambda **k: session)
    return session


def test_scancount_is_a_count():
    result = ScanCount(5, "partial", 2)
    assert result == 5 and result + 1 == 6 and isinstance(result, int)
    assert (result.scope, result.pages_seen) == ("partial", 2)
    assert ScanCount(3).scope == "full"
    with pytest.raises(ValueError):
        ScanCount(1, "unknown")


def test_cards_reaching_the_last_page_are_full(db, monkeypatch):
    pages = {1: _cards_page(1, 3), 2: _cards_page(4, 1)}            # page 2 is short: the end
    _serve(monkeypatch, cards, lambda m, u, kw: FakeResponse(pages[kw["params"]["page"]]))
    result = scrape_cards(db, _site(), max_price=100000)
    assert result == 4 and result.scope == "full" and result.pages_seen == 2


def test_cards_with_an_empty_last_page_are_full(db, monkeypatch):
    pages = {1: _cards_page(1, 3), 2: "<p>no results</p>"}
    _serve(monkeypatch, cards, lambda m, u, kw: FakeResponse(pages[kw["params"]["page"]]))
    result = scrape_cards(db, _site(), max_price=100000)
    assert result == 3 and result.scope == "full"


def test_cards_with_a_failed_later_page_are_partial(db, monkeypatch):
    def handler(m, u, kw):
        page = kw["params"]["page"]
        return FakeResponse(_cards_page(1, 3)) if page == 1 else FakeResponse("", status=503)
    _serve(monkeypatch, cards, handler)
    result = scrape_cards(db, _site(), max_price=100000)
    assert result == 3 and result.scope == "partial" and result.pages_seen == 1


def test_cards_that_run_out_of_pages_are_partial(db, monkeypatch):
    _serve(monkeypatch, cards, lambda m, u, kw: FakeResponse(_cards_page(kw["params"]["page"] * 10, 3)))
    result = scrape_cards(db, _site(max_pages=2), max_price=100000)
    assert result == 6 and result.scope == "partial" and result.pages_seen == 2


def test_single_page_card_site_is_full(db, monkeypatch):
    _serve(monkeypatch, cards, lambda m, u, kw: FakeResponse(_cards_page(1, 5)))
    result = scrape_cards(db, _site(page_param=None), max_price=100000)
    assert result == 5 and result.scope == "full"


def test_a_failed_first_card_page_still_raises(db, monkeypatch):
    _serve(monkeypatch, cards, lambda m, u, kw: FakeResponse("", status=500))
    with pytest.raises(Exception):
        scrape_cards(db, _site(), max_price=100000)


def _portal(monkeypatch, urls):
    portal = market.Portal(name="mkttest", country="PT", base="https://m.example",
                           urls=tuple(urls), href_re="", id_re="")
    monkeypatch.setitem(market.PORTALS, "mkttest", portal)
    monkeypatch.setitem(market.PARSERS, "links", lambda html, p: [
        {"external_id": html, "price": 10000, "title": "Moradia", "description": "", "tipo": "imovel",
         "area_m2": None, "district": None, "concelho": None, "freguesia": None,
         "url": f"https://m.example/{html}", "image_url": None}] if html else [])
    monkeypatch.setattr(market.time, "sleep", lambda s: None)


def test_market_portal_url_list_is_not_the_whole_site(db, monkeypatch):
    _portal(monkeypatch, ["https://m.example/a", "https://m.example/b"])
    _serve(monkeypatch, market, lambda m, u, kw: FakeResponse(u.rsplit("/", 1)[1]))
    result = market.scrape_named(db, "mkttest", 100000)
    assert result == 2 and result.scope == "partial" and result.pages_seen == 2


def test_market_portal_with_a_skipped_page_is_partial(db, monkeypatch):
    _portal(monkeypatch, ["https://m.example/a", "https://m.example/b"])
    _serve(monkeypatch, market, lambda m, u, kw:
           FakeResponse("a") if u.endswith("/a") else FakeResponse("", status=503))
    result = market.scrape_named(db, "mkttest", 100000)
    assert result == 1 and result.scope == "partial" and result.pages_seen == 1


def test_run_source_records_the_scope_a_scancount_reports(db):
    run_source(db, Source("scopetest", "PT", lambda *a, **k: ScanCount(4, "partial", 1)), max_price=1)
    row = db.execute("SELECT scope, pages_seen FROM scrape_log WHERE source='scopetest'").fetchone()
    assert (row["scope"], row["pages_seen"]) == ("partial", 1)
    run_source(db, Source("plainint", "PT", lambda *a, **k: 4), max_price=1)
    assert db.execute("SELECT scope FROM scrape_log WHERE source='plainint'").fetchone()[0] == "full"


def _stale_setup(db, add):
    add("cardtest", "unseen", title="Moradia", price=20000, concelho="Moura")
    old_seen = (NOW - timedelta(days=10)).isoformat()
    db.execute("UPDATE listings SET last_seen=? WHERE external_id='unseen'", (old_seen,))
    db.commit()


def _hidden_reason(db):
    rows = store.load_listings(db, include_hidden=True, now=NOW)
    return next(it for it in rows if it["external_id"] == "unseen").get("hidden_reason") or ""


def test_partial_scrape_does_not_make_unseen_listings_stale(db, add):
    _stale_setup(db, add)
    store.record_scrape(db, "cardtest", count=3, status="ok", scope="partial", timestamp=NOW.isoformat())
    assert not _hidden_reason(db).startswith("stale:")


def test_full_scrape_still_makes_unseen_listings_stale(db, add):
    _stale_setup(db, add)
    store.record_scrape(db, "cardtest", count=3, status="ok", scope="full", timestamp=NOW.isoformat())
    assert _hidden_reason(db).startswith("stale:")


def test_spain_is_partial_when_a_later_page_fails(db, fake_http):
    from sources.es import scrape_spain
    first = ('<a href="?id_busqueda=abc">next</a>'
             '<div class="paginar">Resultados de 100</div>')
    fake_http(lambda m, u, kw: FakeResponse("", status=500) if "id_busqueda" in u
              else FakeResponse(first if m == "POST" else "<html></html>"))
    result = scrape_spain(db, max_price=100000)
    assert result == 0 and result.scope == "partial" and result.pages_seen == 1


def test_spain_is_partial_when_the_page_cap_stops_it(db, fake_http):
    from sources.es import scrape_spain
    card = '<div id="contenido"><h3>SUBASTA SUB-JA-2024-1</h3></div>'
    first = ('<a href="?id_busqueda=abc">next</a>'
             '<div class="paginar">Resultados de 5000</div>' + card)
    fake_http(lambda m, u, kw: FakeResponse(first if m == "POST" else card))
    result = scrape_spain(db, max_price=100000)
    assert result.scope == "partial" and result.pages_seen == 20


def test_france_is_partial_when_a_tribunal_page_fails(db, fake_http):
    from sources.fr import scrape_france
    index = ('<a href="/ventes-judiciaires-immobilieres/paris.html">Paris</a>'
             '<a href="/ventes-judiciaires-immobilieres/lyon.html">Lyon</a>')
    sale = '<a href="/annonce/12345.html">59 Lille Une maison d habitation de 80 m2</a>'

    def handler(method, url, kw):
        if url.endswith("france.html"):
            return FakeResponse(index)
        if url.endswith("paris.html"):
            return FakeResponse(sale)
        if url.endswith("lyon.html"):
            return FakeResponse("", status=500)
        return FakeResponse("<html></html>")

    fake_http(handler)
    result = scrape_france(db, max_price=100000)
    assert result == 1 and result.scope == "partial" and result.pages_seen == 1


def test_france_is_partial_when_the_tribunal_cap_stops_it(db, fake_http):
    from sources.fr import scrape_france
    links = "".join(
        f'<a href="/ventes-judiciaires-immobilieres/t{i}.html">T{i}</a>' for i in range(61))
    fake_http(lambda m, u, kw: FakeResponse(links if u.endswith("france.html") else "<html></html>"))
    result = scrape_france(db, max_price=100000)
    assert result == 0 and result.scope == "partial" and result.pages_seen == 60
