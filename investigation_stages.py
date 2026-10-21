"""investigation_stages.py — where each property is in your research, and why (db v32).

Append-only and separate from shortlist/dismiss status (db.set_listing_status):
recording a stage never shortlists, dismisses, sends a letter or verifies
anything, and no stage means a property is ready to buy. A correction or a
step back is a new row; earlier rows, their reasons and any spending stay.

Each row keeps the sale facts it was recorded against (deadline and the price
figures), so a stage recorded for an earlier sale event or before the notice
changed says so instead of reading as current. Reasons are your own words:
kept in this database, escaped wherever shown, never logged.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone

STAGES = {
    "discovered": "Discovered",
    "screening": "Screening",
    "documents_requested": "Documents requested",
    "evidence_reviewed": "Evidence reviewed",
    "visit_warranted": "Visit warranted",
    "professional_review": "Professional review",
    "decision_recorded": "Decision recorded",
}
# Dispositions: recorded like a stage, never a deletion of evidence or history.
DISPOSITIONS = {"paused": "Paused", "rejected": "Rejected"}
LABELS = {**STAGES, **DISPOSITIONS}
REASON_KINDS = {
    "confirmed_issue": "Confirmed issue",
    "insufficient_evidence": "Insufficient evidence",
    "preference_change": "Changed preference",
    "progress": "Progress",
}
SPEND_CATEGORIES = {
    "registry": "Registry or tax records",
    "survey": "Survey or technical review",
    "legal": "Legal advice",
    "travel": "Travel or visit",
    "other": "Other",
}
REASON_MAX = 1000
SPEND_MAX = 10_000_000
_SALE_FACTS = ("date_end", "price", "min_price", "current_bid")


class StageError(ValueError):
    """A stage body the API refuses (400)."""


class StageConflict(ValueError):
    """The stage changed since the page read it (409): nothing was recorded."""


def _sale_facts(item: dict) -> dict:
    return {k: item.get(k) for k in _SALE_FACTS}


def _spend(body) -> dict:
    """{amount, currency, date, category}, all None when no spending is given. Unknown is not zero."""
    none = {"amount": None, "currency": None, "date": None, "category": None}
    if body in (None, {}, ""):
        return none
    if not isinstance(body, dict):
        raise StageError("spend must be an object")
    raw = body.get("amount")
    if raw in (None, ""):
        if any(body.get(k) not in (None, "") for k in ("currency", "date", "category")):
            raise StageError("a spend needs its amount; leave it all empty when nothing was spent or it is unknown")
        return none
    if isinstance(raw, bool):
        raise StageError("spend amount must be a number")
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        raise StageError("spend amount must be a number") from None
    if not math.isfinite(amount) or amount < 0 or amount > SPEND_MAX:
        raise StageError(f"spend amount must be between 0 and {SPEND_MAX:,}")
    currency = str(body.get("currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise StageError("spend currency must be a three-letter code such as EUR")
    when = str(body.get("date") or "").strip()
    try:
        spent_on = date.fromisoformat(when) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else None
    except ValueError:
        spent_on = None
    if spent_on is None:
        raise StageError("spend date must be a date (YYYY-MM-DD)")
    if spent_on > datetime.now(timezone.utc).date():
        raise StageError("spend date cannot be in the future")
    category = str(body.get("category") or "")
    if category not in SPEND_CATEGORIES:
        raise StageError("unknown spend category")
    return {"amount": round(amount, 2), "currency": currency, "date": when, "category": category}


def history(db, listing_id: str) -> list[dict]:
    """Every stage row of a listing, oldest first."""
    rows = db.execute("SELECT * FROM investigation_stages WHERE listing_id=? ORDER BY id", (listing_id,)).fetchall()
    return [_row(r) for r in rows]


def current_for(db, listing_ids) -> dict[str, dict]:
    """The latest stage row of each listing, by id: one query for the lot."""
    ids = sorted({i for i in listing_ids if i})
    if not ids:
        return {}
    out = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        rows = db.execute(
            f"SELECT s.* FROM investigation_stages s JOIN (SELECT listing_id, MAX(id) AS id FROM investigation_stages"
            f" WHERE listing_id IN ({','.join('?' * len(part))}) GROUP BY listing_id) m ON s.id = m.id", part)
        out.update({r["listing_id"]: _row(r) for r in rows})
    return out


def _row(r) -> dict:
    d = dict(r)
    try:
        refs = json.loads(d.pop("evidence_refs_json") or "{}")
    except ValueError:
        refs = {}
    refs = refs if isinstance(refs, dict) else {}
    return {"id": d["id"], "listing_id": d["listing_id"], "stage": d["stage"], "label": LABELS.get(d["stage"], d["stage"]),
            "previous_stage": d["previous_stage"], "reason": d["reason"], "reason_kind": d["reason_kind"],
            "reason_kind_label": REASON_KINDS.get(d["reason_kind"], d["reason_kind"]),
            "checklist_keys": [k for k in refs.get("checklist") or [] if isinstance(k, str)],
            "sale_facts": refs.get("sale") if isinstance(refs.get("sale"), dict) else {},
            "spend": {"amount": d["spend_amount"], "currency": d["spend_currency"], "date": d["spend_date"],
                      "category": d["spend_category"],
                      "category_label": SPEND_CATEGORIES.get(d["spend_category"]) if d["spend_category"] else None},
            "created_at": d["created_at"]}


def record(db, item: dict, body: dict, checklist_keys) -> dict:
    """Append one stage row from a request body, in one transaction; the row as stored.

    `expected_previous` is the stage the page showed ("" for none): if another
    tab recorded one since, StageConflict and nothing is written. Evidence
    references are this listing's own checklist item keys only.
    """
    if not isinstance(body, dict):
        raise StageError("the body must be an object")
    stage = body.get("stage")
    if stage not in LABELS:
        raise StageError("unknown stage")
    kind = body.get("reason_kind")
    if kind not in REASON_KINDS:
        raise StageError("unknown reason kind")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise StageError("a reason is required, in your own words")
    reason = " ".join(reason.split())
    if len(reason) > REASON_MAX:
        raise StageError(f"the reason is longer than {REASON_MAX} characters")
    keys = body.get("checklist_keys", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise StageError("checklist_keys must be a list of checklist item keys")
    allowed = set(checklist_keys)
    unknown = [k for k in keys if k not in allowed]
    if unknown:
        raise StageError("unknown checklist item for this listing")
    spend = _spend(body.get("spend"))
    expected = body.get("expected_previous")
    if expected is not None and not isinstance(expected, str):
        raise StageError("expected_previous must be a stage or empty")
    refs = json.dumps({"checklist": sorted(set(keys)), "sale": _sale_facts(item)}, default=str)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not db.in_transaction:
        db.execute("BEGIN")
    db.execute("SAVEPOINT investigation_stage_write")
    try:
        # The write lock first (as checklist.set_status): the previous stage read below is the one this row follows.
        if not db.execute("UPDATE listings SET id = id WHERE id = ?", (item["id"],)).rowcount:
            raise StageError("cannot record a stage for an unknown listing")
        last = db.execute("SELECT stage FROM investigation_stages WHERE listing_id=? ORDER BY id DESC LIMIT 1",
                          (item["id"],)).fetchone()
        previous = last["stage"] if last else None
        if expected is not None and (expected or None) != previous:
            raise StageConflict("The stage changed since this page read it: reload and record again.")
        cur = db.execute(
            "INSERT INTO investigation_stages (listing_id, stage, previous_stage, reason, reason_kind,"
            " evidence_refs_json, spend_amount, spend_currency, spend_date, spend_category, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (item["id"], stage, previous, reason, kind, refs, spend["amount"], spend["currency"], spend["date"],
             spend["category"], now))
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT investigation_stage_write")
        db.execute("RELEASE SAVEPOINT investigation_stage_write")
        db.rollback()
        raise
    db.execute("RELEASE SAVEPOINT investigation_stage_write")
    db.commit()
    return _row(db.execute("SELECT * FROM investigation_stages WHERE id=?", (cur.lastrowid,)).fetchone())


def facts_changed(row: dict, item: dict) -> list[str]:
    """Which sale facts differ from those a stage was recorded against (an earlier sale event or a changed notice)."""
    then = row.get("sale_facts") or {}
    names = {"date_end": "deadline", "price": "price", "min_price": "minimum", "current_bid": "current bid"}
    return [names[k] for k in _SALE_FACTS if k in then and str(then.get(k)) != str(item.get(k))]


def summary(rows: list[dict], item: dict) -> dict:
    """The stage panel's view: the current stage, the history and the known spending by currency."""
    current = rows[-1] if rows else None
    totals: dict[str, float] = {}
    for r in rows:
        s = r["spend"]
        if s["amount"] is not None:
            totals[s["currency"]] = round(totals.get(s["currency"], 0) + s["amount"], 2)
    return {"available": True, "current": current, "history": [{**r, "facts_changed": facts_changed(r, item)}
                                                                  for r in rows],
            "spend_totals": totals, "spend_entries": sum(1 for r in rows if r["spend"]["amount"] is not None),
            "stages": [[k, v] for k, v in STAGES.items()], "dispositions": [[k, v] for k, v in DISPOSITIONS.items()],
            "reason_kinds": [[k, v] for k, v in REASON_KINDS.items()],
            "spend_categories": [[k, v] for k, v in SPEND_CATEGORIES.items()],
            "note": "Stages are your research record. None of them, the last included, means the property is "
                    "ready to buy, and recording one changes nothing else."}
