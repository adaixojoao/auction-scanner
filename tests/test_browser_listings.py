"""Batch 3.1: the Listings page in a real browser (headless Chromium through
Playwright), checked by what the page does rather than by its source text.

Every request the page makes is answered in-process by the Flask app on a
temporary database: no server, no socket, no network. A test can hold a
response and release it later, to check what happens when answers arrive
late or out of order. Synthetic data only.

Playwright is a dev-only dependency (requirements-browser.txt) installed by
the CI "browser" job; elsewhere these tests are skipped. Set REQUIRE_BROWSER=1
to make a missing browser a failure instead of a skip.
"""
import json
import os
from urllib.parse import parse_qs, urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import dashboard  # noqa: E402

BASE = "http://app.test"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            exe = os.environ.get("PLAYWRIGHT_CHROMIUM") or None
            launched = pw.chromium.launch(executable_path=exe)
        except Exception as e:  # noqa: BLE001 — no browser on this machine
            if os.environ.get("REQUIRE_BROWSER"):
                raise
            pytest.skip(f"no Chromium for Playwright ({type(e).__name__})")
        yield launched
        launched.close()


class App:
    """The page, its requests, and the Flask app answering them."""

    def __init__(self, page, client):
        self.page, self.client = page, client
        self.hold = set()          # paths whose answers wait for release()
        self.held = []
        self.override = {}         # path -> function(query, body) -> (status, payload)
        self.log = []              # (method, path, query dict, body)
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.route("**/*", self._handle)

    def _handle(self, route):
        req = route.request
        url = urlsplit(req.url)
        if url.netloc != "app.test":
            route.fulfill(status=404, body="")
            return
        body = req.post_data
        self.log.append((req.method, url.path, parse_qs(url.query), body))
        if url.path in self.override:
            status, payload = self.override[url.path](parse_qs(url.query), body)
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))
        elif url.path in self.hold:
            self.held.append((route, url, req.method, body))
        else:
            self._serve(route, url, req.method, body)

    def _serve(self, route, url, method, body):
        resp = self.client.open(url.path + (f"?{url.query}" if url.query else ""), method=method,
                                data=body, content_type="application/json" if body else None)
        route.fulfill(status=resp.status_code, body=resp.get_data(),
                      headers={"Content-Type": resp.headers.get("Content-Type", "text/plain")})

    def release(self, index=0, abort=False):
        route, url, method, body = self.held.pop(index)
        if abort:
            route.abort()
        else:
            self._serve(route, url, method, body)

    def wait_held(self, path, count=1):
        self.page.wait_for_function("() => true")      # let queued requests reach the handler
        for _ in range(100):
            if sum(1 for _r, u, _m, _b in self.held if u.path == path) >= count:
                return
            self.page.wait_for_timeout(20)
        raise AssertionError(f"no held request for {path}")

    def calls(self, path, method=None):
        return [entry for entry in self.log if entry[1] == path and (method is None or entry[0] == method)]

    def open(self, query="mode=home"):
        self.page.goto(f"{BASE}/?{query}")
        self.page.wait_for_selector("#rows tr[data-id], #rows .empty h3")
        self.idle()

    def idle(self):
        self.page.wait_for_function("document.getElementById('listings-results').getAttribute('aria-busy') === 'false'")

    def text(self, selector):
        return self.page.text_content(selector) or ""


@pytest.fixture
def app(browser, db, add):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    for i in range(12):
        add(external_id=f"h{i}", title=f"Moradia fixture {i}", price=20000 + i * 1000,
            date_end="2031-01-01T10:00:00+00:00")
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    harness = App(page, dashboard.app.test_client())
    yield harness
    context.close()
    assert not harness.errors, harness.errors


def _a_research_lead(monkeypatch):
    """Score eleiloes:lead as a priced research lead on Live and invest
    (the way tests/test_live_invest_shortlist.py does)."""
    import scoring
    real = scoring.score_detail

    def fake(item, now=None, targets=None, mode="home"):
        if mode == "both" and item["id"].endswith(":lead"):
            item["live_score"], item["invest_score"], item["research_only"] = 70.0, 30.0, False
            return 60.0, ["fixture lead"]
        return real(item, now=now, targets=targets, mode=mode)
    monkeypatch.setattr(scoring, "score_detail", fake)


