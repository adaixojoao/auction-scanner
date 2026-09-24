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
that disagreed with the report. Keywords go through `common.find_terms()`:
whole words, accent-insensitive, negation-aware. Never go back to `x in text`:
that is how "desocupado" counted as occupied and "11/2023" as a 1/2 share.

## Scrapers

- Live in `sources/<country>.py`, registered with `@register(name, country)`.
- Build rows with `common.make_listing()`, save with `db.upsert_listing()`.
- Let the **first** request's exception propagate: `run_source()` records it and
  it shows on `/health`. Swallowing it makes a dead site look like "0 listings".
- Parse a whole card's text with `find_price()` (needs a € sign), a dedicated
  price field with `parse_price()`.
- Selectors for many sources are unconfirmed. Do not claim a scraper works
  unless you have seen it return listings.

## Scraped data is untrusted

URLs go through `common.safe_url()` (http/https only); titles and descriptions
are escaped in every HTML, Markdown and Telegram output. Keep the dashboard's
`debug` off by default.

## Schema changes

Add a numbered `_migrate_vN` in `db.py` and bump `SCHEMA_VERSION`. Migrations
must work on a database created by any earlier version (use `_add_column`).

## Tests

`python -m pytest -q` must pass and stays offline (a fixture refuses network
access). New parsing logic gets a fixture-based test. CI runs the suite and
pyflakes on every push.

## Personal data

`config.json`, `auctions.db`, generated reports and cartas are gitignored;
keep it that way. The proponente's details belong in `config.json`, and
the review page reads them from `/api/proponente`.
