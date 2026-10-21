"""Acquisition pilot: the drawer's Investigation section and the Investigations
queue page, on the production pages (the harness of tests/test_browser_listings.py:
in-process Flask on a temporary database, no network, synthetic data)."""
import json
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


def test_rows_and_cards_say_what_is_unknown_and_what_is_next(app):
    app.open()
    line = harness.row(app, 0).locator('[data-role="investigation-summary"]').text_content()
    assert "unresolved" in line and "next:" in line
    app.page.click('[data-layout="cards"]')
    card = app.page.locator('#listing-cards article[data-id="eleiloes:h0"] [data-role="investigation-summary"]')
    assert card.text_content() == line


def test_recording_a_finding_in_the_drawer_recalculates_it(app):
    app.open()
    panel = open_details(app, 0)
    title = panel.locator('#property-section-investigation [data-dimension="title"]')
    title.locator("summary", has_text="Record what you found").click()
    record = title.locator('.inv-record[data-key="registry"]')
    record.locator(".inv-status").select_option("concern")
    record.locator(".inv-notes").fill("penhora registada")
    record.locator('[data-role="inv-save"]').click()
    app.page.wait_for_selector('#property-section-investigation [data-dimension="title"][data-state="concern"]')
    assert app.page.locator('#property-section-investigation [data-action="resolve_concern"]').count() == 1
    posted = [json.loads(b) for _m, _p, _q, b in app.calls("/api/checklist", "POST")]
    assert posted == [{"id": "eleiloes:h0", "key": "registry", "status": "concern", "notes": "penhora registada",
                       "reference": "", "checked_on": "", "verified_by": ""}]
    app.page.wait_for_function("""() => (document.querySelector('#rows tr[data-id="eleiloes:h0"] [data-role="investigation-summary"]')
        || {}).textContent.includes('concern')""")


def test_investigate_these_carries_the_listings_filters(app):
    app.open("mode=home&search=fixture%201")
    href = app.page.get_attribute("#investigate-view", "href")
    query = parse_qs(urlsplit(href).query)
    assert urlsplit(href).path == "/investigations" and query["mode"] == ["home"]
    assert query["search"] == ["fixture 1"]
    app.page.goto(BASE + href)
    app.page.wait_for_selector("#inv-queue .inv-entry")
    assert "Filtered as on Listings:" in app.text("#inv-filters") and "search fixture 1" in app.text("#inv-filters")
    ids = app.page.locator("#inv-queue .inv-entry").evaluate_all("els => els.map(e => e.dataset.id)")
    assert set(ids) == {"eleiloes:h1", "eleiloes:h10", "eleiloes:h11"}


def test_the_comparison_shows_unresolved_evidence_and_the_next_action(app):
    app.open()
    for n in (0, 1):
        harness.row(app, n).locator('[data-role="compare"]').click()
    app.page.click("#comparison-open")
    app.page.wait_for_function("""() => document.querySelectorAll('#comparison-content thead th[scope=col]').length === 3
        && !document.querySelector('#comparison-content').textContent.includes('Loading…')""")
    rows = app.page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#comparison-content tbody tr')]
        .map(tr => [tr.querySelector('th').textContent, tr.querySelectorAll('td')[0].textContent]))""")
    assert "unresolved" in rows["Unresolved evidence"] and "ready to buy" in rows["Unresolved evidence"]
    assert rows["Next investigation action"] not in ("", "Not established")


def _stage_form(panel):
    section = panel.locator("#property-section-investigation")
    section.locator("summary", has_text="Record a stage or decision").click()
    return section.locator('[data-role="stage-form"]')


def test_recording_a_stage_updates_the_stepper_and_history(app):
    app.open()
    panel = open_details(app, 0)
    form = _stage_form(panel)
    form.locator(".inv-stage").select_option("documents_requested")
    form.locator(".inv-reason").fill("Asked the court for the <b>edital</b>")
    form.locator('[data-role="stage-save"]').click()
    step = '#property-section-investigation .inv-step[data-step="documents_requested"][aria-current="step"]'
    app.page.wait_for_selector(step)
    history = panel.locator("#property-section-investigation .inv-timeline li").first
    assert "Asked the court for the <b>edital</b>" in history.text_content()      # text, never markup
    assert panel.locator("#property-section-investigation .inv-timeline b", has_text="edital").count() == 0
    assert app.page.locator('#property-section-investigation .inv-step[data-state="done"]').count() == 2
    posted = [json.loads(b) for _m, _p, _q, b in app.calls("/api/investigation/stage", "POST")]
    assert posted[0]["expected_previous"] == "" and posted[0]["spend"] is None
    assert not app.calls("/api/listings/status", "POST")                          # no status change
    app.page.wait_for_function("""() => (document.querySelector('#rows tr[data-id="eleiloes:h0"] [data-role="investigation-summary"]')
        || {}).textContent.includes('Documents requested')""")


def test_a_refused_stage_keeps_the_typed_reason_and_says_why(app):
    app.open()
    panel = open_details(app, 0)
    form = _stage_form(panel)
    form.locator(".inv-stage").select_option("screening")
    form.locator(".inv-reason").fill("my draft reason")
    app.client.post("/api/investigation/stage", json={"id": "eleiloes:h0", "stage": "evidence_reviewed",
                                                      "reason_kind": "progress", "reason": "other tab"})
    form.locator('[data-role="stage-save"]').click()
    app.page.wait_for_selector('#property-section-investigation .inv-step[data-step="evidence_reviewed"][aria-current="step"]')
    form = panel.locator('#property-section-investigation [data-role="stage-form"]')
    assert form.locator(".inv-reason").input_value() == "my draft reason"
    assert "changed since this page read it" in form.locator(".inv-form-error").text_content()
    assert len(app.calls("/api/investigation/stage", "POST")) == 1


def test_a_stage_draft_survives_a_details_refresh(app):
    app.open()
    panel = open_details(app, 0)
    form = _stage_form(panel)
    form.locator(".inv-reason").fill("half typed")
    panel.locator('[data-role="refresh-details"]').click()
    app.page.wait_for_function("""() => { const f = document.querySelector('#property-section-investigation .inv-reason');
        return f && f.value === 'half typed'; }""")


def test_the_queue_filters_by_stage(app):
    app.client.post("/api/investigation/stage", json={"id": "eleiloes:h3", "stage": "visit_warranted",
                                                      "reason_kind": "progress", "reason": "worth a visit"})
    app.page.goto(f"{BASE}/investigations?mode=home&stage=visit_warranted")
    app.page.wait_for_selector("#inv-queue .inv-entry")
    entries = app.page.locator("#inv-queue .inv-entry")
    assert entries.count() == 1 and entries.first.get_attribute("data-id") == "eleiloes:h3"
    assert "Visit warranted" in entries.first.text_content()
    app.page.select_option("#inv-stage-filter", "none")
    app.page.wait_for_function("document.querySelectorAll('#inv-queue .inv-entry').length === 11")
    assert parse_qs(urlsplit(app.page.evaluate("location.href")).query)["stage"] == ["none"]


def test_the_drawer_fits_a_phone_width_with_the_stage_form_open(app):
    app.page.set_viewport_size({"width": 390, "height": 900})
    app.open()
    panel = open_details(app, 0)
    _stage_form(panel)
    widths = app.page.evaluate("""() => { const d = document.querySelector('#property-drawer .details-body');
        return [d.scrollWidth, d.clientWidth]; }""")
    assert widths[0] <= widths[1]