def row(app, n):
    return app.page.locator(f'#rows tr[data-id="eleiloes:h{n}"]')


def open_details(app, n):
    row(app, n).locator('[data-role="details"]').click()
    app.page.wait_for_selector(f'#rows tr.details-row[data-for="eleiloes:h{n}"] textarea.d-note')
    return app.page.locator(f'#rows tr.details-row[data-for="eleiloes:h{n}"]')


# 1. Reversed and aborted reads; who owns the busy state.
def test_a_late_older_answer_never_replaces_the_newer_one(app):
    app.open()
    app.hold.add("/api/listings")
    app.page.fill("#f-search", "fixture 1")
    app.page.keyboard.press("Enter")
    app.wait_held("/api/listings")
    app.page.fill("#f-search", "fixture 2")
    app.page.keyboard.press("Enter")
    app.wait_held("/api/listings", 2)
    app.hold.clear()
    app.release(1)                                   # the newer answer first
    app.idle()
    assert app.text("#rows").count("Moradia fixture 2") == 1
    app.release(0)                                   # the older one late: ignored
    app.page.wait_for_timeout(200)
    assert "Moradia fixture 1" not in app.text("#rows") and "Moradia fixture 2" in app.text("#rows")
    assert app.page.get_attribute("#listings-results", "aria-busy") == "false"
    assert app.page.is_hidden("#listings-load-error")


def test_an_aborted_read_is_not_shown_as_an_error_or_empty(app):
    app.open()
    app.hold.add("/api/listings")
    app.page.fill("#f-search", "fixture 3")
    app.page.keyboard.press("Enter")
    app.wait_held("/api/listings")
    app.page.fill("#f-search", "")
    app.page.keyboard.press("Enter")
    app.wait_held("/api/listings", 2)
    app.hold.clear()
    app.release(1)
    app.idle()
    app.release(0, abort=True)
    app.page.wait_for_timeout(200)
    assert app.page.is_hidden("#listings-load-error") and app.page.locator("#rows tr[data-id]").count() == 12


# 2 and 8. Notes: one save at a time, edits during a save, honest acknowledgement.
def test_notes_save_serially_and_only_show_saved_for_what_was_stored(app):
    app.open()
    panel = open_details(app, 0)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("first")
    field.press("Tab")                                # leaving the field saves at once
    app.wait_held("/api/listing/note")
    field.click()
    field.press("End")
    field.type(" second")
    assert panel.locator(".note-phase").text_content() == "Saving…"
    app.page.wait_for_timeout(900)
    assert len(app.held) == 1                         # never two saves of one note at once
    app.hold.clear()
    app.release()
    app.page.wait_for_function("""() => document.querySelector('tr.details-row .note-phase').textContent === 'Saved'""")
    saves = [json.loads(b)["note"] for _m, _p, _q, b in app.calls("/api/listing/note", "POST")]
    assert saves == ["first", "first second"]
    stored = app.client.post("/api/listing/note", json={"id": "eleiloes:h0", "note": "first second"}).get_json()
    assert stored["length"] == len("first second")


def test_a_malformed_acknowledgement_is_not_saved_and_retry_sends_the_latest(app):
    app.open()
    panel = open_details(app, 1)
    field = panel.locator("textarea.d-note")
    phase = """() => document.querySelector('tr.details-row .note-phase').textContent"""
    app.override["/api/listing/note"] = lambda q, b: (200, {"ok": True})      # no id or length
    field.click()
    field.type("draft")
    field.press("Tab")
    app.page.wait_for_function(f"({phase})() === 'Could not save'")
    # Editing after a failure saves the newest text (still refused here) ...
    field.click()
    field.press("End")
    field.type("!")
    app.page.wait_for_function(f"({phase})() === 'Could not save'")
    assert json.loads(app.calls("/api/listing/note", "POST")[-1][3])["note"] == "draft!"
    # ... and Retry sends that newest text once the server acknowledges properly.
    del app.override["/api/listing/note"]
    panel.locator(".note-retry").click()
    app.page.wait_for_function(f"({phase})() === 'Saved'")
    assert json.loads(app.calls("/api/listing/note", "POST")[-1][3])["note"] == "draft!"


