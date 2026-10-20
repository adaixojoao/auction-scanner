"""Batch 09: comparing up to four selected properties side by side, run against
the production Listings page (the harness of tests/test_browser_listings.py:
in-process Flask on a temporary database, every request answered in the test,
no network, synthetic data)."""
import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, row, close_details = harness.browser, harness.app, harness.row, harness.close_details
DIALOG = "#comparison-dialog"


def _compare(app, n):
    row(app, n).locator('[data-role="compare"]').click()


def _chosen(app):
    return app.page.locator("#comparison-selections li").all_text_contents()


def _open(app):
    app.page.click("#comparison-open")
    app.page.wait_for_selector(f"{DIALOG}[open]")


def _loaded(app, count):
    app.page.wait_for_function(
        """n => { const c = document.querySelectorAll('#comparison-content thead th[scope=col]').length - 1;
                  return c === n && !document.querySelector('#comparison-content').textContent.includes('Loading…'); }""",
        arg=count)


def _column(app, index):
    """The criterion → text of the index-th compared property."""
    return app.page.evaluate("""i => Object.fromEntries([...document.querySelectorAll('#comparison-content tbody tr')]
        .map(tr => [tr.querySelector('th').textContent, tr.querySelectorAll('td')[i].textContent]))""", index)


def _details_with(app, change):
    def answer(q, b):
        resp = app.client.get(f"/api/listing?id={q['id'][0]}")
        payload = resp.get_json()
        change(q["id"][0], payload)
        return resp.status_code, payload
    app.override["/api/listing"] = answer


def _release_all(app):
    app.hold.clear()
    while app.held:
        try:
            app.release()
        except Exception:  # noqa: BLE001 — the page already aborted that request
            pass


def _status_posts(app):
    return app.calls("/api/listings/status", "POST")


# 1. Select from the table, cards and research cards; a duplicate counts once; a fifth is refused.
def test_selection_across_surfaces_at_most_four(app, add, monkeypatch):
    harness._a_research_lead(monkeypatch)
    add("eleiloes", "lead", title="Moradia T3", price=22000, area_m2=90, url="https://example.test/lead",
        date_end="2031-01-01T10:00:00+00:00")
    app.open("mode=both&min_score=0")
    research = app.page.locator("#research-candidates article.research-card").first
    research_id = research.get_attribute("data-id")
    card = research.locator('[data-role="compare"]')
    card.click()
    assert card.get_attribute("aria-pressed") == "true"
    assert card.text_content() == "Selected to compare"
    # The selection is kept across the goal tabs too.
    app.page.click("#goal-home")
    app.idle()
    lead = app.page.locator(f'#rows tr[data-id="{research_id}"] [data-role="compare"]')
    if lead.count():
        assert lead.get_attribute("aria-pressed") == "true"
    for n in (1, 2):
        _compare(app, n)
    app.page.click('[data-layout="cards"]')
    app.page.click('#listing-cards [data-id="eleiloes:h3"] [data-role="compare"]')
    assert len(_chosen(app)) == 4
    assert "4 of 4 selected" in app.text("#comparison-count")
    app.page.click('#listing-cards [data-id="eleiloes:h4"] [data-role="compare"]')
    assert "at most four" in app.text("#comparison-message")
    assert len(_chosen(app)) == 4
    assert app.page.get_attribute('#listing-cards [data-id="eleiloes:h4"] [data-role="compare"]',
                                  "aria-pressed") == "false"
    # The same selection from another surface: unselecting there counts once.
    app.page.click('[data-layout="table"]')
    assert row(app, 3).locator('[data-role="compare"]').get_attribute("aria-pressed") == "true"
    _compare(app, 1)
    assert len(_chosen(app)) == 3
    assert _status_posts(app) == []


# 2. Remove and Clear update every button and change no status.
def test_remove_and_clear_update_buttons_without_status_changes(app):
    app.open()
    for n in (0, 1, 2):
        _compare(app, n)
    assert app.page.is_visible("#comparison-tray")
    assert not app.page.is_disabled("#comparison-open")
    app.page.click('#comparison-selections [data-role="compare-remove"][data-id="eleiloes:h1"]')
    assert row(app, 1).locator('[data-role="compare"]').get_attribute("aria-pressed") == "false"
    assert "Removed" in app.text("#comparison-message")
    assert app.page.evaluate("document.activeElement.id") == "comparison-open"
    app.page.click("#comparison-clear")
    assert app.page.is_hidden("#comparison-tray")
    for n in (0, 2):
        assert row(app, n).locator('[data-role="compare"]').get_attribute("aria-pressed") == "false"
    assert "statuses did not change" in app.text("#comparison-message")
    assert _status_posts(app) == []
    assert app.calls("/api/listing") == []                  # selecting reads nothing


