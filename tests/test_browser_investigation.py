"""Acquisition pilot: the drawer's Investigation section and the Investigations
queue page, on the production pages (the harness of tests/test_browser_listings.py:
in-process Flask on a temporary database, no network, synthetic data)."""
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, open_details = harness.browser, harness.app, harness.open_details
BASE = harness.BASE


def test_the_drawer_shows_unknowns_the_next_action_and_the_brief(app):
    app.open()
    panel = open_details(app, 0)
    section = panel.locator("#property-section-investigation")
    text = section.text_content()
    for heading in ("Opportunity", "Unresolved evidence", "Next action", "Research progress"):
        assert heading in text
    assert "ready to buy" in text and "A high score is not evidence" in text
    assert section.locator('[data-dimension="title"][data-state="unresolved"]').count() == 1
    assert section.locator("[data-action]").count() == 1
    link = section.locator('a[download]')
    href = link.get_attribute("href")
    assert urlsplit(href).path == "/api/investigation/brief.md"
    assert parse_qs(urlsplit(href).query) == {"id": ["eleiloes:h0"], "mode": ["home"]}
    assert panel.locator("#property-sections button", has_text="Investigation").count() == 1


def test_the_queue_page_lists_and_opens_properties(app):
    app.page.goto(f"{BASE}/investigations?mode=home")
    app.page.wait_for_selector("#inv-queue .inv-entry")
    entries = app.page.locator("#inv-queue .inv-entry")
    assert entries.count() == 12
    assert "12 of 12 eligible" in app.text("#inv-status")
    assert "not every opportunity" in app.text("#inv-scope")
    first = entries.first
    assert "Next:" in first.text_content()
    href = first.locator("h2 a").get_attribute("href")
    query = parse_qs(urlsplit(href).query)
    assert query["mode"] == ["home"] and query["property"][0].startswith("eleiloes:h")


def test_a_failed_refresh_keeps_the_list_marked_stale(app):
    app.page.goto(f"{BASE}/investigations?mode=home")
    app.page.wait_for_selector("#inv-queue .inv-entry")
    app.override["/api/investigation-queue"] = lambda q, b: (503, {"error": "Settings changed"})
    app.page.click("#inv-refresh")
    app.page.wait_for_function("document.getElementById('inv-status').textContent.includes('out of date')")
    assert app.page.locator("#inv-queue .inv-entry").count() == 12


def test_an_earlier_answer_never_overwrites_a_later_goal(app):
    app.page.goto(f"{BASE}/investigations?mode=home")
    app.page.wait_for_selector("#inv-queue .inv-entry")
    app.hold.add("/api/investigation-queue")
    app.page.select_option("#inv-mode", "invest")
    app.wait_held("/api/investigation-queue")
    app.page.select_option("#inv-mode", "land")
    app.wait_held("/api/investigation-queue", 2)
    app.release(1)                     # the later choice (land) answers first
    app.page.wait_for_function("document.getElementById('inv-status').textContent.includes('plot')")
    app.release(0)                     # the earlier one (invest) arrives late
    app.page.wait_for_timeout(100)
    assert "plot" in app.text("#inv-status")
    assert parse_qs(urlsplit(app.page.evaluate("location.href")).query)["mode"] == ["land"]
