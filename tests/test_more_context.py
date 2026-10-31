"""More of what a sale says, 2026-10-31: how often it failed to sell and how
old its court case is, and how many licensed holiday lets its parish has."""
import json
from datetime import datetime, timezone

import holiday_lets
import rounds
import scoring


def test_the_case_number_gives_the_year_it_was_filed():
    assert rounds.case_year({"processo": "3841/03.3TBBRR, Juízo de Execução de Almada - Juiz 1"}) == 2003
    assert rounds.case_year({"processo": "4721/14.2TCLRS"}) == 2014
    assert rounds.case_year({"processo": "898/95.4TVLSB"}) == 1995
    assert rounds.case_year({"processo": "SUB-JA-2026-267256"}) is None


def _round(i, end, price):
    return {"id": f"citius:{i}", "source": "citius", "title": "Moradia em Soure", "concelho": "Soure",
            "area_m2": 100, "price": price, "current_bid": None, "min_price": price, "date_end": end,
            "first_seen": end, "raw_json": json.dumps({"processo": "1184/14.6T8CBR"})}


def test_every_earlier_round_is_counted_with_the_drop_from_the_first():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    old = [_round(1, "2026-02-01T10:00", 20000), _round(2, "2026-05-01T10:00", 15000)]
    item = {**_round(3, "2026-11-20T10:00", 10180), "first_seen": "2026-06-01T10:00"}
    found = rounds.earlier_round(item, {"1184/14.6t8cbr": [*old, item]}, now)
    assert found["rounds"] == 2 and found["first_ended"] == "2026-02-01"
    assert found["first_price"] == 20000 and round(found["cheaper_than_first_pct"]) == 49


def test_the_history_is_shown_not_scored():
    item = {"raw_json": json.dumps({"processo": "3841/03.3TBBRR"}),
            "earlier_round": {"rounds": 2, "first_ended": "2026-02-01", "cheaper_than_first_pct": 49.1}}
    reasons = []
    scoring._sale_history(item, reasons)
    assert reasons[0].startswith("3rd time on sale: 2 earlier rounds without a buyer since 2026-02-01, 49% below")
    assert reasons[1].startswith("court case from 2003")
    quiet = []
    scoring._sale_history({"raw_json": json.dumps({"processo": "4721/14.2TCLRS"})}, quiet)
    assert quiet == []                          # 2014: most cases on sale are that old


def test_holiday_lets_where_the_home_is(tmp_path):
    table = tmp_path / "lets.csv"
    table.write_text("municipality,parish,dtmnfr,lets,beds,lets_recent,period,source\n"
                     "Mealhada,Luso,011104,40,210,6,2026-10-31,RNAL\n"
                     "Mealhada,União das freguesias de Mealhada Ventosa do Bairro e Antes,011109,3,12,0,2026-10-31,RNAL\n"
                     "Pinhel,Pinhel,091201,0,0,0,2026-10-31,RNAL\n", encoding="utf-8")
    holiday_lets._table.cache_clear()
    luso = holiday_lets.note({"country": "PT", "concelho": "Mealhada", "freguesia": "Luso"}, path=str(table))
    assert luso.startswith("40 licensed holiday lets in Luso (210 beds; 6 new in 2 years)")
    town = holiday_lets.note({"country": "PT", "concelho": "Mealhada", "freguesia": "Casal Comba"}, path=str(table))
    assert town.startswith("43 licensed holiday lets in Mealhada")
    none = holiday_lets.note({"country": "PT", "concelho": "Pinhel"}, path=str(table))
    assert none.startswith("no licensed holiday lets in Pinhel")
    assert holiday_lets.note({"country": "ES", "concelho": "Madrid"}, path=str(table)) is None
    holiday_lets._table.cache_clear()


def test_case_years_in_spain_and_italy():
    assert rounds.case_year({"expediente": "3465 0000 06 0191 23"}) == 2023
    assert rounds.case_year({"proceduraNumeroAnno": "42/2024"}) == 2024
    assert rounds.case_year({"expediente": "SUB-JA-2026-267256"}) is None


def test_an_old_case_is_old_for_its_own_country():
    spanish = {"country": "ES", "raw_json": json.dumps({"expediente": "3465 0000 06 0191 12"})}
    reasons = []
    scoring._sale_history(spanish, reasons)
    assert reasons and reasons[0].startswith("court case from 2012")     # old for Spain, not for Portugal
    portuguese = {"country": "PT", "raw_json": json.dumps({"processo": "6312/14.9T8LSB"})}
    quiet = []
    scoring._sale_history(portuguese, quiet)
    assert quiet == []


def test_short_let_nights_for_any_eu_region(tmp_path, monkeypatch):
    import prices
    nights = tmp_path / "nights.csv"
    nights.write_text("nuts3,year,nights,prev_year,prev_nights\nES418,2025,386515,2024,309212\n"
                      "ES511,2025,9000000,2024,8000000\nES300,2025,15000000,2024,14000000\n"
                      "ES620,2025,4000000,2024,3800000\n", encoding="utf-8")
    monkeypatch.setattr(prices, "population_outlook",
                        lambda country, place: {"nuts3": "ES418", "name": "Valladolid", "pop_2019": 520000})
    monkeypatch.setattr(prices, "_load_regions", lambda path, mtime: {
        "ES418": {"pop_2019": "520000"}, "ES511": {"pop_2019": "5600000"},
        "ES300": {"pop_2019": "6600000"}, "ES620": {"pop_2019": "1500000"}})
    holiday_lets._nights.cache_clear()
    holiday_lets._country_median.cache_clear()
    note = holiday_lets.region_note({"country": "ES", "concelho": "Valladolid"}, path=str(nights))
    assert note.startswith("386,515 nights booked on Airbnb, Booking and Expedia in Valladolid in 2025 "
                           "(0.7 per resident, +25% on the year before) — quiet for the country")
    holiday_lets._nights.cache_clear()
    holiday_lets._country_median.cache_clear()
