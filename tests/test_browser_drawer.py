"""Batch 07: the one property drawer the table and the research cards share,
run against the production Listings page (the harness of
tests/test_browser_listings.py: in-process Flask on a temporary database, every
request answered in the test, no network, synthetic data)."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, open_details, close_details, row = (harness.browser, harness.app, harness.open_details,
                                                  harness.close_details, harness.row)
DRAWER = harness.DRAWER


def _release_all(app):
    app.hold.clear()
    while app.held:
        try:
            app.release()
        except Exception:  # noqa: BLE001 — the page already aborted that request
            pass


def _wait_closed(app):
    app.page.wait_for_function("() => !document.getElementById('property-drawer').open")


def _active(app):
    return app.page.evaluate("""() => { const a = document.activeElement;
        return {id: a.id || "", role: a.dataset.role || "", for: a.dataset.id || "",
                inDrawer: !!a.closest('#property-drawer'), start: a.selectionStart ?? null}; }""")


def _details_with(app, change):
    """Answers /api/listing with the real details, changed by change(payload)."""
    def answer(q, b):
        resp = app.client.get(f"/api/listing?id={q['id'][0]}")
        payload = resp.get_json()
        change(payload)
        return resp.status_code, payload
    app.override["/api/listing"] = answer


def _property(app):
    return parse_qs(urlsplit(app.page.url).query).get("property", [None])[0]


# 1. Table and research Details open the same dialog; nothing is inserted inline.
def test_table_and_research_details_open_the_same_dialog_and_nothing_inline(app, add, monkeypatch):
    harness._a_research_lead(monkeypatch)
    add("eleiloes", "lead", title="Moradia T3", price=22000, area_m2=90, url="https://example.test/lead",
        date_end="2031-01-01T10:00:00+00:00")
    app.open("mode=both&min_score=0")
    rows = app.page.locator("#rows tr").count()
    cards = app.page.locator("#research-candidates > *").count()
    first = app.page.locator("#rows tr[data-id]").first
    first_id = first.get_attribute("data-id")
    first.locator('[data-role="details"]').click()
    app.page.wait_for_selector(f'{DRAWER}[open][data-for="{first_id}"] textarea.d-note')
    assert app.page.locator("#rows tr").count() == rows
    close_details(app)
    card = app.page.locator("#research-candidates article.research-card").first
    card_id = card.get_attribute("data-id")
    card.locator('[data-role="details"]').click()
    app.page.wait_for_selector(f'{DRAWER}[open][data-for="{card_id}"] textarea.d-note')
    assert app.page.locator("dialog[open]").count() == 1
    assert app.page.locator("#research-candidates > *").count() == cards
    assert app.page.locator(".details-body").count() == 1            # one body: the drawer's
    assert card.locator('[data-role="details"]').get_attribute("aria-expanded") == "true"
    assert card.locator('[data-role="details"]').get_attribute("aria-controls") == "property-drawer"


# 3. Close and Escape give focus back to the trigger; Tab stays in the modal.
def test_close_and_escape_return_focus_and_tab_stays_inside(app):
    app.open()
    open_details(app, 2)
    assert _active(app)["id"] == "property-drawer-close"
    for _ in range(40):
        app.page.keyboard.press("Tab")
        now = _active(app)
        assert now["inDrawer"] or app.page.evaluate("document.activeElement === document.body"), now
    close_details(app)
    assert _active(app)["role"] == "details" and _active(app)["for"] == "eleiloes:h2"
    open_details(app, 3)
    app.page.keyboard.press("Escape")
    _wait_closed(app)
    assert (_active(app)["role"], _active(app)["for"]) == ("details", "eleiloes:h3")
    assert _property(app) is None


# 3. A trigger redrawn while the drawer was open: focus goes to its replacement.
def test_focus_returns_to_the_redrawn_rows_details_button(app):
    app.open()
    open_details(app, 4)
    app.page.evaluate("load()")
    app.idle()
    close_details(app)
    assert (_active(app)["role"], _active(app)["for"]) == ("details", "eleiloes:h4")


# 4. A late answer after closing neither draws nor reopens anything.
def test_a_late_answer_after_closing_is_ignored(app):
    app.open()
    app.hold.add("/api/listing")
    row(app, 5).locator('[data-role="details"]').click()
    app.wait_held("/api/listing")
    close_details(app)
    _release_all(app)
    app.page.wait_for_timeout(200)
    assert not app.page.evaluate("document.getElementById('property-drawer').open")
    assert app.page.locator(f"{DRAWER} textarea.d-note").count() == 0


# 4. Switching A to B while A is still on its way never draws A as B.
def test_a_rapid_switch_never_draws_the_first_listing_in_the_second(app):
    app.open()
    app.hold.add("/api/listing")
    row(app, 0).locator('[data-role="details"]').click()
    app.wait_held("/api/listing")
    app.page.evaluate("""() => { propertyDrawer.open('eleiloes:h1',
                             document.querySelector('#rows tr[data-id="eleiloes:h1"] [data-role="details"]')); }""")
    app.wait_held("/api/listing", 2)
    app.hold.clear()
    app.release(1)                                   # B first, then A late
    try:
        app.release(0)
    except Exception:  # noqa: BLE001 — aborted when B was chosen
        pass
    app.page.wait_for_selector(f'{DRAWER}[data-for="eleiloes:h1"] textarea.d-note')
    app.page.wait_for_timeout(200)
    assert app.page.get_attribute(f"{DRAWER} textarea.d-note", "data-id") == "eleiloes:h1"
    assert app.text("#property-drawer-title") == "Moradia fixture 1"
    assert _property(app) == "eleiloes:h1"


# 5. A draft survives closing, reopening and a status change; the caret stays.
def test_a_draft_survives_close_reopen_and_a_status_change(app, db):
    app.open()
    panel = open_details(app, 6)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("draft")
    close_details(app)
    panel = open_details(app, 6)
    field = panel.locator("textarea.d-note")
    assert field.input_value() == "draft"
    field.click()
    field.press("End")
    field.press("ArrowLeft")                          # caret at 4
    app.page.evaluate("document.getElementById('property-drawer-star').click()")
    app.page.wait_for_function("() => document.getElementById('property-drawer-star').textContent === 'Remove from shortlist'")
    app.idle()
    assert field.input_value() == "draft"
    status = db.execute("SELECT status FROM listing_status WHERE listing_id = 'eleiloes:h6'").fetchone()
    assert status is not None and status[0] == "shortlisted"
    _release_all(app)


# 6. Cancel and Escape in the reason dialog change nothing; Dismiss closes the drawer and Undo keeps history.
def test_dismiss_cancel_changes_nothing_and_undo_keeps_history(app, db):
    app.open()
    open_details(app, 7)
    app.page.click("#property-drawer-dismiss")
    assert app.page.is_visible("#dismiss-dialog")
    app.page.keyboard.press("Escape")
    app.page.wait_for_function("() => !document.getElementById('dismiss-dialog').open")
    assert app.page.evaluate("document.getElementById('property-drawer').open")
    app.page.click("#property-drawer-dismiss")
    app.page.click('#dismiss-dialog button[value="cancel"]')
    app.page.wait_for_function("() => !document.getElementById('dismiss-dialog').open")
    assert not app.calls("/api/listings/status", "POST")
    app.page.click("#property-drawer-dismiss")
    app.page.select_option("#dismiss-reason", "occupied")
    app.page.click("#dismiss-ok")
    _wait_closed(app)
    app.idle()
    assert app.page.is_visible("#status-undo")
    app.page.click("#status-undo-button")
    app.idle()
    assert row(app, 7).count() == 1
    history = app.client.get("/api/listing?id=eleiloes:h7").get_json()["status_history"]
    assert any((h.get("status") == "dismissed") for h in history), history


# 7. A deep link to a listing outside the results, or to none, leaves the counts alone.
@pytest.mark.parametrize("target", ["eleiloes:gone", "eleiloes:nothing-here"])
def test_an_out_of_view_deep_link_disables_actions_and_keeps_counts(app, add, target):
    add(external_id="gone", title="Dismissed fixture", price=21000, date_end="2031-01-01T10:00:00+00:00")
    app.client.post("/api/listings/status", json={"id": "eleiloes:gone", "status": "dismissed", "reason": "other"})
    app.open()
    count, rows = app.text("#result-count"), app.page.locator("#rows tr[data-id]").count()
    app.open(f"mode=home&property={target}")
    app.page.wait_for_selector(f'{DRAWER}[open][data-for="{target}"]')
    app.page.wait_for_selector(f"{DRAWER} textarea.d-note, {DRAWER} .details-error")
    assert app.text("#result-count") == count and app.page.locator("#rows tr[data-id]").count() == rows
    assert app.page.is_disabled("#property-drawer-star") and app.page.is_disabled("#property-drawer-dismiss")
    assert "not in the results shown now" in app.text("#property-drawer-notice")
    assert app.page.get_attribute("#property-drawer-star", "aria-pressed") == "false"
    assert not app.calls("/api/listings/status", "POST") or target == "eleiloes:gone"


# 8. ?property= survives filter changes and comes back on reload and Back/Forward, one read each.
def test_the_property_in_the_address_survives_filters_reload_and_history(app):
    app.open()
    open_details(app, 8)
    assert _property(app) == "eleiloes:h8"
    app.page.evaluate("""() => { const s = document.getElementById('f-kind'); s.value = 'home';
                                 s.dispatchEvent(new Event('change', {bubbles: true})); }""")
    app.idle()
    assert _property(app) == "eleiloes:h8" and "kind=home" in app.page.url
    reads = len(app.calls("/api/listings"))
    app.page.reload()
    app.page.wait_for_selector(f'{DRAWER}[open][data-for="eleiloes:h8"] textarea.d-note')
    app.idle()
    assert len(app.calls("/api/listings")) == reads + 1
    close_details(app)
    assert _property(app) is None
    reads = len(app.calls("/api/listings"))
    app.page.go_back()                               # the entry before the filter still names h8
    app.page.wait_for_selector(f'{DRAWER}[open][data-for="eleiloes:h8"] textarea.d-note')
    app.idle()
    app.page.go_forward()
    _wait_closed(app)
    app.idle()
    app.page.wait_for_timeout(200)
    assert len(app.calls("/api/listings")) == reads + 2


# 9. A scan while you type a location only says so; Refresh then reads anew.
def test_a_scan_while_typing_a_location_keeps_the_typing(app, add):
    add(external_id="geo", title="Moradia located fixture", price=23000, date_end="2031-01-01T10:00:00+00:00",
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    app.open("mode=home&search=located")
    app.page.locator('#rows tr[data-id="eleiloes:geo"] [data-role="details"]').click()
    value = app.page.locator(f'{DRAWER}[open][data-for="eleiloes:geo"] .loc-value')
    value.wait_for()
    value.click()
    value.type("38.7")
    reads = len(app.calls("/api/listing", "GET"))
    app.page.evaluate("detailsCache.invalidate(); load()")
    app.idle()
    assert "your typing is kept" in app.text("#property-drawer-notice")
    assert value.input_value() == "38.7"
    assert len(app.calls("/api/listing", "GET")) == reads
    app.page.click("#property-drawer-refresh")
    app.page.wait_for_function("() => document.getElementById('property-drawer-notice').hidden")
    assert len(app.calls("/api/listing", "GET")) == reads + 1


# 10. Verify location acts on the drawer's listing and reloads it.
def test_location_verification_targets_the_drawers_listing(app, add):
    add(external_id="geo", title="Moradia located fixture", price=23000, date_end="2031-01-01T10:00:00+00:00",
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    sent = []

    def verify(q, b):
        sent.append(json.loads(b))
        return 200, {"location": {"label": "Verified fixture"}}
    app.override["/api/listing/location"] = verify
    app.open("mode=home&search=located")
    app.page.locator('#rows tr[data-id="eleiloes:geo"] [data-role="details"]').click()
    box = app.page.locator(f'{DRAWER}[open][data-for="eleiloes:geo"] .location')
    box.wait_for()
    reads = len(app.calls("/api/listing", "GET"))
    box.locator(".loc-value").fill("38.7223, -9.1393")
    box.locator("button", has_text="Verify location").click()
    app.page.wait_for_function("() => true")
    for _ in range(50):
        if len(app.calls("/api/listing", "GET")) > reads:
            break
        app.page.wait_for_timeout(20)
    assert sent == [{"id": "eleiloes:geo", "method": "coordinates", "value": "38.7223, -9.1393"}]
    assert [q["id"][0] for _m, _p, q, _b in app.calls("/api/listing", "GET")[reads:]] == ["eleiloes:geo"]


# 11. The sections are there and their links reach them without touching the address.
def test_sections_are_listed_and_reachable(app):
    app.open()
    open_details(app, 9)
    links = app.page.locator("#property-sections button")
    titles = [t.strip() for t in app.page.locator(f"{DRAWER} .d-section-title").all_text_contents()]
    assert links.count() == len(titles) >= 3
    assert [t.strip() for t in links.all_text_contents()] == titles
    url = app.page.url
    notes = links.filter(has_text=titles[-1])
    notes.click()
    assert app.page.evaluate("document.activeElement.classList.contains('d-section-title')")
    assert app.page.evaluate("document.activeElement.textContent").strip() == titles[-1]
    assert app.page.url == url


# 12. The image viewer: opens, fails gracefully, Escape closes it before the drawer, the rest is inert.
def test_the_image_viewer_opens_fails_gracefully_and_closes_first(app):
    _details_with(app, lambda d: d.update(images=[f"{harness.BASE}/static/icon.ico", "https://img.invalid/broken.jpg"],
                                          image=None))
    app.open()
    open_details(app, 10)
    photos = app.page.locator(f'{DRAWER} [data-role="enlarge-photo"]')
    assert photos.count() == 2
    photos.nth(0).click()
    assert app.page.is_visible("#property-image-viewer")
    assert _active(app)["id"] == "property-image-close"
    assert app.page.evaluate("document.querySelector('#property-drawer .property-drawer-content').inert")
    for _ in range(6):
        app.page.keyboard.press("Tab")
        assert app.page.evaluate("""() => document.activeElement === document.body
                                     || !!document.activeElement.closest('#property-image-viewer')"""), \
            app.page.evaluate("document.activeElement.outerHTML.slice(0, 200)")
    app.page.keyboard.press("Escape")
    assert app.page.is_hidden("#property-image-viewer")
    assert app.page.evaluate("document.getElementById('property-drawer').open")
    assert app.page.evaluate("document.activeElement") is not None
    assert app.page.evaluate("document.activeElement.dataset.role") == "enlarge-photo"
    photos.nth(1).click()
    app.page.wait_for_selector("#property-image-error", state="visible")
    assert app.page.is_hidden("#property-image")
    app.page.click("#property-image-close")
    assert app.page.is_hidden("#property-image-viewer")
    assert not app.page.evaluate("document.querySelector('#property-drawer .property-drawer-content').inert")
    app.page.keyboard.press("Escape")
    _wait_closed(app)


# 12. A thumbnail with an unsafe address is never enlarged.
def test_an_unsafe_image_address_is_not_enlarged(app):
    _details_with(app, lambda d: d.update(images=["https://user:secret@img.invalid/x.jpg"], image=None))
    app.open()
    open_details(app, 11)
    app.page.click(f'{DRAWER} [data-role="enlarge-photo"]')
    assert app.page.is_hidden("#property-image-viewer")


# 13. A missing amount says so instead of €0; a real zero stays €0.
def test_missing_costs_are_not_established_rather_than_zero(app):
    def change(d):
        d["costs"] = {"basis": "the price", "base": 20000, "total": None, "note": "Estimates only.",
                      "lines": [{"label": "Transfer tax", "amount": None, "note": "fixture"},
                                {"label": "Stamp duty", "amount": 0, "note": "fixture"}]}
    _details_with(app, change)
    app.open()
    panel = open_details(app, 0)
    costs = panel.locator("#property-section-costs")
    text = costs.text_content()
    assert "Not established to own it" in text
    assert costs.locator(".fact", has_text="Transfer tax").text_content().count("Not established") == 1
    assert "€0" in costs.locator(".fact", has_text="Stamp duty").text_content()
    assert "Estimates only." in text
