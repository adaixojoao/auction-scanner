"""UI Batch 1: readability and accessibility of the shared layout and Listings.

Structural checks on the rendered templates and the shared static files. They
do not prove keyboard or visual accessibility in a browser."""
import re

import pytest

import dashboard

PAGES = ("/", "/offers", "/outcomes", "/map", "/sources", "/settings")


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def listings(client):
    return client.get("/").get_data(as_text=True)


def _static(client, name):
    r = client.get(f"/static/{name}")
    try:
        # A Windows checkout has CRLF line endings; compare as LF.
        return r.get_data(as_text=True).replace("\r\n", "\n")
    finally:
        r.close()


def test_filter_labels_are_associated_with_their_controls(listings):
    for label, control in (("Country", "f-country"), ("Property type", "f-kind"), ("Source", "f-source"),
                           ("Status", "f-status"), ("Max listed price", "f-maxprice"), ("Search", "f-search"),
                           ("Climate", "f-climate"), ("Min score", "f-minscore"), ("Sale method", "f-sale-method")):
        assert f'<label for="{control}">{label}</label>' in listings
        assert f'id="{control}"' in listings
    assert 'class="loc-method" aria-label=' in listings and 'class="loc-value" type="text" aria-label=' in listings


def test_goal_tabs_control_one_labelled_panel(listings):
    tabs = re.findall(r'<button type="button" id="(goal-\w+)" data-mode="(\w+)" role="tab" '
                      r'aria-controls="goal-results" aria-selected="(true|false)" tabindex="(0|-1)">', listings)
    assert [t[1] for t in tabs] == ["both", "home", "invest", "land", "forest"]
    assert [t[0] for t in tabs] == [f"goal-{t[1]}" for t in tabs]
    assert [t[2] for t in tabs].count("true") == 1 and [t[3] for t in tabs].count("0") == 1
    assert listings.count('role="tabpanel"') == 1
    assert '<section id="goal-results" role="tabpanel" aria-labelledby="goal-both" tabindex="0">' in listings
    end = '<div class="pagination" id="pagination"></div>\n</section>'
    assert listings.count(end) == 1                                    # the panel closes after pagination
    panel = listings.split('id="goal-results"', 1)[1].split(end, 1)[0]
    for part in ('id="mode-note"', 'id="stats"', 'class="card filters"', 'id="research-candidates"',
                 '<table class="list">'):
        assert part in panel, part
    # The selected state is kept in step by showMode; arrows only move focus.
    assert 'button.setAttribute("aria-selected", String(selected))' in listings
    assert 'setAttribute("aria-labelledby", button.id)' in listings
    assert "AS.initGoalTabs($(\"modes\"), activateGoal)" in listings


def test_sorting_uses_native_header_buttons_and_updates_state(listings):
    for key, label in (("score", "Score"), ("country", "Country"), ("title", "Title"), ("price", "Asking price"),
                       ("current_bid", "Current bid"), ("date_end", "End date")):
        header = re.search(rf'<th scope="col" class="sortable" data-sort="{key}" aria-sort="(\w+)">'
                           rf'<button type="button" class="sort-button" data-sort="{key}" data-label="{label}">',
                           listings)
        assert header, key
    assert listings.count('aria-sort="descending"') == 1           # score, the default
    assert '<span class="sr-only">Actions</span>' in listings
    assert 'th.sortable").forEach' not in listings                 # no second, header-level handler
    assert "function updateSortHeaders()" in listings and "function nextSortDirection(column)" in listings
    assert 'column === "date_end" || column === "price" ? "asc" : "desc"' in listings


def test_row_actions_and_pagination_have_accessible_names(listings):
    for part in ('aria-label="Restore ${propertyLabel}"', 'aria-label="Dismiss ${propertyLabel}"',
                 '"Remove from shortlist" : "Shortlist"}: ${propertyLabel}"', 'aria-pressed="${starred}"',
                 'aria-label="Details: ${propertyLabel}" aria-expanded="false"',
                 'aria-label="Previous page"', 'aria-label="Next page"', "aria-current=\"page\"",
                 'const propertyLabel = AS.esc(it.title || "this listing")'):
        assert part in listings, part
    # Panel IDs come from a counter, not the scraped listing ID.
    assert "details-panel-${detailsSeq}" in listings and "tr.id = nextDetailsId()" in listings


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_one_main_target_and_persistent_announcers(client, path):
    html = client.get(path).get_data(as_text=True)
    assert html.count('id="main-content"') == 1 and html.count("<main") == 1, path
    assert '<a class="skip-link" href="#main-content">Skip to main content</a>' in html
    assert '<nav class="nav" aria-label="Main navigation">' in html
    assert html.count('aria-current="page"') <= 1 or path == "/"
    for region in ("scan-announcement", "ui-announcement"):
        assert html.count(f'id="{region}"') == 1
        assert re.search(rf'<div id="{region}" class="sr-only" role="status" aria-live="polite" '
                         rf'aria-atomic="true"></div>', html)
    assert '<div class="scan-status" id="scan-status"></div>' in html     # not a live region
    assert 'id="scan-btn" type="button" aria-expanded="false" data-menu' in html


def test_the_active_page_is_marked_current(client):
    html = client.get("/offers").get_data(as_text=True)
    assert re.search(r'<a href="/offers" class="active" aria-current="page">', html)


def test_dismissal_cancel_is_native_and_cannot_reuse_a_confirmation(client, listings):
    assert '<dialog id="dismiss-dialog" aria-labelledby="dismiss-title">' in listings
    assert '<p id="dismiss-title"><b>Why are you dismissing this listing?</b></p>' in listings
    assert '<label for="dismiss-reason">Reason</label>' in listings
    assert '<button class="btn" type="submit" value="cancel">Cancel</button>' in listings
    assert '<button class="btn btn-primary" id="dismiss-ok" type="button" value="ok">Dismiss</button>' in listings
    js = _static(client, "app.js")
    reset = js.index('dlg.returnValue = "";')
    assert reset < js.index("dlg.showModal();", reset)
    assert 'finish(dlg.returnValue === "ok")' in js


def test_shared_helpers_are_exported_and_scan_announces_only_transitions(client):
    js = _static(client, "app.js")
    assert "announce, api," in js and "dismiss, initGoalTabs}" in js
    assert 'role="progressbar" aria-label="Scan progress"' in js
    assert 'if (next === scanAnnouncementState) return;' in js
    # Lifecycle toasts do not announce a second time.
    for msg in ('toast("Scan started", "ok", 2800, false)', 'toast("Scan stopped", "ok", 2800, false)',
                'toast("Stopping after the current source", "ok", 2800, false)'):
        assert msg in js, msg
    assert js.count("function initMenus()") == 1 and js.count("initTopbarHeight();") == 1
    assert js.count("pollScan();\n    heartbeat();") == 1                    # no duplicate polling start
    assert 'm.classList.remove("open")' not in js


def test_hidden_is_explicit_and_hidden_rows_stay_readable(client):
    css = _static(client, "app.css")
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}", css)
    assert "tr.dim td { opacity" not in css
    assert "outline: none" not in css.split("input:focus, select:focus, textarea:focus", 1)[1].split("}", 1)[0]
    assert "top: var(--topbar-height)" in css and "prefers-reduced-motion" in css


def test_no_wording_says_dismissal_is_irreversible(listings):
    assert "for good" not in listings
    assert "Restore dismissed listings from Hidden." in listings
