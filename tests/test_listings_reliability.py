"""UI Batch 3: the Listings page's loading, note and refresh markup, and the
server contracts it relies on (note acknowledgement, restore semantics,
never-scanned signal). Browser behaviour is checked separately (see the PR)."""
import re

import pytest

import dashboard
from tests.listings_page import listings_source


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def listings(client):
    return listings_source(client)


def _static(client, name):
    return client.get(f"/static/{name}").get_data(as_text=True).replace("\r\n", "\n")


def test_loading_failure_and_update_regions(listings):
    assert '<div id="listings-load-status" class="small muted load-status" role="status" aria-live="polite" aria-atomic="true"></div>' in listings
    assert re.search(r'<div id="listings-load-error" class="notice warn inline-notice" hidden>\s*<p id="listings-load-error-text"></p>\s*'
                     r'<button type="button" class="btn btn-sm" id="listings-retry">Retry listings</button>', listings)
    assert '<div id="listings-update-notice" class="notice info inline-notice" hidden>' in listings
    assert 'id="listings-refresh">Refresh results</button>' in listings
    # The results, not the whole page, are busy; the first paint says it is loading.
    assert '<div id="listings-results" aria-busy="true">' in listings
    assert '<tbody id="rows"><tr><td colspan="7"><div class="empty"><p>Loading listings…</p>' in listings
    assert "Could not update listings. Previous results are still shown." in listings
    assert "This is not an empty result." in listings


def test_reads_are_latest_wins_and_never_toast_on_abort(client, listings):
    js = _static(client, "app.js")
    assert "function latestRead()" in js and "new AbortController()" in js
    assert 'error.name === "AbortError") return {kind: "obsolete"}' in js
    assert "announce, api, latestRead," in js
    assert "const resultsRead = AS.latestRead();" in listings
    assert 'if (result.kind === "obsolete") return result;' in listings
    assert "fetchLatestListings" not in listings                       # one controller, not two


def test_notes_are_queued_in_memory_with_visible_status(client, listings):
    assert 'onblur="saveNote(this)"' not in listings
    assert 'maxlength="2000"' in listings and 'data-role="note"' in listings
    for phase in ("Unsaved changes", "Saving…", "Saved", "Could not save", "Retry saving note"):
        assert phase in listings, phase
    # One store (static/listing-notes.js, Batch 04), never a second queue beside it.
    assert "function createStore(" in listings and "AuctionNotes.createStore(" in listings
    assert "function createNoteQueue(" not in listings
    # Field IDs from a counter, never from the scraped listing ID.
    assert 'id="note-${AS.esc(d.id)}"' not in listings and "note-field-${++noteSeq}" in listings
    assert 'window.addEventListener("beforeunload"' in listings
    assert '<dialog id="leave-dialog" aria-labelledby="leave-title">' in listings
    # Drafts stay out of the URL and browser storage.
    for name in ("listing-notes.js", "listings-notes.js"):
        script = _static(client, name)
        assert "localStorage" not in script and "sessionStorage" not in script and "history." not in script


def test_scan_suggestion_follows_the_selection(listings):
    assert "Scan Portugal now" not in listings
    assert "function scanSuggestion()" in listings
    for label in ("`Scan ${view.source}`", "`Scan ${name}`", '"Scan all countries"'):
        assert label in listings, label
    assert 'aria-describedby="scan-scope"' in listings


def test_note_endpoint_acknowledges_the_exact_text(client, db, add):
    add(external_id="n1", title="Moradia", price=9000)
    text = "  two spaces kept\n"
    r = client.post("/api/listing/note", json={"id": "eleiloes:n1", "note": text})
    # The listing and the accepted length, never a copy of the text.
    assert r.get_json() == {"ok": True, "id": "eleiloes:n1", "length": len(text)}
    assert db.execute("SELECT user_note FROM listings WHERE id='eleiloes:n1'").fetchone()[0] == text
    # Too long is refused (Batch 3.1: it used to be cut to 2,000 silently); the old note stays.
    r = client.post("/api/listing/note", json={"id": "eleiloes:n1", "note": "x" * 2001})
    assert r.status_code == 400
    assert db.execute("SELECT user_note FROM listings WHERE id='eleiloes:n1'").fetchone()[0] == text
    assert client.post("/api/listing/note", json={"id": "eleiloes:n1", "note": "x" * 2000}).get_json()["length"] == 2000
    client.post("/api/listing/note", json={"id": "eleiloes:n1", "note": ""})
    assert db.execute("SELECT user_note FROM listings WHERE id='eleiloes:n1'").fetchone()[0] is None


def test_undo_puts_back_the_previous_status_and_keeps_the_dismissal_in_history(client, db, add, listings):
    add(external_id="u1", title="Moradia", price=9000)
    post = lambda **body: client.post("/api/listings/status", json={"id": "eleiloes:u1", **body})
    post(status="shortlisted")
    post(status="dismissed", reason="other")
    post(status="shortlisted")                                           # what Undo sends back
    row = db.execute("SELECT status, reason FROM listing_status WHERE listing_id='eleiloes:u1'").fetchone()
    assert tuple(row) == ("shortlisted", None)
    detail = client.get("/api/listing?id=eleiloes:u1").get_json()
    assert [(h["status"], h["reason"]) for h in detail["status_history"]] == [
        ("shortlisted", None), ("dismissed", "other"), ("shortlisted", None)]
    assert "the dismissal and its reason stay in its history" in listings
    assert "the dismissal reason is not kept" not in listings
    assert "function statusHistoryHtml(d)" in listings


def test_listings_say_when_nothing_was_ever_scanned(client, add):
    assert client.get("/api/listings").get_json()["last_scrape"] is None


def test_the_results_table_scrolls_on_its_own_when_narrow(client, listings):
    # At 200% zoom (a 640px-wide view) the page must not scroll sideways.
    assert re.search(r'<div class="table-scroll" role="region" aria-label="Listings results" tabindex="0">\s*'
                     r'<table class="list">', listings)
    css = _static(client, "app.css")
    narrow = css.split("@media (max-width: 900px) {", 1)[1].split("}\n}", 1)[0]
    assert ".table-scroll { overflow-x: auto; position: relative; }" in narrow
    assert ".table-scroll table.list thead th { position: static; }" in narrow
