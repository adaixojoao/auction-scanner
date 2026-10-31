"""A French parcel's own past sales, Italy's landslide and flood zones, local
incomes and population change since 2011, 2026-10-31."""
import json

import incomes
import population_change
import surroundings


def _dvf(mutation, date, price, kind, built, land="", nature="Vente"):
    return {"id_mutation": mutation, "date_mutation": date, "nature_mutation": nature, "valeur_fonciere": str(price),
            "type_local": kind, "surface_reelle_bati": str(built), "surface_terrain": str(land),
            "id_parcelle": "01004000AB0100", "latitude": "45.9", "longitude": "5.3"}


def test_parcel_sales_newest_first():
    sales = surroundings.parcel_sales([
        _dvf("a", "2022-05-10", 185000, "Maison", 95, 820),
        _dvf("a", "2022-05-10", 185000, "Dépendance", "", 820),
        _dvf("b", "2025-02-01", 120000, "Maison", 95, 820, nature="Adjudication"),
        _dvf("c", "2023-01-01", 90000, "Maison", 95, nature="Echange"),
    ])
    assert [s["date"] for s in sales] == ["2025-02-01", "2022-05-10"]
    assert sales[0]["auction"] and sales[1]["eur_m2"] == round(185000 / 95)
    line = surroundings._parcel_note({"ref": "01004000AB0100", "sales": sales}, {"title": "Maison", "country": "FR"})
    assert line == ("this parcel (01004000AB0100) sold 2025-02-01 at auction for €120,000 (house 95 m², land "
                    "820 m²); 2022-05-10 for €185,000 (house 95 m², land 820 m²) (DVF)")
    assert surroundings._parcel_note({"ref": "X", "sales": []}, {}) == \
        "no sale of this parcel (X) recorded since 2021 (DVF)"


def test_a_flats_parcel_is_its_building():
    sales = surroundings.parcel_sales([_dvf("a", "2024-01-05", 472000, "Appartement", 38),
                                       _dvf("b", "2024-03-05", 600000, "Appartement", 50)])
    line = surroundings._parcel_note({"ref": "75105000AQ0017", "sales": sales},
                                     {"title": "Appartement T2 Paris", "property_type": "apartment", "country": "FR"})
    assert line == "this building's parcel sold 2 times since 2021, flats at a median €12,210/m² (2 single-flat sales) (DVF)"


def _fr_item(plot_ref=None):
    raw = {"geo": {"lat": 47.2258, "lon": 2.0706, "precision": "sale"}}
    if plot_ref:
        raw["outline"] = {"pin": [47.2258, 2.0706], "plot": {"ref": plot_ref}}
    return {"id": "x", "country": "FR", "title": "Maison à Vierzon", "raw_json": json.dumps(raw)}


def test_the_parcel_is_asked_once_the_outline_found_it():
    assert "fr_parcel" not in surroundings.missing(_fr_item())
    assert "fr_parcel" in surroundings.missing(_fr_item("18279000AB0012"))


def test_italian_landslide_and_flood_zones():
    h = surroundings.it_hazards([{"cod_per_it": 1}, {"cod_per_it": 3}], [{"scenariop1": "x"}, {"scenariop2": "y"}], [])
    assert h == {"landslide": 3, "flood": 2, "mapped": 0}
    assert surroundings._it_hazard_note(h) == ("landslide hazard high (P3); flood hazard medium (P2, every "
                                               "100–200 years) (ISPRA IdroGEO, basin plans)")
    attention = surroundings.it_hazards([{"cod_per_it": "5"}], [], [{"id": 1}])
    assert surroundings._it_hazard_note(attention) == ("landslide hazard an attention area; a mapped landslide at "
                                                       "the point (IFFI) (ISPRA IdroGEO, basin plans)")
    assert surroundings._it_hazard_note(surroundings.it_hazards([], [], [])).startswith("no landslide or flood")


def test_local_incomes_against_the_country(tmp_path):
    path = tmp_path / "incomes.csv"
    path.write_text("country,code,name,level,year,income\n"
                    "IT,058091,Roma,municipality,2024,32501\nIT,084021,Licata,municipality,2024,16916\n"
                    "IT,IT,Italia,country,2024,25816\nPT,1960910,Pinhel,municipality,2024,13647\n"
                    "PT,PT,Portugal,country,2024,15573\n", encoding="utf-8")
    incomes._table.cache_clear()
    assert incomes.note({"country": "IT", "concelho": "Licata"}, str(path)) == (
        "mean declared income per taxpayer €16,916 a year in Licata, 0.66× the country's €25,816 — well below "
        "(MEF, IRPEF 2024)")
    assert "well above" in incomes.note({"country": "IT", "concelho": "Roma"}, str(path))
    assert incomes.note({"country": "PT", "concelho": "Pinhel"}, str(path)).endswith("0.88× the country's "
                                                                                       "€15,573 (INE 2024)")
    assert incomes.note({"country": "PT", "concelho": "Guarda"}, str(path)) is None
    assert incomes.note({"country": "DE", "concelho": "Roma"}, str(path)) is None
    incomes._table.cache_clear()


def test_population_change_since_2011(tmp_path):
    path = tmp_path / "pop.csv"
    path.write_text("country,code,name,year,pop_2011,pop_latest,change_pct\n"
                    "FR,34172,Montpellier,2023,252998,295542,16.8\nFR,FR,,2023,61184114,65095951,6.4\n"
                    "IT,084021,Licata,2024,39082,34189,-12.5\nIT,IT,,2024,59347715,57767797,-2.7\n",
                    encoding="utf-8")
    population_change._table.cache_clear()
    assert population_change.note({"country": "FR", "concelho": "Montpellier"}, str(path)) == (
        "people in Montpellier: 252,998 in 2011, 295,542 in 2023 (+16.8%; the country +6.4%) — growing much "
        "faster than the country (Eurostat LAU)")
    assert population_change.note({"country": "IT", "concelho": "Licata"}, str(path)).endswith(
        "(-12.5%; the country -2.7%) (Eurostat LAU)")
    assert population_change.note({"country": "ES", "concelho": "Licata"}, str(path)) is None
    population_change._table.cache_clear()
