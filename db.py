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

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from common import (
    LOG, effective_end, find_terms, normalize, parse_dt, safe_url, utcnow, utcnow_iso,
)

DB_PATH = os.environ.get("AUCTION_SCANNER_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "auctions.db")

# A listing is hidden as "stale" once its source has had a successful scrape
# this long after the listing was last seen (i.e. it is gone from the site).
STALE_AFTER = timedelta(days=3)
# "New" badge / new-today counters.
RECENT = timedelta(hours=24)

SCHEMA_VERSION = 2


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


_MIGRATIONS = {1: _migrate_v1, 2: _migrate_v2}


def init_db(db: sqlite3.Connection):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for v in range(version + 1, SCHEMA_VERSION + 1):
        _MIGRATIONS[v](db)
        db.execute(f"PRAGMA user_version = {v}")
        db.commit()
    if version < SCHEMA_VERSION:
        LOG.debug(f"Database schema migrated from v{version} to v{SCHEMA_VERSION}")


# ─── Writes ──────────────────────────────────────────────────────────

_UPDATE_FIELDS = (
    "title", "description", "tipo", "area_m2", "price", "current_bid", "min_price",
    "district", "concelho", "freguesia", "url", "image_url", "date_end", "raw_json",
)


def upsert_listing(db: sqlite3.Connection, row: dict) -> str:
    """Insert or refresh one listing. Returns "inserted" or "updated".

    Updates use COALESCE so a thin search-page scrape never wipes fields a
    detail-page enrichment filled in. Price/bid changes are kept in
    price_history.
    """
    now = utcnow_iso()
    existing = db.execute(
        "SELECT price, current_bid FROM listings WHERE id = ?", (row["id"],)
    ).fetchone()
    new_price, new_bid = row.get("price"), row.get("current_bid")

    if existing:
        sets = ", ".join(f"{f}=COALESCE(?, {f})" for f in _UPDATE_FIELDS)
        db.execute(
            f"UPDATE listings SET {sets}, last_seen=?, is_new=0 WHERE id=?",
            tuple(row.get(f) for f in _UPDATE_FIELDS) + (now, row["id"]),
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


def record_scrape(db: sqlite3.Connection, source: str, *, count: int, status: str,
                  message: str | None = None, duration_s: float | None = None,
                  timestamp: str | None = None):
    db.execute(
        "INSERT INTO scrape_log (source, timestamp, count, status, message, duration_s) "
        "VALUES (?,?,?,?,?,?)",
        (source, timestamp or utcnow_iso(), count, status, message, duration_s),
    )
    db.commit()


def mark_duplicates(db: sqlite3.Connection) -> int:
    """Flag cross-source near-duplicates (same country + concelho, price within
    €500, area within 5 m²). The most complete row stays visible; the others get
    duplicate_of set. Nothing is deleted. Returns how many rows are flagged."""
    rows = db.execute("""
        SELECT * FROM listings
        WHERE price IS NOT NULL AND area_m2 IS NOT NULL
          AND concelho IS NOT NULL AND TRIM(concelho) != ''
    """).fetchall()

    buckets: dict[tuple, list] = defaultdict(list)
    for r in rows:
        buckets[(r["country"], normalize(r["concelho"]).strip())].append(r)

    def completeness(r):
        return (sum(1 for v in r if v is not None), r["first_seen"] or "", r["id"])

    dup_of: dict[str, str] = {}
    for group in buckets.values():
        if len(group) < 2:
            continue
        group = sorted(group, key=completeness, reverse=True)
        for i, keeper in enumerate(group):
            if keeper["id"] in dup_of:
                continue
            for other in group[i + 1:]:
                if other["id"] in dup_of or other["source"] == keeper["source"]:
                    continue
                if (abs(other["price"] - keeper["price"]) < 500
                        and abs(other["area_m2"] - keeper["area_m2"]) < 5):
                    dup_of[other["id"]] = keeper["id"]

    db.execute("UPDATE listings SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
    db.executemany("UPDATE listings SET duplicate_of = ? WHERE id = ?",
                   [(keeper, dup) for dup, keeper in dup_of.items()])
    db.commit()
    if dup_of:
        LOG.info(f"Deduplication: {len(dup_of)} listings flagged as cross-source duplicates")
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
    order = {"error": 0, "broken": 1, "never worked": 2, "never run": 3, "ok": 4}
    out.sort(key=lambda s: (order[s["state"]], s["source"]))
    return out


def last_ok_by_source(db: sqlite3.Connection) -> dict[str, datetime]:
    out = {}
    for source, ts in db.execute(
            "SELECT source, MAX(timestamp) FROM scrape_log WHERE count > 0 GROUP BY source"):
        dt = parse_dt(ts)
        if dt:
            out[source] = dt
    return out


# ─── Reading: the one loader ─────────────────────────────────────────

def filter_reason(item: dict, filters: dict | None) -> str | None:
    """Why the user's config filters exclude this listing, or None."""
    if not filters:
        return None
    countries = filters.get("countries") or []
    if countries and item.get("country") not in countries:
        return f"country {item.get('country')} not in filters.countries"

    keywords = filters.get("exclude_keywords") or []
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


def hidden_category(reason: str | None) -> str | None:
    """Group a hidden_reason into expired / duplicate / stale / filtered / low score."""
    if not reason:
        return None
    for prefix, label in (("expired", "expired"), ("duplicate", "duplicate"),
                          ("stale", "stale"), ("score", "low score")):
        if reason.startswith(prefix):
            return label
    return "filtered"


def _first_prices(db: sqlite3.Connection) -> dict[str, float]:
    first = {}
    for lid, price in db.execute(
            "SELECT listing_id, price FROM price_history WHERE price IS NOT NULL "
            "ORDER BY observed_at ASC"):
        first.setdefault(lid, price)
    return first


def load_listings(db: sqlite3.Connection, *, filters: dict | None = None,
                  include_hidden: bool = False, now: datetime | None = None,
                  where: str = "", params=(), apply_min_score: bool = True) -> list[dict]:
    """Every listing a view should consider, scored, with hidden ones removed.

    Each item gains: score, reasons, category, hidden_reason (None if visible),
    price_drop_pct, is_recent. `where`/`params` are extra SQL conditions on the
    listings table for cheap pre-filtering (country, source, search...).

    Hidden, in order of precedence: expired, duplicate, stale (gone from its
    source), excluded by config filters, below filters.min_score.
    """
    from scoring import categorize, score  # scoring imports common, not db

    now = now or utcnow()
    sql = "SELECT * FROM listings"
    if where:
        sql += f" WHERE {where}"
    rows = db.execute(sql, params).fetchall()

    last_ok = last_ok_by_source(db)
    first_price = _first_prices(db)
    min_score = ((filters or {}).get("min_score") or 0) if apply_min_score else 0

    items = []
    for r in rows:
        item = dict(r)
        # Rows written before safe_url() existed may still hold javascript: links.
        item["url"] = safe_url(item.get("url"))
        reason = None

        end = effective_end(item.get("date_end"))
        if end is not None and end <= now:
            reason = "expired"
        elif item.get("duplicate_of"):
            reason = f"duplicate of {item['duplicate_of']}"
        else:
            seen = parse_dt(item.get("last_seen"))
            ok = last_ok.get(item["source"])
            if seen and ok and ok - seen > STALE_AFTER:
                reason = f"stale: gone from {item['source']} since {seen:%Y-%m-%d}"
            else:
                reason = filter_reason(item, filters)

        if reason and not include_hidden:
            continue

        fp = first_price.get(item["id"])
        if fp and item.get("price") and fp > 0 and item["price"] < fp:
            item["price_drop_pct"] = round((fp - item["price"]) / fp * 100, 1)
        else:
            item["price_drop_pct"] = None

        sc, reasons = score(item, now=now)
        item["score"] = sc
        item["reasons"] = reasons
        item["category"] = categorize(item)

        if reason is None and min_score and sc < min_score:
            reason = f"score {sc:.0f} < filters.min_score {min_score}"
            if not include_hidden:
                continue

        item["hidden_reason"] = reason
        seen_first = parse_dt(item.get("first_seen"))
        item["is_recent"] = bool(seen_first and now - seen_first <= RECENT)
        items.append(item)
    return items
