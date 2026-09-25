"""links.py — the same court sale on Citius and on e-leilões.

Citius lists every Portuguese court sale but has no page per sale, no date for
most, no photos and no bids. When the sale is an electronic auction it also
runs on e-leilões, which has all of that. The two are joined by the case number
(e-leilões gives it once its details are read) or, failing that, by the
municipality and the exact base value when only one e-leilões sale has them.

The Citius row then takes e-leilões' link, end date, photo, current bid and
minimum accepted, and keeps its own full description and registry details. The
e-leilões row is marked as its duplicate (db.mark_duplicates), so the sale is
listed once.
"""
from __future__ import annotations

import json
from collections import defaultdict

from common import LOG, normalize


def _processo(raw: dict) -> str:
    return str(raw.get("processo") or "").split(",")[0].split(" - ")[0].strip().upper()


def _key(concelho, price) -> tuple | None:
    if not concelho or not price:
        return None
    return normalize(str(concelho)).strip(), round(float(price))


def link_court_sales(db) -> int:
    """Join Citius sales to their e-leilões auction. Returns how many are linked."""
    by_proc: dict[str, list] = defaultdict(list)
    by_key: dict[tuple, list] = defaultdict(list)
    for r in db.execute("SELECT id, url, date_end, image_url, current_bid, min_price, price, concelho, raw_json "
                        "FROM listings WHERE source = 'eleiloes'"):
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except ValueError:
            raw = {}
        proc = _processo(raw)
        if proc:
            by_proc[proc].append(r)
        key = _key(r["concelho"], r["price"])
        if key:
            by_key[key].append(r)

    linked = 0
    for r in db.execute("SELECT id, price, concelho, raw_json FROM listings WHERE source = 'citius'").fetchall():
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except ValueError:
            continue
        found = by_proc.get(_processo(raw)) or []
        electronic = "eletr" in normalize(str(raw.get("modalidade") or ""))
        if len(found) != 1 and electronic:          # only e-auctions run on e-leilões
            same = by_key.get(_key(r["concelho"], r["price"])) or []
            found = same if len(same) == 1 else []
        if len(found) != 1:
            continue
        el = found[0]
        if raw.get("eleiloes_id") != el["id"]:
            raw["eleiloes_id"] = el["id"]
        db.execute("""UPDATE listings SET url = ?, date_end = COALESCE(?, date_end),
                      image_url = COALESCE(image_url, ?), current_bid = ?, min_price = COALESCE(?, min_price),
                      raw_json = ? WHERE id = ?""",
                   (el["url"], el["date_end"], el["image_url"], el["current_bid"], el["min_price"],
                    json.dumps(raw, ensure_ascii=False), r["id"]))
        linked += 1
    db.commit()
    if linked:
        LOG.info(f"Citius: {linked} sales joined to their e-leilões auction")
    return linked


def linked_pairs(db) -> dict[str, str]:
    """{e-leilões id: Citius id} for the joined sales."""
    out = {}
    for lid, raw in db.execute("SELECT id, raw_json FROM listings WHERE source = 'citius' "
                               "AND raw_json LIKE '%\"eleiloes_id\"%'"):
        try:
            el = json.loads(raw).get("eleiloes_id")
        except ValueError:
            continue
        if el:
            out[el] = lid
    return out
