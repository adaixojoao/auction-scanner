"""confirm.py — open the links of the top of each goal before they are shown.

A house can stay at the top after it has sold. Once a scan, the top of each
goal is opened. A deleted page, or a page whose title says sold or reserved,
takes the listing off the list with that reason. A network failure does not:
the link is tried again next scan.
"""
from __future__ import annotations

import json

from common import LOG, find_terms, utcnow_iso

TOP_N = 20
# Reserved or withdrawn marks are not final: each scan rechecks a few of the
# oldest ones, at most once a week each, so a lot back on sale can return.
RECOVER_N = 5
RECOVER_AFTER_DAYS = 7
# Whole words, in the page title or the headings. The description of a house
# for sale often mentions a neighbour that sold; that is not this house.
GONE_WORDS = ("vendido", "vendida", "reservado", "reservada", "vendu", "vendue", "venduto", "venduta")
PAUSE_S = 0.4

# ─── Typed availability states (F12) ─────────────────────────────────────────
# availability  meaning
# ----------    -------
# available     The page affirmatively shows this lot's sale as open (needs a
#               source-specific, fixture-tested mapping; no generic page does).
# withdrawn     404/410 or explicit sold/deleted language: the sale is over.
# reserved      "reservado/reservada/…" in title: in negotiation, not yet sold.
# ended         Sale date passed; no bid observed; outcome unknown.
# blocked       403 or bot-detection response: can't determine state.
# error         5xx or network failure: transient; retry next scan.
# unknown       Response received but it does not say: too thin, or a page
#               that loads without stating this lot's sale state (reachable is
#               not "still for sale").
AVAILABILITY_STATES = frozenset(
    ["available", "withdrawn", "reserved", "ended", "blocked", "error", "unknown"]
)

# A login wall, a CAPTCHA or a bot check: stop, never try to get past it.
_BLOCK_WORDS = ("captcha", "recaptcha", "hcaptcha", "are you a robot", "verify you are human",
                "attention required", "access denied", "iniciar sessão", "iniciar sesión", "inicie sessão",
                "connexion requise", "log in to continue", "sign in to continue", "cf-chl")
PARSER_VERSION = "confirm/2"
# Words whose presence in a title/heading signals a reservation (not full sale).
_RESERVED_WORDS = ("reservado", "reservada", "réservé", "réservée")
# Words whose presence signals the sale is fully concluded.
_SOLD_WORDS = ("vendido", "vendida", "vendu", "vendue", "venduto", "venduta")

# ─── Sale-state disambiguator (F14) ──────────────────────────────────────────
# sale_state    meaning
# -----------   -------
# available     No bid yet; sale open.
# observed_bid  A bid exists but the deadline has not passed; not a confirmed sale.
# confirmed_bid Deadline passed with a bid; awaiting homologation.
# accepted_award Court has homologated the sale; title not yet transferred.
# completed_purchase Title transferred; fully sold.
# withdrawn     Sale cancelled or listing removed before conclusion.
SALE_STATES = frozenset(
    ["available", "observed_bid", "confirmed_bid", "accepted_award",
     "completed_purchase", "withdrawn"]
)


