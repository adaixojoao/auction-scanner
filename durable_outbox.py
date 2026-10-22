"""Durable outbox for offers the app sends itself (Alpha).

The exact letter, recipient, PDF bytes and hash, amount, cost scenario and
gate records are stored BEFORE anything is sent, under an idempotency key:
clicking Send twice, or Telegram's Send twice, names the same operation and
never sends twice. A send moves pending → sending (claimed by one worker) →
sent / failed / delivery_unknown / needs_review.

- sent: the SMTP server accepted the intended recipient. That is not delivery
  and not a legally valid submission.
- failed: positively known not accepted (nothing reached DATA, or the
  recipient was refused). Only a failed operation may be tried again.
- delivery_unknown: something broke after the message may have been
  accepted. Never resent blindly: check the Sent folder of the mailbox.

Recording the accepted send and its carta_log row happens in one short
transaction (`complete`). If that transaction fails after acceptance the row
stays "sending" for reconciliation. The schema is installed by db.py's
migration (v33), never at import time. No secrets are stored.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from datetime import datetime, timezone

from offer_safety import SafetyError, fingerprint

STATES = ("pending", "sending", "sent", "failed", "delivery_unknown", "needs_review")
RESULTS = ("sent", "failed", "delivery_unknown", "needs_review")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS operational_outbox (
        id TEXT PRIMARY KEY, idem_key TEXT NOT NULL UNIQUE,
        listing_id TEXT, kind TEXT NOT NULL DEFAULT 'email_letter',
        payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN
          ('pending','sending','sent','failed','delivery_unknown','needs_review')),
        claim_token TEXT, worker_id TEXT, message_id TEXT NOT NULL,
        carta_log_id INTEGER, detail TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS operational_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
        worker_id TEXT NOT NULL, started_at TEXT NOT NULL,
        finished_at TEXT, result TEXT, detail TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_operational_outbox_listing ON operational_outbox(listing_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_operational_attempts_op ON operational_attempts(operation_id, id)",
)
DETAIL_MAX = 500


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def install_schema(db) -> None:
    """For db.py's migration and for tests on a bare connection."""
    for statement in SCHEMA:
        db.execute(statement)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def enqueue(db, idem_key: str, snapshot: dict, *, listing_id: str | None = None,
            identity: dict | None = None) -> str:
    """Store the exact operation once. The same key with other content is refused.

    `identity` is what "the same content" means when it is narrower than the
    snapshot (a regenerated PDF with a new timestamp is still the same letter);
    the first snapshot stored is the one that is sent."""
    if not isinstance(idem_key, str) or not 1 <= len(idem_key) <= 128:
        raise SafetyError("Invalid idempotency key")
    digest = fingerprint(snapshot if identity is None else identity)
    encoded = json.dumps(snapshot, sort_keys=True, allow_nan=False)
    ident = str(uuid.uuid4())
    with db:
        db.execute("""INSERT OR IGNORE INTO operational_outbox
          (id,idem_key,listing_id,payload_hash,payload_json,state,message_id,created_at,updated_at)
          VALUES(?,?,?,?,?,'pending',?,?,?)""",
                   (ident, idem_key, listing_id, digest, encoded, f"<{ident}@auction-scanner.local>", now(), now()))
        row = db.execute("SELECT id,payload_hash FROM operational_outbox WHERE idem_key=?",
                         (idem_key,)).fetchone()
        if row[1] != digest:
            raise SafetyError("Idempotency key already names different content")
    return row[0]


def get(db, ident: str) -> dict | None:
    row = db.execute("""SELECT id, idem_key, listing_id, state, message_id, carta_log_id, detail,
                               created_at, updated_at, payload_json FROM operational_outbox WHERE id=?""",
                     (ident,)).fetchone()
    if not row:
        return None
    keys = ("id", "idem_key", "listing_id", "state", "message_id", "carta_log_id", "detail",
            "created_at", "updated_at")
    out = dict(zip(keys, row[:9]))
    out["snapshot"] = json.loads(row[9])
    return out


def claim(db, ident: str, worker: str) -> str | None:
    token = str(uuid.uuid4())
    with db:
        cur = db.execute("""UPDATE operational_outbox
          SET state='sending',claim_token=?,worker_id=?,updated_at=?
          WHERE id=? AND state='pending'""", (token, worker, now(), ident))
        if cur.rowcount == 1:
            db.execute("INSERT INTO operational_attempts (operation_id, worker_id, started_at) VALUES (?,?,?)",
                       (ident, worker, now()))
    return token if cur.rowcount == 1 else None


def reopen_failed(db, ident: str) -> bool:
    """A positively failed send may be tried again; nothing else may."""
    with db:
        cur = db.execute("""UPDATE operational_outbox SET state='pending', claim_token=NULL, updated_at=?
                            WHERE id=? AND state='failed'""", (now(), ident))
    return cur.rowcount == 1


def _finish(db, ident: str, token: str, state: str, detail: str | None) -> None:
    if state not in RESULTS:
        raise SafetyError("Invalid send result")
    cur = db.execute("""UPDATE operational_outbox SET state=?, detail=?, updated_at=?
      WHERE id=? AND state='sending' AND claim_token=?""",
                     (state, (detail or "")[:DETAIL_MAX] or None, now(), ident, token))
    if cur.rowcount != 1:
        raise SafetyError("Operation claim changed")
    db.execute("""UPDATE operational_attempts SET finished_at=?, result=?, detail=?
                  WHERE id = (SELECT MAX(id) FROM operational_attempts WHERE operation_id=?)""",
               (now(), state, (detail or "")[:DETAIL_MAX] or None, ident))


def finish(db, ident: str, token: str, state: str, detail: str | None = None) -> None:
    """The bare transition (reference primitive)."""
    with db:
        _finish(db, ident, token, state, detail)


def complete(db, ident: str, token: str, state: str, detail: str | None = None, on_sent=None):
    """Finish the operation and, when it was sent, record it (on_sent(db) → carta_log id)
    in the same transaction. Returns the carta_log id or None."""
    log_id = None
    with db:
        _finish(db, ident, token, state, detail)
        if state == "sent" and on_sent is not None:
            log_id = on_sent(db)
            db.execute("UPDATE operational_outbox SET carta_log_id=? WHERE id=?", (log_id, ident))
    return log_id


def recover_dead_worker(db, worker: str) -> int:
    """Call only after independently establishing the worker is dead.
    Never mark another live process's send uncertain merely on app startup.
    """
    with db:
        cur = db.execute("""UPDATE operational_outbox SET state='delivery_unknown',updated_at=?
          WHERE state='sending' AND worker_id=?""", (now(), worker))
    return cur.rowcount


def process_one(db, ident, worker, current_matches, send_snapshot):
    """Reference loop: claim, re-check, send, finish. Exceptions → delivery_unknown."""
    token = claim(db, ident, worker)
    if token is None:
        return "not_claimed"
    row = db.execute("SELECT payload_json,message_id FROM operational_outbox WHERE id=?",
                     (ident,)).fetchone()
    snapshot = json.loads(row[0])
    try:
        if not current_matches(snapshot):
            state = "needs_review"
        else:
            state = send_snapshot(snapshot, row[1])
            if state not in ("sent", "failed", "delivery_unknown"):
                state = "delivery_unknown"
    except Exception:
        state = "delivery_unknown"
    finish(db, ident, token, state)
    return state
