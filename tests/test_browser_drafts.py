"""Batch 04: note drafts, details freshness and focus in a real browser (the
harness of tests/test_browser_listings.py: in-process Flask, no network,
synthetic data)."""
import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, open_details, row = harness.browser, harness.app, harness.open_details, harness.row


def _active(app):
    return app.page.evaluate("""() => { const a = document.activeElement;
        return {tag: a.tagName, role: a.dataset.role || "", id: a.dataset.id || "", value: a.value || "",
                start: a.selectionStart ?? null}; }""")


def test_a_finished_scan_invalidates_details_even_while_the_redraw_waits(app):
    app.open()
    panel = open_details(app, 0)
    field = panel.locator("textarea.d-note")
    field.wait_for()
    app.hold.add("/api/listing/note")
    field.click()
    field.type("draft kept")                              # unsaved: the redraw waits
    reads = len(app.calls("/api/listing", "GET"))
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    assert app.page.is_visible("#listings-update-notice")
    row(app, 1).locator('[data-role="details"]').click()  # another panel opens fresh, as ever
    row(app, 0).locator('[data-role="details"]').click()  # close this one…
    row(app, 0).locator('[data-role="details"]').click()  # …and open it again: read anew, not reused
    app.page.wait_for_function("() => true")
    for _ in range(50):
        if len(app.calls("/api/listing", "GET")) >= reads + 2:
            break
        app.page.wait_for_timeout(20)
    ids = [q["id"][0] for _m, _p, q, _b in app.calls("/api/listing", "GET")[reads:]]
    assert "eleiloes:h0" in ids
    again = app.page.locator('tr.details-row[data-for="eleiloes:h0"] textarea.d-note')
    again.wait_for()
    assert again.input_value() == "draft kept"
    app.hold.clear()
    while app.held:
        app.release()


def test_a_panel_with_earlier_details_keeps_the_note_focused_while_it_reloads(app):
    app.open()
    panel = open_details(app, 1)
    field = panel.locator("textarea.d-note")
    field.wait_for()
    app.hold.add("/api/listing/note")
    field.click()
    field.type("abc")
    field.press("ArrowLeft")
    app.hold.add("/api/listing")
    app.page.evaluate("detailsCache.invalidate(); load()")   # invalidated: shown as earlier while it reloads
    app.idle()
    app.wait_held("/api/listing")
    now = _active(app)
    assert (now["role"], now["id"], now["value"], now["start"]) == ("note", "eleiloes:h1", "abc", 2)
    assert "loaded earlier" in app.text('tr.details-row[data-for="eleiloes:h1"] .details-freshness')
    app.hold.clear()
    while app.held:
        app.release()


# A panel with nothing earlier to show (dropped from the bounded cache) draws
# later than the rows: focus comes back when it does.
FORGET = "detailsCache.invalidate(); detailsCache.earlier = () => null; load()"


def test_focus_returns_to_the_note_once_its_reloading_panel_is_drawn(app):
    app.open()
    panel = open_details(app, 1)
    field = panel.locator("textarea.d-note")
    field.wait_for()
    app.hold.add("/api/listing/note")
    field.click()
    field.type("abc")
    field.press("ArrowLeft")                              # caret at 2
    app.hold.add("/api/listing")
    app.page.evaluate(FORGET)
    app.idle()
    app.wait_held("/api/listing")
    assert _active(app)["role"] == "details"              # meanwhile: its Details button
    app.hold.discard("/api/listing")
    while any(u.path == "/api/listing" for _r, u, _m, _b in app.held):
        app.release(next(i for i, h in enumerate(app.held) if h[1].path == "/api/listing"))
    app.page.wait_for_function("() => document.activeElement && document.activeElement.matches('textarea.d-note')")
    now = _active(app)
    assert (now["id"], now["value"], now["start"]) == ("eleiloes:h1", "abc", 2)
    app.hold.clear()
    while app.held:
        app.release()


def test_a_late_panel_never_takes_focus_back_from_where_you_moved_it(app):
    app.open()
    panel = open_details(app, 2)
    field = panel.locator("textarea.d-note")
    field.wait_for()
    app.hold.add("/api/listing/note")
    field.click()
    field.type("x")
    app.hold.add("/api/listing")
    app.page.evaluate(FORGET)
    app.idle()
    app.wait_held("/api/listing")
    app.page.focus("#f-search")                           # you moved on
    app.hold.discard("/api/listing")
    while any(u.path == "/api/listing" for _r, u, _m, _b in app.held):
        app.release(next(i for i, h in enumerate(app.held) if h[1].path == "/api/listing"))
    app.page.locator('tr.details-row[data-for="eleiloes:h2"] textarea.d-note').wait_for()
    assert app.page.evaluate("document.activeElement.id") == "f-search"
    app.hold.clear()
    while app.held:
        app.release()


def test_leave_with_a_failed_save_keeps_you_and_the_text(app):
    app.override["/api/listing/note"] = lambda q, b: (500, {"error": "disk full"})
    app.open()
    panel = open_details(app, 3)
    field = panel.locator("textarea.d-note")
    field.wait_for()
    field.click()
    field.type("must not be lost")
    app.page.click('a[href="/sources"]')
    assert app.page.is_visible("#leave-dialog")
    app.page.click('#leave-dialog [data-leave="save"]')
    app.page.wait_for_function("() => document.getElementById('leave-text').textContent.includes('could not be saved')")
    assert app.page.is_visible("#leave-dialog")
    assert urlpath(app) == "/"
    app.page.click('#leave-dialog [data-leave="stay"]')
    assert not app.page.is_visible("#leave-dialog")
    assert field.input_value() == "must not be lost"
    assert panel.locator(".note-phase").text_content() == "Could not save"


def urlpath(app):
    from urllib.parse import urlsplit
    return urlsplit(app.page.url).path


@pytest.mark.parametrize("text", ["x" * 2000, "\U0001F3E0" * 1000, ""])
def test_notes_at_the_limit_with_emoji_or_empty_are_acknowledged(app, text):
    app.client.post("/api/listing/note", json={"id": "eleiloes:h4", "note": "earlier"})
    app.open()
    panel = open_details(app, 4)
    field = panel.locator("textarea.d-note")
    field.wait_for()
    field.fill(text)                                      # 2,000 UTF-16 units each (or none)
    field.press("Tab")
    app.page.wait_for_function("""() => document.querySelector('tr.details-row .note-phase').textContent === 'Saved'""")
    sent = json.loads(app.calls("/api/listing/note", "POST")[-1][3])
    assert sent == {"id": "eleiloes:h4", "note": text}
    stored = app.client.get("/api/listing?id=eleiloes:h4").get_json().get("user_note")
    assert (stored or "") == text
