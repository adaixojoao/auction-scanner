"""Producer-owned evidence (Beta): append-only observations plus one current
row per (listing, producer), written in one short transaction.

The producer captures the listing's dependency context before any external
work and passes its hash and the revision it saw; a changed context or a
newer observation refuses the write. Installed by db migration v34, never at
import time. Provenance must not hold credentials.
"""
import json
import uuid
from datetime import datetime, timezone

from collection_contracts import content_hash


class EvidenceConflict(RuntimeError):
    pass


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS evidence_current (
      listing_id TEXT NOT NULL, producer TEXT NOT NULL,
      revision INTEGER NOT NULL, binding TEXT NOT NULL,
      payload_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
      observed_at TEXT NOT NULL, PRIMARY KEY(listing_id,producer))""",
    """CREATE TABLE IF NOT EXISTS evidence_observations (
      observation_id TEXT PRIMARY KEY, listing_id TEXT NOT NULL,
      producer TEXT NOT NULL, revision INTEGER NOT NULL,
      binding TEXT NOT NULL, payload_json TEXT NOT NULL,
      provenance_json TEXT NOT NULL, observed_at TEXT NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS idx_evidence_observations_lot
      ON evidence_observations(listing_id,producer,observed_at)""",
)

# Producers allowed to write; an API client cannot pick its own namespace.
PRODUCERS = frozenset({"source_detail", "location", "water", "cadastre", "climate",
                       "photos", "link_check", "archive", "checklist"})


def install_schema(db):
    for statement in _SCHEMA:
        db.execute(statement)


def write_observation(db, listing_id, producer, payload, provenance,
                      expected_revision, expected_listing_hash, listing_context):
    """payload=None is an explicit unknown/retraction, not 'no new observation'.
    Accepted observations are append-only; current producer evidence is updated.
    """
    if not isinstance(listing_id, str) or not listing_id:
        raise ValueError("Listing identity required")
    if not isinstance(producer, str) or not producer:
        raise ValueError("Producer required")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Nonnegative integer revision required")
    if not isinstance(provenance, dict):
        raise ValueError("Provenance must be an object")
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
    prov = json.dumps(provenance, sort_keys=True, allow_nan=False)
    if db.in_transaction:
        raise ValueError("Use a clean dedicated connection")
    db.execute("BEGIN IMMEDIATE")
    try:
        context = listing_context(db, listing_id)
        if context is None or content_hash(context) != expected_listing_hash:
            raise EvidenceConflict("Listing inputs changed during assessment")
        row = db.execute("""SELECT revision,payload_json,binding FROM evidence_current
          WHERE listing_id=? AND producer=?""", (listing_id, producer)).fetchone()
        current_rev = row[0] if row else 0
        if current_rev != expected_revision:
            raise EvidenceConflict("A newer observation exists")
        binding = content_hash({"inputs": context, "payload": payload})
        changed = row is None or binding != row[2]
        revision = current_rev + int(changed)
        at = datetime.now(timezone.utc).isoformat()
        ident = str(uuid.uuid4())
        db.execute("INSERT INTO evidence_observations VALUES(?,?,?,?,?,?,?,?)",
                   (ident, listing_id, producer, revision, binding, encoded, prov, at))
        db.execute("""INSERT INTO evidence_current VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(listing_id,producer) DO UPDATE SET
          revision=excluded.revision,binding=excluded.binding,
          payload_json=excluded.payload_json,provenance_json=excluded.provenance_json,
          observed_at=excluded.observed_at""",
          (listing_id, producer, revision, binding, encoded, prov, at))
        db.commit()
        return {"observation_id": ident, "revision": revision, "binding": binding}
    except BaseException:
        db.rollback()
        raise


def write_owned(db, listing_id, producer, payload, provenance,
                expected_revision, expected_listing_hash, listing_context):
    """write_observation for a registered producer only."""
    if producer not in PRODUCERS:
        raise ValueError(f"Unknown evidence producer: {producer!r}")
    return write_observation(db, listing_id, producer, payload, provenance,
                             expected_revision, expected_listing_hash, listing_context)


def current(db, listing_id, producer):
    """The current observation as a dict, or None when nothing was recorded."""
    row = db.execute("""SELECT revision,binding,payload_json,provenance_json,observed_at
      FROM evidence_current WHERE listing_id=? AND producer=?""", (listing_id, producer)).fetchone()
    if row is None:
        return None
    return {"revision": row[0], "binding": row[1], "payload": json.loads(row[2]),
            "provenance": json.loads(row[3]), "observed_at": row[4]}
