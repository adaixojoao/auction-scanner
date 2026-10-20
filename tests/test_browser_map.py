"""Batch 10: the map of the current Listings page, run against the production
page (the harness of tests/test_browser_listings.py: in-process Flask on a
temporary database, every request answered in the test, no network,
synthetic data). Leaflet is replaced by a small recording stand-in served at
its usual address, so no map library or tile is ever fetched."""
import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app, row, close_details = harness.browser, harness.app, harness.row, harness.close_details

# What listings-map.js uses of Leaflet, recording what it is asked to do.
FAKE_LEAFLET = r"""
(() => {
  const log = window.__leaflet = {fits: 0, views: 0, markers: [], tiles: null, removed: false};
  function emitter(o) { o._on = {}; o.on = (ev, fn) => { o._on[ev] = fn; return o; }; return o; }
  window.L = {
    map(el) {
      const popup = document.createElement("div"); popup.className = "fake-popup"; el.appendChild(popup);
      return {_el: el, _popup: popup, _z: 6,
        setView(c, z) { log.views++; this._c = c; this._z = z; return this; },
        fitBounds(b) { log.fits++; log.bounds = b; return this; },
        getZoom() { return this._z; }, invalidateSize() {}, remove() { log.removed = true; }};
    },
    tileLayer(url) { return emitter({url, addTo(m) { log.tiles = this; this._map = m; return this; }}); },
    layerGroup() {
      return {addTo(m) { this._map = m; return this; }, clearLayers() { log.markers = []; }};
    },
    circleMarker(latlng, style) {
      const marker = {latlng, style: {...style},
        bindPopup(fn) { this._content = fn; return this; }, bindTooltip(t) { this.tooltip = t; return this; },
        addTo(layer) { this._map = layer._map; log.markers.push(this); return this; },
        setStyle(s) { this.style = {...s}; return this; }, bringToFront() {}, getLatLng() { return latlng; },
        openPopup() { this._map._popup.replaceChildren(this._content()); return this; }};
      return marker;
    },
    latLngBounds(points) { return points; }
  };
})();
"""


def _with_leaflet(app):
    app.foreign = []

    def serve(route):
        url = route.request.url
        app.foreign.append(url)
        if url.endswith("leaflet.js"):
            route.fulfill(status=200, content_type="application/javascript", body=FAKE_LEAFLET)
        else:
            route.fulfill(status=200, content_type="text/css", body="")
    app.page.route("https://unpkg.com/**", serve)


def _show(app):
    app.page.click("#show-map")
    # Settled: the library has loaded (or failed), not just the counts drawn while it loads.
    app.page.wait_for_function("""() => { const t = document.getElementById('listings-map-status').textContent;
        return /stored position/.test(t) && !t.includes('Loading the map'); }""")


def _ids(app, selector):
    return app.page.locator(selector).evaluate_all("els => els.map(e => e.dataset.id)")


def _located(app):
    app.page.wait_for_function("() => window.__leaflet && window.__leaflet.markers.length > 0")
    return app.page.evaluate("""() => window.__leaflet.markers.map(m => ({
        at: m.latlng, dashed: !!m.style.dashArray, weight: m.style.weight, tooltip: m.tooltip}))""")


def _pins(add):
    add(external_id="p1", title="Moradia exacta", price=20500, raw_json={"lat": 38.71, "lon": -9.14},
        date_end="2031-01-01T10:00:00+00:00")
    add(external_id="p2", title="Moradia vila", price=20600, concelho="Lisboa",
        raw_json={"geo": {"lat": 38.72, "lon": -9.13, "precision": "municipality", "query": "Lisboa"}},
        date_end="2031-01-01T10:00:00+00:00")
    add(external_id="p3", title="Moradia mesmo sítio", price=20700, raw_json={"lat": 38.71, "lon": -9.14},
        date_end="2031-01-01T10:00:00+00:00")
    add(external_id="bad", title="Moradia fora", price=20800, raw_json={"lat": 95, "lon": 0},
        date_end="2031-01-01T10:00:00+00:00")


# The same page as the results: located and unlocated add up to its IDs.
def test_the_map_shows_this_page_and_keeps_every_unlocated_property(app, add):
    _pins(add)
    _with_leaflet(app)
    app.open()
    assert app.page.is_hidden("#listings-map-section")
    assert app.foreign == []                                   # nothing fetched until the map is shown
    _show(app)
    rows = _ids(app, "#rows tr[data-id]")
    located = _ids(app, "#listings-map-located li")
    unlocated = _ids(app, "#listings-map-unlocated li")
    assert sorted(located + unlocated) == sorted(rows)
    assert sorted(located) == ["eleiloes:p1", "eleiloes:p2", "eleiloes:p3"]
    assert "eleiloes:bad" in unlocated                          # out of range: unlocated, not clamped
    assert app.page.locator('#rows tr[data-id="eleiloes:bad"]').count() == 1
    status = app.text("#listings-map-status")
    assert f"3 of {len(rows)} properties on this page have a stored position; {len(rows) - 3} have none." in status
    assert "(3)" in app.text("#listings-map-located-summary")
    # The municipality pin says it is approximate, in words and in its drawing.
    town = app.page.locator('#listings-map-located li[data-id="eleiloes:p2"]').text_content()
    assert "Municipality estimate" in town and "approximate" in town
    marks = _located(app)
    assert len(marks) == 2                                      # p1 and p3 share one spot
    assert [m["dashed"] for m in marks if m["at"] == [38.72, -9.13]] == [True]
    assert [m["dashed"] for m in marks if m["at"] == [38.71, -9.14]] == [False]
    assert [m["tooltip"] for m in marks if m["at"] == [38.71, -9.14]] == ["2 properties at this spot"]
    assert app.calls("/api/listing") == []                      # drawing reads no details
    assert not any("openstreetmap" in u or "nominatim" in u for u in app.foreign)


