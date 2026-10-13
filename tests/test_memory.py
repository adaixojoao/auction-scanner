"""The start must stay well under the memory that froze a 6 GB PC.

The start scores the two open goals. When it scored every goal (2026-10-13)
that peaked at 139 MB of Python allocations, and keeping every goal's scores
and all five lists peaked at 378 MB — the shape that reached about 1 GB on
the full database. The limit stays under that.
"""
import gc
import tracemalloc

# Above the measured start, below the leak.
STARTUP_MEMORY_LIMIT = 256 * 1024 * 1024
LISTINGS = 20_000


def _insert_listings(db, n: int) -> None:
    """Shops, already ended, under the price cap: scored and then slimmed, like
    the hidden rows that are almost the whole database. A shop is not a home or
    a plot, so each goal returns at once, and the rows are still read, scored
    and held the way a start does."""
    desc = ("Loja com montra para a rua, arrecadacao e instalacao eletrica. " * 12)
    now = "2026-10-13T12:00:00"
    rows = [
        (f"mem:{i}", "mem", "PT", str(i), "Loja no centro", desc, "loja", 80, 40000,
         None, None, "Guarda", "Guarda", "Seia", f"https://example.test/mem/{i}", None,
         "2020-01-01", None, now, now, 1)
        for i in range(n)
    ]
    db.executemany(
        """INSERT INTO listings (id, source, country, external_id, title, description, tipo,
           area_m2, price, current_bid, min_price, district, concelho, freguesia, url,
           image_url, date_end, raw_json, first_seen, last_seen, is_new)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    db.commit()


def _drop_kept_lists() -> None:
    """The lists and scores are what the limit is about; leave none of them for the next test."""
    import dashboard
    import db as dbmod
    dashboard._LISTS.clear()
    dashboard._LISTS_USED.clear()
    dashboard._LISTS_BUILDING.clear()
    dashboard._LISTS_ACTIVE.clear()
    dbmod.forget_scores()
    for conn in dbmod._SCORE_DBS.values():
        conn.close()
    dbmod._SCORE_DBS.clear()


def test_startup_scoring_of_20000_listings_stays_under_the_memory_limit(db):
    import app
    import dashboard
    _insert_listings(db, LISTINGS)
    gc.collect()
    held: dict = {}
    peak = 0
    tracemalloc.start()
    try:
        app.warm_up()
        peak = tracemalloc.get_traced_memory()[1]
        held = {mode: len(entry[1]) for mode, entry in dashboard._LISTS.items()}
    finally:
        tracemalloc.stop()
        _drop_kept_lists()
    # The first two goals keep their rows. An empty result would mean the start
    # was skipped, and a skipped start uses almost no memory.
    assert held == {"both": LISTINGS, "home": LISTINGS}
    assert peak < STARTUP_MEMORY_LIMIT, f"start used {peak / 1024 ** 2:.0f} MB"
