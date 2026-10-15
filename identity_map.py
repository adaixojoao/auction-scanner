"""
identity_map.py — Stable lot identity for Citius and Leilosoc (Milestone B).

Problem (F01): scraper-local `per_process` / `per_auction` counters reset on
every scan, so if the first lot of a processo drops from the page the second lot
inherits the first's ID.  Offers, letters, geo pins and learned descriptions then
point at the wrong property.

Fix: a persistent `lot_map` table (added by db._migrate_v21) records the stable
mapping from (source, processo, lot_key) → listing_id.  The lot_key is derived
from legal identifiers that survive re-publication (land-registry reference, tax
matricial reference, or a deterministic hash of the description excerpt).

Conflict quarantine: when two scraped lots resolve to the same lot_key within the
same processo, or when observable identifiers diverge from what was stored, the
conflict is recorded in `lot_map.conflict_details` and the listing is left as-is.
The conflicting lot is not silently updated or merged.

Interface used by sources/pt.py:
    eid = assign_citius_lot_id(db, processo, base_eid, lot_data)
    eid = assign_leilosoc_lot_id(db, auction_id, batch_id)

The returned `eid` is the external_id component of the listing id (without the
source: prefix).  It is the same across scans as long as the lot's legal identity
is stable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3

LOG = logging.getLogger(__name__)


# ─── Lot-key derivation ──────────────────────────────────────────────────────

def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", (s or "").upper())


def lot_key_citius(lot_data: dict) -> str:
    """Stable discriminator for one lot within a Citius processo.

    Priority:
      1. Land registry reference (registo) — most stable, legal identifier
      2. Property tax reference (art_matricial)
      3. Description excerpt hash — falls back to content, not position
    """
    registo = _norm(lot_data.get("registo"))
    if registo:
        return f"reg:{registo}"
    mat = _norm(lot_data.get("art_matricial"))
    if mat:
        return f"mat:{mat}"
    desc = re.sub(r"\s+", " ", (lot_data.get("desc") or "").strip()).lower()[:160]
    return "desc:" + hashlib.sha1(desc.encode()).hexdigest()[:20]


def lot_key_leilosoc(batch_id: str | int) -> str:
    """Stable discriminator for one lot within a Leilosoc auction.

    Leilosoc assigns a persistent batchId to each lot (different from the
    sequential encounter position used by the old per_auction counter).
    """
    return f"batch:{batch_id}"


# ─── Resolve / assign stable eid ─────────────────────────────────────────────

def _next_eid_for_processo(db: sqlite3.Connection, source: str,
                            processo: str, base_eid: str) -> str:
    """Return the next unused eid in the processo's sequence."""
    existing = db.execute(
        "SELECT listing_id FROM lot_map WHERE source=? AND processo=? ORDER BY rowid",
        (source, processo)).fetchall()
    if not existing:
        return base_eid
    return f"{base_eid}-{len(existing) + 1}"


def assign_citius_lot_id(
        db: sqlite3.Connection,
        processo: str,
        base_eid: str,
        lot_data: dict,
) -> str:
    """Return the stable eid for this Citius lot, creating a mapping if needed.

    Parameters
    ----------
    processo:
        Normalised court-case string used as the processo key (same as base_eid
        before the suffix is added, e.g. "165103TBMRA").
    base_eid:
        The ID root derived from the processo (or stable_id fallback); used when
        creating a new entry.
    lot_data:
        The parsed lot dict from parse_citius_item; used to derive the lot_key.
    """
    lot_key = lot_key_citius(lot_data)
    return _resolve(db, "citius", processo, lot_key, base_eid, lot_data)


def assign_leilosoc_lot_id(
        db: sqlite3.Connection,
        auction_id: str,
        batch_id: str | int,
) -> str:
    """Return the stable eid for this Leilosoc lot, creating a mapping if needed.

    Parameters
    ----------
    auction_id:
        The Leilosoc auction identifier (used as both processo key and base_eid).
    batch_id:
        The lot's batchId; forms the lot_key.
    """
    lot_key = lot_key_leilosoc(batch_id)
    return _resolve(db, "leilosoc", auction_id, lot_key, auction_id, {})


def _resolve(
        db: sqlite3.Connection,
        source: str,
        processo: str,
        lot_key: str,
        base_eid: str,
        lot_data: dict,
) -> str:
    """Core resolve: look up existing mapping or create a new one.

    Returns the external_id component (without the source: prefix).
    """
    row = db.execute(
        "SELECT listing_id, conflict_details FROM lot_map"
        " WHERE source=? AND processo=? AND lot_key=?",
        (source, processo, lot_key)).fetchone()

    if row:
        if row["conflict_details"]:
            LOG.warning(
                "lot_map: %s %s lot_key=%s is in conflict quarantine: %s",
                source, processo, lot_key, row["conflict_details"])
        listing_id = row["listing_id"]
        return listing_id.removeprefix(f"{source}:")

    # New lot_key for this processo — check for potential conflicts before inserting.
    conflict_details = _check_conflict(db, source, processo, lot_key, base_eid, lot_data)
    new_eid = _next_eid_for_processo(db, source, processo, base_eid)
    new_listing_id = f"{source}:{new_eid}"

    db.execute(
        "INSERT INTO lot_map (source, processo, lot_key, listing_id, conflict_details)"
        " VALUES (?,?,?,?,?)",
        (source, processo, lot_key, new_listing_id,
         json.dumps(conflict_details) if conflict_details else None))
    return new_eid


