"""
db.py — the SQLite store: schema, migrations, writes, and the one loader every
view (report, dashboard, alerts, cartas) reads listings through.

Rules this module enforces:
  * Listings are never deleted. Filters, de-duplication and staleness decide what
    is *shown*; the row stays, so a listing that comes back is not "new" again
    and nothing in carta_log points at a vanished row.
  * The schema is versioned with PRAGMA user_version. Add a new _migrate_vN
    rather than editing an old one.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from common import (
    LOG, effective_end, find_terms, has_term, normalize, parse_dt, safe_url, utcnow, utcnow_iso,
)

DB_PATH = os.environ.get("AUCTION_SCANNER_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "auctions.db")

# A listing is hidden as "stale" once its source has had a successful scrape
# this long after the listing was last seen (i.e. it is gone from the site).
STALE_AFTER = timedelta(days=3)
# A source that fails (or is not scanned) never makes its listings stale, so a
# sold house stays on show. Past this, the score says it is unconfirmed — but
# only when the source was tried and failed after the listing was last seen.
# A source that simply has not been read yet (a fortnightly scan) is not a failure.
UNCONFIRMED_AFTER = timedelta(days=5)
# Dashboard and Telegram: a source with stored listings and no good read this long.
QUIET_AFTER = timedelta(days=3)
# "New" badge / new-today counters.
RECENT = timedelta(hours=24)

SCHEMA_VERSION = 29

# What the user decided about a listing (Listings/Offers pages).
STATUSES = ("shortlisted", "dismissed")
# Why a listing was dismissed. The dashboard asks; a script turns the answers into tests.
DISMISS_REASONS = ("occupied", "heavy work", "not a home", "sold", "false price", "no papers", "other")


# ─── Connection & migrations ─────────────────────────────────────────

def connect(path: str | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(path or DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        # Lets the dashboard read while a scrape is writing.
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    init_db(db)
    return db


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(db: sqlite3.Connection, table: str, column: str, decl: str):
    if column not in _columns(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_v1(db: sqlite3.Connection):
    """The original schema (idempotent: pre-versioning databases already have it)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id          TEXT PRIMARY KEY,  -- source:external_id
            source      TEXT NOT NULL,
            country     TEXT NOT NULL DEFAULT 'PT',  -- ISO 3166-1 alpha-2
            external_id TEXT NOT NULL,
            title       TEXT,
            description TEXT,
            tipo        TEXT,
            area_m2     REAL,
            price       REAL,             -- asking / valor base
            current_bid REAL,
            min_price   REAL,             -- valor minimo
            district    TEXT,
            concelho    TEXT,
            freguesia   TEXT,
            url         TEXT,
            image_url   TEXT,
            date_end    TEXT,             -- auction end datetime (ISO)
            raw_json    TEXT,
            first_seen  TEXT NOT NULL,
            last_seen   TEXT NOT NULL,
            is_new      INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS scrape_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            count     INTEGER,
            status    TEXT,
            message   TEXT
        );
        CREATE TABLE IF NOT EXISTS carta_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT,
            processo TEXT,
            tribunal TEXT,
            country TEXT DEFAULT 'PT',
            sent_date TEXT,
            bid_amount REAL,
            method TEXT DEFAULT 'email',
            outcome TEXT DEFAULT 'pending',
            notes TEXT,
            created_at TEXT NOT NULL
        );
    """)
    _add_column(db, "listings", "country", "TEXT NOT NULL DEFAULT 'PT'")
    _add_column(db, "carta_log", "country", "TEXT DEFAULT 'PT'")
    db.executescript("""
        CREATE INDEX IF NOT EXISTS idx_source ON listings(source);
        CREATE INDEX IF NOT EXISTS idx_price ON listings(price);
        CREATE INDEX IF NOT EXISTS idx_district ON listings(district);
        CREATE INDEX IF NOT EXISTS idx_date_end ON listings(date_end);
        CREATE INDEX IF NOT EXISTS idx_country ON listings(country);
        CREATE INDEX IF NOT EXISTS idx_carta_outcome ON carta_log(outcome);
        CREATE INDEX IF NOT EXISTS idx_carta_processo ON carta_log(processo);
    """)


def _migrate_v2(db: sqlite3.Connection):
    """Non-destructive dedup, price history, per-channel alert log, job clock,
    scrape durations."""
    _add_column(db, "listings", "duplicate_of", "TEXT")
    _add_column(db, "scrape_log", "duration_s", "REAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS price_history (
            listing_id  TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            price       REAL,
            current_bid REAL
        );
        CREATE INDEX IF NOT EXISTS idx_price_history ON price_history(listing_id, observed_at);

        CREATE TABLE IF NOT EXISTS alert_log (
            listing_id TEXT NOT NULL,
            channel    TEXT NOT NULL,
            sent_at    TEXT NOT NULL,
            PRIMARY KEY (listing_id, channel)
        );

        CREATE TABLE IF NOT EXISTS job_runs (
            job      TEXT PRIMARY KEY,
            last_run TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scrape_log_source ON scrape_log(source, timestamp);
    """)
    # Baseline: listings that already existed were already "not new" under the
    # old is_new flag, so the first run after upgrading must not re-alert them.
    now = utcnow_iso()
    for channel in ("telegram", "email"):
        db.execute("""
            INSERT OR IGNORE INTO alert_log (listing_id, channel, sent_at)
            SELECT id, ?, ? FROM listings WHERE is_new = 0
        """, (channel, now))
    # Seed price history with what we know today.
    db.execute("""
        INSERT INTO price_history (listing_id, observed_at, price, current_bid)
        SELECT id, last_seen, price, current_bid FROM listings
        WHERE price IS NOT NULL OR current_bid IS NOT NULL
    """)


def _migrate_v3(db: sqlite3.Connection):
    """The user's decisions per listing, and scan progress shared between the
    app, the scheduler and the command line."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS listing_status (
            listing_id TEXT PRIMARY KEY,
            status     TEXT NOT NULL,       -- shortlisted | dismissed
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scan_state (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            running     INTEGER NOT NULL DEFAULT 0,
            label       TEXT,
            total       INTEGER,
            done        INTEGER,
            current     TEXT,
            started_at  TEXT,
            finished_at TEXT,
            summary     TEXT
        );
        INSERT OR IGNORE INTO scan_state (id, running) VALUES (1, 0);
        CREATE INDEX IF NOT EXISTS idx_carta_listing ON carta_log(listing_id);
    """)


def _migrate_v4(db: sqlite3.Connection):
    """Letters other than offers: which letter was sent (letters.LETTER_TYPES),
    whether it was an offer (an information request is not), and to whom."""
    _add_column(db, "carta_log", "letter_type", "TEXT")
    _add_column(db, "carta_log", "is_offer", "INTEGER NOT NULL DEFAULT 1")
    _add_column(db, "carta_log", "sent_to", "TEXT")


def _migrate_v5(db: sqlite3.Connection):
    """The letter exactly as it was sent, so the Sent tab shows and reprints
    that letter, not one rebuilt today."""
    _add_column(db, "carta_log", "letter_text", "TEXT")
    _add_column(db, "carta_log", "letter_subject", "TEXT")
    _add_column(db, "carta_log", "letter_filename", "TEXT")


