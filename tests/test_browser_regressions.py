"""Batch 05: the Batch 05 regression scenarios not already covered by
tests/test_browser_listings.py, test_browser_results.py and test_browser_drafts.py,
run against the production Listings page (the same harness: in-process Flask on
a temporary database, every request answered in the test, no network, synthetic
data)."""
import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, open_details, close_details, row = (harness.browser, harness.app, harness.open_details,
                                                  harness.close_details, harness.row)


def _set_description(db, n, text):
    db.execute("UPDATE listings SET description = ? WHERE id = ?", (text, f"eleiloes:h{n}"))
    db.commit()


def _release_all(app):
    app.hold.clear()
    while app.held:
        try:
            app.release()
        except Exception:  # noqa: BLE001 — the page already aborted that request
            pass


# 1. Two reads of one panel's details answered in reverse: the newer one shows,
#    and reopening the panel reuses it rather than the late, older answer.
def test_reversed_detail_reads_keep_the_newer_details_shown_and_cached(app, db):
    app.open()
    panel = open_details(app, 0)
    app.hold.add("/api/listing")
    _set_description(db, 0, "older description")
    panel.locator('[data-role="refresh-details"]').click()
    app.wait_held("/api/listing")
    panel.locator('[data-role="refresh-details"]').click()
    app.wait_held("/api/listing", 2)
    _set_description(db, 0, "newer description")
    app.hold.clear()
    app.release(1)                                   # the newer read first
    app.page.wait_for_function("""() => (document.querySelector('#property-drawer .d-desc') || {}).textContent
                                       === 'newer description'""")
    try:
        app.release(0)                               # the older one late (the page aborted it)
    except Exception:  # noqa: BLE001
        pass
    app.page.wait_for_timeout(200)
    assert panel.locator(".d-desc").text_content() == "newer description"
    assert panel.locator(".details-freshness").get_attribute("data-state") == "current"
    reads = len(app.calls("/api/listing"))
    close_details(app)
    row(app, 0).locator('[data-role="details"]').click()          # reopen from the cache
    reopened = app.page.locator('#property-drawer[open][data-for="eleiloes:h0"]')
    reopened.locator(".d-desc").wait_for()
    assert reopened.locator(".d-desc").text_content() == "newer description"
    assert len(app.calls("/api/listing")) == reads


# 2. An older listings answer cannot clear the busy state of a newer read still on its way.
def test_an_older_listings_answer_leaves_the_newer_read_busy(app):
    app.open()
    app.hold.add("/api/listings")
    app.page.fill("#f-search", "fixture 1")
    app.page.keyboard.press("Enter")
    app.wait_held("/api/listings")
    app.page.fill("#f-search", "fixture 2")
    app.page.keyboard.press("Enter")
    app.wait_held("/api/listings", 2)
    app.hold.clear()
    try:
        app.release(0)                               # the older answer, while the newer waits
    except Exception:  # noqa: BLE001 — aborted by the newer read
        pass
    app.page.wait_for_timeout(200)
    assert app.page.get_attribute("#listings-results", "aria-busy") == "true"
    assert "Moradia fixture 1" in app.text("#rows")  # neither the older answer nor an empty table
    assert app.page.locator("#rows tr[data-id]").count() == 12
    app.release(0)
    app.idle()
    assert app.page.locator("#rows tr[data-id]").count() == 1
    assert "Moradia fixture 2" in app.text("#rows")


# 8. Save and leave saves first; Stay keeps the page and the draft.
def test_save_and_leave_saves_the_note_then_leaves(app, db):
    app.open()
    panel = open_details(app, 1)
    field = panel.locator("textarea.d-note")
    field.click()
    field.type("kept before leaving")
    app.page.click('#property-drawer a[href="/offers"]')
    assert app.page.is_visible("#leave-dialog")
    app.page.click('#leave-dialog [data-leave="stay"]')
    assert not app.page.is_visible("#leave-dialog") and field.input_value() == "kept before leaving"
    field.click()
    field.press("End")
    field.type("!")
    app.page.click('#property-drawer a[href="/offers"]')
    with app.page.expect_navigation():
        app.page.click('#leave-dialog [data-leave="save"]')
    assert app.page.url.endswith("/offers")
    stored = db.execute("SELECT user_note FROM listings WHERE id = 'eleiloes:h1'").fetchone()[0]
    assert stored == "kept before leaving!"


# 8. A finished scan does not redraw under an unsaved note: it waits for Refresh.
def test_a_finished_scan_waits_for_refresh_while_a_note_is_unsaved(app):
    app.open()
    panel = open_details(app, 2)
    field = panel.locator("textarea.d-note")
    app.hold.add("/api/listing/note")
    field.click()
    field.type("half a thought")
    reads = len(app.calls("/api/listings"))
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    assert "A scan finished" in app.text("#property-drawer-notice")
    close_details(app)
    assert app.page.is_visible("#listings-update-notice")
    app.page.wait_for_timeout(200)
    assert len(app.calls("/api/listings")) == reads                 # nothing redrawn yet
    assert field.input_value() == "half a thought"
    app.page.click("#listings-refresh")
    app.idle()
    assert len(app.calls("/api/listings")) == reads + 1
    assert app.page.is_hidden("#listings-update-notice")
    again = open_details(app, 2).locator("textarea.d-note")
    assert again.input_value() == "half a thought"
    _release_all(app)


