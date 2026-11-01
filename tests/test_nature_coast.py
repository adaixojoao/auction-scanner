"""Protected nature EU-wide (Natura 2000, national protected areas), Portugal's
coastal safeguard strips and France's coastline-retreat communes, 2026-10-31."""
import coast_retreat
import rent_rules
import surroundings


def test_natura_and_national_sites_without_the_same_site_twice():
    found = surroundings.nature_found(
        [{"SITECODE": "ES0000024", "SITENAME": "Doñana", "SITETYPE": "C"},
         {"SITECODE": "ES0000024", "SITENAME": "Doñana", "SITETYPE": "C"}],
        [{"siteName": "Doñana", "nationalId": "ES0000024", "iucnDescri": "Habitat/Species Management Area (IV)"},
         {"siteName": "Doñana", "nationalId": "ES610010", "iucnDescri": "National Park (II)"},
         {"siteName": "Massif du Mont Blanc", "nationalId": "FR1", "iucnDescri": "Other notApplicable; notReported"}])
    assert found == {"natura": [["ES0000024", "Doñana", "habitats and birds"]],
                     "national": [["Doñana", "National Park (II)"], ["Massif du Mont Blanc", ""]]}
    lines = surroundings._nature_note(found)
    assert lines[0] == ("inside Natura 2000 site Doñana (ES0000024, habitats and birds): works or a change of use "
                        "that could harm it need an environmental assessment and may be refused (EEA; Habitats "
                        "Directive art. 6)")
    assert lines[1].startswith("inside a protected area under national law (Doñana — National Park (II), Massif "
                               "du Mont Blanc): building is restricted")
    assert surroundings._nature_note(surroundings.nature_found([], [])) == []


def test_portuguese_coastal_strips():
    found = surroundings.pt_coast_found([
        {"layerId": 0, "attributes": {"poc": "POC Ovar - Marinha Grande"}},
        {"layerId": 10, "attributes": {"legenda_4": "Nível II", "poc": "POC Ovar - Marinha Grande"}},
        {"layerId": 11, "attributes": {"legenda_4": "Nível I", "poc": "POC Ovar - Marinha Grande"}},
    ])
    assert found == {"poc": "POC Ovar - Marinha Grande", "flooding": "level II", "erosion": "level I"}
    assert surroundings._pt_coast_note(found) == (
        "in the coastal erosion safeguard strip (level I), the overtopping and coastal flooding safeguard strip "
        "(level II): new building and enlarging are banned or restricted there (APA, POC Ovar - Marinha Grande)")
    zone = surroundings.pt_coast_found([{"layerId": 16, "attributes": {"legenda_4": "Null", "poc": "POC X"}}])
    assert surroundings._pt_coast_note(zone) == ("in the 500 m land protection zone: new building is limited and "
                                                 "must follow the programme (APA, POC X)")
    assert surroundings._pt_coast_note(surroundings.pt_coast_found([])) is None


def test_coastline_retreat_communes(tmp_path):
    path = tmp_path / "coast.csv"
    path.write_text("insee,name,since,decree,url\n06004,Antibes,2023-08-01,2026-95,https://x\n", encoding="utf-8")
    rent_rules._table.cache_clear()
    line = coast_retreat.note({"country": "FR", "concelho": "Antibes"}, str(path))
    assert line.startswith("Antibes is on the coastline-retreat list (since 2023, décret 2026-95): once it maps")
    assert coast_retreat.note({"country": "FR", "concelho": "Vierzon"}, str(path)) is None
    assert coast_retreat.note({"country": "PT", "concelho": "Antibes"}, str(path)) is None
    rent_rules._table.cache_clear()


def test_land_use_at_the_point_and_nuisances_near_it():
    found = surroundings.land_use_found({"112"}, {"112", "121"}, {"112", "121", "124", "211"})
    assert found == {"at": "112", "near": ["121", "124"]}
    assert surroundings._land_use_note(found) == ("land here: town or village; an industrial or commercial area "
                                                  "within 300 m, an airport within 1 km (CORINE Land Cover 2018, EEA)")
    airport = surroundings.land_use_found({"124"}, {"124"}, {"124", "121"})
    assert surroundings._land_use_note(airport) == "land here: airport (CORINE Land Cover 2018, EEA)"
    assert surroundings._land_use_note(surroundings.land_use_found(set(), set(), set())) is None


def test_the_nearest_bathing_water_and_its_class():
    sites = [{"bathingWaterName": "AREINHO", "bwWaterCategory": "Transitional", "latitude": 40.84583,
              "longitude": -8.66194, "qualityStatus": "Good", "qualityStatus_minus1": "Good",
              "qualityStatus_minus2": "Excellent"},
             {"bathingWaterName": "FURADOURO", "bwWaterCategory": "Coastal", "latitude": 40.87371,
              "longitude": -8.67755, "qualityStatus": "Excellent",
              **{f"qualityStatus_minus{i}": "Excellent" for i in range(1, 11)}}]
    found = surroundings.bathing_found(sites, 40.875, -8.674, 2025)
    assert found["nearest"]["name"] == "Furadouro" and found["nearest"]["km"] == 0.3
    assert surroundings._bathing_note(found) == ("nearest official bathing water: Furadouro (sea, 0.3 km), Excellent "
                                                 "in 2025, Excellent every season since 2020 (EEA, Bathing Water "
                                                 "Directive)")
    estuary = surroundings.bathing_found(sites[:1], 40.875, -8.674, 2025)
    assert surroundings._bathing_note(estuary).endswith("(estuary, 3.4 km), Good in 2025 (before: Good, Excellent) "
                                                        "(EEA, Bathing Water Directive)")
    assert surroundings._bathing_note(surroundings.bathing_found([], 40.0, -8.0, 2025)) is None


def test_building_age_against_the_country(tmp_path, monkeypatch):
    import building_age
    import prices
    path = tmp_path / "age.csv"
    path.write_text("geo,before_1946,y1946_1980,y1981_2000,since_2001\nDEA11,21,57,13,9\nDE,15,51,19,15\n",
                    encoding="utf-8")
    monkeypatch.setattr(prices, "population_outlook", lambda c, p: {"nuts3": "DEA11", "name": "Düsseldorf"})
    building_age._table.cache_clear()
    assert building_age.note({"country": "DE", "concelho": "Düsseldorf"}, str(path)) == (
        "homes in Düsseldorf built 21% before 1946, 57% 1946–80, 13% 1981–2000, 9% since 2001 (78% before 1981; "
        "country 66%) (Census 2021, Eurostat)")
    path.write_text("geo,before_1946,y1946_1980,y1981_2000,since_2001\nDEA11,40,50,5,5\nDE,10,40,25,25\n",
                    encoding="utf-8")
    building_age._table.cache_clear()
    assert "an older stock than the country's" in building_age.note({"country": "DE"}, str(path))
    building_age._table.cache_clear()
