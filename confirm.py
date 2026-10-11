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


def _raw(item: dict) -> dict:
    try:
        raw = json.loads(item.get("raw_json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


def gone_reason(item: dict) -> str | None:
    """The stored reason this link is no longer a sale, or None."""
    check = _raw(item).get("link_check") or {}
    if check.get("gone"):
        return check.get("why") or "page gone"
    return None


def page_gone(status: int, html: str) -> str | None:
    """Why this response is not a live sale, or None when it still is."""
    if status in (404, 410):
        return "page gone"
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001 — a parser miss is not a sold house
        return None
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heads = " ".join(h.get_text(" ", strip=True) for h in soup.select("h1, h2")[:3])
    hits = find_terms(f"{title} {heads}", GONE_WORDS, negations=False)
    if hits:
        return hits[0]
    body = soup.get_text(" ", strip=True)
    if len(body) < 400:
        hits = find_terms(body, GONE_WORDS, negations=False)
        if hits:
            return hits[0]
    return None


def _store(db, item: dict, gone: str | None) -> None:
    raw = _raw(item)
    raw["link_check"] = {"gone": bool(gone), "why": gone, "at": utcnow_iso()}
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
            why = page_gone(status, getattr(resp, "text", "") or "")
        except Exception as e:  # noqa: BLE001 — try again next scan; do not call it sold
            LOG.info(f"Link check of {it['id']} failed ({type(e).__name__})")
            continue
        _store(db, it, why)
        if why:
            marked += 1
            LOG.info(f"Link check: {it['id']} left the list ({why})")
        time.sleep(PAUSE_S)
    if chosen:
        LOG.info(f"Link check: {len(chosen)} links, {marked} no longer for sale")
    return marked
