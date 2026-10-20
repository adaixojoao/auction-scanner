"""Batch 04: note drafts, details freshness and focus in a real browser (the
harness of tests/test_browser_listings.py: in-process Flask, no network,
synthetic data)."""
import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, open_details, close_details, row = (harness.browser, harness.app, harness.open_details,
                                                  harness.close_details, harness.row)


def _active(app):
    return app.page.evaluate("""() => { const a = document.activeElement;
        return {tag: a.tagName, role: a.dataset.role || "", id: a.dataset.id || "", value: a.value || "",
                start: a.selectionStart ?? null}; }""")


def test_a_finished_scan_invalidates_details_even_while_the_redraw_waits(app):
    app.open()
    panel = open_details(app, 0)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("draft kept")                              # unsaved: the redraw waits
    reads = len(app.calls("/api/listing", "GET"))
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    assert "A scan finished" in app.text("#property-drawer-notice")
    close_details(app)
    assert app.page.is_visible("#listings-update-notice")
    open_details(app, 0)                                  # read anew, not reused from before the scan
    ids = [q["id"][0] for _m, _p, q, _b in app.calls("/api/listing", "GET")[reads:]]
    assert ids == ["eleiloes:h0"]
    assert panel.locator("textarea.d-note").input_value() == "draft kept"
    app.hold.clear()
    while app.held:
        app.release()


def _reload_details(app, script):
    app.hold.add("/api/listing")
    app.page.evaluate(script)
    app.idle()
    app.wait_held("/api/listing")


def _release_details(app):
    app.hold.discard("/api/listing")
    while any(u.path == "/api/listing" for _r, u, _m, _b in app.held):
        app.release(next(i for i, h in enumerate(app.held) if h[1].path == "/api/listing"))


# Invalidated details stay drawn as "loaded earlier" while they reload; with
# the cache emptied, what the drawer shows still counts as earlier. Either
# way the note keeps focus and caret.
@pytest.mark.parametrize("script", ["detailsCache.invalidate(); load()",
                                    "detailsCache.invalidate(); detailsCache.earlier = () => null; load()"])
def test_a_reloading_drawer_keeps_the_note_focused_and_its_caret(app, script):
    app.open()
    panel = open_details(app, 1)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("abc")
    field.press("ArrowLeft")                              # caret at 2
    _reload_details(app, script)
    now = _active(app)
    assert (now["role"], now["id"], now["value"], now["start"]) == ("note", "eleiloes:h1", "abc", 2)
    assert "loaded earlier" in app.text("#property-drawer .details-freshness")
    _release_details(app)
    app.page.wait_for_function("() => document.querySelector('#property-drawer .details-freshness').dataset.state === 'current'")
    now = _active(app)
    assert (now["role"], now["id"], now["value"], now["start"]) == ("note", "eleiloes:h1", "abc", 2)
    app.hold.clear()
    while app.held:
        app.release()


def test_a_late_reload_never_takes_focus_back_from_where_you_moved_it(app):
    app.open()
    panel = open_details(app, 2)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("x")
    _reload_details(app, "detailsCache.invalidate(); load()")
    app.page.focus("#property-drawer-refresh")            # you moved on, inside the drawer
    _release_details(app)
    app.page.wait_for_function("() => document.querySelector('#property-drawer .details-freshness').dataset.state === 'current'")
    assert app.page.evaluate("document.activeElement.id") == "property-drawer-refresh"
    assert panel.locator("textarea.d-note").input_value() == "x"
    app.hold.clear()
    while app.held:
        app.release()


def test_leave_with_a_failed_save_keeps_you_and_the_text(app):
    app.override["/api/listing/note"] = lambda q, b: (500, {"error": "disk full"})
    app.open()
    panel = open_details(app, 3)
    field = panel.locator("textarea.d-note")
    field.click()
    field.type("must not be lost")
    app.page.click('#property-drawer a[href="/offers"]')    # the drawer's own link out
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
    field.fill(text)                                      # 2,000 UTF-16 units each (or none)
    field.press("Tab")
    app.page.wait_for_function("""() => document.querySelector('#property-drawer .note-phase').textContent === 'Saved'""")
    sent = json.loads(app.calls("/api/listing/note", "POST")[-1][3])
    assert sent == {"id": "eleiloes:h4", "note": text}
    stored = app.client.get("/api/listing?id=eleiloes:h4").get_json().get("user_note")
    assert (stored or "") == text
