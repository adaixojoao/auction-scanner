"""Property tax from what the listing states, homes that are nobody's main
home (Census 2021), and Portugal's urban regeneration areas, 2026-10-31."""
import json

import holiday_lets
import property_tax
import surroundings


def test_taxe_fonciere_as_the_listing_gives_it():
    assert property_tax.note({"country": "FR", "description": "Taxe foncière : 2 600 euros. Chauffage gaz."}) == \
        "taxe foncière €2,600 a year (the listing)"
    assert property_tax.note({"country": "FR", "description": "Taxe foncière 2025 : 618 €"}) == \
        "taxe foncière €618 a year (the listing)"
    assert property_tax.note({"country": "FR", "description": "taxe foncière et 3000€ de charges"}) is None
    assert property_tax.note({"country": "FR", "description": "taxe foncière ou d'habitation"}) is None


def test_imu_from_the_rendita_catastale():
    line = property_tax.note({"country": "IT", "description": "Foglio 3, rendita catastale Euro 357,90."})
    assert line.startswith("IMU on a second home about €517–€637 a year (rendita catastale €357.90")
    two = property_tax.note({"country": "IT", "description": "A/2 rendita € 300,00; C/6 rendita € 50,00"})
    assert "rendita catastale €350.00" in two


def test_imi_from_the_taxable_value():
    line = property_tax.note({"country": "PT", "description": "com o valor patrimonial tributário de 42.037,00 €"})
    assert line == ("IMI about €126–€189 a year (taxable value €42,037; the câmara sets the rate between "
                    "0.30% and 0.45%)")
    assert property_tax.note({"country": "PT", "description": "sem valor indicado"}) is None


def test_homes_that_are_nobodys_main_home(tmp_path, monkeypatch):
    import prices
    dw = tmp_path / "dw.csv"
    dw.write_text("geo,dwellings,not_main_home\nPT,5970677,1828096\nPT150,391416,198021\nPT16E,284288,104024\n",
                  encoding="utf-8")
    nights = tmp_path / "n.csv"
    nights.write_text("nuts3,year,nights,prev_year,prev_nights\nPT150,2025,20000000,2024,19000000\n", encoding="utf-8")
    monkeypatch.setattr(prices, "population_outlook",
                        lambda c, p: {"nuts3": "PT150", "name": "Algarve", "pop_2019": 438000} if p == "Albufeira"
                        else {"nuts3": "PT16E", "name": "Região de Coimbra", "pop_2019": 437000})
    monkeypatch.setattr(holiday_lets, "_country_median", lambda c, p: 3.0)
    holiday_lets._dwellings.cache_clear()
    holiday_lets._nights.cache_clear()
    albufeira = holiday_lets.dwellings_note({"country": "PT", "concelho": "Albufeira"}, str(dw), str(nights))
    assert albufeira == ("51% of homes in Algarve are nobody's main home (empty or second homes; country 31%) — "
                         "many for the country: a holiday area, many are second homes (Census 2021, Eurostat)")
    mealhada = holiday_lets.dwellings_note({"country": "PT", "concelho": "Mealhada"}, str(dw), str(nights))
    assert mealhada.endswith("— about average for the country (Census 2021, Eurostat)")
    holiday_lets._dwellings.cache_clear()
    holiday_lets._nights.cache_clear()


ARU_XML = ('<FeatureCollection><gml:featureMember><Layer Name="ARU"><Attribute Name="ID">1009</Attribute>'
           '<Attribute Name="Concelho">Mealhada</Attribute><Attribute Name="Designação">ARU da Mealhada</Attribute>'
           '<Attribute Name="DR">Aviso n.º 12998/2015 - Diário da República n.º 218</Attribute></Layer>'
           '</gml:featureMember></FeatureCollection>')


def _pt_home(found):
    raw = {"geo": {"lat": 40.3786, "lon": -8.4497, "precision": "sale"},
           "surroundings": {"v": surroundings.VERSION, "pin": [40.3786, -8.4497], "found": found, "failed": {}}}
    return {"id": "x", "country": "PT", "title": "Moradia em Mealhada", "raw_json": json.dumps(raw)}


def test_an_urban_regeneration_area():
    aru = surroundings.aru_attributes(ARU_XML)
    assert aru == {"name": "ARU da Mealhada", "notice": "Aviso n.º 12998/2015 - Diário da República n.º 218",
                   "town": "Mealhada"}
    line = [n for n in surroundings.notes(_pt_home({"pt_aru": aru})) if "ARU" in n]
    assert line[0].startswith("in the ARU da Mealhada; Aviso n.º 12998/2015: renovating can qualify for 6% VAT")
    miss = surroundings.notes(_pt_home({"pt_aru": surroundings.aru_attributes("<FeatureCollection/>")}))
    assert miss[0].startswith("not in an urban regeneration area (ARU) on DGT's register")
