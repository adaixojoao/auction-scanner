"""Batch 10: one synthetic research session through every Listings feature of
the ten batches, on the production page (the harness of
tests/test_browser_listings.py: in-process Flask on a temporary database, every
request answered in the test, no network, synthetic data, no offer or email).

filters → saved search → table, cards and map → comparison → the shared
drawer → a note that fails to save and is retried → shortlist, dismiss and
Undo → a refresh after the deadline has passed."""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402
import test_browser_map as maps  # noqa: E402

browser, app, row = harness.browser, harness.app, harness.row


def test_a_research_session_end_to_end(app, add, db):
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    add(external_id="w1", title="Moradia workflow", price=20500, raw_json={"lat": 38.71, "lon": -9.14},
        date_end=soon)
    maps._with_leaflet(app)
    app.open()
    population = sorted(maps._ids(app, "#rows tr[data-id]"))

    # Filters, then a saved search of them.
    app.page.fill("#f-search", "Moradia")
    app.page.keyboard.press("Enter")
    app.idle()
    filtered = sorted(maps._ids(app, "#rows tr[data-id]"))
    assert filtered and set(filtered) <= set(population)
    app.page.click("#saved-search-create")
    app.page.wait_for_selector("#saved-search-name-dialog[open]")
    app.page.fill("#saved-search-name", "Workflow")
    app.page.click("#saved-search-name-ok")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")

    # Table, cards and map show the same page.
    app.page.click('[data-layout="cards"]')
    assert sorted(maps._ids(app, "#listing-cards article.listing-card")) == filtered
    app.page.click('[data-layout="table"]')
    maps._show(app)
    mapped = maps._ids(app, "#listings-map-located li") + maps._ids(app, "#listings-map-unlocated li")
    assert sorted(mapped) == filtered

    # Compare two, one of them from the map list.
    app.page.click("#listings-map-located-summary")
    app.page.click('#listings-map-located [data-role="compare"][data-id="eleiloes:w1"]')
    row(app, 1).locator('[data-role="compare"]').click()
    app.page.click("#comparison-open")
    app.page.wait_for_selector("#comparison-dialog[open]")
    app.page.wait_for_function("() => !document.querySelector('#comparison-content').textContent.includes('Loading…')")
    assert "Research comparison only" in app.text("#comparison-dialog")

    # Comparison → the shared drawer → a note that fails, then is retried.
    app.page.click('#comparison-content [data-role="compare-open"][data-id="eleiloes:w1"]')
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:w1"] textarea.d-note')
    phase = "() => document.querySelector('#property-drawer .note-phase').textContent"
    app.override["/api/listing/note"] = lambda q, b: (500, {"error": "disk full"})
    note = app.page.locator("#property-drawer textarea.d-note")
    note.click()
    note.type("check the roof")
    note.press("Tab")
    app.page.wait_for_function(f"({phase})() === 'Could not save'")
    assert note.input_value() == "check the roof"                       # no draft loss
    del app.override["/api/listing/note"]
    app.page.locator("#property-drawer .note-retry").click()
    app.page.wait_for_function(f"({phase})() === 'Saved'")
    assert json.loads(app.calls("/api/listing/note", "POST")[-1][3])["note"] == "check the roof"

    # Shortlist from the drawer, then dismiss another row and Undo it.
    app.page.click("#property-drawer-star")
    app.page.wait_for_function("() => document.getElementById('property-drawer-star').getAttribute('aria-pressed') === 'true'")
    harness.close_details(app)
    assert app.page.evaluate("document.activeElement.id") == "comparison-open"
    row(app, 2).locator('[data-role="dismiss"]').click()
    app.page.select_option("#dismiss-reason", "occupied")
    app.page.click("#dismiss-ok")
    app.idle()
    app.page.wait_for_selector("#status-undo", state="visible")
    app.page.click("#status-undo-button")
    row(app, 2).wait_for()
    app.idle()
    assert sorted(maps._ids(app, "#rows tr[data-id]")) == filtered     # the population is unchanged
    assert len(app.page.locator("#comparison-selections li").all_text_contents()) == 2

    # The deadline passes and a refresh drops what has closed; the map follows the same page.
    db.execute("UPDATE listings SET date_end = ? WHERE id = 'eleiloes:w1'",
               ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"),))
    db.commit()
    app.page.evaluate("load()")
    app.idle()
    now = sorted(maps._ids(app, "#rows tr[data-id]"))
    mapped = maps._ids(app, "#listings-map-located li") + maps._ids(app, "#listings-map-unlocated li")
    assert sorted(mapped) == now
    # The selection says so instead of dropping it, and nothing was sent anywhere else.
    assert "eleiloes:w1" not in now and "eleiloes:w1" not in mapped
    assert "(out of current results)" in app.text("#comparison-selections")
    stored = app.client.get("/api/listing?id=eleiloes:w1").get_json()
    assert stored["user_note"] == "check the roof"
    assert not [u for u in app.foreign if "unpkg.com" not in u]
    assert not app.calls("/api/offers/sent", "POST")