def _check_conflict(
        db: sqlite3.Connection,
        source: str,
        processo: str,
        lot_key: str,
        base_eid: str,
        lot_data: dict,
) -> dict | None:
    """Return a conflict dict if this new lot appears to conflict with an existing
    listing stored under the base_eid, otherwise None.

    A conflict arises when:
    - The base_eid listing already exists with a different stable legal identifier
      (registo or art_matricial) than this lot's lot_key.
    - This suggests the existing entry belongs to a different legal property.
    """
    existing_id = f"{source}:{base_eid}"
    row = db.execute(
        "SELECT raw_json FROM listings WHERE id=?", (existing_id,)).fetchone()
    if not row or not row["raw_json"]:
        return None
    try:
        stored_raw = json.loads(row["raw_json"])
    except (ValueError, TypeError):
        return None

    stored_registo = _norm(stored_raw.get("registo"))
    stored_mat = _norm(stored_raw.get("art_matricial"))
    new_registo = _norm(lot_data.get("registo"))
    new_mat = _norm(lot_data.get("art_matricial"))

    conflicts = {}
    if stored_registo and new_registo and stored_registo != new_registo:
        conflicts["registo_mismatch"] = {
            "stored": stored_registo, "new": new_registo}
    if stored_mat and new_mat and stored_mat != new_mat:
        conflicts["art_matricial_mismatch"] = {
            "stored": stored_mat, "new": new_mat}

    if conflicts:
        LOG.warning(
            "lot_map: %s %s NEW lot_key=%s conflicts with %s: %s",
            source, processo, lot_key, existing_id, conflicts)
    return conflicts or None


# ─── Migration helpers ───────────────────────────────────────────────────────

def backfill_citius(db: sqlite3.Connection) -> int:
    """Populate lot_map from existing Citius listings.

    Groups by the processo stored in raw_json, assigns lot_keys in rowid order
    (which approximates original insertion order), and inserts without conflicts.
    Returns the number of rows inserted.
    """
    rows = db.execute(
        "SELECT id, raw_json FROM listings WHERE source='citius'"
        " ORDER BY rowid").fetchall()

    inserted = 0
    per_processo: dict[str, list] = {}
    for row in rows:
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except (ValueError, TypeError):
            raw = {}
        processo = _norm(raw.get("processo") or "")
        if not processo:
            # Fall back to the id itself as its own processo group
            processo = row["id"].removeprefix("citius:")
        per_processo.setdefault(processo, []).append((row["id"], raw))

    for processo, lots in per_processo.items():
        for listing_id, raw in lots:
            lot_key = lot_key_citius(raw)
            existing = db.execute(
                "SELECT listing_id FROM lot_map"
                " WHERE source='citius' AND processo=? AND lot_key=?",
                (processo, lot_key)).fetchone()
            if existing:
                continue
            # Check for lot_key collision within the same processo
            collision = db.execute(
                "SELECT lot_key FROM lot_map"
                " WHERE source='citius' AND processo=? AND listing_id=?",
                (processo, listing_id)).fetchone()
            if collision:
                continue
            db.execute(
                "INSERT OR IGNORE INTO lot_map"
                " (source, processo, lot_key, listing_id) VALUES (?,?,?,?)",
                ("citius", processo, lot_key, listing_id))
            inserted += 1

    return inserted


def backfill_leilosoc(db: sqlite3.Connection) -> int:
    """Populate lot_map from existing Leilosoc listings.

    For each listing, the batch_id is stored in raw_json.  When present, the
    lot_key is batch:<batch_id>.  When absent (pre-batch_id rows), a synthetic
    lot_key of batch:legacy-<external_id> is used to keep the mapping stable.
    Returns the number of rows inserted.
    """
    rows = db.execute(
        "SELECT id, external_id, raw_json FROM listings WHERE source='leilosoc'"
        " ORDER BY rowid").fetchall()

    inserted = 0
    for row in rows:
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except (ValueError, TypeError):
            raw = {}

        # The auctionId (processo) is the numeric prefix of the external_id.
        # external_id is one of: "12345" (first lot) or "12345-678" (later lots).
        ext_id = row["external_id"] or ""
        if "-" in ext_id:
            auction_id, batch_from_id = ext_id.split("-", 1)
        else:
            auction_id = ext_id
            batch_from_id = None

        batch_id = raw.get("batch_id") or batch_from_id
        if batch_id is not None:
            lot_key = lot_key_leilosoc(batch_id)
        else:
            lot_key = f"batch:legacy-{ext_id}"

        existing = db.execute(
            "SELECT listing_id FROM lot_map"
            " WHERE source='leilosoc' AND processo=? AND lot_key=?",
            (auction_id, lot_key)).fetchone()
        if existing:
            continue
        collision = db.execute(
            "SELECT lot_key FROM lot_map"
            " WHERE source='leilosoc' AND processo=? AND listing_id=?",
            (auction_id, row["id"])).fetchone()
        if collision:
            continue
        db.execute(
            "INSERT OR IGNORE INTO lot_map"
            " (source, processo, lot_key, listing_id) VALUES (?,?,?,?)",
            ("leilosoc", auction_id, lot_key, row["id"]))
        inserted += 1

    return inserted