# 3. Filters and the layout switch keep the selection; one outside the results says so.
def test_filters_and_layout_keep_the_selection(app):
    app.open()
    _compare(app, 2)
    _compare(app, 5)
    app.page.fill("#f-search", "fixture 2")
    app.page.keyboard.press("Enter")
    app.idle()
    assert row(app, 5).count() == 0
    chosen = _chosen(app)
    assert len(chosen) == 2
    assert "(out of current results)" in chosen[1] and "(out of current results)" not in chosen[0]
    assert row(app, 2).locator('[data-role="compare"]').get_attribute("aria-pressed") == "true"
    app.page.click('[data-layout="cards"]')
    assert app.page.get_attribute('#listing-cards [data-id="eleiloes:h2"] [data-role="compare"]',
                                  "aria-pressed") == "true"
    _open(app)
    _loaded(app, 2)
    assert "Out of current results" in app.text("#comparison-content thead")
    for gone in ("compare", "eleiloes:h5", "selected"):
        assert gone not in app.page.evaluate("location.search")


# 4. Real summaries: the price basis, unverified checks, date-only precision; gaps say so.
def test_the_comparison_shows_each_listings_own_summaries(app, add):
    add(external_id="d1", title="Moradia data", price=30000, date_end="2031-03-04")
    app.open()
    _compare(app, 0)
    app.page.click('#rows tr[data-id="eleiloes:d1"] [data-role="compare"]')
    _open(app)
    _loaded(app, 2)
    first, dated = _column(app, 0), _column(app, 1)
    assert first["Price basis"] != "Price basis not established" and ":" in first["Price basis"]
    assert "Not published" not in first["Price basis"]
    assert "verified)" not in first["Ownership, possession, and legal access"].replace("unverified)", "")
    assert "closing time not published; date only" in dated["Deadline and precision"]
    assert first["Floor / plot area"] == "Not established"
    assert "not verification" in first["Goal and opportunity score"]
    assert "Research comparison only" in app.text(DIALOG)
    assert app.page.locator(f"{DIALOG} .winner, {DIALOG} [data-best]").count() == 0


# 5. Reads: the latest answer wins, a removed or closed one is ignored, a failed refresh says so.
def test_reverse_answers_removal_close_and_failed_refresh(app):
    app.open()
    for n in (0, 1, 2):
        _compare(app, n)
    app.hold.add("/api/listing")
    _open(app)
    app.wait_held("/api/listing", 3)
    assert "Loading…" in app.text("#comparison-content")
    app.hold.clear()
    app.release(2)                                          # the last first
    app.release(1)
    app.page.wait_for_function("() => document.querySelectorAll('#comparison-content tbody tr:first-child td').length === 3")
    app.page.click('#comparison-content [data-role="compare-remove"][data-id="eleiloes:h0"]')
    assert app.page.evaluate("document.activeElement.id") == "comparison-close"
    app.release(0)                                          # removed meanwhile: ignored
    _loaded(app, 2)
    titles = app.page.locator("#comparison-content thead th[scope=col] > div").all_text_contents()
    assert titles == ["Moradia fixture 1", "Moradia fixture 2"]
    app.override["/api/listing"] = lambda q, b: (500, {"error": "boom"})
    app.page.click('#comparison-content [data-role="compare-refresh"][data-id="eleiloes:h1"]')
    app.page.wait_for_function("() => document.querySelector('#comparison-content').textContent.includes('Unavailable — refresh to retry')")
    assert _column(app, 1)["Price basis"] != "Unavailable — refresh to retry"
    # Closing while a read is on its way: nothing is drawn afterwards.
    del app.override["/api/listing"]
    app.hold.add("/api/listing")
    app.page.click('#comparison-content [data-role="compare-refresh"][data-id="eleiloes:h2"]')
    app.wait_held("/api/listing")
    app.page.click("#comparison-close")
    _release_all(app)
    app.page.wait_for_timeout(150)
    assert not app.page.evaluate("document.getElementById('comparison-dialog').open")
    assert app.page.evaluate("document.activeElement.id") == "comparison-open"


