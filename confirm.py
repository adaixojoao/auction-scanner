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
# Whole words, in the page title or the headings. The description of a house
# for sale often mentions a neighbour that sold; that is not this house.
GONE_WORDS = ("vendido", "vendida", "reservado", "reservada", "vendu", "vendue", "venduto", "venduta")
PAUSE_S = 0.4

# ─── Typed availability states (F12) ─────────────────────────────────────────
# availability  meaning
# ----------    -------
# available     Page OK; sale is ongoing.
# withdrawn     404/410 or explicit sold/deleted language: the sale is over.
# reserved      "reservado/reservada/…" in title: in negotiation, not yet sold.
# ended         Sale date passed; no bid observed; outcome unknown.
# blocked       403 or bot-detection response: can't determine state.
# error         5xx or network failure: transient; retry next scan.
# unknown       Response received but too thin to classify.
AVAILABILITY_STATES = frozenset(
    ["available", "withdrawn", "reserved", "ended", "blocked", "error", "unknown"]
)

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


def gone_reason(item: dict) -> str | None:
    """The stored reason this link is no longer a sale, or None.

    Checks both the new typed availability field and the legacy gone/why fields
    for backwards compatibility.
    """
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
    if avail in AVAILABILITY_STATES:
        return avail
    # Map legacy gone field
    if check.get("gone"):
        return "withdrawn"
    if check.get("gone") is False:
        return "available"
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
        if len(html or "") < 200:
            return "unknown", "thin response"
        return "available", None
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heads = " ".join(h.get_text(" ", strip=True) for h in soup.select("h1, h2")[:3])
    combined = f"{title} {heads}"
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
    return "available", None


def _store(db, item: dict, state: str, why: str | None) -> None:
    raw = _raw(item)
    raw["link_check"] = {
        "availability": state,
        "gone": state in ("withdrawn", "reserved", "ended"),  # legacy compat
        "why": why,
        "at": utcnow_iso(),
    }
    db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
               (json.dumps(raw, ensure_ascii=False), item["id"]))
    db.commit()


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
    marked = 0
    for it in chosen:
        try:
            resp = session.get(it["url"], timeout=20)
            status = getattr(resp, "status_code", 0) or 0
            state, why = classify_response(status, getattr(resp, "text", "") or "")
        except Exception as e:  # noqa: BLE001 — try again next scan; do not call it sold
            LOG.info(f"Link check of {it['id']} failed ({type(e).__name__})")
            continue
        _store(db, it, state, why)
        if state in ("withdrawn", "reserved", "ended"):
            marked += 1
            LOG.info(f"Link check: {it['id']} left the list ({why or state})")
        time.sleep(PAUSE_S)
    if chosen:
        LOG.info(f"Link check: {len(chosen)} links, {marked} no longer for sale")
    return marked