# Properties at one spot stay separately reachable; Details opens the right drawer; Compare is the same selection.
def test_same_spot_popup_details_and_compare(app, add):
    _pins(add)
    _with_leaflet(app)
    app.open()
    _show(app)
    app.page.evaluate("() => { window.__leaflet.markers.find(m => m.tooltip === '2 properties at this spot').openPopup(); }")
    popup = app.page.locator("#listings-map .fake-popup")
    assert popup.locator(".map-popup-item").count() == 2
    assert "2 properties at this spot" in popup.text_content()
    popup.locator('[data-role="compare"][data-id="eleiloes:p3"]').click()
    assert app.page.get_attribute('#rows tr[data-id="eleiloes:p3"] [data-role="compare"]', "aria-pressed") == "true"
    assert app.page.get_attribute('#listings-map-located [data-role="compare"][data-id="eleiloes:p3"]',
                                  "aria-pressed") == "true"
    popup.locator('[data-role="map-details"][data-id="eleiloes:p1"]').click()
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:p1"] textarea.d-note')
    assert app.page.evaluate("Math.max(...window.__leaflet.markers.map(m => m.style.weight))") == 4
    close_details(app)
    # The list is the keyboard way in, with its own Details.
    app.page.click('#listings-map-section summary#listings-map-unlocated-summary')
    button = app.page.locator('#listings-map-unlocated [data-role="map-details"][data-id="eleiloes:bad"]')
    button.focus()
    app.page.keyboard.press("Enter")
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:bad"] textarea.d-note')
    close_details(app)
    assert app.page.evaluate("document.activeElement.dataset.id") == "eleiloes:bad"
    assert app.calls("/api/listings/status", "POST") == []


# The first view fits the page; later refreshes keep the view; Fit this page fits again.
def test_no_refit_on_refresh_and_fit_this_page(app, add):
    _pins(add)
    _with_leaflet(app)
    app.open()
    _show(app)
    _located(app)
    assert app.page.evaluate("window.__leaflet.fits") == 1
    app.page.evaluate("load()")
    app.idle()
    app.page.evaluate("load()")
    app.idle()
    assert app.page.evaluate("window.__leaflet.fits") == 1
    row(app, 2).locator('[data-role="details"]').click()
    app.page.wait_for_selector('#property-drawer[open][data-for="eleiloes:h2"] textarea.d-note')
    close_details(app)
    assert app.page.evaluate("window.__leaflet.fits") == 1     # selection does not refit
    app.page.click("#listings-map-fit")
    assert app.page.evaluate("window.__leaflet.fits") == 2
    # The visibility choice is the only thing kept.
    assert app.page.evaluate("localStorage.getItem('auction-scanner:listings-map:v1')") == "shown"
    app.page.reload()
    app.page.wait_for_selector("#rows tr[data-id]")
    app.idle()
    assert app.page.is_visible("#listings-map-section")


# A failed refresh labels the map as the previous view; a tile failure says so; no library: the lists remain.
def test_failed_refresh_tiles_and_missing_library_are_honest(app, add):
    _pins(add)
    _with_leaflet(app)
    app.open()
    _show(app)
    _located(app)
    app.page.evaluate("() => { window.__leaflet.tiles._on.tileerror(); }")
    assert "Some map tiles could not be loaded" in app.text("#listings-map-status")
    app.override["/api/listings"] = lambda q, b: (500, {"error": "boom"})
    app.page.evaluate("load()")
    app.page.wait_for_function("() => /Previous results/.test(document.getElementById('listings-map-status').textContent)")
    assert app.page.locator("#listings-map-located li").count() == 3


def test_without_the_map_library_the_lists_still_hold_every_property(app, add):
    _pins(add)                                     # no stand-in: unpkg.com answers 404 here
    app.open()
    app.page.click("#show-map")
    app.page.wait_for_function("() => /could not be loaded/.test(document.getElementById('listings-map-status').textContent)")
    rows = _ids(app, "#rows tr[data-id]")
    assert sorted(_ids(app, "#listings-map-located li") + _ids(app, "#listings-map-unlocated li")) == sorted(rows)
    assert app.page.locator('#listings-map-located [data-role="map-locate"]').count() == 0
    assert "0 listings" not in app.text("#listings-map-status")


# Hostile titles stay text in the lists and popups.
def test_hostile_titles_stay_text_on_the_map(app, add):
    hostile = '<img src=x onerror="window.__pwned=1">'
    add(external_id="x1", title=f"Moradia {hostile}", price=20500, raw_json={"lat": 38.7, "lon": -9.1},
        date_end="2031-01-01T10:00:00+00:00")
    _with_leaflet(app)
    app.open()
    _show(app)
    app.page.evaluate("() => { window.__leaflet.markers[0].openPopup(); }")
    assert hostile in app.text("#listings-map .fake-popup")
    assert hostile in app.text("#listings-map-located")
    assert app.page.locator("#listings-map-section img").count() == 0
    assert not app.page.evaluate("window.__pwned === 1")
