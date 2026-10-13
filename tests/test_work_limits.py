"""The work spent on a listing (2026-10-13): over common.check_max_price it is
kept only so the scanner knows it; from common.light_checks_from the heavy
checks wait for the light ones."""
import json

import config


def _ids(items):
    return {it["id"] for it in items}


def test_over_the_limit_a_listing_is_never_scored_or_shown(db, add):
    from db import load_best, load_listings, set_listing_status
    add("eleiloes", "cheap", title="Moradia T3", tipo="moradia", price=40000, area_m2=120)
    add("eleiloes", "dear", title="Moradia T4", tipo="moradia", price=150000, area_m2=200)
    add("eleiloes", "bid", title="Moradia T2", tipo="moradia", price=60000, current_bid=120000, area_m2=90)
    add("eleiloes", "starred", title="Moradia T5", tipo="moradia", price=180000, area_m2=250)
    add("eleiloes", "offered", title="Moradia T1", tipo="moradia", price=130000, area_m2=70)
    add("eleiloes", "noprice", title="Moradia T3", tipo="moradia", area_m2=110)
    set_listing_status(db, "eleiloes:starred", "shortlisted")
    db.execute("INSERT INTO carta_log (listing_id, sent_date, bid_amount, outcome, created_at, is_offer) "
               "VALUES ('eleiloes:offered', '2026-10-01', 120000, 'pending', '2026-10-01', 1)")
    db.commit()
    for mode in ("home", "invest", "land", "forest", "both"):
        seen = _ids(load_listings(db, include_hidden=True, apply_min_score=False, mode=mode))
        assert seen == {"eleiloes:cheap", "eleiloes:starred", "eleiloes:offered", "eleiloes:noprice"}, mode
    assert "eleiloes:dear" not in _ids(load_best(db, include_hidden=True))
    # Still in the database, so the next scan knows it.
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 6


def test_the_limit_can_be_turned_off(db, add):
    from db import load_listings
    add("eleiloes", "dear", title="Moradia T4", tipo="moradia", price=150000, area_m2=200)
    config.save_config({"check_max_price": 0})
    assert _ids(load_listings(db, include_hidden=True)) == {"eleiloes:dear"}


def test_the_heavy_checks_wait_for_the_light_ones():
    from db import heavy_checks_spared
    home = dict(source="eleiloes", country="PT", title="Moradia T3", tipo="moradia", area_m2=120,
                category="imoveis", kind="home")
    assert heavy_checks_spared({**home, "price": 60000}, 40000, 50000)          # over the cash before repairs
    assert not heavy_checks_spared({**home, "price": 45000}, 40000, 50000)      # under €50,000: every check
    assert not heavy_checks_spared({**home, "price": 60000}, 90000, 50000)      # can fit the cash
    assert not heavy_checks_spared({**home, "price": 60000}, 0, 50000)          # no cash set: nothing ruled out
    assert not heavy_checks_spared({**home, "price": 60000, "status": "shortlisted"}, 40000, 50000)
    assert not heavy_checks_spared({**home, "price": 60000}, 40000, 0)          # the rule turned off


def test_live_and_invest_reads_no_climate_for_a_house_it_can_never_show(db, add, monkeypatch):
    """A seaside house over €50,000 whose price and taxes are over the cash is
    not read for Live and invest; one under €50,000 is."""
    import climate
    import geo
    config.save_config({"cash_on_hand": 40000})
    pin = json.dumps({"lat": 40.60, "lon": -7.60})
    add("citius", "sea-cheap", title="Moradia pela praia", tipo="moradia", area_m2=90, price=30000, raw_json=pin)
    add("citius", "sea-dear", title="Moradia pela praia", tipo="moradia", area_m2=90, price=70000, raw_json=pin)
    monkeypatch.setattr(geo, "nearest_beach", lambda item, path=None, towns=None: {"km": 0.4, "approx": False})
    order = climate._seaside_ahead(db, [], {})
    assert _ids(order) == {"citius:sea-cheap"}
    shown = {"id": "citius:sea-dear", "title": "Moradia pela praia", "tipo": "moradia", "area_m2": 90,
             "price": 70000, "country": "PT", "raw_json": pin}
    assert "citius:sea-dear" in _ids(climate._seaside_ahead(db, [shown], {}))     # shown elsewhere: read


def test_both_limits_are_on_the_settings_page(db):
    import dashboard
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    client = dashboard.app.test_client()
    for key in ("check_max_price", "light_checks_from"):
        assert client.post("/api/settings", json={key: -1}).status_code == 400
        assert client.post("/api/settings", json={key: "a lot"}).status_code == 400
    assert client.post("/api/settings", json={"check_max_price": 80000, "light_checks_from": 0}).status_code == 200
    saved = config.load_config()
    assert (saved["check_max_price"], saved["light_checks_from"]) == (80000, 0)
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="check_max_price"' in page and 'id="light_checks_from"' in page
