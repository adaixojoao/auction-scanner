"""Batch 4: the decision-focused Listings table in a real browser (the harness
of tests/test_browser_listings.py: in-process Flask, no network, synthetic
data). Checked by what the page does, not by its source text."""
import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

# The same browser and page harness as tests/test_browser_listings.py.
browser, app, _a_research_lead = harness.browser, harness.app, harness._a_research_lead


def _listing(**fields):
    """One /api/listings row as the server sends it, with its presentation summary."""
    base = {"id": "x:1", "title": "Fixture home", "source": "eleiloes", "country": "PT", "url": "",
            "status": "", "kind": "home", "score": 50, "rank": 50, "excellent": False,
            "reasons": ["first reason", "second reason"], "hidden_reason": None, "detail_token": "t",
            "presentation": {
                "price": {"amount": 30000, "kind": "current_bid", "label": "Current bid",
                          "note": "Not a final purchase price: bidding may go higher.", "secondary": []},
                "all_in": {"low": None, "high": None, "known_subtotal": 32000, "repairs_priced": False,
                           "basis_label": "Estimate at the current bid", "headline": "",
                           "missing": ["Repairs not priced: floor area not established"],
                           "excludes": ["fire insurance"]},
                "checks": {"items": [{"area": "title", "label": "Title and charges", "state": "unverified",
                                      "text": "No verification recorded"}], "concerns": 0},
                "deadline": {"precision": "date", "date": "2031-03-04", "time": None,
                             "timezone": "Europe/Lisbon", "instant": None, "days": 30},
                "thumbnail": None}}
    return {**base, **fields}


def _answer(items, research=None):
    def listings(q, b):
        return 200, {"items": items, "total": len(items), "found": len(items), "cap": 100,
                     "matched_total": len(items), "display_total": len(items), "effective_page": 1, "page": 1,
                     "stats": {"total": len(items), "new_today": 0, "shortlisted": 0, "sources_failing": 2,
                               "sources_quiet": 1, "hidden": {}},
                     "cash": {"set": True, "amount": 100000}, "last_scrape": "2030-01-01", "research": research}
    return listings


def test_seven_columns_and_every_full_width_row_spans_them(app):
    app.open()
    assert app.page.locator("table.list thead th").count() == 7
    first = app.page.locator("#rows tr[data-id]").first
    assert first.locator("td").count() == 7
    first.locator('[data-role="details"]').click()
    assert app.page.get_attribute("#rows tr.details-row td", "colspan") == "7"
    app.page.fill("#f-search", "nothing matches this")
    app.page.press("#f-search", "Enter")
    app.idle()
    assert app.page.get_attribute("#rows td", "colspan") == "7"


def test_every_sort_is_reachable_and_follows_the_address(app):
    app.open()
    app.page.select_option("#sort-by", "current_bid")
    app.idle()
    assert "sort=current_bid" in app.page.url
    app.page.select_option("#sort-dir", "asc")
    app.idle()
    assert "dir=asc" in app.page.url
    app.page.click('th[data-sort="date_end"] .sort-button')
    app.idle()
    assert app.page.input_value("#sort-by") == "date_end"
    assert app.page.get_attribute('th[data-sort="date_end"]', "aria-sort") in ("ascending", "descending")
    app.page.go_back()
    app.idle()
    assert app.page.input_value("#sort-by") == "current_bid" and app.page.input_value("#sort-dir") == "asc"


def test_the_real_server_summaries_label_the_price_and_keep_a_date_a_date(app, add):
    add(external_id="bid", title="Moradia lance", price=90000, min_price=60000, current_bid=65000,
        date_end="2031-02-03")
    app.open("mode=home&search=lance")
    cells = row_cells(app, "eleiloes:bid")
    assert "Current bid" in cells["price"] and "Not a final purchase price" in cells["price"]
    assert "Minimum offer" in cells["price"] and "Base value" in cells["price"]
    assert "Estimated" in cells["allin"]
    assert "closing time not published" in cells["deadline"]
    assert "00:00" not in cells["deadline"] and "23:59" not in cells["deadline"]
    assert "Title and charges" in cells["checks"] and "Verified" not in cells["checks"]


def row_cells(app, listing_id):
    tr = app.page.locator(f'#rows tr[data-id="{listing_id}"]')
    return {name: tr.locator(f"td.t-{name}").text_content() for name in ("price", "allin", "why", "checks", "deadline")}


def test_a_broken_thumbnail_becomes_the_neutral_fallback(app):
    item = _listing(presentation={**_listing()["presentation"], "thumbnail": "/broken-image.jpg"})
    app.override["/api/listings"] = _answer([item, _listing(id="x:2")])
    app.open()
    app.page.wait_for_selector('#rows tr[data-id="x:1"] .result-thumbnail-fallback')
    assert app.page.locator('#rows tr[data-id="x:1"] img').count() == 0
    assert app.page.text_content('#rows tr[data-id="x:2"] .result-thumbnail-fallback') == "No image"