# 5. A listing that leaves the results on a passive refresh hands focus to its neighbour.
def test_focus_moves_to_the_neighbour_when_its_listing_leaves(app):
    app.open()
    ids = [el.get_attribute("data-id") for el in app.page.locator("#rows tr[data-id]").all()]
    gone, after = ids[4], ids[5]
    app.page.locator(f'#rows tr[data-id="{gone}"] [data-role="star"]').focus()
    app.client.post("/api/listings/status", json={"id": gone, "status": "dismissed", "reason": "other"})
    app.page.evaluate("document.dispatchEvent(new CustomEvent('scan-finished'))")
    app.idle()
    app.page.wait_for_function(f"""() => !document.querySelector('#rows tr[data-id="{gone}"]')""")
    focused = app.page.evaluate("""() => { const a = document.activeElement, tr = a.closest('#rows tr');
                                          return {role: a.dataset.role || '', id: tr ? tr.dataset.id : ''}; }""")
    assert focused == {"role": "details", "id": after}


# 9. Back and Forward restore the view without adding history or reading twice.
def test_back_and_forward_restore_the_view_once_without_new_history(app):
    app.open()
    app.page.select_option("#f-kind", "home")
    app.idle()
    app.page.click('th[data-sort="date_end"] .sort-button')
    app.idle()
    length = app.page.evaluate("history.length")
    reads = len(app.calls("/api/listings"))
    app.page.go_back()
    app.idle()
    assert "sort=date_end" not in app.page.url and app.page.input_value("#f-kind") == "home"
    assert app.page.input_value("#sort-by") == "score"
    app.page.go_forward()
    app.idle()
    assert "sort=date_end" in app.page.url and app.page.input_value("#sort-by") == "date_end"
    assert app.page.evaluate("history.length") == length
    app.page.wait_for_timeout(200)
    assert len(app.calls("/api/listings")) == reads + 2              # one read per step
    assert app.calls("/api/listings")[-1][2]["sort"] == ["date_end"]


# 10. Sort headers: Enter and Space each sort exactly once and say which way.
@pytest.mark.parametrize("key", ["Enter", " "])
def test_sort_headers_sort_once_per_key_and_report_the_direction(app, key):
    app.open()
    header = app.page.locator('th[data-sort="date_end"]')
    button = header.locator(".sort-button")
    assert header.get_attribute("aria-sort") == "none"
    assert button.get_attribute("aria-label") == "Sort by Deadline, ascending"
    reads = len(app.calls("/api/listings"))
    button.focus()
    app.page.keyboard.press(key)
    app.idle()
    assert len(app.calls("/api/listings")) == reads + 1
    assert header.get_attribute("aria-sort") == "ascending"
    assert app.page.get_attribute('th[data-sort="score"]', "aria-sort") == "none"
    assert button.get_attribute("aria-label") == "Sort by Deadline, descending"
    assert "dir=asc" in app.page.url and "sort=date_end" in app.page.url
    app.page.keyboard.press(key)
    app.idle()
    assert len(app.calls("/api/listings")) == reads + 2
    assert header.get_attribute("aria-sort") == "descending"


# 11. Escape closes a dropdown and returns focus to its toggle; the idle Stop is not reachable.
def test_dropdown_escape_returns_focus_and_idle_stop_is_not_focusable(app):
    app.open()
    toggle = app.page.locator("#scan-btn")
    toggle.focus()
    app.page.keyboard.press("ArrowDown")
    assert toggle.get_attribute("aria-expanded") == "true"
    assert app.page.evaluate("!!document.activeElement.closest('.menu-list')")
    app.page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert app.page.evaluate("document.activeElement.id") == "scan-btn"
    assert app.page.is_hidden("#scan-stop")
    # Stop sits just before the toggle: Shift+Tab from the toggle must skip it.
    app.page.keyboard.press("Shift+Tab")
    assert app.page.evaluate("document.activeElement.id") != "scan-stop"
    app.page.keyboard.press("Tab")
    assert app.page.evaluate("document.activeElement.id") == "scan-btn"


# 12. A long, hostile ID and title stay text, and actions reach that exact listing.
def test_a_hostile_id_and_title_stay_text_and_target_their_listing(app, add, db):
    external = 'x"><img src=x onerror="window.pwned=1">' + "y" * 200
    add(external_id=external, title='<b onmouseover="window.pwned=1">Hostile</b> moradia', price=19000,
        date_end="2031-01-01T10:00:00+00:00")
    app.open()
    listing_id = f"eleiloes:{external}"
    target = app.page.locator("#rows tr[data-id]").filter(has_text="Hostile")
    assert target.count() == 1 and target.get_attribute("data-id") == listing_id
    assert '<b onmouseover="window.pwned=1">Hostile</b> moradia' in target.text_content()
    assert app.page.locator("#rows img[src='x'], #rows b[onmouseover]").count() == 0
    target.locator('[data-role="star"]').click()
    app.page.wait_for_function("() => true")
    for _ in range(50):
        if app.calls("/api/listings/status", "POST"):
            break
        app.page.wait_for_timeout(20)
    assert json.loads(app.calls("/api/listings/status", "POST")[-1][3])["id"] == listing_id
    app.idle()
    status = db.execute("SELECT status FROM listing_status WHERE listing_id = ?", (listing_id,)).fetchone()
    assert status is not None and status[0] == "shortlisted"
    assert app.page.evaluate("window.pwned === undefined")