def test_a_save_for_a_missing_listing_cannot_show_saved(app, db):
    app.open()
    panel = open_details(app, 2)
    db.execute("DELETE FROM listings WHERE id = 'eleiloes:h2'")       # gone on the server meanwhile
    db.commit()
    field = panel.locator("textarea.d-note")
    field.click()
    field.type("note")
    field.press("Tab")
    app.page.wait_for_function("""() => document.querySelector('tr.details-row .note-phase').textContent === 'Could not save'""")
    assert app.page.is_visible("tr.details-row .note-retry")


# 3 and 5. Context and drafts across a refresh; details refresh without losing a draft.
def test_a_refresh_keeps_open_details_drafts_and_shows_newer_details(app, db):
    app.open()
    panel = open_details(app, 3)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("unsaved thought")
    app.page.locator("#reset-filters").focus()          # not typing any more
    db.execute("UPDATE listings SET current_bid = 23456 WHERE id = 'eleiloes:h3'")   # a scan changed it
    db.commit()
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    notice_or_reload = app.page.is_visible("#listings-update-notice")
    if notice_or_reload:                                  # a note is unsaved: it asks first
        app.page.click("#listings-refresh")
    app.idle()
    reopened = app.page.locator('#rows tr.details-row[data-for="eleiloes:h3"]')
    reopened.locator("textarea.d-note").wait_for()
    assert reopened.locator("textarea.d-note").input_value() == "unsaved thought"
    app.page.wait_for_function("""() => document.querySelector('#rows tr.details-row .details-freshness').dataset.state === 'current'""")
    assert app.calls("/api/listing")[-1][2]["id"] == ["eleiloes:h3"]       # fetched again, not the old copy
    app.hold.clear()
    while app.held:
        app.release()


def test_failed_detail_refresh_keeps_the_earlier_details_labelled(app, db):
    app.open()
    open_details(app, 4)
    db.execute("UPDATE listings SET current_bid = 24000 WHERE id = 'eleiloes:h4'")
    db.commit()
    app.override["/api/listing"] = lambda q, b: (500, {"error": "fixture failure"})
    app.page.click("#listings-refresh") if app.page.is_visible("#listings-refresh") else \
        app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    app.idle()
    state = app.page.locator('#rows tr.details-row[data-for="eleiloes:h4"] .details-freshness')
    state.wait_for()
    app.page.wait_for_function("""() => document.querySelector('#rows tr.details-row .details-freshness').dataset.state === 'failed'""")
    assert "may be out of date" in state.text_content()