def _raw(item: dict) -> dict:
    try:
        raw = json.loads(item.get("raw_json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


# The ad itself says a buyer is already through: "SOUS OFFRE D'ACHAT ACCEPTÉE –
# DOSSIER EN COURS CHEZ LE NOTAIRE" (bienici, 2026-10-29). Not bare "sous offre".
UNDER_OFFER_WORDS = (
    "sous offre d'achat acceptée", "offre d'achat acceptée", "sous offre d’achat acceptée",
    "offre d’achat acceptée", "offre acceptée", "sous compromis",
    "compromis signé", "compromis de vente signé", "under offer", "sale agreed",
    "reservada por", "en proceso de venta", "com proposta aceite",
    "proposta accettata", "sotto proposta", "onder bod", "verkocht onder voorbehoud",
)


def gone_reason(item: dict) -> str | None:
    """The stored reason this link is no longer a sale, or None.

    Checks both the new typed availability field and the legacy gone/why fields
    for backwards compatibility, and the ad's own words for a buyer already
    through (UNDER_OFFER_WORDS).
    """
    said = find_terms(f"{item.get('title') or ''} {item.get('description') or ''}", UNDER_OFFER_WORDS)
    if said:
        return f"under offer: the ad says \"{said[0]}\""
    check = _raw(item).get("link_check") or {}
    avail = check.get("availability")
    if avail in ("withdrawn", "reserved", "ended"):
        return check.get("why") or avail
    # Legacy format: gone=True/False
    if check.get("gone"):
        return check.get("why") or "page gone"
    return None


def availability_state(item: dict) -> str:
    """The current typed availability state, or 'unknown' when not yet checked."""
    check = _raw(item).get("link_check") or {}
    avail = check.get("availability")
    if avail == "available" and check.get("basis") != "source_lot":
        # Older checks called any page that loaded "available". Only a
        # source-specific reading of this lot's sale state can say so.
        return "unknown"
    if avail in AVAILABILITY_STATES:
        return avail
    # Map legacy gone field
    if check.get("gone"):
        return "withdrawn"
    return "unknown"


def page_gone(status: int, html: str) -> str | None:
    """Why this response is not a live sale, or None when it still is.

    Kept for callers that only need the old gone/why string.  New callers should
    use classify_response() for the full typed availability state.
    """
    state, why = classify_response(status, html)
    if state in ("withdrawn", "reserved", "ended"):
        return why
    return None


def classify_response(status: int, html: str) -> tuple[str, str | None]:
    """Return (availability_state, why_string) for an HTTP response.

    availability_state is one of the AVAILABILITY_STATES values.
    why_string is a short human-readable phrase or None for 'available'.
    """
    if status in (404, 410):
        return "withdrawn", "page gone"
    if status == 403:
        return "blocked", "access denied (403)"
    if status >= 500:
        return "error", f"server error ({status})"
    if status == 0:
        return "error", "network failure"
    if status not in range(200, 400):
        return "unknown", f"unexpected status {status}"
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001 — a parser miss is not a sold house
        return "unknown", "page could not be read"
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heads = " ".join(h.get_text(" ", strip=True) for h in soup.select("h1, h2")[:3])
    combined = f"{title} {heads}"
    low = (html or "").lower()
    if any(w in combined.lower() for w in _BLOCK_WORDS) or "g-recaptcha" in low or "h-captcha" in low \
            or (soup.select_one("input[type=password]") is not None):
        return "blocked", "a login or bot check stands in front of the page"
    if find_terms(combined, _RESERVED_WORDS, negations=False):
        return "reserved", find_terms(combined, _RESERVED_WORDS, negations=False)[0]
    if find_terms(combined, _SOLD_WORDS, negations=False):
        return "withdrawn", find_terms(combined, _SOLD_WORDS, negations=False)[0]
    body = soup.get_text(" ", strip=True)
    if len(body) < 400:
        if find_terms(body, _RESERVED_WORDS, negations=False):
            return "reserved", find_terms(body, _RESERVED_WORDS, negations=False)[0]
        if find_terms(body, _SOLD_WORDS, negations=False):
            return "withdrawn", find_terms(body, _SOLD_WORDS, negations=False)[0]
        return "unknown", "thin response"
    # The page loads and does not say sold or reserved. Without a source-specific
    # reading of this lot's sale state, that is reachable — not "still for sale".
    return "unknown", "page reachable; this lot's sale state is not confirmed by the page"


def _store(db, item: dict, state: str, why: str | None, *, requested: str | None = None,
           final: str | None = None) -> None:
    check = {
        "availability": state,
        "gone": state in ("withdrawn", "reserved", "ended"),  # legacy compat
        "why": why,
        "at": utcnow_iso(),
        "basis": "generic_page",          # never a matched, source-specific sale state
        "parser": PARSER_VERSION,
        "requested_url": requested,
        "final_url": final,
        "redirected": bool(requested and final and requested != final),
    }
    # Owned-key patch: reload raw_json inside one short write and change only
    # link_check, so a detail fetch saved since this copy was loaded survives.
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT raw_json FROM listings WHERE id = ?", (item["id"],)).fetchone()
        raw = _raw({"raw_json": row[0]}) if row else _raw(item)
        raw["link_check"] = check
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
                   (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if isinstance(item.get("raw_json"), str):
        item["raw_json"] = json.dumps(raw, ensure_ascii=False)


def _due_for_recovery(db, now=None) -> list[dict]:
    """The oldest reserved/withdrawn link checks, bounded, for a recheck."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    before = (now - timedelta(days=RECOVER_AFTER_DAYS)).isoformat()
    at = "CASE WHEN json_valid(raw_json) THEN json_extract(raw_json, '$.link_check.at') END"
    state = "CASE WHEN json_valid(raw_json) THEN json_extract(raw_json, '$.link_check.availability') END"
    rows = db.execute(
        f"SELECT * FROM listings WHERE {state} IN ('reserved', 'withdrawn') AND {at} < ?"
        f" AND url IS NOT NULL AND url != '' ORDER BY {at} LIMIT ?", (before, RECOVER_N)).fetchall()
    return [dict(r) for r in rows]


# A listing gone from its source's list is "stale" (db.STALE_AFTER). Before
# that hides Live and invest's candidates, their pages are opened: a deleted
# page or one that says sold hides them for that reason, and a page that still
# opens keeps them on the tab as unconfirmed (owner's choice, 2026-10-26: Avilés
# was hidden as stale while its page was up).
STALE_CHECK_N = 80


def reachable_since(item: dict, seen) -> bool:
    """The page was opened after the source last listed it, and it did not say
    sold, reserved or deleted: the sale may still be on."""
    from common import parse_dt
    check = _raw(item).get("link_check") or {}
    at = parse_dt(check.get("at"))
    return bool(at and seen and at > seen and check.get("availability") == "unknown")


def check_stale(db, cfg: dict, session, limit: int = STALE_CHECK_N, rows: list[dict] | None = None) -> int:
    """Open the pages of Live and invest's candidates that are about to be hidden
    as stale and were not opened since. Returns how many were opened. `rows`:
    Live and invest already loaded (pipeline.both_rows), not loaded again."""
    import time

    from common import parse_dt
    if rows is None:
        from pipeline import both_rows
        rows = both_rows(db, cfg)
    due = []
    for it in rows:
        if not (it.get("hidden_reason") or "").startswith("stale") or it.get("both_factor") is None:
            continue
        full = db.execute("SELECT * FROM listings WHERE id = ?", (it["id"],)).fetchone()
        if not full or not full["url"]:
            continue
        full = dict(full)
        check = parse_dt((_raw(full).get("link_check") or {}).get("at"))
        if check and check > parse_dt(full.get("last_seen")):
            continue                                   # opened since it was last listed
        due.append((it.get("rank") or 0, full))
    due.sort(key=lambda pair: -pair[0])
    opened = 0
    for _, it in due[:limit]:
        try:
            resp = session.get(it["url"], timeout=20)
            status = getattr(resp, "status_code", 0) or 0
            state, why = classify_response(status, getattr(resp, "text", "") or "")
        except Exception as e:  # noqa: BLE001 — stays stale, tried again next scan
            LOG.info(f"Stale check of {it['id']} failed ({type(e).__name__})")
            continue
        _store(db, it, state, why, requested=it["url"], final=str(getattr(resp, "url", "") or "") or None)
        opened += 1
        time.sleep(PAUSE_S)
    if opened:
        LOG.info(f"Stale check: {opened} pages opened")
    return opened


def check_top(db, cfg: dict, session) -> int:
    """Open the top of each goal. Returns how many listings left the list."""
    import time

    from db import load_listings
    from scoring import MODES
    chosen: list[dict] = []
    seen: set[str] = set()
    for mode in MODES:
        rows = sorted(load_listings(db, filters=cfg.get("filters"), mode=mode),
                      key=lambda it: -it.get("rank", it["score"]))
        for it in rows[:TOP_N]:
            if it["id"] in seen or gone_reason(it) or not it.get("url"):
                continue
            seen.add(it["id"])
            chosen.append(it)
    chosen += [it for it in _due_for_recovery(db) if it["id"] not in seen]
    marked = 0
    for it in chosen:
        try:
            resp = session.get(it["url"], timeout=20)
            status = getattr(resp, "status_code", 0) or 0
            state, why = classify_response(status, getattr(resp, "text", "") or "")
        except Exception as e:  # noqa: BLE001 — try again next scan; do not call it sold
            LOG.info(f"Link check of {it['id']} failed ({type(e).__name__})")
            continue
        _store(db, it, state, why, requested=it["url"], final=str(getattr(resp, "url", "") or "") or None)
        if state in ("withdrawn", "reserved", "ended"):
            marked += 1
            LOG.info(f"Link check: {it['id']} left the list ({why or state})")
        time.sleep(PAUSE_S)
    if chosen:
        LOG.info(f"Link check: {len(chosen)} links, {marked} no longer for sale")
    return marked