def _migrate_v6(db: sqlite3.Connection):
    """Small named values the app keeps between runs (the Telegram update
    offset, short references for Telegram buttons)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)


def _migrate_v7(db: sqlite3.Connection):
    """Information requests the app prepared by itself, waiting for your OK
    (outbox.py). A request is sent only when you tap Send."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS letter_queue (
            listing_id  TEXT PRIMARY KEY,
            letter_type TEXT NOT NULL,
            to_email    TEXT,
            status      TEXT NOT NULL,      -- waiting | sent | skipped
            note        TEXT,
            created_at  TEXT NOT NULL,
            decided_at  TEXT
        );
    """)


def _migrate_v8(db: sqlite3.Connection):
    """Where each municipality's main town is (geo.py), looked up once on
    OpenStreetMap and kept: how far a property is from town is the honest
    version of "good location". lat/lon NULL means "looked for, not found"."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS places (
            key        TEXT PRIMARY KEY,   -- country:municipality, accent-free
            country    TEXT NOT NULL,
            name       TEXT NOT NULL,
            lat        REAL,
            lon        REAL,
            checked_at TEXT NOT NULL
        );
    """)


def _migrate_v9(db: sqlite3.Connection):
    """Positions you verified or cleared (geo.verify_location), with what you
    entered; and, on each offer, how exact the location was when it was sent
    and the reason you gave for sending anyway (the Offers location check)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS location_checks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT NOT NULL,
            action     TEXT NOT NULL,      -- verified | cleared
            method     TEXT,               -- coordinates | address | cadastre
            input      TEXT,               -- what you pasted
            lat        REAL,
            lon        REAL,
            precision  TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_location_checks ON location_checks(listing_id, created_at);
    """)
    _add_column(db, "carta_log", "location_level", "TEXT")
    _add_column(db, "carta_log", "location_override", "TEXT")


def _migrate_v10(db: sqlite3.Connection):
    """Your due-diligence checklist per listing (checklist.py), every change to
    it, the reasons you gave for sending an offer with blocking items left, and
    on each offer the checklist's summary when it was sent."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS checklist_items (
            listing_id  TEXT NOT NULL,
            item_key    TEXT NOT NULL,
            status      TEXT NOT NULL,     -- not_started | requested | verified | not_applicable | concern
            notes       TEXT,
            reference   TEXT,              -- a link, or where the document is
            checked_on  TEXT,
            verified_by TEXT,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (listing_id, item_key)
        );
        CREATE TABLE IF NOT EXISTS checklist_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id   TEXT NOT NULL,
            carta_log_id INTEGER,
            item_key     TEXT,
            action       TEXT NOT NULL,    -- status | override
            old_status   TEXT,
            new_status   TEXT,
            reason       TEXT,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_checklist_log ON checklist_log(listing_id, created_at);
    """)
    _add_column(db, "carta_log", "checklist_summary", "TEXT")


def _migrate_v11(db: sqlite3.Connection):
    """With each offer: the bid calculator's recommended and absolute figures
    (bidcap.py), and a note when the amount you sent differed from them."""
    _add_column(db, "carta_log", "bid_cap_recommended", "REAL")
    _add_column(db, "carta_log", "bid_cap_absolute", "REAL")
    _add_column(db, "carta_log", "bid_cap_note", "TEXT")


def _migrate_v12(db: sqlite3.Connection):
    """Optional detail on how an offer ended (analytics.py): winning bid, all-in
    cost, why it was lost, diligence blocker, and what you found on occupancy,
    title, access and condition after the fact."""
    for col, decl in (
            ("winning_bid", "REAL"),
            ("all_in_cost", "REAL"),
            ("lost_reason", "TEXT"),
            ("diligence_blocker", "INTEGER"),
            ("occupancy_found", "TEXT"),
            ("title_found", "TEXT"),
            ("access_found", "TEXT"),
            ("condition_after", "TEXT"),
    ):
        _add_column(db, "carta_log", col, decl)


def _migrate_v13(db: sqlite3.Connection):
    """Repair place fields that earlier scrapers wrote wrong: Spanish article-only
    towns, French glued titles, Portuguese streets in concelho, Croatian courts
    sitting in district. Parsers refuse these going forward; this clears what is
    already stored so the next lookup does not use them."""
    from sources.es import _ARTICLE, servihabitat_town
    from sources.fr import parse_licitor_list_title
    from sources.hr import _COURT, fina_place
    from sources.pt import _plausible_place

    for row in db.execute(
            "SELECT id, title, concelho FROM listings WHERE source = 'servihabitat'"):
        c = (row["concelho"] or "").strip()
        if not c or not _ARTICLE.match(c):
            continue
        town = servihabitat_town(row["title"] or "")
        if town and not _ARTICLE.match(town.strip()):
            db.execute("UPDATE listings SET concelho = ? WHERE id = ?", (town, row["id"]))
        else:
            db.execute("UPDATE listings SET concelho = NULL WHERE id = ?", (row["id"],))

    for row in db.execute(
            "SELECT id, title, concelho FROM listings WHERE source = 'france'"):
        title, town = parse_licitor_list_title(row["title"] or "")
        if not town:
            continue
        db.execute(
            "UPDATE listings SET title = ?, concelho = COALESCE(?, concelho) WHERE id = ?",
            (title, town, row["id"]))

    for row in db.execute(
            "SELECT id, district, concelho, freguesia FROM listings WHERE source = 'citius'"):
        for field in ("district", "concelho", "freguesia"):
            val = row[field]
            if val and not _plausible_place(val):
                db.execute(f"UPDATE listings SET {field} = NULL WHERE id = ?", (row["id"],))

    for row in db.execute(
            "SELECT id, title, description, district, concelho FROM listings "
            "WHERE source = 'fina'"):
        place = fina_place(f"{row['title'] or ''} {row['description'] or ''}")
        sets, args = [], []
        if place and not row["concelho"]:
            sets.append("concelho = ?")
            args.append(place)
        if row["district"] and _COURT.search(row["district"]):
            sets.append("district = NULL")
        if sets:
            db.execute(f"UPDATE listings SET {', '.join(sets)} WHERE id = ?",
                       (*args, row["id"]))


def _migrate_v14(db: sqlite3.Connection):
    """Clear Spain bidding-step floors that still look like bargains, and polish
    leftover place/title junk the v13 parsers left behind."""
    from sources.es import clear_step_min_price
    from sources.fr import parse_licitor_list_title
    from sources.hr import fina_place
    from sources.pt import _plausible_place

    for row in db.execute(
            "SELECT id, price, min_price FROM listings WHERE source = 'spain' "
            "AND price IS NOT NULL AND min_price IS NOT NULL"):
        clear_step_min_price(db, {"id": row["id"]})

    for row in db.execute(
            "SELECT id, district, concelho, freguesia FROM listings WHERE source = 'citius'"):
        for field in ("district", "concelho", "freguesia"):
            val = row[field]
            if val and not _plausible_place(val):
                db.execute(f"UPDATE listings SET {field} = NULL WHERE id = ?", (row["id"],))

    for row in db.execute(
            "SELECT id, title, description, concelho FROM listings WHERE source = 'fina'"):
        place = fina_place(f"{row['title'] or ''} {row['description'] or ''}")
        if place and place != row["concelho"]:
            db.execute("UPDATE listings SET concelho = ? WHERE id = ?", (place, row["id"]))
        elif row["concelho"] and row["concelho"].endswith(")"):
            db.execute("UPDATE listings SET concelho = ? WHERE id = ?",
                       (row["concelho"].rstrip(")"), row["id"]))

    for row in db.execute("SELECT id, title, concelho FROM listings WHERE source = 'france'"):
        title, town = parse_licitor_list_title(row["title"] or "")
        if title != (row["title"] or "") or (town and not row["concelho"]):
            db.execute(
                "UPDATE listings SET title = ?, concelho = COALESCE(?, concelho) WHERE id = ?",
                (title, town, row["id"]))


def _migrate_v15(db: sqlite3.Connection):
    """Rewrite Whitestar união-de-freguesias labels into geocodable parish names."""
    from common import uf_parish

    for row in db.execute(
            "SELECT id, freguesia FROM listings "
            "WHERE freguesia IS NOT NULL AND freguesia LIKE 'U.F.%'"):
        cleaned = uf_parish(row["freguesia"])
        if cleaned and cleaned != row["freguesia"]:
            db.execute("UPDATE listings SET freguesia = ? WHERE id = ?",
                       (cleaned, row["id"]))


def _migrate_v16(db: sqlite3.Connection):
    """A running scan can be asked to stop once the source it is on has finished."""
    _add_column(db, "scan_state", "stop", "INTEGER NOT NULL DEFAULT 0")


def _migrate_v17(db: sqlite3.Connection):
    """Why an offer was sent above the cash you can lose."""
    _add_column(db, "carta_log", "cash_override", "TEXT")


def _migrate_v18(db: sqlite3.Connection):
    """Free-text notes and contact log per listing."""
    _add_column(db, "listings", "user_note", "TEXT")


def _migrate_v19(db: sqlite3.Connection):
    """What a won purchase then let for or sold for, and after how many months."""
    _add_column(db, "carta_log", "monthly_rent", "REAL")
    _add_column(db, "carta_log", "sale_price", "REAL")
    _add_column(db, "carta_log", "months_held", "REAL")


def _migrate_v20(db: sqlite3.Connection):
    """Why a listing was dismissed (occupied, heavy work, sold, …)."""
    _add_column(db, "listing_status", "reason", "TEXT")


def _migrate_v21(db: sqlite3.Connection):
    """Stable lot identity map for Citius and Leilosoc (F01 / Milestone B).

    lot_map records (source, processo, lot_key) → listing_id so that scraper-local
    encounter-order counters are replaced by persistent, legal-identifier-keyed
    assignments.  conflict_details (JSON) is set when two lots resolve to the same
    key or observable identifiers diverge.
    """
    db.executescript("""
        CREATE TABLE IF NOT EXISTS lot_map (
            source           TEXT NOT NULL,
            processo         TEXT NOT NULL,
            lot_key          TEXT NOT NULL,
            listing_id       TEXT NOT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            conflict_details TEXT,
            PRIMARY KEY (source, processo, lot_key)
        );
        CREATE INDEX IF NOT EXISTS idx_lot_map_listing
            ON lot_map(source, listing_id);
    """)
    # Backfill existing rows so historical IDs are preserved.
    import identity_map as _im
    _im.backfill_citius(db)
    _im.backfill_leilosoc(db)


def _migrate_v22(db: sqlite3.Connection):
    """Non-suppressive similarity hint for cross-source near-matches (F02 / Milestone C).

    possible_match stores the id of one similar listing when the evidence is not
    strong enough to confirm they are the same legal property.  Unlike duplicate_of
    it does not hide the listing; it is surfaced as a research note.
    """
    _add_column(db, "listings", "possible_match", "TEXT")


def _migrate_v23(db: sqlite3.Connection):
    """Evidence revision tracking — content-hash per evidence key (F16 / Milestone D).

    evidence_revisions records a sha1 of each evidence payload the last time it
    was stored.  When the content changes the rev counter increments so the
    evaluator can tell whether a cached Decision is still fresh without
    re-running every check.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS evidence_revisions (
            listing_id   TEXT NOT NULL,
            evidence_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            rev          INTEGER NOT NULL DEFAULT 1,
            observed_at  TEXT NOT NULL,
            PRIMARY KEY (listing_id, evidence_key)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ev_rev_listing ON evidence_revisions(listing_id)")


def _migrate_v24(db: sqlite3.Connection):
    """Scan-completeness columns in scrape_log (F13 / Milestone E).

    scope:        "full" = scraper reached natural end; "partial" = hit budget or
                  error mid-scrape; NULL for old rows (treated as "full" for back-compat).
    pages_seen:   how many source pages the scraper fetched before stopping.

    last_ok_by_source() now excludes partial-scope runs so a budget-capped scrape
    does not advance the freshness clock for unseen listings.
    """
    _add_column(db, "scrape_log", "scope", "TEXT")
    _add_column(db, "scrape_log", "pages_seen", "INTEGER")


def _migrate_v25(db: sqlite3.Connection):
    """Investigation clearances for extreme-discount items (F05 / Milestone G).

    An extreme-discount item is blocked from finalist promotion (load_best)
    until it has been reviewed and cleared.  This table records that clearance.
    Items without a row here remain in the research tab but never reach reports,
    alerts or the Offers shortlist.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS investigation_clearances (
            listing_id  TEXT PRIMARY KEY,
            cleared_at  TEXT NOT NULL,
            note        TEXT
        )
    """)


def _migrate_v26(db: sqlite3.Connection):
    """Normalise lot_map processo keys to match the scraper's canonical form (F01 fix).

    backfill_citius (v21) stored processo as _norm(raw_json.processo), which kept
    punctuation (e.g. "9/21.0TBLSB").  The live scraper strips all non-alphanumeric
    characters (e.g. "9210TBLSB").  On first rescan after v21 the process keys never
    matched so every lot appeared new and risked getting a fresh ID.

    This migration rewrites each citius lot_map row to the canonical form used by
    the scraper.  Collisions (two raw strings that canonicalise to the same key but
    with different lot_keys) are resolved by keeping the existing canonical row and
    marking the old-form row as superseded in conflict_details.
    """
    import json as _json
    import identity_map as _im

    rows = db.execute(
        "SELECT source, processo, lot_key, listing_id, created_at, conflict_details"
        " FROM lot_map WHERE source='citius'"
    ).fetchall()

    for row in rows:
        canonical = _im.processo_root(row["processo"])
        if canonical == row["processo"]:
            continue  # already normalised

        # Check whether the canonical key already exists in lot_map.
        existing = db.execute(
            "SELECT lot_key, listing_id FROM lot_map"
            " WHERE source='citius' AND processo=? AND lot_key=?",
            (canonical, row["lot_key"])).fetchone()

        if existing:
            # Collision: a canonical row already exists for this lot_key.
            # Record the conflict and remove the stale punctuated row.
            conflict = {
                "superseded_by": f"citius:{canonical}:{row['lot_key']}",
                "reason": "processo_canonicalisation_collision",
                "old_processo": row["processo"],
            }
            db.execute(
                "DELETE FROM lot_map WHERE source='citius' AND processo=? AND lot_key=?",
                (row["processo"], row["lot_key"]))
            db.execute(
                "UPDATE lot_map SET conflict_details=? WHERE source='citius' AND processo=? AND lot_key=?",
                (_json.dumps(conflict), canonical, row["lot_key"]))
        else:
            # No collision: rename the row to the canonical processo key,
            # keeping when the mapping was first made (created_at).
            db.execute(
                "UPDATE lot_map SET processo=? WHERE source='citius' AND processo=? AND lot_key=?",
                (canonical, row["processo"], row["lot_key"]))


def _migrate_v27(db: sqlite3.Connection):
    """Bind investigation clearances to the evidence revision sum at the time of review (Task 7).

    Adds evidence_rev_sum to investigation_clearances so a clearance is automatically
    invalidated when material evidence (location, hazard, checklist, identity) changes.
    An existing clearance without the column is preserved with rev_sum=NULL; it will be
    treated as stale and removed the next time load_listings runs for that listing.
    """
    _add_column(db, "investigation_clearances", "evidence_rev_sum", "INTEGER")


def _migrate_v28(db: sqlite3.Connection):
    """Persist evaluator Decisions for freshness tracking (F11 / F16 / Milestone D).

    evaluator_decisions stores the most recent shadow-eval result per listing.
    evidence_rev_sum is the SUM(rev) from evidence_revisions at evaluation time.
    A stored Decision is considered stale when the current evidence_rev_sum differs,
    causing the next load to re-evaluate and overwrite this row.

    state is one of: "research" | "finalist" | "bid_ready".
    requirements_json is a JSON array of {name, outcome, dangerous, reason?} dicts.
    provenance_json is the Decision.provenance dict serialised as JSON.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS evaluator_decisions (
            listing_id        TEXT PRIMARY KEY,
            state             TEXT NOT NULL,
            requirements_json TEXT NOT NULL,
            provenance_json   TEXT NOT NULL,
            evidence_rev_sum  INTEGER,
            evaluated_at      TEXT NOT NULL
        )
    """)


def _migrate_v29(db: sqlite3.Connection):
    """Append-only history of effective material changes accepted by upsert."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS listing_change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            previous_url TEXT,
            current_url TEXT,
            changes_json TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_listing_change_log
        ON listing_change_log(listing_id, id)
    """)


_MIGRATIONS = {1: _migrate_v1, 2: _migrate_v2, 3: _migrate_v3, 4: _migrate_v4, 5: _migrate_v5,
               6: _migrate_v6, 7: _migrate_v7, 8: _migrate_v8, 9: _migrate_v9, 10: _migrate_v10,
               11: _migrate_v11, 12: _migrate_v12, 13: _migrate_v13, 14: _migrate_v14,
               15: _migrate_v15, 16: _migrate_v16, 17: _migrate_v17, 18: _migrate_v18,
               19: _migrate_v19, 20: _migrate_v20, 21: _migrate_v21, 22: _migrate_v22,
               23: _migrate_v23, 24: _migrate_v24, 25: _migrate_v25, 26: _migrate_v26,
               27: _migrate_v27, 28: _migrate_v28, 29: _migrate_v29}


