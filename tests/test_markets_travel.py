"""Mortgage rates and house prices by country, the trip from Amsterdam and the
rules on holiday lets, 2026-11-01."""
import markets
import short_lets
import surroundings


def test_mortgage_rates_and_the_payment(tmp_path):
    path = tmp_path / "rates.csv"
    rows = ["country,month,rate"] + [f"PT,2025-{m:02d},2.88" for m in range(8, 13)] + \
        [f"PT,2026-{m:02d},{2.9 + m / 100:.2f}" for m in range(1, 9)] + ["EA,2026-08,3.6", "EURIBOR,2026-09,3.25"]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    markets._rates.cache_clear()
    assert round(markets.monthly_payment(3.0)) == 422
    line = markets.mortgage_note({"country": "PT"}, str(path))
    assert line.startswith("new mortgages in Portugal cost 2.98% a year on average (2026-08, up 0.10 points in a "
                           "year; euro area 3.60%): €421 a month per €100,000 over 30 years; 12-month Euribor 3.25%")
    assert markets.mortgage_note({"country": "PL"}, str(path)) is None
    markets._rates.cache_clear()


def test_house_prices_against_the_euro_area(tmp_path):
    path = tmp_path / "hpi.csv"
    path.write_text("country,quarter,year_pct,five_year_pct\nEA,2026-Q2,4.0,19.2\nPT,2026-Q2,16.5,81.1\n",
                    encoding="utf-8")
    markets._prices.cache_clear()
    assert markets.house_price_note({"country": "PT"}, str(path)) == (
        "house prices in Portugal: +16.5% over the year to 2026-Q2, +81.1% in five years (euro area +4.0%, +19.2%) "
        "(Eurostat house price index)")
    assert markets.house_price_note({"country": "BG"}, str(path)) is None
    markets._prices.cache_clear()


def test_the_trip_from_amsterdam_by_plane_and_by_land():
    legs = [{"mode": "WALK", "duration": 120}, {"mode": "COACH", "duration": 38100},
            {"mode": "HIGHSPEED_RAIL", "duration": 12000}, {"mode": "CAR", "duration": 2100}]
    ground = surroundings.travel_found([{"duration": 79560, "transfers": 3, "legs": legs}], True)
    assert ground == {"hours": 22.1, "changes": 3, "by": ["coach", "high-speed train"], "taxi_min": 35, "taxi": True}
    porto = {"name": "Francisco de Sá Carneiro Airport", "iata": "OPO", "icao": "LPPR", "lat": 41.2481, "lon": -8.6814}
    flight = surroundings.flight_found(porto, {"hours": 0.8, "taxi": True}, 40.875, -8.674)
    assert flight["flight_h"] == 2.6 and flight["hours"] == 5.9
    line = surroundings._travel_note({**ground, "flight": flight})
    assert line.startswith("from Amsterdam: about 5.9 h by plane via OPO (2 h to the gate, 2.6 h flight, then 0.8 h "
                           "by taxi); 22.1 h by train or coach (3 changes: coach, high-speed train; the last 35 min "
                           "by taxi) (")
    assert "too far" not in line                       # the plane makes it
    far = surroundings._travel_note({**ground, "flight": None})
    assert "— over 20 h even at best, too far" in far
    assert surroundings._travel_note({"hours": None, "taxi": True, "flight": None}).startswith(
        "from Amsterdam: no train, coach or ferry journey found within 48 h")


def test_served_airports_nearest_first(tmp_path):
    path = tmp_path / "flights.csv"
    path.write_text("icao,iata,name,country,lat,lon,passengers,year\nLPPR,OPO,Porto,PT,41.2481,-8.6814,1,2024\n"
                    "LPFR,FAO,Faro,PT,37.0159,-7.9709,1,2024\nLEMD,MAD,Madrid,ES,40.4934,-3.5722,1,2024\n",
                    encoding="utf-8")
    surroundings._airports.cache_clear()
    assert [a["iata"] for a in surroundings.served_airports(39.6, -8.4, str(path))] == ["OPO", "FAO"]
    surroundings._airports.cache_clear()


def test_holiday_let_rules_for_the_city_and_the_country(tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text("country,place,rule,law,checked\nES,*,national rule,Ley 1,2026-11-01\n"
                    "ES,Barcelona,no new licences,Decreto 3,2026-11-01\nGR,Athina,frozen,Greek law,2026-11-01\n",
                    encoding="utf-8")
    short_lets._rules.cache_clear()
    assert short_lets.notes({"country": "ES", "concelho": "Barcelona"}, str(path)) == [
        "holiday lets in Barcelona: no new licences (Decreto 3)", "holiday lets: national rule (Ley 1)"]
    assert short_lets.notes({"country": "GR", "concelho": "Athens"}, str(path)) == [
        "holiday lets in Athina: frozen (Greek law)"]
    assert short_lets.notes({"country": "IT", "concelho": "Roma"}, str(path)) == []
    short_lets._rules.cache_clear()