def test_a_scan_or_status_change_marks_the_comparison_stale(app):
    app.open()
    _compare(app, 0)
    _compare(app, 1)
    _open(app)
    _loaded(app, 2)
    app.page.evaluate("document.dispatchEvent(new Event('scan-finished'))")
    app.page.wait_for_function("() => !document.getElementById('comparison-stale').hidden")
    assert "A scan finished" in app.text("#comparison-stale")
    assert app.text("#comparison-content thead").count("May be out of date") == 2
    app.page.click('#comparison-content [data-role="compare-refresh"][data-id="eleiloes:h0"]')
    app.page.wait_for_function(
        "() => document.querySelector('#comparison-content thead').textContent.split('May be out of date').length === 2")


# 6. Comparison → details: one modal at a time, the selection stays, focus comes back.
def test_open_details_from_the_comparison_and_back(app):
    app.open()
    _compare(app, 3)
    _compare(app, 4)
    _open(app)
    _loaded(app, 2)
    app.page.click('#comparison-content [data-role="compare-open"][data-id="eleiloes:h4"]')
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:h4"] textarea.d-note')
    assert app.page.locator("dialog[open]").count() == 1
    assert app.page.get_attribute("#property-drawer-compare", "data-id") == "eleiloes:h4"
    close_details(app)
    app.page.wait_for_function("() => document.activeElement.id === 'comparison-open'")
    assert len(_chosen(app)) == 2
    # The drawer's own Compare button is the same selection.
    harness.open_details(app, 4)
    app.page.click("#property-drawer-compare")
    close_details(app)
    assert len(_chosen(app)) == 1
    assert row(app, 4).locator('[data-role="compare"]').get_attribute("aria-pressed") == "false"


# 7. Hostile text stays text.
def test_hostile_text_is_shown_as_text(app, add):
    hostile = '<img src=x onerror="window.__pwned=1">'
    add(external_id="x1", title=f"Moradia {hostile}", price=30000, date_end="2031-01-01T10:00:00+00:00")
    _details_with(app, lambda _id, p: p.update(reasons=[hostile], concelho=hostile))
    app.open()
    app.page.click('#rows tr[data-id="eleiloes:x1"] [data-role="compare"]')
    _compare(app, 0)
    assert hostile in _chosen(app)[0]
    _open(app)
    _loaded(app, 2)
    assert app.page.locator(f"{DIALOG} img, #comparison-tray img").count() == 0
    assert hostile in _column(app, 0)["Existing opportunity reasons"]
    assert not app.page.evaluate("window.__pwned === 1")


# 8. Keyboard only, and a phone-width viewport.
def test_keyboard_and_narrow_viewport(app):
    app.page.set_viewport_size({"width": 375, "height": 700})
    app.open()
    for n in (0, 1):
        button = row(app, n).locator('[data-role="compare"]')
        button.focus()
        app.page.keyboard.press("Enter")
    assert len(_chosen(app)) == 2
    app.page.focus("#comparison-open")
    app.page.keyboard.press("Enter")
    app.page.wait_for_selector(f"{DIALOG}[open]")
    _loaded(app, 2)
    assert app.page.evaluate("document.activeElement.id") == "comparison-close"
    for _ in range(12):
        app.page.keyboard.press("Tab")
        assert app.page.evaluate("!!document.activeElement.closest('#comparison-dialog') || document.activeElement === document.body")
    width = app.page.evaluate("document.getElementById('comparison-dialog').getBoundingClientRect().width")
    assert width <= 375
    assert app.page.evaluate("document.documentElement.scrollWidth") <= 375 + 1
    app.page.keyboard.press("Escape")
    app.page.wait_for_function("() => document.activeElement.id === 'comparison-open'")


# 9. Notes never reach the comparison, the address or storage; unselected listings are not read.
def test_no_notes_anywhere_and_no_reads_for_unselected(app, db):
    db.execute("UPDATE listings SET user_note = 'secret note text' WHERE id = 'eleiloes:h1'")
    db.commit()
    app.open()
    _compare(app, 1)
    _compare(app, 2)
    _open(app)
    _loaded(app, 2)
    assert "secret note text" not in app.text(DIALOG)
    assert "secret note text" not in app.text("#comparison-tray")
    assert "secret note text" not in app.page.evaluate("location.href")
    storage = app.page.evaluate("JSON.stringify({...localStorage, ...sessionStorage})")
    assert "secret note text" not in storage and "eleiloes:h1" not in storage
    read = sorted(q["id"][0] for _m, _p, q, _b in app.calls("/api/listing"))
    assert read == ["eleiloes:h1", "eleiloes:h2"]
    assert json.dumps(app.page.evaluate("comparison.snapshot()")).count("note") == 0