def init_db(db: sqlite3.Connection):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for v in range(version + 1, SCHEMA_VERSION + 1):
        _MIGRATIONS[v](db)
        db.execute(f"PRAGMA user_version = {v}")
        db.commit()
    if version < SCHEMA_VERSION:
        LOG.debug(f"Database schema migrated from v{version} to v{SCHEMA_VERSION}")


# ─── Evidence revisions ──────────────────────────────────────────────

def record_evidence_revision(db: sqlite3.Connection, listing_id: str, key: str, content) -> int:
    """Record or update the revision counter for one evidence payload.

    Computes a content hash of `content` (JSON-serialised, keys sorted).  If
    the hash differs from the last stored value the rev counter increments.
    Returns the current rev (1 on first write, higher when the content changed).
    """
    h = hashlib.sha1(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    row = db.execute(
        "SELECT content_hash, rev FROM evidence_revisions WHERE listing_id=? AND evidence_key=?",
        (listing_id, key),
    ).fetchone()
    now = utcnow_iso()
    if row is None:
        db.execute(
            "INSERT INTO evidence_revisions (listing_id, evidence_key, content_hash, rev, observed_at)"
            " VALUES (?,?,?,1,?)",
            (listing_id, key, h, now),
        )
        return 1
    if row["content_hash"] == h:
        return row["rev"]
    new_rev = row["rev"] + 1
    db.execute(
        "UPDATE evidence_revisions SET content_hash=?, rev=?, observed_at=?"
        " WHERE listing_id=? AND evidence_key=?",
        (h, new_rev, now, listing_id, key),
    )
    return new_rev


def get_evidence_revisions(db: sqlite3.Connection, listing_id: str) -> dict[str, int]:
    """Return {evidence_key: rev} for all evidence recorded for a listing."""
    rows = db.execute(
        "SELECT evidence_key, rev FROM evidence_revisions WHERE listing_id=?", (listing_id,)
    ).fetchall()
    return {r["evidence_key"]: r["rev"] for r in rows}


# ─── Investigation clearances (F05) ──────────────────────────────────

def clear_extreme_discount(db: sqlite3.Connection, listing_id: str,
                           note: str | None = None,
                           evidence_rev_sum: int | None = None) -> None:
    """Record that `listing_id` has been investigated and cleared for finalist promotion.

    `evidence_rev_sum` is the sum of all evidence revision counters at the time of
    review (from the evidence_revisions table).  When the sum later increases, the
    clearance is treated as stale and revoked automatically by load_listings.

    After this call the item will pass the extreme-discount gate in load_best and
    can appear in reports, alerts and the Offers shortlist.  The item remains visible
    in the listing tab (load_listings) regardless.
    """
    db.execute(
        "INSERT OR REPLACE INTO investigation_clearances"
        " (listing_id, cleared_at, note, evidence_rev_sum)"
        " VALUES (?, ?, ?, ?)",
        (listing_id, utcnow_iso(), note, evidence_rev_sum),
    )
    db.commit()


def revoke_extreme_clearance(db: sqlite3.Connection, listing_id: str) -> None:
    """Remove an investigation clearance (e.g. after a re-score still shows extreme discount)."""
    db.execute("DELETE FROM investigation_clearances WHERE listing_id=?", (listing_id,))
    db.commit()


def _extreme_clearances(db: sqlite3.Connection, listing_ids: set[str],
                        ev_rev_sums: dict[str, int] | None = None) -> set[str]:
    """Return the subset of `listing_ids` cleared for finalist promotion.

    If `ev_rev_sums` is supplied, a clearance whose stored `evidence_rev_sum` does not
    match the current sum is treated as stale and left out of the result.  This is a
    read: stale rows are never deleted here (or by any loader) — they are the review
    history, and only an explicit revoke_extreme_clearance() removes one.
    NULL stored sum (pre-v27 clearance) is stale.
    """
    if not listing_ids:
        return set()
    placeholders = ",".join("?" * len(listing_ids))
    rows = db.execute(
        f"SELECT listing_id, evidence_rev_sum FROM investigation_clearances"
        f" WHERE listing_id IN ({placeholders})",
        tuple(listing_ids),
    ).fetchall()
    cleared: set[str] = set()
    for r in rows:
        lid = r["listing_id"]
        stored_rev = r["evidence_rev_sum"]
        if ev_rev_sums is not None:
            current_rev = ev_rev_sums.get(lid, 0)
            if stored_rev is None or stored_rev != current_rev:
                continue
        cleared.add(lid)
    return cleared


# ─── Writes ──────────────────────────────────────────────────────────

_UPDATE_FIELDS = (
    "title", "description", "tipo", "area_m2", "price", "current_bid", "min_price",
    "district", "concelho", "freguesia", "url", "image_url", "date_end", "raw_json",
)


def _is_cut_copy(new: str | None, old: str | None) -> bool:
    """A search page's shortened text ("… concelho de Vila Franca do Cam....
    Modalidade: …") of a text a detail page filled in: keeping it would lose
    what the listing is."""
    if not new or not old or len(new) >= len(old):
        return False
    cut = re.search(r"\.{3,}|…", new)
    if not cut:
        return False
    def loose(text: str) -> str:     # portals trim dots and spaces differently in the preview
        return re.sub(r"[\s.…]+", " ", text).strip()
    stem = loose(new[:cut.start()])
    return len(stem) >= 20 and loose(old).startswith(stem[:-3])


# What the app found out itself (geo.py, photos.py, links.py): a source's fresh
# raw data does not know it, so it is carried over.
LEARNED_RAW_KEYS = ("geo", "geo_checked", "photo_check", "outside_check", "kept_photos", "eleiloes_id",
                    "water_check", "cadastre_checked", "fr_cadastre", "fr_commune_miss", "department",
                    "climate", "verified_geo")


def _keep_learned(new: str | None, old: str | None) -> str | None:
    if not new or not old or not any(f'"{k}"' in old for k in LEARNED_RAW_KEYS):
        return new
    try:
        fresh, before = json.loads(new), json.loads(old)
    except ValueError:
        return new
    if not isinstance(fresh, dict) or not isinstance(before, dict):
        return new
    kept = {k: before[k] for k in LEARNED_RAW_KEYS if k in before and k not in fresh}
    return json.dumps({**fresh, **kept}, ensure_ascii=False) if kept else new


def _upsert_listing_without_change_log(db: sqlite3.Connection, row: dict) -> str:
    """Insert or refresh one listing. Returns "inserted" or "updated".

    Updates use COALESCE so a thin search-page scrape never wipes fields a
    detail-page enrichment filled in. Price/bid changes are kept in
    price_history.
    """
    now = utcnow_iso()
    existing = db.execute(
        "SELECT price, current_bid, title, description, raw_json FROM listings WHERE id = ?", (row["id"],)
    ).fetchone()
    new_price, new_bid = row.get("price"), row.get("current_bid")

    if existing:
        values = {f: row.get(f) for f in _UPDATE_FIELDS}
        values["raw_json"] = _keep_learned(values["raw_json"], existing[4])
        for field in ("title", "description"):
            if _is_cut_copy(values[field], existing[2 if field == "title" else 3]):
                values[field] = None                  # keep the full text
        sets = ", ".join(f"{f}=COALESCE(?, {f})" for f in _UPDATE_FIELDS)
        db.execute(
            f"UPDATE listings SET {sets}, last_seen=?, is_new=0 WHERE id=?",
            tuple(values[f] for f in _UPDATE_FIELDS) + (now, row["id"]),
        )
        old_price, old_bid = existing[0], existing[1]
        changed = ((new_price is not None and new_price != old_price)
                   or (new_bid is not None and new_bid != old_bid))
        if changed:
            db.execute(
                "INSERT INTO price_history (listing_id, observed_at, price, current_bid) "
                "VALUES (?,?,?,?)",
                (row["id"], now,
                 new_price if new_price is not None else old_price,
                 new_bid if new_bid is not None else old_bid),
            )
        return "updated"

    db.execute(f"""
        INSERT INTO listings (id, source, country, external_id, {", ".join(_UPDATE_FIELDS)},
                              first_seen, last_seen, is_new)
        VALUES (?,?,?,?,{",".join("?" * len(_UPDATE_FIELDS))},?,?,1)
    """, (row["id"], row["source"], row.get("country") or "PT", row["external_id"],
          *(row.get(f) for f in _UPDATE_FIELDS), now, now))
    if new_price is not None or new_bid is not None:
        db.execute(
            "INSERT INTO price_history (listing_id, observed_at, price, current_bid) VALUES (?,?,?,?)",
            (row["id"], now, new_price, new_bid),
        )
    return "inserted"


_MATERIAL_LISTING_FIELDS = (
    "title", "description", "tipo", "area_m2", "price", "current_bid",
    "min_price", "district", "concelho", "freguesia", "date_end",
)


def _source_payload_for_history(value):
    """Semantic source payload, excluding established locally learned keys."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, dict):
        return {key: val for key, val in value.items() if key not in LEARNED_RAW_KEYS}
    return value


def _listing_material_changes(before, after) -> dict:
    changes = {}
    for field in _MATERIAL_LISTING_FIELDS:
        if before[field] != after[field]:
            changes[field] = {"before": before[field], "after": after[field]}
    old_payload = _source_payload_for_history(before["raw_json"])
    new_payload = _source_payload_for_history(after["raw_json"])
    if old_payload != new_payload:
        changes["raw_json"] = {"before": old_payload, "after": new_payload}
    return changes


def upsert_listing(db: sqlite3.Connection, row: dict) -> str:
    """Preserve original upsert semantics and atomically journal material changes.

    No commit: caller retains transaction ownership. Initial insertion is not
    a change event. History reflects effective stored values, not rejected thin
    or shortened input. Direct SQL writes are outside this journal boundary.
    """
    # Opening a transaction first prevents releasing the outermost SAVEPOINT
    # from implicitly committing when the caller had no transaction yet.
    if not db.in_transaction:
        db.execute("BEGIN")
    db.execute("SAVEPOINT listing_change_upsert")
    try:
        before = db.execute(
            "SELECT * FROM listings WHERE id = ?", (row["id"],)
        ).fetchone()
        result = _upsert_listing_without_change_log(db, row)
        if before is not None:
            after = db.execute(
                "SELECT * FROM listings WHERE id = ?", (row["id"],)
            ).fetchone()
            changes = _listing_material_changes(before, after)
            if changes:
                db.execute(
                    "INSERT INTO listing_change_log"
                    " (listing_id, source, observed_at, previous_url, current_url, changes_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (after["id"], after["source"], after["last_seen"],
                     safe_url(before["url"]), safe_url(after["url"]),
                     json.dumps(changes, sort_keys=True, ensure_ascii=False)),
                )
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT listing_change_upsert")
        db.execute("RELEASE SAVEPOINT listing_change_upsert")
        raise
    db.execute("RELEASE SAVEPOINT listing_change_upsert")
    return result


def listing_changes(db: sqlite3.Connection, listing_id: str, *,
                    limit: int = 100, before_id: int | None = None) -> list[dict]:
    """Newest-first structured change explanations for a known listing ID.

    Read-only history access; not an alternative listing loader. Consumers must
    first obtain visible listings through load_listings/load_best and escape
    every historical value on output. before_id enables bounded pagination.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("history limit must be an integer between 1 and 1000")
    if before_id is not None and (
            isinstance(before_id, bool) or not isinstance(before_id, int) or before_id < 1):
        raise ValueError("before_id must be a positive integer")
    sql = "SELECT * FROM listing_change_log WHERE listing_id = ?"
    params = [listing_id]
    if before_id is not None:
        sql += " AND id < ?"
        params.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    events = []
    for row in db.execute(sql, params):
        event = dict(row)
        event["changes"] = json.loads(event.pop("changes_json"))
        event["previous_url"] = safe_url(event["previous_url"])
        event["current_url"] = safe_url(event["current_url"])
        events.append(event)
    return events


def record_scrape(db: sqlite3.Connection, source: str, *, count: int, status: str,
                  message: str | None = None, duration_s: float | None = None,
                  timestamp: str | None = None, scope: str | None = None,
                  pages_seen: int | None = None):
    """Log one source run.

    scope: "full" when the scraper reached its natural end; "partial" when it
    stopped early due to a page budget, timeout or mid-scrape error.  Callers
    that don't know yet may omit it (NULL, treated as "full" by last_ok_by_source).
    """
    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status, message, duration_s, scope, pages_seen) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (source, timestamp or utcnow_iso(), count, status, message, duration_s, scope, pages_seen),
    )
    db.commit()


