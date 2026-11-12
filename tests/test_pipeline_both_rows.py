"""Enrichment must not spend the scan on sales that can no longer be bought."""
import pipeline


def test_both_rows_skips_expired_and_dismissed(db, add):
    from db import set_listing_status
    add(external_id="live", title="Moradia T3", price=25000, area_m2=90,
        date_end="2030-06-01T12:00:00")
    add(external_id="gone", title="Moradia T3", price=20000, area_m2=90,
        date_end="2020-01-01T12:00:00")
    dismissed = add(external_id="no", title="Moradia T3", price=18000, area_m2=90,
                    date_end="2030-06-01T12:00:00")
    set_listing_status(db, dismissed["id"], "dismissed", "occupied")
    # both_rows does not take now=; pin "today" via load_listings through monkeypatch if needed.
    # date_end 2020 is always past.
    ids = {it["id"] for it in pipeline.both_rows(db, {})}
    assert "eleiloes:live" in ids
    assert "eleiloes:gone" not in ids
    assert dismissed["id"] not in ids
