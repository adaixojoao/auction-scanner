"""What ended auctions closed at, and what a live one will likely close at (outcomes.py)."""
from datetime import datetime, timezone

import outcomes
from conftest import FakeResponse

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def ended(db, add, n, base, bid, district="Viseu", seen="2026-10-01T09:00:00", end="2026-10-01T10:00:00",
          source="eleiloes", title="Moradia T3"):
    add(source, f"e{n}", title=title, tipo="moradia", area_m2=120, price=base, current_bid=bid,
        district=district, date_end=end)
    db.execute("UPDATE listings SET last_seen = ? WHERE id = ?", (seen, f"{source}:e{n}"))


def test_ended_sales_are_recorded_once_with_their_last_bid(db, add):
    ended(db, add, 1, 10000, 14000)
    ended(db, add, 2, 10000, 0)                                              # nobody bid
    ended(db, add, 3, 10000, 9000, seen="2026-09-20T09:00:00")              # last seen days before the end
    add("eleiloes", "live", title="Moradia", price=10000, date_end="2026-11-01T10:00:00")
    add("citius", "c1", title="Moradia", price=10000, date_end="2026-10-01T10:00:00")   # sealed: no bids to see
    assert outcomes.record_results(db, NOW) == 3
    got = dict(db.execute("SELECT listing_id, outcome FROM auction_results").fetchall())
    assert got == {"eleiloes:e1": "sold", "eleiloes:e2": "no bids", "eleiloes:e3": "unknown"}
    assert outcomes.record_results(db, NOW) == 0


def test_a_live_sale_is_judged_on_what_similar_ones_closed_at(db, add):
    for n in range(8):                                   # houses in Viseu closed at 1.5× the base
        ended(db, add, n, 10000, 15000)
    for n in range(8, 16):                               # elsewhere at the base
        ended(db, add, n, 10000, 10000, district="Beja")
    outcomes.record_results(db, NOW)
    table = outcomes.stats(db)
    live = {"source": "eleiloes", "price": 20000, "district": "Viseu", "current_bid": 0}
    got = outcomes.predict(live, table, "home")
    assert got["price"] == 30000 and got["ratio"] == 1.5 and got["n"] == 8
    assert "likely to close around €30,000 (150% of the base in 8 ended home sales in Viseu)" == got["text"]
    # Few sales in the district: the whole site's houses (16, median 1.25).
    assert outcomes.predict({**live, "district": "Faro"}, table, "home")["ratio"] == 1.25
    # Never below the current bid; nothing for a site without public bids.
    assert outcomes.predict({**live, "current_bid": 40000}, table, "home")["price"] == 40000
    assert outcomes.predict({**live, "source": "citius"}, table, "home") is None
    assert outcomes.predict(live, {}, "home") is None


def test_the_score_uses_the_likely_final_price(db, add):
    from scoring import score_detail
    house = {"source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia", "area_m2": 120,
             "price": 20000}
    plain, _ = score_detail(house)
    dearer, reasons = score_detail({**house, "predicted_final": {"price": 80000, "text": "likely to close around €80,000"}})
    assert dearer < plain and "likely to close around €80,000" in reasons
    same, reasons = score_detail({**house, "predicted_final": {"price": 20000, "text": "x"}})
    assert same == plain and "x" not in reasons


def test_the_closing_watch_reads_the_final_bid(db, add):
    add("eleiloes", "c1", title="Moradia", price=10000, date_end="2026-10-10T11:30:00",
        raw_json={"referencia": "NP1"})

    class Session:
        def get(self, url, **kw):
            assert url.endswith("/NP1")
            return FakeResponse(json_data={"item": {"lanceAtual": 12500.0, "dataFim": "2026-10-10T11:35:00"}})
    assert outcomes.watch_closing(db, Session(), NOW) == 1
    row = db.execute("SELECT outcome, final_bid FROM auction_results WHERE listing_id='eleiloes:c1'").fetchone()
    assert tuple(row) == ("sold", 12500.0)
