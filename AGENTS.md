# AGENTS.md — Auction Scanner

Rules for anyone changing this repo: human, Claude Code, Cursor or anything else.
`CLAUDE.md` only points here; add new guidance to this file.

## Never delete listings

Filters, de-duplication and staleness decide what is **shown**, in
`db.load_listings()`. They must not `DELETE`. Deleting made a listing come back
as "new" on the next scrape and left `carta_log` pointing at nothing. If a view
needs different rules, add a parameter to `load_listings()`; do not query
`listings` directly for anything a user sees.

## IDs must be stable

A listing's ID is `source:external_id` and must be the same on every run. Never
use Python's `hash()` for it (it changes per process, so every row looked new
every run and alerts repeated). Use the site's own ID, else
`common.stable_id(...)`. When changing how a source builds IDs, keep the old
form for rows that already exist (see Citius's `-2` suffix, `id_prefix`).

## One score, one loader

`scoring.score()` is the only scorer; the dashboard used to have its own copy
that disagreed with the report. It encodes the owner's goal — homes and plots
at very low prices; rural plots only big and cheap; homes in a good location
without heavy work — and `scoring.property_kind()` is the one classifier
(Offers and letters use it too). Keep changes to the score pointed at that goal,
with a test, and keep `buyer_priorities()` (the AI check's copy) in step. Keywords go through `common.find_terms()`:
whole words, accent-insensitive, negation-aware. Never go back to `x in text`:
that is how "desocupado" counted as occupied and "11/2023" as a 1/2 share.

## One app, one of each

This is a desktop app (`app.py` → `dashboard.py` in its own window). Keep it
congruent — one way to do each thing:

- **One scan routine:** `pipeline.run_scan()`. The app's "Scan now", the
  timetable and `scraper.py` all call it; it holds `scan.lock` and writes
  progress to `scan_state`, which is how the app shows scans started elsewhere.
- **One letter builder:** `letters.build_letter()`. The Offers preview, its PDF,
  its e-mail (sent or opened in a mail app) and `--cartas` all use it. Never
  write letter text in JavaScript or a second template. Which letters a sale
  gets, and how it is bid on (`channel`: letter / online / hearing / formal /
  lawyer), is decided by `letters.LETTER_TYPES`; add a new letter there, with a
  test. Information requests are logged in `carta_log` with `is_offer = 0` and
  never count as an offer. A sent letter is a record: `carta_log.letter_text`
  holds it exactly as sent (edits included), and the Sent tab and its PDF use
  that text; never rebuild a sent letter from today's data. Legal claims in letters and guidance are hedged ("normally",
  "check with…"): do not state rules the code cannot confirm.
- **One layout:** every page extends `templates/base.html` and uses
  `static/app.css` / `static/app.js`. No inline page-specific design systems,
  no second nav. UI text is English; letters are in the sale's language.
- **One place for user choices:** shortlist/dismiss in `listing_status`,
  offers in `carta_log`, settings in `config.json` via the Settings page.

## Scrapers

- Live in `sources/<country>.py`, registered with `@register(name, country)`.
- Build rows with `common.make_listing()`, save with `db.upsert_listing()`.
- Let the **first** request's exception propagate: `run_source()` records it and
  it shows on the Sources page. Swallowing it makes a dead site look like "0 listings".
- Parse a whole card's text with `find_price()` (needs a € sign), a dedicated
  price field with `parse_price()`.
- Selectors for many sources are unconfirmed. Do not claim a scraper works
  unless you have seen it return listings.

## Scraped data is untrusted

URLs go through `common.safe_url()` (http/https only); titles and descriptions
are escaped in every HTML, Markdown and Telegram output (`AS.esc` in the
browser; pass IDs through `data-` attributes, never into `onclick` source).
Keep the dashboard's `debug` off. The local server refuses foreign Host headers
and cross-origin POSTs (`_guard_local`) — it can change settings and install a
scheduled task, so keep that guard.

## Schema changes

Add a numbered `_migrate_vN` in `db.py` and bump `SCHEMA_VERSION`. Migrations
must work on a database created by any earlier version (use `_add_column`).

## Tests

`python -m pytest -q` must pass and stays offline (a fixture refuses network
access). New parsing logic gets a fixture-based test. CI runs the suite and
pyflakes on every push.

## Personal data

`config.json`, `auctions.db`, `reports/`, generated cartas and logs are
gitignored; keep it that way. The proponente's details belong in `config.json`
(edited on the Settings page). **This repository is public:** never put real
names, tax numbers, addresses, phone numbers, tokens or passwords in code,
defaults, tests or fixtures (a test checks the config defaults).
