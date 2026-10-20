"""Batch 08: saved searches and the results as cards, run against the
production Listings page (the harness of tests/test_browser_listings.py:
in-process Flask on a temporary database, every request answered in the test,
no network, synthetic data)."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, close_details, row = harness.browser, harness.app, harness.close_details, harness.row
KEY = "auction-scanner:saved-searches:v1"


def _stored(app):
    text = app.page.evaluate(f"localStorage.getItem('{KEY}')")
    return json.loads(text) if text else None


def _put(app, document):
    app.page.evaluate("([k, v]) => localStorage.setItem(k, v)",
                      [KEY, document if isinstance(document, str) else json.dumps(document)])


def _query(app):
    return {k: v[0] for k, v in parse_qs(urlsplit(app.page.url).query, keep_blank_values=True).items()}


def _message(app):
    return app.text("#saved-search-message")


def _save_as(app, name):
    app.page.click("#saved-search-create")
    app.page.wait_for_selector("#saved-search-name-dialog[open]")
    app.page.fill("#saved-search-name", name)
    app.page.click("#saved-search-name-ok")


def _choose(app, name):
    app.page.select_option("#saved-search-select", label=name)


def _search(app, text):
    app.page.fill("#f-search", text)
    app.page.keyboard.press("Enter")
    app.idle()


def _record(mode="home", **view):
    base = {"mode": mode, "country": "", "source": "", "kind": "", "status": "", "max_price": "", "min_score": 0,
            "search": "", "properties": True, "climate": "", "climate_exact": False, "sale_method": "",
            "sort": "score", "dir": "desc"}
    return {**base, **view}


def _layout(app, name):
    app.page.click(f'[data-layout="{name}"]')


def _ids(app, selector):
    return [el.get_attribute("data-id") for el in app.page.locator(selector).all()]


# 1. Save, apply, update, rename and delete, each asked for, with Modified in between.
def test_save_apply_update_rename_and_delete(app):
    app.open()
    _search(app, "fixture 1")
    _save_as(app, "Fixture one")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    assert "Saved “Fixture one” in this browser." in _message(app)
    [record] = _stored(app)["records"]
    assert record["name"] == "Fixture one" and record["view"]["search"] == "fixture 1"
    assert app.page.is_hidden("#saved-search-modified")
    _search(app, "fixture 2")
    assert app.page.is_visible("#saved-search-modified")
    reads = len(app.calls("/api/listings"))
    app.page.click("#saved-search-apply")
    app.idle()
    assert _query(app)["search"] == "fixture 1" and app.page.input_value("#f-search") == "fixture 1"
    assert len(app.calls("/api/listings")) == reads + 1
    assert app.page.is_hidden("#saved-search-modified")
    app.page.select_option("#sort-by", "date_end")
    app.idle()
    app.page.once("dialog", lambda d: d.dismiss())
    app.page.click("#saved-search-update")
    assert _stored(app)["records"][0]["view"]["sort"] == "score" and "Not changed" in _message(app)
    app.page.once("dialog", lambda d: d.accept())
    app.page.click("#saved-search-update")
    assert _stored(app)["records"][0]["view"]["sort"] == "date_end"
    app.page.click("#saved-search-rename")
    assert app.page.input_value("#saved-search-name") == "Fixture one"
    app.page.fill("#saved-search-name", "  ")
    app.page.click("#saved-search-name-ok")
    assert "1 to 80" in app.text("#saved-search-name-error") and app.page.is_visible("#saved-search-name-dialog")
    app.page.fill("#saved-search-name", "Renamed")
    app.page.keyboard.press("Enter")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    assert app.page.evaluate("document.activeElement.id") == "saved-search-rename"
    assert _stored(app)["records"][0]["name"] == "Renamed"
    app.page.once("dialog", lambda d: d.dismiss())
    app.page.click("#saved-search-delete")
    assert len(_stored(app)["records"]) == 1
    app.page.once("dialog", lambda d: d.accept())
    app.page.click("#saved-search-delete")
    assert _stored(app)["records"] == []
    assert app.page.locator("#saved-search-select option").all_text_contents() == ["Choose…"]
    assert _query(app)["search"] == "fixture 1"                      # deleting changes no listings


# 2. A number that is not valid yet is never saved.
def test_an_invalid_number_cannot_be_saved(app):
    app.open()
    app.page.click(".more-filters summary")
    app.page.fill("#f-minscore", "150")
    app.page.click("#saved-search-create")
    assert app.page.is_hidden("#saved-search-name-dialog")
    assert "Correct the highlighted filters" in _message(app) and "Nothing was saved" in _message(app)
    assert _stored(app) is None


# 3. Applying sets the goal, starts at page 1, and adds one history entry and one read.
def test_apply_sets_the_goal_resets_the_page_and_reads_once(app, add):
    for i in range(45):
        add(external_id=f"more{i}", title=f"Moradia extra {i}", price=30000 + i, date_end="2031-01-01T10:00:00+00:00")
    app.open()
    _save_as(app, "Every home")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    app.page.locator("#pagination button", has_text="2").click()
    app.idle()
    assert _query(app)["page"] == "2"
    for goal_first in (False, True):
        if goal_first:                                         # from another goal: back to its own
            app.page.click("#goal-invest")
            app.idle()
        length = app.page.evaluate("history.length")
        reads = len(app.calls("/api/listings"))
        _choose(app, "Every home")
        app.page.click("#saved-search-apply")
        app.idle()
        app.page.wait_for_timeout(200)
        assert _query(app)["mode"] == "home" and _query(app)["page"] == "1"
        assert app.page.get_attribute("#goal-home", "aria-selected") == "true"
        assert app.page.evaluate("history.length") == length + 1
        assert len(app.calls("/api/listings")) == reads + 1
        assert app.calls("/api/listings")[-1][2]["mode"] == ["home"]


# 4. A source or method not offered now, or lists that failed, are never widened to All.
def test_an_unavailable_source_is_not_applied(app):
    app.open()
    _put(app, {"version": 1, "records": [{"id": "s1", "name": "Gone source", "view": _record(source="nosuch")}]})
    app.page.click("#saved-search-reload")
    _choose(app, "Gone source")
    url, reads = app.page.url, len(app.calls("/api/listings"))
    app.page.click("#saved-search-apply")
    app.page.wait_for_timeout(200)
    assert "Not applied: Source “nosuch” is not available" in _message(app)
    assert app.page.url == url and len(app.calls("/api/listings")) == reads
    assert len(_stored(app)["records"]) == 1                     # kept, not dropped


def test_failed_metadata_keeps_a_saved_source_from_being_widened(app):
    app.override["/api/meta"] = lambda q, b: (500, {"error": "fixture failure"})
    app.open()
    _put(app, {"version": 1, "records": [{"id": "s1", "name": "Eleiloes only", "view": _record(source="eleiloes")}]})
    app.page.click("#saved-search-reload")
    _choose(app, "Eleiloes only")
    app.page.click("#saved-search-apply")
    assert "Not applied: Source “eleiloes”" in _message(app)
    assert _query(app)["source"] == ""


def test_a_maximum_above_the_budget_is_narrowed_and_said(app):
    app.open()
    budget = app.page.evaluate("GOAL_BUDGETS.home")
    if not budget:
        pytest.skip("no home budget in this configuration")
    _put(app, {"version": 1, "records": [{"id": "s1", "name": "Over", "view": _record(max_price=budget + 5000)}]})
    app.page.click("#saved-search-reload")
    _choose(app, "Over")
    app.page.click("#saved-search-apply")
    app.idle()
    assert "above this goal's budget now" in _message(app)
    assert _query(app)["max_price"] == str(budget)
    assert _stored(app)["records"][0]["view"]["max_price"] == budget + 5000   # kept as saved
    assert app.page.is_hidden("#saved-search-modified")


# 5. Corrupt, blocked or full storage: said, nothing replaced, browsing still works.
def test_corrupt_storage_is_reported_and_left_alone(app):
    app.open()
    _put(app, "corrupt {")
    app.page.click("#saved-search-reload")
    assert app.page.is_visible("#saved-search-warning") and "could not be loaded" in app.text("#saved-search-warning")
    assert app.page.is_disabled("#saved-search-create") and app.page.is_disabled("#saved-search-select")
    assert app.page.evaluate(f"localStorage.getItem('{KEY}')") == "corrupt {"
    _search(app, "fixture 3")
    assert app.page.locator("#rows tr[data-id]").count() == 1


def test_full_storage_says_so_and_never_shows_saved(app):
    app.open()
    app.page.evaluate("""() => { const real = Storage.prototype.setItem;
        Storage.prototype.setItem = function (k, v) {
          if (k === 'auction-scanner:saved-searches:v1') throw new DOMException('full', 'QuotaExceededError');
          return real.call(this, k, v); }; }""")
    _save_as(app, "Will not fit")
    assert "could not save this change" in app.text("#saved-search-name-error")
    assert app.page.is_visible("#saved-search-name-dialog")
    assert "Saved" not in _message(app)
    app.page.click('#saved-search-name-dialog button[value="cancel"]')
    assert _stored(app) is None


def test_blocked_storage_is_reported(browser, db, add):
    import config
    import dashboard
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    add(external_id="h0", title="Moradia fixture 0", price=20000, date_end="2031-01-01T10:00:00+00:00")
    context = browser.new_context()
    context.add_init_script("""Object.defineProperty(window, 'localStorage',
        {get() { throw new DOMException('blocked', 'SecurityError'); }});""")
    page = context.new_page()
    blocked = harness.App(page, dashboard.app.test_client())
    try:
        blocked.open()
        assert "could not be loaded" in blocked.text("#saved-search-warning")
        assert page.locator("#rows tr[data-id]").count() == 1
        assert not blocked.errors, blocked.errors
    finally:
        context.close()


# 6. Another tab's change is said, never applied here; editing waits for Reload.
def test_another_tabs_change_waits_for_reload(app):
    app.open()
    _save_as(app, "Mine")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    url, reads = app.page.url, len(app.calls("/api/listings"))
    theirs = _stored(app)
    theirs["records"][0]["name"] = "Theirs"
    _put(app, theirs)
    app.page.evaluate(f"window.dispatchEvent(new StorageEvent('storage', {{key: '{KEY}'}}))")
    assert "changed in another tab" in app.text("#saved-search-warning")
    assert app.page.is_disabled("#saved-search-rename") and app.page.is_disabled("#saved-search-create")
    app.page.wait_for_timeout(200)
    assert app.page.url == url and len(app.calls("/api/listings")) == reads
    app.page.click("#saved-search-reload")
    assert app.page.is_hidden("#saved-search-warning")
    assert app.page.locator("#saved-search-select option").all_text_contents() == ["Choose…", "Theirs"]


def test_a_change_without_a_storage_event_is_still_caught(app):
    app.open()
    _save_as(app, "Mine")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    theirs = _stored(app)
    theirs["records"][0]["name"] = "Theirs"
    _put(app, theirs)
    _choose(app, "Mine")
    app.page.click("#saved-search-rename")
    app.page.fill("#saved-search-name", "Mine again")
    app.page.click("#saved-search-name-ok")
    assert "another tab" in app.text("#saved-search-name-error")
    assert _stored(app)["records"][0]["name"] == "Theirs"


# 7. Notes, the open property, the page and display choices never go into a saved search.
def test_notes_and_the_open_property_are_never_saved(app):
    app.open()
    panel = harness.open_details(app, 2)
    app.hold.add("/api/listing/note")
    field = panel.locator("textarea.d-note")
    field.click()
    field.type("private note text")
    close_details(app)
    app.page.goto(f"{harness.BASE}/?mode=home&property=eleiloes:h2")
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:h2"]')
    close_details(app)
    _layout(app, "cards")
    app.page.click('[data-density="compact"]')
    _save_as(app, "Plain")
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    text = app.page.evaluate(f"localStorage.getItem('{KEY}')")
    for private in ("private note text", "eleiloes:h2", "property", "page", "compact", "cards", "user_note"):
        assert private not in text, private
    assert "Stored in this browser only" in app.text("#saved-searches-help")
    assert "avoid anything sensitive" in app.text("#saved-searches-help")
    harness_release(app)


def harness_release(app):
    app.hold.clear()
    while app.held:
        try:
            app.release()
        except Exception:  # noqa: BLE001
            pass


# 8. Table and cards: the same listings, count and order; switching asks the server nothing.
def test_table_and_cards_show_the_same_page_without_new_reads(app):
    app.open()
    table = _ids(app, "#rows tr[data-id]")
    count = app.text("#result-count")
    reads = len(app.calls("/api/listings")) + len(app.calls("/api/listing"))
    _layout(app, "cards")
    assert app.page.is_hidden("#listings-table") and app.page.is_visible("#listing-cards")
    assert _ids(app, "#listing-cards article.listing-card") == table
    assert app.page.locator("#rows tr").count() == 0             # one set of buttons per listing
    assert app.text("#result-count") == count
    assert app.page.get_attribute('[data-layout="cards"]', "aria-pressed") == "true"
    _layout(app, "table")
    assert _ids(app, "#rows tr[data-id]") == table and app.page.locator("#listing-cards article").count() == 0
    _layout(app, "cards")
    app.page.wait_for_timeout(200)
    assert len(app.calls("/api/listings")) + len(app.calls("/api/listing")) == reads
    reads = len(app.calls("/api/listings"))
    app.page.reload()
    app.page.wait_for_selector("#listing-cards article.listing-card")
    app.idle()
    assert app.page.is_hidden("#listings-table")
    assert len(app.calls("/api/listings")) == reads + 1


# 9. A card says what the row says: price basis, all-in, checks and deadline.
def test_cards_keep_the_rows_summaries(app):
    app.open()
    squash = lambda s: re.sub(r"\s+", " ", s or "").strip()  # noqa: E731
    tr = row(app, 0)
    table = {k: squash(tr.locator(sel).text_content()) for k, sel in
             (("price", ".t-price"), ("allin", ".t-allin"), ("checks", ".t-checks"), ("deadline", ".t-deadline"))}
    _layout(app, "cards")
    card = app.page.locator('#listing-cards article[data-id="eleiloes:h0"]')
    for key, label in (("price", "Price basis"), ("allin", "Estimated all-in"),
                       ("checks", "Unresolved checks"), ("deadline", "Deadline")):
        section = squash(card.locator(f'section[aria-label="{label}"]').text_content())
        assert section.startswith(label) and section[len(label):].strip() == table[key], key
    assert "not independent verification" in card.text_content()


# 10. Details, status and notes from a card; a pending change stays disabled across a switch.
def test_card_details_status_and_pending_buttons(app, db):
    app.open()
    _layout(app, "cards")
    card = app.page.locator('#listing-cards article[data-id="eleiloes:h3"]')
    card.locator('[data-role="details"]').click()
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:h3"] textarea.d-note')
    assert card.locator('[data-role="details"]').get_attribute("aria-expanded") == "true"
    note = app.page.locator("#property-drawer textarea.d-note")
    note.fill("card note")
    note.press("Tab")
    app.page.wait_for_function("() => document.querySelector('#property-drawer .note-phase').textContent === 'Saved'")
    close_details(app)
    assert app.page.evaluate("document.activeElement.closest('#listing-cards article').dataset.id") == "eleiloes:h3"
    app.hold.add("/api/listings/status")
    card.locator('[data-role="star"]').click()
    app.wait_held("/api/listings/status")
    _layout(app, "table")
    assert row(app, 3).locator('[data-role="star"]').is_disabled()
    _layout(app, "cards")
    assert card.locator('[data-role="star"]').is_disabled()
    app.hold.clear()
    app.release()
    app.page.wait_for_function("""() => (document.querySelector('#listing-cards article[data-id="eleiloes:h3"] [data-role="star"]')
                                       || {}).textContent === 'On Offers'""")
    status = db.execute("SELECT status FROM listing_status WHERE listing_id = 'eleiloes:h3'").fetchone()
    assert status[0] == "shortlisted"
    assert db.execute("SELECT user_note FROM listings WHERE id = 'eleiloes:h3'").fetchone()[0] == "card note"


# 11. Keyboard, the hidden layout, focus after a switch, and a narrow screen.
def test_keyboard_switch_keeps_focus_and_fits_a_narrow_screen(app):
    app.page.set_viewport_size({"width": 360, "height": 740})
    app.open()
    app.page.focus('[data-layout="cards"]')
    app.page.keyboard.press("Enter")
    assert app.page.evaluate("document.activeElement.dataset.layout") == "cards"
    assert app.page.is_hidden("#listings-table")
    assert app.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    app.page.locator('#listing-cards article[data-id="eleiloes:h5"] [data-role="details"]').focus()
    app.page.evaluate("load()")                                    # a redraw keeps the card focused
    app.idle()
    assert app.page.evaluate("document.activeElement.closest('#listing-cards article').dataset.id") == "eleiloes:h5"
    app.page.keyboard.press("Enter")
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:h5"]')
    app.page.keyboard.press("Escape")
    app.page.wait_for_function("() => !document.getElementById('property-drawer').open")
    assert app.page.evaluate("document.activeElement.closest('#listing-cards article').dataset.id") == "eleiloes:h5"


# 12. Hostile names, titles and IDs stay text.
def test_hostile_names_titles_and_ids_stay_text(app, add):
    external = 'z"><img src=x onerror="window.pwned=1">'
    add(external_id=external, title='<img src=x onerror="window.pwned=1">Hostile card moradia', price=19000,
        date_end="2031-01-01T10:00:00+00:00")
    app.open()
    _layout(app, "cards")
    card = app.page.locator("#listing-cards article.listing-card").filter(has_text="Hostile card")
    assert card.get_attribute("data-id") == f"eleiloes:{external}"
    assert app.page.locator("#listing-cards img[src='x']").count() == 0
    name = '<b onmouseover="window.pwned=1">Name</b>'
    _save_as(app, name)
    app.page.wait_for_function("() => !document.getElementById('saved-search-name-dialog').open")
    assert name in app.page.locator("#saved-search-select option").all_text_contents()
    assert app.page.locator("#saved-searches b").count() == 0
    assert app.page.evaluate("window.pwned === undefined")
