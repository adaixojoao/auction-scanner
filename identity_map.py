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

Resolution result: assign_citius_lot_id / assign_leilosoc_lot_id return a
ResolutionResult whose .kind distinguishes:
  - RESOLVED:    existing mapping, identifiers confirmed.
  - NEW:         first time this lot_key seen; fresh eid allocated.
  - PROVISIONAL: new lot matched by weak description-hash only.
  - QUARANTINED: existing mapping has a recorded conflict; the caller MUST NOT
                 update the protected listing or inherit its attachments.

Callers must check result.kind before upsert:
    result = assign_citius_lot_id(db, processo, base_eid, lot_data)
    if result.kind is ResolutionKind.QUARANTINED:
        continue  # skip upsert
    upsert_listing(db, make_row(result.eid))
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from enum import Enum

LOG = logging.getLogger(__name__)


# ─── Resolution result ────────────────────────────────────────────────────────

class ResolutionKind(str, Enum):
    RESOLVED    = "resolved"     # existing mapping, identifiers confirmed
    NEW         = "new"          # new lot, fresh eid allocated
    PROVISIONAL = "provisional"  # new lot matched by weak desc-hash only
    QUARANTINED = "quarantined"  # existing mapping in conflict; skip upsert


@dataclass
class ResolutionResult:
    kind: ResolutionKind
    eid: str | None         # None when QUARANTINED
    lot_key: str
    conflict_details: dict | None = field(default=None)


# ─── Processo-key canonicalisation ───────────────────────────────────────────

def canonical_processo(p: str | None) -> str:
    """Strip all non-alphanumeric characters, preserving original case.

    Used by both the scraper (when building base_eid) and backfill so the
    lot_map processo key is the same regardless of how the raw text arrived.
    E.g. "9/21.0TBLSB" → "9210TBLSB".
    """
    return re.sub(r"[^A-Za-z0-9]", "", (p or ""))


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
    desc = _desc_text(lot_data.get("desc"))[:DESC_KEY_CHARS]
    return "desc:" + hashlib.sha1(desc.encode()).hexdigest()[:20]


#: Characters of the description a desc: lot_key is built from. Two lots can
#: share this much text (a court's boilerplate) and differ only in the tail.
DESC_KEY_CHARS = 160
#: Characters of the processo kept as the ID root (the scraper's base_eid).
PROCESSO_ROOT_CHARS = 40


def processo_root(p: str | None) -> str:
    """The lot_map processo key exactly as the scraper builds it: canonical,
    then cut to PROCESSO_ROOT_CHARS. Backfill and migrations use this too."""
    return canonical_processo(p)[:PROCESSO_ROOT_CHARS]


def _desc_text(desc: str | None) -> str:
    return re.sub(r"\s+", " ", (desc or "").strip()).lower()


_FRACTION_RE = re.compile(r"\bfra[cç]{1,2}[aã]o\s+(?:aut[oó]noma\s+)?(?:designada\s+(?:pela\s+letra\s+)?)?[\"'«]?([a-z]{1,3})\b")
_ARTICLE_RE = re.compile(r"\bartigo\s+(?:matricial\s+)?(?:urbano\s+|r[uú]stico\s+)?(?:n\.?\s*[oº]?\s*)?(\d{1,6})\b")


def unit_markers(text: str | None) -> dict:
    """The fraction letters and tax-article numbers a lot's text names."""
    t = _desc_text(text)
    return {"fractions": sorted({m.group(1) for m in _FRACTION_RE.finditer(t)}),
            "articles": sorted({m.group(1) for m in _ARTICLE_RE.finditer(t)})}


def lot_key_leilosoc(batch_id: str | int) -> str:
    """Stable discriminator for one lot within a Leilosoc auction.

    Leilosoc assigns a persistent batchId to each lot (different from the
    sequential encounter position used by the old per_auction counter).
    """
    return f"batch:{batch_id}"


# ─── Resolve / assign stable eid ─────────────────────────────────────────────

def _next_eid_for_processo(db: sqlite3.Connection, source: str,
                            processo: str, base_eid: str) -> str:
    """Return the next unused eid in the processo's sequence.

    Checks both lot_map rows and existing listing IDs so that a candidate eid
    is never allocated if a listing under that ID already exists.
    """
    existing_in_map = {
        row["listing_id"]
        for row in db.execute(
            "SELECT listing_id FROM lot_map WHERE source=? AND processo=?",
            (source, processo)).fetchall()
    }
    if not existing_in_map:
        candidate = base_eid
    else:
        candidate = f"{base_eid}-{len(existing_in_map) + 1}"

    # Ensure the candidate listing_id is not already taken by any listing.
    n = len(existing_in_map) + 1
    while True:
        candidate_id = f"{source}:{candidate}"
        taken_in_map = candidate_id in existing_in_map
        taken_in_listings = db.execute(
            "SELECT 1 FROM listings WHERE id=?", (candidate_id,)).fetchone() is not None
        if not taken_in_map and not taken_in_listings:
            return candidate
        n += 1
        candidate = f"{base_eid}-{n}"


