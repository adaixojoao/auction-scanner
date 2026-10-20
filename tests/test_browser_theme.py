"""Batch 10: Appearance (System / Light / Dark), kept in this browser only,
run against the production pages (the harness of tests/test_browser_listings.py:
in-process Flask on a temporary database, no network, synthetic data)."""
import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app = harness.browser, harness.app
KEY = "auction-scanner:theme:v1"

# WCAG contrast of an element's text against the first opaque background behind it.
CONTRAST = r"""sel => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const parse = c => (c.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
                                     .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  let node = el, bg = null;
  while (node && node.nodeType === 1) {
    const c = parse(getComputedStyle(node).backgroundColor);
    if (c.length === 3 || (c.length === 4 && c[3] > 0.9)) { bg = c.slice(0, 3); break; }
    node = node.parentElement;
  }
  bg = bg || parse(getComputedStyle(document.body).backgroundColor).slice(0, 3);
  const fg = parse(getComputedStyle(el).color).slice(0, 3);
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}"""


def _theme(app):
    return app.page.evaluate("document.documentElement.dataset.theme")


def _settings(app):
    app.page.goto(f"{harness.BASE}/settings")
    app.page.wait_for_selector("#theme-preference")


def test_system_follows_the_os_only_while_chosen(app):
    app.page.emulate_media(color_scheme="light")
    app.open()
    assert _theme(app) == "light"
    app.page.emulate_media(color_scheme="dark")
    app.page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    _settings(app)
    assert app.page.input_value("#theme-preference") == "system"
    before = len(app.log)
    app.page.select_option("#theme-preference", "light")
    assert _theme(app) == "light"
    assert len(app.log) == before                          # no reload and no request
    assert app.page.evaluate(f"localStorage.getItem('{KEY}')") == "light"
    app.page.emulate_media(color_scheme="light")
    app.page.emulate_media(color_scheme="dark")
    app.page.wait_for_timeout(50)
    assert _theme(app) == "light"                          # an explicit choice ignores the OS
    assert app.text("#theme-preference-message") == ""
    # Only the preference is stored: nothing else under the key, no other new key.
    assert app.page.evaluate("Object.keys(localStorage).filter(k => k.includes('theme'))") == [KEY]


def test_the_theme_is_set_before_the_stylesheet_and_on_every_page(app):
    app.page.add_init_script(f"localStorage.setItem('{KEY}', 'light')")
    app.open()
    head = app.page.evaluate("[...document.head.children].map(e => e.getAttribute('src') || e.getAttribute('href') || '')")
    theme = next(i for i, h in enumerate(head) if h.endswith("theme-preference.js"))
    sheet = next(i for i, h in enumerate(head) if h.endswith("app.css"))
    assert theme < sheet
    for path in ("/", "/offers", "/sources", "/settings", "/outcomes"):
        app.page.goto(f"{harness.BASE}{path}")
        assert _theme(app) == "light", path
        assert app.page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(245, 247, 250)", path


@pytest.mark.parametrize("stored", ["purple", "", "<script>"])
def test_an_invalid_stored_value_falls_back_to_system(app, stored):
    app.page.add_init_script(f"localStorage.setItem('{KEY}', {stored!r})")
    app.page.emulate_media(color_scheme="dark")
    _settings(app)
    assert app.page.input_value("#theme-preference") == "system"
    assert _theme(app) == "dark"


def test_blocked_storage_says_what_was_not_kept(app):
    app.page.add_init_script("""Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }});""")
    app.page.emulate_media(color_scheme="light")
    _settings(app)
    assert "could not be read" in app.text("#theme-preference-message")
    assert _theme(app) == "light"
    app.page.select_option("#theme-preference", "dark")
    assert _theme(app) == "dark"
    assert "could not be saved in this browser" in app.text("#theme-preference-message")


def test_another_tab_changes_the_theme_without_touching_the_page(app):
    app.open("mode=home&search=fixture")
    other = app.page.context.new_page()
    other.route("**/*", app._handle)
    other.goto(f"{harness.BASE}/settings")
    other.wait_for_selector("#theme-preference")
    listings_reads = len(app.calls("/api/listings"))
    harness.open_details(app, 3)
    note = app.page.locator("#property-drawer textarea.d-note")
    app.hold.add("/api/listing/note")
    note.click()
    note.type("draft text")
    other.select_option("#theme-preference", "light")
    app.page.wait_for_function("document.documentElement.dataset.theme === 'light'")
    assert note.input_value() == "draft text"
    assert len(app.calls("/api/listings")) == listings_reads
    assert "search=fixture" in app.page.evaluate("location.search")
    other.close()
    app.hold.clear()
    while app.held:
        app.release()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_surfaces_stay_readable_in_both_themes(app, theme, add):
    app.page.add_init_script(f"localStorage.setItem('{KEY}', '{theme}')")
    app.open()
    checks = ["body", "#result-count", ".results-toolbar .btn", "label[for='f-search']", "#rows tr td",
              "#goal-home", ".page-head h1"]
    for sel in checks:
        ratio = app.page.evaluate(CONTRAST, sel)
        assert ratio is None or ratio >= 4.5, (theme, sel, ratio)
    harness.open_details(app, 1)
    for sel in ("#property-drawer-title", "#property-drawer .small.muted", "#property-drawer textarea.d-note"):
        ratio = app.page.evaluate(CONTRAST, sel)
        assert ratio is not None and ratio >= 4.5, (theme, sel, ratio)
    harness.close_details(app)
    for n in (1, 2):
        harness.row(app, n).locator('[data-role="compare"]').click()
    app.page.click("#comparison-open")
    app.page.wait_for_selector("#comparison-dialog[open]")
    for sel in ("#comparison-title", "#comparison-dialog .notice", "#comparison-content caption"):
        ratio = app.page.evaluate(CONTRAST, sel)
        assert ratio is not None and ratio >= 4.5, (theme, sel, ratio)
    app.page.keyboard.press("Escape")
    app.page.click("#saved-search-create")
    app.page.wait_for_selector("#saved-search-name-dialog[open]")
    ratio = app.page.evaluate(CONTRAST, "#saved-search-name-dialog label")
    assert ratio is not None and ratio >= 4.5, (theme, ratio)
    # Focus stays visible: the ring is drawn in the focus colour.
    app.page.keyboard.press("Escape")
    app.page.focus("#f-search")
    assert app.page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"
