"""Batch 06: the decision-focused Listings table (built in Batch 4) checked on
the real page against the doc's integration list: the real server's check
states, the badges and wishes the row keeps, the Citius title that opens
details, and a page load that reads no details and nothing outside. Same
harness as tests/test_browser_listings.py: in-process Flask, synthetic data."""
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402
from test_browser_results import _answer, _listing  # noqa: E402

browser, app = harness.browser, harness.app


def _item(db, listing_id):
    from db import load_listings
    return next(it for it in load_listings(db, include_hidden=True) if it["id"] == listing_id)


def test_a_page_load_reads_no_details_and_nothing_outside(app):
    outside = []
    app.page.on("request", lambda r: outside.append(r.url) if urlsplit(r.url).netloc != "app.test" else None)
    app.open()
    assert app.page.locator("#rows tr[data-id]").count() == 12
    assert app.calls("/api/listing") == []                  # summaries come with the page, not per row
    assert len(app.calls("/api/listings")) == 1
    assert outside == []


def test_the_real_server_check_states_put_a_concern_first_and_never_call_old_checks_current(app, add, db):
    import checklist
    add(external_id="concern", title="Moradia concern", price=30000, date_end="2031-01-01T10:00:00+00:00")
    add(external_id="stale", title="Moradia stale", price=30000, date_end="2031-01-01T10:00:00+00:00")
    checklist.set_status(db, _item(db, "eleiloes:concern"), "registry", "concern", notes="synthetic charge")
    stale = _item(db, "eleiloes:stale")
    for key in ("registry", "charges"):
        checklist.set_status(db, stale, key, "verified")
    db.commit()
    add(external_id="stale", title="Moradia stale com penhora", price=30000,      # the notice changed since
        date_end="2031-01-01T10:00:00+00:00")
    app.open("mode=home&search=Moradia")
    first = app.page.locator('#rows tr[data-id="eleiloes:concern"] td.t-checks li').first
    assert "Concern recorded" in first.text_content()
    stale_checks = app.page.locator('#rows tr[data-id="eleiloes:stale"] td.t-checks').text_content()
    assert "Needs rechecking" in stale_checks and "Verified by you" not in stale_checks


def test_badges_wishes_and_excellent_stay_on_the_decision_row(app):
    item = _listing(is_recent=True, offer_outcome="sent", price_drop_pct=12.4, climate_grade="A",
                    excellent=["sea within 1.5 km"],
                    wishes=[{"label": "Airport within an hour", "state": "yes", "text": "52 min"}])
    app.override["/api/listings"] = _answer([item])
    app.open("mode=home")
    tr = app.page.locator('#rows tr[data-id="x:1"]')
    text = tr.text_content()
    for badge in ("NEW", "offer sent", "↓12%", "climate A", "★ Excellent", "Airport ✓"):
        assert badge in text, badge
    assert tr.locator('[data-role="star"]').get_attribute("aria-pressed") == "false"


def test_a_citius_title_opens_its_details_and_leaves_the_shortlist_alone(app, add):
    add("citius", "c1", title="Penhora fixture casa", price=25000, url="https://example.test/citius",
        date_end="2031-01-01T10:00:00+00:00")
    app.open("mode=home&search=Penhora")
    tr = app.page.locator('#rows tr[data-id="citius:c1"]')
    title = tr.locator('[data-role="title"]')
    assert title.get_attribute("href") == "#"                 # no page per sale: never an outside link
    title.click()
    app.page.wait_for_selector('#property-drawer[open][data-for="citius:c1"] textarea.d-note')
    assert urlsplit(app.page.url).path == "/"
    assert "How to find this sale on" in app.text("#property-drawer")
    assert title.get_attribute("aria-expanded") == "true"
    assert tr.locator('[data-role="details"]').get_attribute("aria-expanded") == "true"
    assert tr.locator('[data-role="star"]').get_attribute("aria-pressed") == "false"
    app.page.keyboard.press("Escape")                          # closing gives focus back to the title
    app.page.wait_for_function("() => !document.getElementById('property-drawer').open")
    # The triggers and the focus settle on the close event, a task after the drawer shuts.
    app.page.wait_for_function("""() => document.querySelector('#rows tr[data-id="citius:c1"] [data-role="details"]')
        .getAttribute('aria-expanded') === 'false'""")
    assert app.page.evaluate("document.activeElement.dataset.role") == "title"