def test_research_card_details_and_drafts_survive_a_refresh(app, add, monkeypatch):
    _a_research_lead(monkeypatch)
    add("eleiloes", "lead", title="Moradia T3", price=22000, area_m2=90, description="moradia em bom estado",
        url="https://example.test/lead", date_end="2031-01-01T10:00:00+00:00",
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    app.open("mode=both&min_score=0")
    assert app.page.locator("#research-candidates article.research-card").count() >= 1
    card = app.page.locator("#research-candidates article.research-card").first
    card_id = card.get_attribute("data-id")
    card.locator('[data-role="details"]').click()
    field = card.locator(".candidate-details textarea.d-note")
    field.wait_for()
    app.hold.add("/api/listing/note")
    field.click()
    field.type("card draft")
    app.page.locator("#reset-filters").focus()
    app.page.click("#listings-refresh") if app.page.is_visible("#listings-refresh") else \
        app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    if app.page.is_visible("#listings-refresh"):
        app.page.click("#listings-refresh")
    app.idle()
    again = app.page.locator(f'#research-candidates article.research-card[data-id="{card_id}"] .candidate-details textarea.d-note')
    again.wait_for()
    assert again.input_value() == "card draft"
    app.hold.clear()
    while app.held:
        app.release()


# 4. Scrolling or moving focus while a read is on its way is not undone.
def test_focus_and_scroll_moved_during_a_read_stay_where_the_user_put_them(app):
    app.page.set_viewport_size({"width": 1280, "height": 500})
    app.open()
    row(app, 6).locator('[data-role="star"]').focus()
    app.hold.add("/api/listings")
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    app.wait_held("/api/listings")
    app.page.locator("#reset-filters").focus()          # the user moved on
    app.page.evaluate("window.scrollTo(0, 260)")
    y = app.page.evaluate("window.scrollY")
    app.hold.clear()
    app.release()
    app.idle()
    assert app.page.evaluate("document.activeElement.id") == "reset-filters"
    assert abs(app.page.evaluate("window.scrollY") - y) < 40


# 6. A status change on its way keeps its redrawn buttons waiting.
def test_pending_status_buttons_stay_disabled_through_a_redraw(app):
    app.open()
    app.hold.add("/api/listings/status")
    row(app, 5).locator('[data-role="star"]').click()
    app.wait_held("/api/listings/status")
    app.page.evaluate("load()")
    app.idle()
    assert row(app, 5).locator('[data-role="star"]').is_disabled()
    assert not row(app, 6).locator('[data-role="star"]').is_disabled()
    app.hold.clear()
    app.release()
    app.page.wait_for_function("""() => !document.querySelector('#rows tr[data-id="eleiloes:h5"] [data-role="star"]').disabled""")


# 7. Address, Back/Forward, Reset, keyboard goal tabs and sorting.
def test_url_history_reset_tabs_and_sorting(app):
    app.open()
    app.page.select_option("#f-kind", "home")
    app.idle()
    assert "kind=home" in app.page.url
    # Listed price has no column header any more (Batch 4): the Sort by control reaches it.
    app.page.select_option("#sort-by", "price")
    app.idle()
    assert "sort=price" in app.page.url
    assert app.page.get_attribute('th[data-sort="score"]', "aria-sort") == "none"
    app.page.go_back()
    app.idle()
    assert "sort=price" not in app.page.url and app.page.input_value("#f-kind") == "home"
    app.page.go_forward()
    app.idle()
    assert "sort=price" in app.page.url
    app.page.click("#reset-filters")
    app.idle()
    assert app.page.input_value("#f-kind") == "" and "kind=home" not in app.page.url
    app.page.focus("#goal-home")
    app.page.keyboard.press("ArrowRight")                # moves focus; Enter chooses
    assert app.page.evaluate("document.activeElement.id") == "goal-invest"
    app.page.keyboard.press("Enter")
    app.idle()
    assert app.page.evaluate("document.activeElement.id") == "goal-invest"
    assert app.page.get_attribute("#goal-invest", "aria-selected") == "true" and "mode=invest" in app.page.url


# 9. A metadata failure keeps the source and method asked for.
def test_metadata_failure_keeps_the_requested_source_and_method(app):
    app.override["/api/meta"] = lambda q, b: (500, {"error": "fixture failure"})
    app.open("mode=home&source=eleiloes&sale_method=carta_fechada")
    assert "source=eleiloes" in app.page.url and "sale_method=carta_fechada" in app.page.url
    assert app.page.input_value("#f-source") == "eleiloes"
    assert "not confirmed" in app.text("#f-source")
    asked = app.calls("/api/listings")[-1][2]
    assert asked["source"] == ["eleiloes"] and asked["sale_method"] == ["carta_fechada"]
    assert "Kept as asked" in app.text("#view-note")


# 10. Unknown money is not zero; the sale-method help says what it covers.
def test_unknown_amounts_are_not_shown_as_zero_and_help_is_visible(app):
    card = {"id": "x:1", "title": "Card", "source": "x", "url": "", "status": "", "lookup": "x:1",
            "live_score": None, "invest_score": None, "valuation": "", "gaps": [], "detail_token": "t",
            "planning": {"acquisition": {"label": "Asking price", "amount": 30000},
                         "renovation": {"low": None, "high": None}, "renovation_point": "low"}}

    def listings(q, b):
        return 200, {"items": [], "total": 0, "found": 0, "cap": 100, "matched_total": 0, "display_total": 0,
                     "effective_page": 1, "page": 1, "stats": {"total": 0, "new_today": 0, "shortlisted": 0,
                                                               "sources_failing": 0, "hidden": {}},
                     "cash": {"set": True, "amount": 5}, "last_scrape": "2026-10-01",
                     "research": {"heading": "Research", "valued": [card], "unvalued": []}}
    app.override["/api/listings"] = listings
    app.open("mode=both")
    text = app.text("#research-candidates")
    assert "low end not priced" in text and "high end not priced" in text and "€0" not in text
    assert app.page.is_visible("#f-sale-method-help")
    assert "Portuguese and Dutch" in app.text("#f-sale-method-help")