def assign_citius_lot_id(
        db: sqlite3.Connection,
        processo: str,
        base_eid: str,
        lot_data: dict,
) -> ResolutionResult:
    """Return a ResolutionResult for this Citius lot.

    Parameters
    ----------
    processo:
        Canonical court-case string used as the processo key (e.g. "9210TBLSB").
    base_eid:
        The ID root derived from the processo (or stable_id fallback).
    lot_data:
        The parsed lot dict from parse_citius_item; used to derive the lot_key.

    Callers MUST check result.kind before calling upsert_listing.  When
    result.kind is QUARANTINED, result.eid is None and the listing must not
    be updated.
    """
    lot_key = lot_key_citius(lot_data)
    return _resolve(db, "citius", processo, lot_key, base_eid, lot_data)


def assign_leilosoc_lot_id(
        db: sqlite3.Connection,
        auction_id: str,
        batch_id: str | int,
) -> ResolutionResult:
    """Return a ResolutionResult for this Leilosoc lot.

    Parameters
    ----------
    auction_id:
        The Leilosoc auction identifier (used as both processo key and base_eid).
    batch_id:
        The lot's batchId; forms the lot_key.

    Callers MUST check result.kind before calling upsert_listing.
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
) -> ResolutionResult:
    """Core resolve: look up existing mapping or create a new one.

    Returns a ResolutionResult.  Callers must not update the protected
    listing when kind is QUARANTINED.
    """
    row = db.execute(
        "SELECT listing_id, conflict_details FROM lot_map"
        " WHERE source=? AND processo=? AND lot_key=?",
        (source, processo, lot_key)).fetchone()

    if row:
        eid = row["listing_id"].removeprefix(f"{source}:")
        # The same key on an existing mapping is only an upsert target once the
        # full material unit identity agrees (not just the key's 160 characters).
        if row["conflict_details"]:
            cd = json.loads(row["conflict_details"])
            LOG.warning(
                "lot_map: %s %s lot_key=%s is in conflict quarantine: %s",
                source, processo, lot_key, cd)
            return ResolutionResult(
                kind=ResolutionKind.QUARANTINED,
                eid=None,
                lot_key=lot_key,
                conflict_details=cd,
            )
        # Existing mapping — verify material identifiers have not diverged.
        divergence = _check_identifier_divergence(db, source, eid, lot_data, lot_key)
        if divergence:
            # Record divergence in the existing row and quarantine.
            LOG.warning(
                "lot_map: %s %s lot_key=%s identifier divergence from stored: %s",
                source, processo, lot_key, divergence)
            db.execute(
                "UPDATE lot_map SET conflict_details=? WHERE source=? AND processo=? AND lot_key=?",
                (json.dumps(divergence), source, processo, lot_key))
            return ResolutionResult(
                kind=ResolutionKind.QUARANTINED,
                eid=None,
                lot_key=lot_key,
                conflict_details=divergence,
            )
        # A lot known only by its description hash stays PROVISIONAL however
        # often it is seen: repetition of the same weak data is not new support.
        kind = ResolutionKind.PROVISIONAL if lot_key.startswith("desc:") else ResolutionKind.RESOLVED
        return ResolutionResult(kind=kind, eid=eid, lot_key=lot_key)

    # New lot_key for this processo — check for same-key conflicts before inserting.
    conflict_details = _check_same_key_conflict(db, source, processo, lot_key, lot_data)
    new_eid = _next_eid_for_processo(db, source, processo, base_eid)
    new_listing_id = f"{source}:{new_eid}"
    is_provisional = lot_key.startswith("desc:")

    db.execute(
        "INSERT INTO lot_map (source, processo, lot_key, listing_id, conflict_details)"
        " VALUES (?,?,?,?,?)",
        (source, processo, lot_key, new_listing_id,
         json.dumps(conflict_details) if conflict_details else None))

    kind = (ResolutionKind.QUARANTINED if conflict_details
            else ResolutionKind.PROVISIONAL if is_provisional
            else ResolutionKind.NEW)
    return ResolutionResult(
        kind=kind,
        eid=None if kind is ResolutionKind.QUARANTINED else new_eid,
        lot_key=lot_key,
        conflict_details=conflict_details,
    )


def _check_same_key_conflict(
        db: sqlite3.Connection,
        source: str,
        processo: str,
        lot_key: str,
        lot_data: dict,
) -> dict | None:
    """Return a conflict dict when the same lot_key is already mapped to a different
    listing_id in this processo (i.e. two physically different lots share a key).

    A second lot with a *different* registry number in the same processo is not a
    conflict — it is a distinct legal unit and will receive a new suffixed eid.
    A conflict only arises when the *same* lot_key would be mapped twice.
    """
    existing = db.execute(
        "SELECT listing_id FROM lot_map"
        " WHERE source=? AND processo=? AND lot_key=?",
        (source, processo, lot_key)).fetchone()
    if not existing:
        return None
    conflict = {
        "same_lot_key_collision": {
            "lot_key": lot_key,
            "existing_listing_id": existing["listing_id"],
        }
    }
    LOG.warning(
        "lot_map: %s %s lot_key=%s already mapped to %s; quarantining new observation",
        source, processo, lot_key, existing["listing_id"])
    return conflict


def _check_identifier_divergence(
        db: sqlite3.Connection,
        source: str,
        eid: str,
        lot_data: dict,
        lot_key: str = "",
) -> dict | None:
    """Return a conflict dict when the current lot_data's strong identifiers diverge
    from those stored in the existing listing, otherwise None.

    Also compares the material unit identity named in the text — fraction letters
    and tax articles — and, for a lot keyed by its description's first
    DESC_KEY_CHARS characters, the rest of that description: two lots of one
    processo sharing a long boilerplate opening must not share an ID.

    Divergence is only flagged when both the stored record and the new observation
    carry a non-empty value for the same strong identifier (registo or art_matricial)
    and those values differ.  Empty fields or additions (one side is empty) are not
    divergences — they represent data enrichment.
    """
    if not lot_data:
        return None
    listing_id = f"{source}:{eid}"
    try:
        row = db.execute(
            "SELECT raw_json, description FROM listings WHERE id=?", (listing_id,)).fetchone()
    except sqlite3.OperationalError:          # a table without the description column
        row = db.execute(
            "SELECT raw_json, NULL AS description FROM listings WHERE id=?", (listing_id,)).fetchone()
    if not row:
        return None
    try:
        stored_raw = json.loads(row["raw_json"] or "{}")
    except (ValueError, TypeError):
        stored_raw = {}
    if not isinstance(stored_raw, dict):
        stored_raw = {}
    stored_text = row["description"] or ""

    stored_registo = _norm(stored_raw.get("registo"))
    stored_mat = _norm(stored_raw.get("art_matricial"))
    new_registo = _norm(lot_data.get("registo"))
    new_mat = _norm(lot_data.get("art_matricial"))

    divergence = {}
    if stored_registo and new_registo and stored_registo != new_registo:
        divergence["registo_divergence"] = {
            "stored": stored_registo, "new": new_registo}
    if stored_mat and new_mat and stored_mat != new_mat:
        divergence["art_matricial_divergence"] = {
            "stored": stored_mat, "new": new_mat}

    new_desc = lot_data.get("desc") or ""
    if stored_text and new_desc:
        old_m, new_m = unit_markers(stored_text), unit_markers(new_desc)
        for kind in ("fractions", "articles"):
            if old_m[kind] and new_m[kind] and old_m[kind] != new_m[kind]:
                divergence[f"{kind}_divergence"] = {"stored": old_m[kind], "new": new_m[kind]}
        if lot_key.startswith("desc:"):
            # The listing's description starts with the lot text it was made from.
            # A list page's copy is often cut ("… com uma ..."): a cut copy of
            # the stored text, or of its full detail text, is the same lot.
            new_t = re.sub(r"[\s.…]+$", "", _desc_text(new_desc))[:3000]
            stored = [_desc_text(stored_text), _desc_text(stored_raw.get("descricao_completa"))]
            same = any(t and (t.startswith(new_t) or new_t.startswith(t)) for t in stored)
            if len(new_t) > DESC_KEY_CHARS and not same:
                divergence["description_tail_divergence"] = {
                    "shared_prefix_chars": DESC_KEY_CHARS,
                    "stored_tail": stored[0][DESC_KEY_CHARS:DESC_KEY_CHARS + 120],
                    "new_tail": new_t[DESC_KEY_CHARS:DESC_KEY_CHARS + 120]}
    return divergence or None


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
        # The same key the scraper builds (canonical, cut to 40 characters).
        processo = processo_root(raw.get("processo") or "")
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
