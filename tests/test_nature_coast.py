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