RELISTING_SOURCES = {"fotocasa", "imovirtual", "bienici", "greenacres", "servihabitat", "aliseda", "altamira",
                     "pisos", "thinkspain", "immoweb", "solvia", "imot", "indexoglasi", "nehnutelnosti", "sslv", "safer"}


TWIN_UNIT_TOLERANCE = 0.10
TWIN_UNIT_OPENING = 80    # characters of description that must match: portals title by town only


_FRACTION_RE = re.compile(r"\bfra(?:c|cc)ao\s+(?:autonoma\s+)?(?:designada\s+(?:pela\s+letra\s+)?)?\"?([a-z]{1,3})\b")
_ARTICLE_RE = re.compile(r"\bartigo\s+(?:matricial\s+)?(?:urbano\s+|rustico\s+)?(?:n\.?\s*o?\s*)?(\d{1,6})\b")


def _unit_markers(r) -> dict:
    """What says which legal unit a row is: its street address and its land
    registry identifiers (description number, fraction, tax articles), from
    the stored raw data and from the wording ("fração B", "artigo 1234").
    Empty values are left out: a missing marker neither confirms nor contradicts."""
    try:
        raw = json.loads(r["raw_json"] or "{}")
    except (ValueError, TypeError, IndexError, KeyError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    address = None
    for key in ("morada", "address", "street", "direccion"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            address = re.sub(r"\s+", " ", normalize(val)).strip()
            break
    registry: set = set()
    for entry in raw.get("registo_predial") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("descricao"):
            registry.add(("descricao", str(entry["descricao"]).strip().lower(),
                          str(entry.get("fracao") or "").strip().lower()))
        for art in entry.get("artigos") or []:
            if isinstance(art, dict) and art.get("numero"):
                registry.add(("artigo", str(art["numero"]).strip()))
    if raw.get("fracao"):
        registry.add(("fracao", str(raw["fracao"]).strip().lower()))
    for key in ("artigo", "artigos"):
        val = raw.get(key)
        for v in (val if isinstance(val, list) else [val]):
            if isinstance(v, dict):
                v = v.get("numero")
            if v not in (None, ""):
                registry.add(("artigo", str(v).strip()))
    text = normalize(f"{r['title'] or ''} {r['description'] or ''}")
    for m in _FRACTION_RE.finditer(text):
        registry.add(("fracao", m.group(1)))
    for m in _ARTICLE_RE.finditer(text):
        registry.add(("artigo", m.group(1)))
    return {"address": address, "registry": frozenset(registry)}


def _unit_relation(a, b) -> str:
    """'same' when the two rows share a unit marker and none disagrees,
    'different' when any marker present on both disagrees, else 'unknown'
    (price, size and wording alone never make two rows one legal unit)."""
    ma, mb = _unit_markers(a), _unit_markers(b)
    agree = False
    if ma["address"] and mb["address"]:
        if ma["address"] != mb["address"]:
            return "different"
        agree = True
    ra, rb = ma["registry"], mb["registry"]
    if ra and rb:
        for kind in {k[0] for k in ra} & {k[0] for k in rb}:
            if {k for k in ra if k[0] == kind} != {k for k in rb if k[0] == kind}:
                return "different"
            agree = True
    return "same" if agree else "unknown"


def _twin_units(rows, dup_of: dict) -> list[list]:
    """Groups of rows from one source with the same title, place and opening words whose price
    and size differ by at most 10%: the floors of one building in a court sale,
    the flats of one promotion. Rows already flagged are left out."""
    by_title: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["id"] not in dup_of and r["title"]:
            opening = normalize(r["description"] or "")[:TWIN_UNIT_OPENING].strip()
            by_title[(r["source"], normalize(r["concelho"]).strip(), normalize(r["title"]).strip(),
                      opening)].append(r)
    groups = []
    for same in by_title.values():
        if len(same) < 2:
            continue
        same.sort(key=lambda r: r["price"])
        group = [same[0]]
        for r in same[1:]:
            base = group[0]
            # Without both sizes two lots cannot be told to be the same flat: keep both.
            if (r["price"] <= base["price"] * (1 + TWIN_UNIT_TOLERANCE)
                    and r["area_m2"] is not None and base["area_m2"] is not None
                    and abs(r["area_m2"] - base["area_m2"]) <= base["area_m2"] * TWIN_UNIT_TOLERANCE):
                group.append(r)
            else:
                if len(group) > 1:
                    groups.append(group)
                group = [r]
        if len(group) > 1:
            groups.append(group)
    return groups


CROSS_SITE_AREA = 0.02
CROSS_SITE_PRICE = 0.15
CROSS_SITE_KM = 3.0


def _phrases(text: str | None) -> set[str]:
    words = re.findall(r"[a-z]{3,}", normalize(text or ""))
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def _same_house_elsewhere(a, b) -> bool:
    if not ((a["area_m2"] or 0) >= 40 and (b["area_m2"] or 0) >= 40):   # a size is needed to match
        return False
    if abs(a["area_m2"] - b["area_m2"]) > CROSS_SITE_AREA * max(a["area_m2"], b["area_m2"]):
        return False
    if abs(a["price"] - b["price"]) > CROSS_SITE_PRICE * max(a["price"], b["price"]):
        return False
    import geo
    pa, pb = geo.position(dict(a)), geo.position(dict(b))
    if pa and pb and geo.distance_km(pa["lat"], pa["lon"], pb["lat"], pb["lon"]) <= CROSS_SITE_KM:
        return True
    return len(_phrases(a["description"]) & _phrases(b["description"])) >= 3


def mark_duplicates(db: sqlite3.Connection) -> int:
    """Identify duplicate and near-duplicate listings.

    Sets duplicate_of when there is corroborated evidence that two rows describe
    the same legal property (same-source relisting, geo/phrase match, same court
    sale lot, or a verified link).  Sets possible_match for rows that are similar
    by price and area alone but whose addresses may differ (F02 fix).

    Nothing is deleted.  Returns the number of rows where duplicate_of is set.
    """
    rows = db.execute("""
        SELECT * FROM listings
        WHERE price IS NOT NULL
          AND concelho IS NOT NULL AND TRIM(concelho) != ''
    """).fetchall()

    buckets: dict[tuple, list] = defaultdict(list)
    for r in rows:
        buckets[(r["country"], normalize(r["concelho"]).strip())].append(r)

    def completeness(r):
        return (sum(1 for v in r if v is not None), r["first_seen"] or "", r["id"])

    dup_of: dict[str, str] = {}
    # Speculative similarity (price±€500, area±5m²) across sources: not sufficient
    # to confirm same legal property.  Recorded as possible_match instead of
    # duplicate_of so both listings remain visible in the research panel.
    possible: dict[str, str] = {}

    for group in buckets.values():
        if len(group) < 2:
            continue
        group = sorted(group, key=completeness, reverse=True)
        for i, keeper in enumerate(group):
            if keeper["id"] in dup_of:
                continue
            for other in group[i + 1:]:
                if other["id"] in dup_of:
                    continue
                if other["source"] == keeper["source"]:
                    # Portals re-post the same house under a new id. The same
                    # price and size alone is only similarity: it hides the
                    # re-post only when its address or registry unit is the same;
                    # otherwise both stay visible with a possible_match hint.
                    if (keeper["source"] in RELISTING_SOURCES and other["price"] == keeper["price"]
                            and (keeper["area_m2"] is None or other["area_m2"] is None
                                 or other["area_m2"] == keeper["area_m2"])):
                        relation = _unit_relation(keeper, other)
                        if relation == "same":
                            dup_of[other["id"]] = keeper["id"]
                            possible.pop(other["id"], None)
                        elif other["id"] not in possible:
                            possible[other["id"]] = keeper["id"]
                    continue
                ka, oa = keeper["area_m2"], other["area_m2"]
                area_match = (ka is None or oa is None or abs(ka - oa) < 5)
                if abs(other["price"] - keeper["price"]) < 500 and area_match:
                    # Speculative: similar price/area but address may differ.
                    # Record as a hint, not a suppression.
                    if other["id"] not in possible:
                        possible[other["id"]] = keeper["id"]

    # The same house on two portals at a different price: size to 2%, price
    # within 15%, pins within 3 km or same wording.  Geo/phrase match is
    # circumstantial — two different addresses can satisfy it.  Record as a
    # non-suppressive hint only.
    for group in buckets.values():
        for i, a in enumerate(group):
            for o in group[i + 1:]:
                if a["id"] in dup_of or o["id"] in dup_of or a["source"] == o["source"]:
                    continue
                if _same_house_elsewhere(a, o):
                    keep, drop = sorted((a, o), key=lambda r: (r["price"], r["id"]))
                    if drop["id"] not in possible:
                        possible[drop["id"]] = keep["id"]

    # Flats of one building or promotion (same source, same title, price and
    # size within 10%) are similar, which is not the same legal unit: equal,
    # missing or identical short descriptions prove nothing. Suppress only when
    # the address or registry unit is the same; otherwise (different units
    # such as "fração A" vs "fração B", or no marker) both stay, with a hint.
    for group in _twin_units(rows, dup_of):
        cheapest = min(group, key=lambda r: (r["price"], r["id"]))
        for r in group:
            if r["id"] == cheapest["id"]:
                continue
            relation = _unit_relation(cheapest, r)
            if relation == "same":
                dup_of[r["id"]] = cheapest["id"]
                possible.pop(r["id"], None)
            elif r["id"] not in possible:
                possible[r["id"]] = cheapest["id"]

    # A Citius sale joined to its e-leilões auction (links.py) is one sale.
    from links import linked_pairs
    for el, citius in linked_pairs(db).items():
        dup_of[el] = citius
        possible.pop(el, None)

    db.execute("UPDATE listings SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
    db.execute("UPDATE listings SET possible_match = NULL WHERE possible_match IS NOT NULL")
    db.executemany("UPDATE listings SET duplicate_of = ? WHERE id = ?",
                   [(keeper, dup) for dup, keeper in dup_of.items()])
    db.executemany("UPDATE listings SET possible_match = ? WHERE id = ?",
                   [(match, lid) for lid, match in possible.items()])
    db.commit()
    if dup_of:
        LOG.info(f"Deduplication: {len(dup_of)} listings flagged as cross-source duplicates"
                 f"; {len(possible)} as possible matches")
    return len(dup_of)


# ─── Alerts & jobs ───────────────────────────────────────────────────

def not_yet_alerted(db: sqlite3.Connection, channel: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    sent = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        sent.update(r[0] for r in db.execute(
            f"SELECT listing_id FROM alert_log WHERE channel = ? "
            f"AND listing_id IN ({','.join('?' * len(chunk))})", (channel, *chunk)))
    return set(ids) - sent


def alerted_at(db: sqlite3.Connection, channel: str) -> dict[str, datetime]:
    """When each listing was last alerted on this channel. For alerts that can
    happen again (a second price cut), where "already told" is not enough."""
    out = {}
    for lid, sent_at in db.execute("SELECT listing_id, sent_at FROM alert_log WHERE channel = ?", (channel,)):
        when = parse_dt(sent_at)
        if when:
            out[lid] = when
    return out


def mark_alerted(db: sqlite3.Connection, channel: str, ids):
    now = utcnow_iso()
    db.executemany(
        "INSERT OR REPLACE INTO alert_log (listing_id, channel, sent_at) VALUES (?,?,?)",
        [(i, channel, now) for i in ids],
    )
    db.commit()


def job_last_run(db: sqlite3.Connection, job: str) -> datetime | None:
    row = db.execute("SELECT last_run FROM job_runs WHERE job = ?", (job,)).fetchone()
    return parse_dt(row[0]) if row else None


def set_job_last_run(db: sqlite3.Connection, job: str, when: datetime | None = None):
    db.execute("INSERT OR REPLACE INTO job_runs (job, last_run) VALUES (?, ?)",
               (job, (when or utcnow()).isoformat()))
    db.commit()


def set_listing_status(db: sqlite3.Connection, listing_id: str, status: str | None,
                       reason: str | None = None):
    """Shortlist or dismiss a listing; None clears the decision.

    A dismissal can carry a reason from DISMISS_REASONS. Omitting it is allowed
    for older callers (the Telegram buttons); the dashboard always sends one.
    """
    if status is None:
        db.execute("DELETE FROM listing_status WHERE listing_id = ?", (listing_id,))
    elif status in STATUSES:
        if status != "dismissed":
            reason = None
        elif reason is not None and reason not in DISMISS_REASONS:
            raise ValueError(f"unknown dismiss reason {reason!r}")
        db.execute(
            "INSERT OR REPLACE INTO listing_status (listing_id, status, updated_at, reason) "
            "VALUES (?,?,?,?)",
            (listing_id, status, utcnow_iso(), reason))
    else:
        raise ValueError(f"unknown status {status!r}")
    db.commit()


def dismiss_reasons(db: sqlite3.Connection) -> dict[str, str]:
    return {lid: reason for lid, reason in db.execute(
        "SELECT listing_id, reason FROM listing_status WHERE reason IS NOT NULL")}


def get_kv(db: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_kv(db: sqlite3.Connection, key: str, value: str):
    db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def listing_statuses(db: sqlite3.Connection) -> dict[str, str]:
    return dict(db.execute("SELECT listing_id, status FROM listing_status").fetchall())


FOLLOW_UP_DAYS = 10   # a letter with no answer after this long deserves a phone call or a reminder


def awaiting_reply(db: sqlite3.Connection, *, days: int = FOLLOW_UP_DAYS,
                   now: datetime | None = None) -> list[dict]:
    """Letters sent at least `days` ago that are still marked pending, oldest
    first. Bids logged for online auctions expect no answer and are left out."""
    cutoff = ((now or utcnow()) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [dict(r) for r in db.execute(
        "SELECT * FROM carta_log WHERE outcome = 'pending' AND COALESCE(method, '') != 'online' "
        "AND sent_date IS NOT NULL AND sent_date <= ? ORDER BY sent_date, id", (cutoff,))]


def latest_offers(db: sqlite3.Connection) -> dict[str, dict]:
    """Most recent offer (not information request) per listing, from carta_log."""
    out = {}
    for r in db.execute("SELECT * FROM carta_log WHERE listing_id IS NOT NULL AND is_offer = 1 "
                        "ORDER BY created_at ASC, id ASC"):
        out[r["listing_id"]] = dict(r)
    return out


# ─── Source health ───────────────────────────────────────────────────

def source_health(db: sqlite3.Connection, known_sources=None) -> list[dict]:
    """Per-source status from scrape_log, worst first.

    `failing_runs` counts consecutive runs since the last one that returned
    listings — a scraper stuck at 0 is almost always a site change.
    """
    runs: dict[str, list] = defaultdict(list)
    for r in db.execute(
            "SELECT source, timestamp, count, status, message, duration_s FROM scrape_log "
            "ORDER BY timestamp DESC, id DESC"):
        if len(runs[r["source"]]) < 50:
            runs[r["source"]].append(r)

    listing_counts = dict(db.execute(
        "SELECT source, COUNT(*) FROM listings GROUP BY source").fetchall())

    names = set(runs) | set(known_sources or ())
    out = []
    for name in names:
        history = runs.get(name, [])
        last = history[0] if history else None
        last_ok = next((h for h in history if (h["count"] or 0) > 0), None)
        failing = 0
        for h in history:
            if (h["count"] or 0) > 0:
                break
            failing += 1
        if not history:
            state = "never run"
        elif last["status"] == "blocked":
            state = "blocked"
        elif last["status"] == "error":
            state = "error"
        elif (last["count"] or 0) > 0:
            state = "ok"
        elif last_ok is None:
            state = "never worked"
        else:
            state = "broken"
        out.append({
            "source": name,
            "state": state,
            "last_run": last["timestamp"] if last else None,
            "last_count": last["count"] if last else None,
            "last_status": last["status"] if last else None,
            "last_message": last["message"] if last else None,
            "last_duration_s": last["duration_s"] if last else None,
            "last_ok": last_ok["timestamp"] if last_ok else None,
            "failing_runs": failing,
            "listings": listing_counts.get(name, 0),
        })
    # blocked sits with error: the site is not giving us listings either way.
    order = {"error": 0, "blocked": 0, "broken": 1, "fixture failing": 1,
             "never worked": 2, "never run": 3, "ok": 4}
    out.sort(key=lambda s: (order.get(s["state"], 9), s["source"]))
    return out


def last_ok_by_source(db: sqlite3.Connection) -> dict[str, datetime]:
    """Most-recent positive-count successful full run per source.

    Only status='ok' and scope='full' advance the freshness clock. Partial
    and unknown non-NULL scopes do not prove that unseen listings left the
    source. NULL scope retains the legacy full-run interpretation.
    Zero-count runs do not advance this clock: no retained rows alone is
    insufficient evidence that previously stored listings disappeared.
    """
    out = {}
    for source, ts in db.execute(
            "SELECT source, MAX(timestamp) FROM scrape_log"
            " WHERE count > 0 AND status = 'ok'"
            " AND (scope IS NULL OR scope = 'full')"
            " GROUP BY source"):
        dt = parse_dt(ts)
        if dt:
            out[source] = dt
    return out


def latest_failure(db: sqlite3.Connection) -> dict[str, datetime]:
    """When a source's newest scrape failed, that time.

    Absent when the newest scrape returned listings: the source is being read,
    and a listing it has not shown is stale, not unconfirmed.
    """
    out = {}
    rows = db.execute(
        "SELECT s.source, s.timestamp, s.count, s.status FROM scrape_log s "
        "JOIN (SELECT source, MAX(id) AS id FROM scrape_log GROUP BY source) last "
        "ON s.id = last.id")
    for r in rows:
        failed = r["status"] in ("error", "blocked", "empty") or not (r["count"] or 0)
        if not failed:
            continue
        dt = parse_dt(r["timestamp"])
        if dt:
            out[r["source"]] = dt
    return out


def attention_reason(row: dict, now: datetime | None = None) -> str | None:
    """Why a source needs a look: two failures in a row, or no good read for days."""
    now = now or utcnow()
    parts = []
    if row.get("state") in ("error", "broken", "blocked") and (row.get("failing_runs") or 0) >= 2:
        parts.append(f"failed {row['failing_runs']} times in a row")
    ok = parse_dt(row.get("last_ok")) if row.get("last_ok") else None
    if row.get("listings") and ok and now - ok > QUIET_AFTER:
        parts.append(f"no good read since {ok:%Y-%m-%d}")
    return "; ".join(parts) or None


# ─── Reading: the one loader ─────────────────────────────────────────

def _cash_on_hand() -> float:
    """config.cash_on_hand, or 0 when it is missing or not a number."""
    try:
        from config import load_config
        return max(0.0, float(load_config().get("cash_on_hand") or 0))
    except (TypeError, ValueError):
        return 0.0


def _load_config_safe() -> dict:
    """Full config dict, or {} on any error."""
    try:
        from config import load_config
        return load_config()
    except Exception:
        return {}


def _work_limits() -> tuple[float, float]:
    """(common.check_max_price, common.light_checks_from) from the settings."""
    from common import check_max_price, light_checks_from
    from config import load_config
    cfg = load_config()
    return check_max_price(cfg), light_checks_from(cfg)


def list_price_sql(table: str = "") -> str:
    """SQL for the price on the list page (common.price_to_pay, without debts
    or a portal note). A minimum under 20% of the base is a bidding step."""
    p = f"{table}." if table else ""
    price, bid, floor = (f"COALESCE({p}{name}, 0)" for name in ("price", "current_bid", "min_price"))
    real_floor = f"CASE WHEN {price} > 0 AND {floor} > 0 AND {floor} < 0.2 * {price} THEN 0 ELSE {floor} END"
    return (f"CASE WHEN {bid} > 0 THEN MAX({bid}, {real_floor}) "
            f"WHEN {real_floor} > 0 THEN {real_floor} ELSE {price} END")


def still_worth_a_page_sql(table: str = "") -> tuple[str, tuple]:
    """An AND-clause: the list page does not already show a price over the cap.

    A price not known yet is still opened. A shortlisted listing, or one an
    offer was made on, is still opened. ("", ()) when the cap is off."""
    cap = _work_limits()[0]
    if not cap:
        return "", ()
    p = f"{table}." if table else ""
    kept = (f"{p}id IN (SELECT listing_id FROM listing_status WHERE status = 'shortlisted')"
            f" OR {p}id IN (SELECT listing_id FROM carta_log WHERE is_offer = 1)")
    return f"AND (({list_price_sql(table)}) <= ? OR {kept})", (cap,)


def unchecked_over_cap(db: sqlite3.Connection) -> tuple[int, float]:
    """How many stored listings the cap leaves unchecked, and the cap.

    (0, 0) when the cap is off. One you shortlisted or made an offer on is
    still checked, so it is not in the count."""
    cap = _work_limits()[0]
    if not cap:
        return 0, 0.0
    n = db.execute(
        f"SELECT COUNT(*) FROM listings WHERE ({list_price_sql()}) > ?"
        " AND id NOT IN (SELECT listing_id FROM listing_status WHERE status = 'shortlisted')"
        " AND id NOT IN (SELECT listing_id FROM carta_log WHERE is_offer = 1)",
        (cap,),
    ).fetchone()[0]
    return int(n), cap


def heavy_checks_spared(item: dict, cash: float, light_from: float) -> bool:
    """From common.light_checks_from, the light checks come first: a listing
    whose price, taxes and fees are already over the cash can never be shown
    where the cash decides (Live and invest), so no climate or photo is read
    for that. A shortlisted listing is always read."""
    if not light_from or item.get("status") == "shortlisted":
        return False
    import costs
    from common import price_to_pay
    return price_to_pay(dict(item)) > light_from and costs.over_before_repairs(dict(item), cash)


def filter_reason(item: dict, filters: dict | None, mode: str = "home") -> str | None:
    """Why the user's config filters exclude this listing, or None.

    Occupancy keywords stay for a home to live in; on Investment home a sitting
    tenant is scored as income (scoring._score_invest), not hidden.
    """
    if not filters:
        return None
    countries = filters.get("countries") or []
    if countries and item.get("country") not in countries:
        return f"country {item.get('country')} not in filters.countries"

    keywords = list(filters.get("exclude_keywords") or [])
    if mode == "invest" and keywords:
        from scoring import OCCUPANCY_PATTERNS
        keywords = [k for k in keywords
                    if not has_term(k, OCCUPANCY_PATTERNS, negations=False)]
    if keywords:
        hits = find_terms(f"{item.get('title') or ''} {item.get('description') or ''}", keywords)
        if hits:
            return f"keyword '{hits[0]}'"

    min_area = filters.get("min_area_m2") or 0
    if min_area and item.get("area_m2") is not None and item["area_m2"] < min_area:
        return f"area {item['area_m2']:.0f} m² < {min_area}"

    types = filters.get("types") or []
    if types and item.get("tipo") is not None and item["tipo"] not in types:
        return f"type {item['tipo']} not in filters.types"

    districts = filters.get("districts") or []
    if districts and item.get("district") is not None:
        d = normalize(item["district"])
        if not any(normalize(x) in d for x in districts):
            return f"district {item['district']} not in filters.districts"
    return None


def minimum_bar(item: dict, mode: str) -> float:
    """The score a minimum is applied to.

    A priced lead uses the combined score. An unvalued Live and invest lead
    has no investment rank, so the minimum applies to the living score. The
    combined score stays 0 and no investment score is invented.
    """
    if mode == "both" and item.get("research_only"):
        return float(item.get("live_score") or 0)
    return float(item.get("score") or 0)


def hidden_category(reason: str | None) -> str | None:
    """Group a hidden_reason into dismissed / expired / duplicate / stale / filtered / low score."""
    if not reason:
        return None
    for prefix, label in (("dismissed", "dismissed"), ("expired", "expired"), ("duplicate", "duplicate"),
                          ("gone", "gone"), ("stale", "stale"), ("score", "low score"), ("cash", "over cash")):
        if reason.startswith(prefix):
            return label
    return "filtered"


# hidden_category() labels that research selection never overrides. Exact
# membership on the category, never a substring test on the reason text: a
# cash or keyword exclusion ("cash …", "keyword '…'") is as final as a dismissal.
RESEARCH_HARD_HIDDEN = frozenset({"dismissed", "expired", "duplicate", "gone", "stale",
                                  "over cash", "filtered"})
# The only hidden categories an extreme-discount lead may bypass for research.
RESEARCH_SOFT_HIDDEN = frozenset({"low score"})


def research_eligible(item: dict) -> bool:
    """Whether a scored item may be a research candidate (Live and invest)."""
    reason = item.get("hidden_reason")
    if not reason:
        return True
    category = hidden_category(reason)
    if category in RESEARCH_HARD_HIDDEN:
        return False
    return bool(item.get("extreme_discount")) and category in RESEARCH_SOFT_HIDDEN


def research_selection(items: list, per_group: int = 3) -> tuple[list, list]:
    """Up to `per_group` priced leads and, separately, unvalued ones.

    The one selection the Listings research panel and the scan's enrichment
    order both use. Pass the full scored list (hidden rows included, before any
    request score cutoff): selection happens first, a view's cutoff after.
    """
    valued, unvalued = [], []
    ranked = sorted((it for it in items if research_eligible(it)),
                    key=lambda it: it.get("score") or 0, reverse=True)
    for it in ranked:
        unpriced = bool(it.get("research_only"))
        if not unpriced and it.get("invest_score") is None:
            continue
        bucket = unvalued if unpriced else valued
        if unpriced or (it.get("score") or 0) > 0 or it.get("extreme_discount"):
            if len(bucket) < per_group:
                bucket.append(it)
    return valued, unvalued


def _first_prices(db: sqlite3.Connection) -> dict[str, float]:
    first = {}
    for lid, price in db.execute(
            "SELECT listing_id, price FROM price_history WHERE price IS NOT NULL "
            "ORDER BY observed_at ASC"):
        first.setdefault(lid, price)
    return first


# ─── Score cache ─────────────────────────────────────────────────────
# Scoring every listing takes ~30 s at 15,000 listings and grows with each new
# country. A listing's score depends on its own row and on a few shared things,
# so the result is kept per listing, keyed by its row, and thrown away when the
# day, the filters (with the weights), the known towns or the auction results change.
# The shared lookups are rebuilt only when their tables change.
# The five wishes are not kept: only the rows on screen show them
# (dashboard._public), and they were the largest of these fields.
_DERIVED = ("price_drop_pct", "earlier_round", "case_land", "town_distance", "place_conflict", "beach",
            "airport", "station", "guarda", "climate", "unlocated", "predicted_final", "score", "rank",
            "reasons", "excellent", "category", "kind", "live_score", "invest_score",
            "research_only", "extreme_discount", "floor_guess")
_SCORED: dict[str, dict[str, tuple]] = {}      # mode → {listing id → (row key, results, inputs)}
_UNSCORED = {"last_seen", "is_new"}
# What a hidden listing keeps when load_listings(slim_hidden=True): what a list
# filters, searches, sorts and counts it by (dashboard.api_listings, the
# report's counts, load_best's choice of goal). 99.7% of each goal's listings
# are hidden, and full rows for all of them did not fit a 6 GB PC; a page that
# shows hidden ones reads those rows in full (dashboard._full_rows).
_SLIM = ("id", "source", "country", "tipo", "title", "description", "concelho", "district", "price",
         "current_bid", "area_m2", "date_end", "first_seen", "raw_json", "status", "hidden_reason",
         "score", "rank", "category", "kind", "climate", "is_recent",
         # what research selection reads (research_selection): a hidden lead
         # must still be selectable for investigation from the slim list.
         "extreme_discount", "invest_score", "live_score", "research_only", "investigation_cleared")
_SCORED_FOR: list = [None]
_SHARED: dict = {"key": None, "value": None}


def _land_market(db) -> dict:
    """The median asking price per hectare of rural land, by country and
    district, from the plots the scanner itself has seen (land_prices.py)."""
    import land_prices
    rows = db.execute("SELECT country, district, title, tipo, area_m2, price FROM listings "
                      "WHERE price > 0 AND area_m2 >= ?", (land_prices.MIN_PLOT_M2,))
    return land_prices.observed_index(rows)


def _floor_market(db) -> dict:
    """The floors the scanner has seen for each type of home, by place: what a
    floor the notice does not state is guessed from (guesses.py)."""
    import guesses
    rows = db.execute("SELECT country, district, title, description, tipo, area_m2 FROM listings "
                      "WHERE area_m2 BETWEEN ? AND ?", guesses.FLOOR_RANGE)
    return guesses.observed_floors(rows)


def _shared_lookups(db, now):
    """(first prices, rounds index, towns, auction results, land market, floor
    market): rebuilt
    when the day, the database or the row counts of the tables they come from change."""
    import geo
    import outcomes
    import rounds
    outcomes.ensure_table(db)
    path = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")
    counts = tuple(db.execute(
        "SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM price_history), "
        "(SELECT COUNT(*) FROM places WHERE lat IS NOT NULL), (SELECT COUNT(*) FROM auction_results)").fetchone())
    key = (path or id(db), now.date(), counts)
    if _SHARED["key"] != key:
        _SHARED.update(key=key, value=(_first_prices(db), rounds.index(db), geo.town_index(db),
                                       outcomes.stats(db), _land_market(db), _floor_market(db)))
    return _SHARED["value"]


def forget_scores(db: sqlite3.Connection | None = None) -> None:
    """Drop every kept score (after a code or data change the cache cannot see)."""
    _SCORED.clear()
    _LOADED_MODES.clear()
    _SHARED.update(key=None, value=None)
    sdb = _score_db(db) if db is not None else None
    if sdb is not None:
        with _SCORE_LOCK, sdb:
            sdb.execute("DELETE FROM score_cache")


# Scores are also kept on disk, so a restart or a new window does not score
# 60,000 listings again: only listings whose row (or the day, or the filters) changed.
_LOADED_MODES: set = set()

# Which goals' kept scores stay in memory between calls. None keeps them all
# (the command line, the tests). The app keeps only the goals whose lists it
# has open (dashboard.MAX_LISTS): at 140,000 listings one goal's scores take
# about half a gigabyte, and the report scoring all four filled a 6 GB PC.
_KEEP: list = [None]


def keep_scores_for(modes) -> None:
    """Keep only these goals' scores in memory. The others are read back from
    the scores file when a goal is asked for again, which takes seconds."""
    keep = set(modes)
    _KEEP[0] = keep
    for mode in list(_SCORED):
        if mode not in keep:
            _release(mode)


def _kept_in_memory(mode: str) -> bool:
    return _KEEP[0] is None or mode in _KEEP[0]


def _release(mode: str) -> None:
    _SCORED.pop(mode, None)
    _LOADED_MODES.discard(mode)


_SCORE_DBS: dict = {}


# A dashboard or scraper change must not throw away every kept score. Only
# these decide the number a listing gets.
_SCORE_CODE = ("scoring.py", "costs.py", "forestry.py", "land_prices.py", "common.py",
               "climate.py", "geo.py", "outcomes.py", "rounds.py", "prices.py", "guesses.py", "outline.py")


@functools.lru_cache(maxsize=1)
def _code_version() -> str:
    """A fingerprint of the scoring code: a new version of the app scores again
    instead of showing scores kept from the old one."""
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha1()
    for name in _SCORE_CODE:
        h.update(name.encode())
        with open(os.path.join(here, name), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:12]
_SCORE_LOCK = threading.Lock()     # one file, shared by the app's threads


def _score_db(db) -> sqlite3.Connection | None:
    """The kept scores live in their own file next to the database (auctions.db.scores),
    so saving them never locks the listings a scan is writing. None for an in-memory database."""
    path = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not path:
        return None
    conn = _SCORE_DBS.get(path)
    if conn is None:
        conn = sqlite3.connect(path + ".scores", timeout=5, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS score_cache (mode TEXT, id TEXT, key TEXT, inputs TEXT, "
                     "data BLOB, PRIMARY KEY (mode, id))")
        _SCORE_DBS[path] = conn
    return conn


def _unpack(data: bytes) -> dict | None:
    import pickle
    try:
        results = pickle.loads(data)
    except (pickle.PickleError, EOFError, AttributeError, ValueError, TypeError):
        return None                          # scored again
    results.pop("wishes", None)              # kept before the wishes moved to the page
    return results


def _load_kept(db, mode: str, ids: list[str] | None = None) -> dict:
    """A goal's kept scores, read from the scores file the first time: all of
    them, or only those of `ids` when they are few and the goal is not kept in
    memory (one listing's panel must not read 140,000 scores). They stay packed
    until a listing uses one (load_listings), so reading a goal costs the bytes,
    not 140,000 unpacked results at once."""
    kept = _SCORED.setdefault(mode, {})
    if mode in _LOADED_MODES:
        return kept
    few = ids is not None and len(ids) <= 500 and not _kept_in_memory(mode)
    if not few:
        _LOADED_MODES.add(mode)
    try:
        sdb = _score_db(db)
        if sdb is None:
            return kept
        with _SCORE_LOCK:
            if few:
                rows = sdb.execute("SELECT id, key, inputs, data FROM score_cache WHERE mode = ? "
                                   f"AND id IN ({','.join('?' * len(ids))})", (mode, *ids)).fetchall()
            else:
                rows = sdb.execute("SELECT id, key, inputs, data FROM score_cache WHERE mode = ?",
                                   (mode,)).fetchall()
        for lid, key, inputs, data in rows:
            if lid not in kept:
                kept[lid] = (key, bytes(data), inputs)
    except sqlite3.Error:
        pass                                 # only a speed-up
    return kept


def _save_kept(db, mode: str, new: list) -> None:
    import pickle
    if not new:
        return
    try:
        sdb = _score_db(db)
        if sdb is None:
            return
        with _SCORE_LOCK, sdb:
            sdb.executemany("INSERT OR REPLACE INTO score_cache VALUES (?, ?, ?, ?, ?)",
                            [(mode, lid, key, inputs, pickle.dumps(data)) for lid, key, inputs, data in new])
    except sqlite3.Error:
        pass                                 # busy (a scan is writing): kept in memory, saved next time


def _open_home_supply(rows) -> dict[tuple[str, str], int]:
    """How many homes are still listed in each district, on the sites scanned.

    {"PT|guarda": 12}. Asking supply the scanner can see, not the whole market.
    A district with no name is left out: those rows are not one place."""
    from common import normalize
    from scoring import property_kind
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        district = (r["district"] or "").strip()
        if not district:
            continue
        kind = property_kind({"title": r["title"] or "", "description": r["description"] or "",
                              "tipo": r["tipo"], "area_m2": r["area_m2"], "country": r["country"]})
        if kind != "home":
            continue
        key = ((r["country"] or "PT").upper(), normalize(district))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _score_one(item: dict, now, filters, first_price, cases, towns, closes, land_market,
               climate_on, mode="home", floor_market=None) -> float:
    """Everything load_listings works out for one listing (kept in the score cache)."""
    import climate
    import geo
    import outcomes
    import rounds
    from scoring import GUARDA, categorize, display_score, excellent, property_kind, score_detail
    fp = first_price.get(item["id"])
    if fp and item.get("price") and fp > 0 and item["price"] < fp:
        item["price_drop_pct"] = round((fp - item["price"]) / fp * 100, 1)
    else:
        item["price_drop_pct"] = None
    item["earlier_round"] = rounds.earlier_round(item, cases, now)
    item["case_land"] = rounds.land_in_case(item, cases, now, property_kind)
    item["town_distance"] = geo.distance_to_town(item, towns) if towns else None
    item["place_conflict"] = geo.title_town_conflict(item, towns) if towns else None
    item["beach"] = geo.nearest_beach(item, towns=towns)
    item["airport"] = geo.nearest_hub(item, "airport", towns=towns)
    item["station"] = geo.nearest_hub(item, "station", towns=towns)
    item["guarda"] = geo.distance_to_place(item, *GUARDA, "Guarda", towns=towns)
    item["climate"] = climate.stored(item)       # read by the scan (climate.assess_pending)
    item["live_score"] = None
    item["invest_score"] = None
    # Live and invest cannot judge a climate that was never read. An exact
    # seaside home is read here, once, and kept on the listing.
    if mode == "both" and not item["climate"] and _both_worth_reading(item):
        fresh = climate.for_item(item)
        if fresh:
            item["climate"] = fresh
            climate.remember(item, fresh)
    item["unlocated"] = climate_on and not item["climate"] and not geo._place(item, towns)
    item["predicted_final"] = outcomes.predict(item, closes, property_kind(item)) if closes else None
    item["land_market"] = land_market        # what land goes for here (land_prices.py)
    item["floor_market"] = floor_market or {}  # what floors homes have here (guesses.py)
    item.pop("floor_guess", None)             # worked out again with them (costs.floor_guess)

    rank, reasons = score_detail(item, now=now, targets=filters, mode=mode)
    sc = display_score(rank)
    item["score"] = sc
    item["rank"] = rank          # unclamped: orders listings that all reach 100
    item["reasons"] = reasons
    item["excellent"] = excellent(item, sc, reasons)
    item["category"] = categorize(item)
    item["kind"] = property_kind(item) if item["category"] == "imoveis" else None
    return sc


def _both_worth_reading(item: dict) -> bool:
    """An exact pin, the sea within 10 km of it, and a house: worth a climate
    read, unless the light checks already rule it out (heavy_checks_spared)."""
    import geo
    from scoring import SWIM_MAX_KM, property_kind
    if property_kind(item) != "home":
        return False
    pos = geo.position(item) or {}
    if pos.get("precision") not in geo.EXACT_ENOUGH:
        return False
    beach = item.get("beach") or {}
    km = beach.get("km")
    if km is None or beach.get("approx") or km > SWIM_MAX_KM:
        return False
    return not heavy_checks_spared(item, _cash_on_hand(), _work_limits()[1])


def _stored_photo_check(item: dict) -> dict | None:
    """The photo check result: top-level if a caller set one, else the payload
    photos.py stored in the listing's raw_json (the loader does not lift it)."""
    pc = item.get("photo_check")
    if isinstance(pc, dict) and pc:
        return pc
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, dict):
        stored = raw.get("photo_check")
        if isinstance(stored, dict):
            return stored
    return None


def _shadow_evidence(item: dict) -> dict:
    """The evaluator evidence dict _shadow_evaluate builds for `item`."""
    return {
        "property_identity": item.get("identity") or {},
        "ownership_type": item.get("ownership_type"),
        "occupation": item.get("occupation"),
        "delivery_terms": item.get("delivery_terms"),
        "title_status": item.get("title_status"),
        "legal_access": item.get("legal_access"),
        "raw_json": item.get("raw_json"),
        "location_precision": item.get("location_precision"),
        "beach": item.get("beach"),
        "floor_m2": item.get("area_m2"),
        "floor_source": item.get("area_source"),
        "climate": item.get("climate"),
        "sale_availability": item.get("sale_availability"),
        "photo_check": _stored_photo_check(item),
        "apparent_discount_pct": item.get("invest_discount_pct"),
        # Only a DB clearance with matching evidence_rev_sum counts (Task 7).
        "anomaly_investigated": bool(item.get("investigation_cleared")),
        "valuation_source": item.get("valuation_source"),
        "conservative_margin_eur": item.get("conservative_margin_eur"),
        "available_cash_eur": item.get("available_cash_eur"),
        "total_project_cost_eur": item.get("total_project_cost_eur"),
    }


def _shadow_evaluate(item: dict, now, db: sqlite3.Connection | None = None,
                     ev_rev_sum: int | None = None) -> None:
    """Call evaluator.evaluate() in shadow mode, log and persist the decision (F01/F11).

    Never gates the item — the result is observability only until Decision promotion
    is wired.  Persists the Decision to evaluator_decisions (F11/F16) so freshness
    can be checked against evidence_revisions without re-evaluating.
    """
    try:
        import evaluator
        evidence = _shadow_evidence(item)
        decision = evaluator.evaluate(evidence, now=now)
        LOG.debug(
            "shadow-eval %s → %s (%d reqs, %d blocking)",
            item.get("id"), decision.state,
            len(decision.requirements), len(decision.blocking),
        )
        item["evaluator_state"] = decision.state
        if db is not None and item.get("id"):
            _persist_decision(db, item["id"], decision, ev_rev_sum)
    except Exception:  # noqa: BLE001
        LOG.debug("shadow-eval failed for %s", item.get("id"), exc_info=True)


def _persist_decision(db: sqlite3.Connection, listing_id: str, decision, ev_rev_sum) -> None:
    """Upsert a Decision row into evaluator_decisions (F11/F16)."""
    try:
        reqs_json = json.dumps([
            {"key": r.key, "outcome": r.outcome, "dangerous": r.dangerous,
             "code": r.code, "message": r.message}
            for r in decision.requirements
        ])
        prov_json = json.dumps(decision.provenance)
        db.execute(
            """INSERT INTO evaluator_decisions
               (listing_id, state, requirements_json, provenance_json,
                evidence_rev_sum, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(listing_id) DO UPDATE SET
                   state=excluded.state,
                   requirements_json=excluded.requirements_json,
                   provenance_json=excluded.provenance_json,
                   evidence_rev_sum=excluded.evidence_rev_sum,
                   evaluated_at=excluded.evaluated_at""",
            (listing_id, decision.state, reqs_json, prov_json,
             ev_rev_sum, decision.provenance.get("evaluated_at", utcnow_iso()))
        )
        db.commit()
    except Exception:  # noqa: BLE001
        LOG.debug("persist_decision failed for %s", listing_id, exc_info=True)


def load_decision(db: sqlite3.Connection, listing_id: str,
                  ev_rev_sum: int | None = None) -> dict | None:
    """Return the stored Decision for listing_id if still fresh, else None.

    A Decision is stale when ev_rev_sum is provided and does not match the
    stored evidence_rev_sum.  Pass ev_rev_sum=None to skip the freshness check
    and always return whatever is stored (useful for audits and tests).
    """
    row = db.execute(
        "SELECT state, requirements_json, provenance_json, evidence_rev_sum, evaluated_at"
        " FROM evaluator_decisions WHERE listing_id = ?",
        (listing_id,)
    ).fetchone()
    if row is None:
        return None
    if ev_rev_sum is not None and row["evidence_rev_sum"] != ev_rev_sum:
        return None  # stale
    return {
        "state": row["state"],
        "requirements": json.loads(row["requirements_json"]),
        "provenance": json.loads(row["provenance_json"]),
        "evidence_rev_sum": row["evidence_rev_sum"],
        "evaluated_at": row["evaluated_at"],
    }


def load_listings(db: sqlite3.Connection, *, filters: dict | None = None,
                  include_hidden: bool = False, now: datetime | None = None,
                  where: str = "", params=(), apply_min_score: bool = True, mode: str = "home",
                  refresh: bool = False, slim_hidden: bool = False) -> list[dict]:
    """Every listing a view should consider, scored, with hidden ones removed.

    Each item gains: score, reasons, category, hidden_reason (None if visible),
    price_drop_pct, earlier_round (rounds.py), town_distance (geo.py), is_recent, status
    (shortlisted/dismissed/None) and offer_outcome (latest carta_log outcome, or None). `where`/`params` are extra SQL conditions on the
    listings table for cheap pre-filtering (country, source, search...).
    With `slim_hidden`, a hidden listing keeps only the fields in _SLIM and
    `_slim`: True; the same call with where="id IN (...)" gives its full row.

    Hidden, in order of precedence: dismissed by the user, expired, duplicate,
    stale (gone from its source), excluded by config filters, below
    filters.min_score, over config.cash_on_hand (price, taxes, fees and the
    low end of repairs). A shortlisted listing ignores the last three: the user
    picked it on purpose. Cash of 0 is not set and hides nothing. Live and
    invest has no asking-price cap: the all-in estimate against the cash is
    the ceiling, using the middle of the repairs unless the condition is good.
    """
    now = now or utcnow()
    sql = "SELECT * FROM listings"
    query_params = tuple(params)
    clauses = []
    if where:
        clauses.append(f"({where})")
    if not include_hidden:
        # Ended sales are hidden on the way out. Leaving them in the read is
        # what made every list walk tens of thousands of dead rows. A bare date
        # counts for that whole day, so the comparison is the date only.
        clauses.append("(date_end IS NULL OR date_end = '' OR substr(date_end, 1, 10) >= ?)")
        query_params = query_params + (now.date().isoformat(),)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = db.execute(sql, query_params).fetchall()
    # A scan touches last_seen on every listing it sees; the score never reads it.
    scored_cols = [i for i, k in enumerate(rows[0].keys()) if k not in _UNSCORED] if rows else []

    # Task 7: load investigation clearances upfront so _shadow_evaluate can use the
    # correct anomaly_investigated value rather than the legacy investigation_note field.
    _all_ids = {r["id"] for r in rows}
    _ev_rev_sums_ll: dict[str, int] = {
        row["listing_id"]: row["s"]
        for row in db.execute(
            "SELECT listing_id, SUM(rev) AS s FROM evidence_revisions GROUP BY listing_id"
        ).fetchall()
    }
    _investigation_cleared: set[str] = _extreme_clearances(
        db, _all_ids, ev_rev_sums=_ev_rev_sums_ll
    )

    from confirm import gone_reason
    last_ok = last_ok_by_source(db)
    failed_at = latest_failure(db)
    statuses = listing_statuses(db)
    why_dismissed = dismiss_reasons(db)
    offers = latest_offers(db)
    # first prices; rounds (all listings: they span sites and dates); where each
    # municipality's town is; what ended sales closed at. Shared, rebuilt when their tables change.
    first_price, cases, towns, closes, land_market, floor_market = _shared_lookups(db, now)
    import climate                 # heat in 2081-2100, water, fire, flood (public datasets)
    climate_on = climate.available()
    maps_before = climate.maps_loaded()
    min_score = ((filters or {}).get("min_score") or 0) if apply_min_score else 0
    cash = _cash_on_hand()
    _cfg = _load_config_safe()
    from common import price_to_pay
    cap = _work_limits()[0]
    # A new day or new filters (with the weights) score again; new towns, auction
    # results or land prices (a scan adds some every few minutes) do not throw the
    # scores away: the kept ones stay on show until refresh=True (after each scan).
    context = (now.date().isoformat(), json.dumps(filters or {}, sort_keys=True, default=str), climate_on,
               _code_version())
    # Homes still listed in each district. The investment score mentions the
    # count and does not change its points for it, so a scan's refresh is enough.
    supply: dict[tuple[str, str], int] = _open_home_supply(rows) if mode == "invest" else {}
    supply_fp = ",".join(f"{a}|{b}:{n}" for (a, b), n in sorted(supply.items()))
    inputs = json.dumps([len(towns), sum(len(v) for v in closes.values()), len(land_market), supply_fp,
                         len(floor_market)])
    if _SCORED_FOR[0] != context:
        _SCORED.clear()
        _LOADED_MODES.clear()
        _SCORED_FOR[0] = context
    scored = _load_kept(db, mode, [r["id"] for r in rows] if len(rows) <= 500 else None)
    # Sum of all evidence revision counters per listing (F11): a location, hazard,
    # checklist or identity update increments a rev, changing this sum and busting
    # the score cache key so the item is re-scored with the new evidence.
    ev_rev_sums: dict[str, int] = {
        row["listing_id"]: row["s"]
        for row in db.execute(
            "SELECT listing_id, SUM(rev) AS s FROM evidence_revisions GROUP BY listing_id"
        ).fetchall()
    }
    new_scores = []
    climate_writes = []

    items = []
    for r in rows:
        item = dict(r)
        # Rows written before safe_url() existed may still hold javascript: links.
        item["url"] = safe_url(item.get("url"))
        item["status"] = statuses.get(item["id"])
        offer = offers.get(item["id"])
        item["offer_outcome"] = offer["outcome"] if offer else None
        # Task 7: mark whether this item has a valid (non-stale) investigation clearance.
        item["investigation_cleared"] = item["id"] in _investigation_cleared
        # Over common.check_max_price: kept so the scanner knows it, never scored,
        # checked or shown. One you shortlisted or made an offer on stays.
        if cap and item["status"] != "shortlisted" and offer is None and price_to_pay(dict(item)) > cap:
            continue
        reason = None

        end = effective_end(item.get("date_end"))
        if item["status"] == "dismissed":
            why = why_dismissed.get(item["id"])
            item["dismiss_reason"] = why
            reason = f"dismissed: {why}" if why else "dismissed"
        elif end is not None and end <= now:
            reason = "expired"
        elif item.get("duplicate_of"):
            reason = f"duplicate of {item['duplicate_of']}"
        else:
            seen = parse_dt(item.get("last_seen"))
            ok = last_ok.get(item["source"])
            gone = gone_reason(item)
            if gone:
                reason = f"gone: {gone}"
            elif seen and ok and ok - seen > STALE_AFTER:
                reason = f"stale: gone from {item['source']} since {seen:%Y-%m-%d}"
            elif item["status"] != "shortlisted":
                reason = filter_reason(item, filters, mode)
            # Unconfirmed only after a failed read of this source, later than
            # the listing was last shown. Not being on this week's timetable
            # is not a failure.
            failed = failed_at.get(item["source"])
            if seen and failed and failed > seen and now - seen > UNCONFIRMED_AFTER:
                item["unconfirmed_since"] = f"{seen:%Y-%m-%d}"

        if reason and not include_hidden:
            continue

        row_key = hashlib.sha1(repr((tuple(r[i] for i in scored_cols), item["status"],
                                     item["offer_outcome"], context,
                                     item.get("unconfirmed_since"),
                                     ev_rev_sums.get(item["id"], 0))).encode()).hexdigest()
        kept = scored.get(item["id"])
        results = None
        if kept and kept[0] == row_key and not (refresh and kept[2] != inputs):
            results = kept[1]
            if isinstance(results, bytes):           # as read from the scores file
                results = _unpack(results)
                if results is not None and _kept_in_memory(mode):
                    scored[item["id"]] = (kept[0], results, kept[2])
        if results is not None:
            item.update(results)
        else:
            if supply:
                district = (item.get("district") or "").strip()
                if district:
                    n = supply.get(((item.get("country") or "PT").upper(), normalize(district)), 0)
                    if n > 1:
                        item["open_homes_here"] = n - 1
            _score_one(item, now, filters, first_price, cases, towns, closes, land_market,
                       climate_on, mode, floor_market)
            if mode in ("both", "invest"):
                _shadow_evaluate(item, now, db=db,
                                 ev_rev_sum=ev_rev_sums.get(item["id"]))
            _optional = {"research_only", "extreme_discount", "floor_guess"}
            data = {k: item[k] for k in _DERIVED if k not in _optional}
            for _k in _optional:
                if _k in item:
                    data[_k] = item[_k]
            if item.get("area_note"):
                data["area_m2"] = item["area_m2"]
                data["area_note"] = item["area_note"]
            if _kept_in_memory(mode):
                scored[item["id"]] = (row_key, data, inputs)
            new_scores.append((item["id"], row_key, inputs, data))
            if len(new_scores) >= 2000:      # saved as they come: 140,000 held to the end did not fit a 6 GB PC
                _save_kept(db, mode, new_scores)
                new_scores = []
            if item.get("_climate_write"):
                climate_writes.append((item["raw_json"], item["id"]))
                item.pop("_climate_write", None)

        if reason is None and min_score and item["status"] != "shortlisted":
            # Priced leads clear the minimum on the combined score. An unvalued
            # Live and invest lead has no investment rank, so its living score
            # is the number that has to clear it. The combined score stays 0.
            bar = minimum_bar(item, mode)
            if bar < min_score:
                reason = f"score {bar:.0f} < filters.min_score {min_score}"
                if not include_hidden:
                    continue

        # Price plus taxes, fees and repairs, against cash you can lose.
        # Live and invest always plans on the middle of the repair range,
        # including a home advertised as being in good condition. An unresolved
        # floor stays a known subtotal and is hidden only when that subtotal
        # is already over the cash. The other tabs keep the low end.
        # A shortlisted listing stays. Cash of 0 is not set and hides nothing.
        if reason is None and cash and item["status"] != "shortlisted":
            import costs
            typical = mode == "both"
            if mode == "both":
                chosen = costs.selected_acquisition(item)
                over = costs.over_cash(item, cash, bid=chosen["amount"] or None,
                                       typical=typical, basis=chosen["label"],
                                       settings=_cfg)
            else:
                over = costs.over_cash(item, cash, typical=typical, settings=_cfg)
            if over:
                reason = over
                if not include_hidden:
                    continue

        item["hidden_reason"] = reason
        seen_first = parse_dt(item.get("first_seen"))
        item["is_recent"] = bool(seen_first and now - seen_first <= RECENT)
        if reason and slim_hidden:
            item = {**{k: item[k] for k in _SLIM if k in item}, "_slim": True}
        items.append(item)
    if climate_writes:
        db.executemany("UPDATE listings SET raw_json = ? WHERE id = ?", climate_writes)
        for rj, lid in climate_writes:
            try:
                climate_payload = json.loads(rj).get("climate")
                if climate_payload is not None:
                    record_evidence_revision(db, lid, "climate", climate_payload)
            except (ValueError, TypeError):
                pass
        db.commit()
    _save_kept(db, mode, new_scores)
    if not _kept_in_memory(mode):
        _release(mode)               # the items hold what they need; the scores file keeps the rest
    if not maps_before and climate.maps_loaded():
        climate.release_maps()       # a seaside home read for Live and invest: not kept for the life of the app
    return items


def load_best(db: sqlite3.Connection, modes: tuple[str, ...] | None = None, **kw) -> list[dict]:
    """Every listing scored for the goal it suits (scoring.FAMILY_GOALS), with
    `mode` and `mode_label` on each.

    A home is only My home or Investment home; a plot is only Investment land
    or Forestry. The shown score picks between those two. Listings pages rank
    one goal at a time; everything that speaks for the whole app — alerts, the
    report, the Offers shortlist — asks this instead, so a listing that is
    only interesting as a let or as a plot is not judged as somewhere to live
    and silently dropped. `modes` limits which goals compete: Offers passes
    the ones that can return cash.
    """
    from common import mode_max_price
    from config import load_config
    from scoring import FAMILY_GOALS, MODES, goal_family
    kw.pop("mode", None)
    chosen = tuple(m for m in (modes or MODES) if m in MODES) or tuple(MODES)
    # Live and invest is a tab. It is the worse of two scores, so it is not a
    # label that competes with My home or Investment home.
    family_modes = {m for goals in FAMILY_GOALS.values() for m in goals}
    cfg = load_config()
    best: dict[str, dict] = {}
    # Extreme-discount IDs collected across all mode passes (F05). The flag is
    # set only during invest-mode scoring, so a home-mode pass for the same item
    # would not see it. We collect across modes and post-filter so the gate fires
    # regardless of which mode scored the item first.
    extreme_ids: set[str] = set()
    for mode in chosen:
        if mode not in family_modes:
            continue
        budget = mode_max_price(cfg, mode, cfg.get("max_price") or 0)
        for item in load_listings(db, mode=mode, **kw):
            # Each goal pays its own price: a listing over this goal's budget
            # cannot be bought for it, whatever it scores.
            if budget and (item.get("price") or 0) > budget:
                continue
            if item.get("extreme_discount"):
                extreme_ids.add(item["id"])
            family = goal_family(item)
            allowed = FAMILY_GOALS.get(family)
            if allowed is not None and mode not in allowed:
                continue
            kept = best.get(item["id"])
            # The shown 0–100 score, not the raw rank: the raw numbers are
            # not on one scale. On a tie the goal earlier in MODES stays.
            if kept is None or (item.get("score") or 0) > (kept.get("score") or 0):
                best[item["id"]] = {**item, "mode": mode, "mode_label": MODES[mode]}
    # Extreme-discount items remain visible in the listing tab (researchable and
    # enrichable) but must not reach reports, alerts or the Offers shortlist until
    # they have been reviewed and cleared via clear_extreme_discount() (F05).
    # Shortlist membership or offers do not waive this requirement.
    # Pass current evidence revision sums so stale clearances (evidence changed
    # since the review) do not count. They are excluded, never deleted: a read
    # must not write, and the stale row is the review history.
    ev_rev_sums_best: dict[str, int] = {}
    if extreme_ids:
        ev_rev_sums_best = {
            row["listing_id"]: row["s"]
            for row in db.execute(
                "SELECT listing_id, SUM(rev) AS s FROM evidence_revisions"
                f" WHERE listing_id IN ({','.join('?' * len(extreme_ids))})"
                " GROUP BY listing_id",
                tuple(extreme_ids),
            ).fetchall()
        }
    cleared = _extreme_clearances(db, extreme_ids, ev_rev_sums=ev_rev_sums_best)
    blocked = extreme_ids - cleared
    return [it for it in best.values() if it["id"] not in blocked]


# ─── Publication snapshot (F15) ─────────────────────────────────────────────

_SNAP_LOCK = threading.Lock()
# {cache_key: (monotonic_time, listings_list)}
_SNAP_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
# How long (seconds) a snapshot is considered fresh.  Multiple consumers within
# the same enrichment cycle see the same listing states; after the TTL the next
# call re-runs load_listings so a new scrape is reflected promptly.
SNAP_TTL: float = 10.0


def publication_snapshot(db: sqlite3.Connection, *, filters: dict | None = None,
                         mode: str = "home", include_hidden: bool = False,
                         slim_hidden: bool = False, now: datetime | None = None,
                         ttl: float | None = None) -> list[dict]:
    """Database-scoped snapshot; consumers cannot mutate the cached copy."""
    import copy

    effective_ttl = ttl if ttl is not None else SNAP_TTL
    filters_key = json.dumps(filters, sort_keys=True, default=str) if filters else "null"
    paths = [row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"]
    path = paths[0] if paths else ""
    identity = ("file", os.path.realpath(path)) if path else ("memory", id(db))
    time_key = now.isoformat() if now is not None else None
    policy_key = json.dumps(_load_config_safe(), sort_keys=True, default=str)
    cache_key = (identity, mode, include_hidden, slim_hidden, filters_key, time_key, policy_key)
    with _SNAP_LOCK:
        stamp = time.monotonic()
        entry = _SNAP_CACHE.get(cache_key)
        if entry is not None and stamp - entry[0] < effective_ttl:
            return copy.deepcopy(entry[1])
        listings = load_listings(db, filters=filters, mode=mode,
                                 include_hidden=include_hidden, slim_hidden=slim_hidden, now=now)
        stored_copy = copy.deepcopy(listings)
        _SNAP_CACHE[cache_key] = (time.monotonic(), stored_copy)
        return copy.deepcopy(stored_copy)


def invalidate_snapshot(mode: str | None = None) -> None:
    """Invalidate all snapshots or those for one goal."""
    with _SNAP_LOCK:
        if mode is None:
            _SNAP_CACHE.clear()
        else:
            for key in list(_SNAP_CACHE):
                if isinstance(key, tuple) and len(key) > 1 and key[1] == mode:
                    del _SNAP_CACHE[key]