def test_reasons_titles_and_check_labels_stay_text(app):
    hostile = '<img src=x onerror="window.pwned=1">'
    item = _listing(title=hostile * 20, reasons=["a", "b", hostile, "d"],
                    presentation={**_listing()["presentation"],
                                  "checks": {"items": [{"area": "title", "label": hostile, "state": "concern",
                                                        "text": hostile}], "concerns": 1}})
    app.override["/api/listings"] = _answer([item])
    app.open()
    tr = app.page.locator('#rows tr[data-id="x:1"]')
    assert tr.locator("td.t-why details summary").text_content() == "2 more reasons"
    tr.locator("td.t-why details summary").click()
    assert hostile in tr.locator("td.t-why details").text_content()
    assert hostile in tr.locator("td.t-checks").text_content()
    assert tr.locator("td.t-why img, td.t-checks img, .result-title img").count() == 0
    assert not app.page.evaluate("window.pwned === 1")


def test_compact_changes_only_the_presentation(app):
    app.override["/api/listings"] = _answer([_listing(), _listing(id="x:2")])
    app.open()
    reads = len(app.calls("/api/listings"))
    app.page.click('[data-density="compact"]')
    assert "results-density-compact" in app.page.get_attribute("#listings-results", "class")
    assert app.page.get_attribute('[data-density="compact"]', "aria-pressed") == "true"
    assert len(app.calls("/api/listings")) == reads                     # no new read, same rows
    assert app.page.locator("#rows tr[data-id]").count() == 2
    for text in ("Incomplete cost estimate", "Repairs not priced", "Current bid", "closing time not published",
                 "No verification recorded"):
        assert app.page.locator(f'#rows tr[data-id="x:1"] :text("{text}")').first.is_visible(), text
    app.page.reload()
    app.page.wait_for_selector("#rows tr[data-id]")
    assert "results-density-compact" in app.page.get_attribute("#listings-results", "class")


def test_a_density_that_cannot_be_stored_still_switches(app):
    app.page.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }})")
    app.override["/api/listings"] = _answer([_listing()])
    app.open()
    app.page.click('[data-density="compact"]')
    assert "results-density-compact" in app.page.get_attribute("#listings-results", "class")


def test_three_counters_and_source_health_folded_away(app):
    app.override["/api/listings"] = _answer([_listing()])
    app.open()
    labels = app.page.locator("#stats .stat .label").all_text_contents()
    assert labels == ["Matching", "New (24 h)", "On Offers"]
    summary = app.page.locator("#stats-more details.source-health summary")
    assert "2 failing" in summary.text_content() and "1 with no good read" in summary.text_content()
    assert not app.page.locator("#stats-more details.source-health a[href='/sources']").is_visible()
    summary.click()
    assert app.page.locator("#stats-more details.source-health a[href='/sources']").is_visible()


def test_goal_intro_is_short_and_the_full_explanation_expands(app):
    app.open("mode=forest")
    assert app.text("#mode-intro") == "Land for a forestry project."
    assert not app.page.is_visible("#mode-note")
    app.page.click(".mode-details summary")
    assert "10 ha is the project" in app.text("#mode-note")


def test_research_warning_stays_visible_collapsed_and_drafts_survive(app, add, monkeypatch):
    _a_research_lead(monkeypatch)
    add("eleiloes", "lead", title="Moradia T3", price=22000, area_m2=90, description="moradia em bom estado",
        url="https://example.test/lead", date_end="2031-01-01T10:00:00+00:00",
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    app.open("mode=both&min_score=0")
    summary = app.page.locator("#research-details > summary")
    assert "1 candidate" in summary.text_content()
    card = app.page.locator("#research-candidates article.research-card").first
    card.locator('[data-role="details"]').click()
    field = card.locator(".candidate-details textarea.d-note")
    field.wait_for()
    app.hold.add("/api/listing/note")
    field.click()
    field.type("draft kept")
    summary.click()                                                   # collapse
    assert app.page.locator("#research-details .research-warning").is_visible()
    assert "Not verified as ready to buy" in app.text("#research-details .research-warning")
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    if app.page.is_visible("#listings-refresh"):
        app.page.click("#listings-refresh")
    app.idle()
    assert app.page.get_attribute("#research-details", "open") is None     # stays as you left it
    app.page.locator("#research-details > summary").click()
    again = app.page.locator("#research-candidates article.research-card .candidate-details textarea.d-note")
    again.wait_for()
    assert again.input_value() == "draft kept"
    app.hold.clear()
    while app.held:
        app.release()
