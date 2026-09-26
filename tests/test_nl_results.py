"""What Dutch auctions nearby sold for (sources/nl.py, listing_info.past_results)."""
import json
from datetime import datetime, timezone

import listing_info
from sources.nl import NL_RESULTS_KEY, parse_nl_results

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def lot(name, status, afslag, when="/Date(1790163000000)/", lat=51.59, lng=4.46):
    return {"kavelNaam": name, "status": status, "afslag": afslag, "zittingdatum": when,
            "woningtype": "Woonhuis", "lat": lat, "lng": lng, "url": "/kavel/1/x"}


ANSWER = {"results": [{"objectenPerRegio": [{"objects": [
    lot("Kerkstraat 1, OUD GASTEL", "Gegund", "€ 220.000"),
    lot("Dorpsstraat 2, ROOSENDAAL", "Gesloten", "€ 354.000", lat=51.53, lng=4.46),
    lot("Laan 3, OUD GASTEL", "Niet Gegund", "€ 90.000"),                   # the seller said no
    lot("Weg 4, OUD GASTEL", "Vervallen", None),                            # never held
    lot("Plein 5, OUD GASTEL", "Veiling", "€ 300.000", when="/Date(1800000000000)/"),   # not yet
    lot("Straat 6, MAASTRICHT", "Gegund", "€ 150.000", lat=50.85, lng=5.69),
]}]}]}


def test_only_real_final_bids_are_kept():
    rows = parse_nl_results(ANSWER, NOW)
    assert [(r["town"], r["price"]) for r in rows] == [("Oud Gastel", 220000), ("Roosendaal", 354000),
                                                         ("Maastricht", 150000)]
    assert rows[0]["url"] == "https://www.openbareverkoop.nl/kavel/1/x" and rows[0]["date"] == "2026-09-23"


def test_a_dutch_listing_shows_what_nearby_auctions_sold_for(db):
    from db import set_kv
    set_kv(db, NL_RESULTS_KEY, json.dumps(parse_nl_results(ANSWER, NOW)))
    house = {"country": "NL", "raw_json": json.dumps({"lat": 51.5867, "lon": 4.4599})}
    got = listing_info.past_results(db, house)
    assert got["km"] == 25 and got["count"] == 2 and got["median"] == 287000
    assert (got["low"], got["high"]) == (220000, 354000) and got["lots"][0]["km"] < got["lots"][1]["km"]
    assert listing_info.past_results(db, {**house, "country": "PT"}) is None
    assert listing_info.past_results(db, {"country": "NL", "raw_json": "{}"}) is None
    far = {"country": "NL", "raw_json": json.dumps({"lat": 53.2, "lon": 6.56})}           # Groningen
    assert listing_info.past_results(db, far) is None
