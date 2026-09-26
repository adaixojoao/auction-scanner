"""outcomes.py — what auctions really close at, and what a live one will likely close at.

A sale is ranked on what you would pay. The base value says little about that:
on e-leilões bidding opens at 50% of it and a house in a good spot can end at
twice the base, a plot in the interior below it. So:

1. Recording. When a sale on a site with public bids (RESULT_SOURCES) ends, its
   last bid is kept in `auction_results`: from the scans (every ~2h) and from
   `watch_closing()`, which the scheduler runs every half hour to read the bids
   of the e-leilões sales closing now. A sale last seen long before its end is
   kept as "unknown" (not counted), one that ended without a bid as "no bids".
2. Learning. The final bid ÷ base of the ended sales, as a median, first for the
   same site, kind (home, plot…) and district, else the site and kind, else the
   site: the first group with at least MIN_GROUP sales.
3. Using. `predict()` gives the likely final price of a live sale (never below
   its current bid); the score uses it instead of the base when it is higher.

Nothing changes until enough sales have ended: the table fills from the day
this runs.
"""
from __future__ import annotations

import json
from datetime import timedelta
from statistics import median

from common import LOG, normalize, parse_dt, price_to_pay, utcnow, utcnow_iso

RESULT_SOURCES = ("eleiloes", "financas")
MIN_GROUP = 8
FRESH_HOURS = 6          # a bid seen longer than this before the end is not the final one
WATCH_BEFORE_MIN = 60    # watch_closing(): sales ending within this…
WATCH_AFTER_H = 3        # …or that ended this long ago and have no result yet


def ensure_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS auction_results (
            listing_id  TEXT PRIMARY KEY,
            source      TEXT NOT NULL,
            country     TEXT,
            district    TEXT,
            concelho    TEXT,
            kind        TEXT,
            base        REAL,
            final_bid   REAL,
            outcome     TEXT NOT NULL,     -- sold | no bids | unknown
            ended_at    TEXT,
            recorded_at TEXT NOT NULL
        )""")


def record_results(db, now=None) -> int:
    """Keep the result of every public-bid sale that has ended since the last run."""
    from scoring import property_kind
    ensure_table(db)
    now = now or utcnow()
    marks = ",".join("?" * len(RESULT_SOURCES))
    rows = db.execute(f"""
        SELECT * FROM listings WHERE source IN ({marks}) AND date_end IS NOT NULL AND date_end < ?
          AND id NOT IN (SELECT listing_id FROM auction_results)""",
                      (*RESULT_SOURCES, now.strftime("%Y-%m-%dT%H:%M:%S"))).fetchall()
    done = 0
    for r in rows:
        item = dict(r)
        end, seen = parse_dt(item["date_end"]), parse_dt(item.get("last_seen"))
        if not end or end > now:
            continue
        bid = item.get("current_bid") or 0
        if not seen or end - seen > timedelta(hours=FRESH_HOURS):
            outcome = "unknown"
        else:
            outcome = "sold" if bid > 0 else "no bids"
        db.execute("""INSERT OR REPLACE INTO auction_results (listing_id, source, country, district, concelho,
                          kind, base, final_bid, outcome, ended_at, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (item["id"], item["source"], item.get("country"), item.get("district"), item.get("concelho"),
                    property_kind(item), item.get("price"), bid or None, outcome, item["date_end"], utcnow_iso()))
        done += 1
    db.commit()
    if done:
        LOG.info(f"Auction results: {done} ended sales recorded")
    return done


def watch_closing(db, session=None, now=None) -> int:
    """Read the bids of the e-leilões sales closing now, so the last bid kept is
    the final one (bids in the last minutes extend the end)."""
    from sources.pt import ELEILOES_DETAIL_API, _eleiloes_session
    ensure_table(db)
    now = now or utcnow()
    rows = db.execute("""
        SELECT id, raw_json FROM listings WHERE source = 'eleiloes' AND date_end BETWEEN ? AND ?
          AND id NOT IN (SELECT listing_id FROM auction_results)""",
                      ((now - timedelta(hours=WATCH_AFTER_H)).strftime("%Y-%m-%dT%H:%M:%S"),
                       (now + timedelta(minutes=WATCH_BEFORE_MIN)).strftime("%Y-%m-%dT%H:%M:%S"))).fetchall()
    session = session or _eleiloes_session()
    read = 0
    for listing_id, raw_text in rows:
        try:
            raw = json.loads(raw_text or "{}")
            ref = raw.get("referencia") or listing_id.split(":", 1)[1]
            item = (session.get(ELEILOES_DETAIL_API.format(id=ref), timeout=15).json() or {}).get("item")
        except Exception as e:  # noqa: BLE001: the last bid from the scans is kept instead
            LOG.debug(f"Closing watch: {listing_id} failed ({e})")
            continue
        if not item:
            continue                          # gone at the end: the scans' last bid stands
        db.execute("UPDATE listings SET current_bid = ?, date_end = COALESCE(?, date_end), last_seen = ? WHERE id = ?",
                   (item.get("lanceAtual") or None, item.get("dataFim"), now.strftime("%Y-%m-%dT%H:%M:%S"), listing_id))
        read += 1
    db.commit()
    record_results(db, now)
    return read


def _groups(item: dict, kind: str | None) -> list[tuple]:
    src = item.get("source") or ""
    district = normalize(item.get("district") or "")
    return [(src, kind, district), (src, kind), (src,)]


def stats(db) -> dict[tuple, list[float]]:
    """{group: [final ÷ base, …]} of the sales that ended with a bid."""
    ensure_table(db)
    out: dict[tuple, list[float]] = {}
    for r in db.execute("SELECT source, kind, district, base, final_bid FROM auction_results "
                        "WHERE outcome = 'sold' AND base > 0 AND final_bid > 0"):
        ratio = r[4] / r[3]
        if not 0.05 < ratio < 20:            # a typo in either figure
            continue
        for key in _groups({"source": r[0], "district": r[2]}, r[1]):
            out.setdefault(key, []).append(ratio)
    return out


_GROUP_WORDS = {3: "{kind} sales in {district}", 2: "{kind} sales", 1: "sales on this site"}


def predict(item: dict, table: dict[tuple, list[float]], kind: str | None = None) -> dict | None:
    """{"price", "ratio", "n", "text"}: the likely final price of a live sale."""
    base = item.get("price") or 0
    if not base or not table or (item.get("source") or "") not in RESULT_SOURCES:
        return None
    for key in _groups(item, kind):
        ratios = table.get(key) or []
        if len(ratios) >= MIN_GROUP:
            ratio = median(ratios)
            price = max(base * ratio, price_to_pay(item))
            group = _GROUP_WORDS[len(key)].format(kind=kind or "similar", district=item.get("district") or "")
            return {"price": round(price), "ratio": round(ratio, 2), "n": len(ratios),
                    "text": f"likely to close around €{price:,.0f} "
                            f"({ratio:.0%} of the base in {len(ratios)} ended {group})"}
    return None
