"""Official flood maps (Spain, Portugal, France's risk plans), French polluted
ground and Seveso plants, 2026-10-31."""
import json

import surroundings


def test_spanish_flood_depth_from_the_map():
    assert surroundings.flood_depth_text("Results ...\n-----\nGRAY_INDEX = 1.2849999\n-----") == 1.28
    for dry in ("GRAY_INDEX = 999.0", "GRAY_INDEX = -3.0", "GRAY_INDEX = 3.3999999521443642E38",
                "GRAY_INDEX = 0.0", "GRAY_INDEX = -9999.0", "nothing"):
        assert surroundings.flood_depth_text(dry) is None, dry
    line = surroundings._flood_note({"river": {"10": 1.28, "100": 2.85, "500": 4.21}, "sea": {}}, "MITECO flood maps")
    assert line == ("in the 10-year river flood zone, water up to 1.3 m (2.9 m in a 100-year one; 4.2 m in a "
                    "500-year one) (MITECO flood maps)")
    assert surroundings._flood_note({"river": {}, "sea": {}}, "MITECO flood maps").startswith(
        "not in a mapped flood zone (MITECO flood maps; the maps cover")


def test_portuguese_flood_depths_and_legal_zones():
    depths = [{"attributes": {"Profundidade": "1,7109", "pretorno": "T0020", "Local": "Tomar"}},
              {"attributes": {"Profundidade": "2,1048", "pretorno": "T0100", "Local": "Tomar"}},
              {"attributes": {"Profundidade": "2,6027", "pretorno": "T1000", "Local": "Tomar"}}]
    found = surroundings.pt_floods(depths, [])
    assert found == {"river": {"20": 1.71, "100": 2.1, "1000": 2.6}, "place": "Tomar", "adjacent": False}
    assert surroundings._flood_note(found, "APA flood maps") == (
        "in the 20-year river flood zone, water up to 1.7 m (2.1 m in a 100-year one; 2.6 m in a 1,000-year one) "
        "(APA flood maps)")
    legal = surroundings.pt_floods([], [{"attributes": {}}])
    assert surroundings._flood_note(legal, "APA flood maps") == (
        "in a legal flood zone (zona adjacente): building there is restricted (APA flood maps)")


def test_french_risk_plans_from_the_servitudes():
    found = surroundings.fr_protections([
        {"properties": {"suptype": "pm1", "typeass": "Enveloppe", "nomsuplitt": "PPRi du département de Paris"}},
        {"properties": {"suptype": "pm3", "typeass": "Zone", "nomsuplitt": ""}},
    ])
    assert found["ppr"] == ["PPRi du département de Paris"] and found["pprt"] == ["unnamed plan"]
    lines = surroundings._heritage_notes({"fr_heritage": found})
    assert any(x.startswith("inside the perimeter of a natural risk prevention plan (PPRi du département de "
                            "Paris)") for x in lines)
    assert any("technological risk prevention plan" in x for x in lines)


def test_an_answer_without_the_plans_is_asked_again():
    raw = {"geo": {"lat": 47.2258, "lon": 2.0706, "precision": "sale"},
           "surroundings": {"v": surroundings.VERSION, "pin": [47.2258, 2.0706], "failed": {},
                            "found": {"fr_heritage": {"monument": [], "near": [], "site": [], "spr": []}}}}
    item = {"id": "x", "country": "FR", "title": "Maison à Vierzon", "raw_json": json.dumps(raw)}
    assert "fr_heritage" in surroundings.missing(item)


def test_polluted_ground_and_seveso_plants():
    plants = [{"raisonSociale": "HUNTSMAN", "statutSeveso": "Seveso seuil haut", "latitude": 45.709, "longitude": 4.856},
              {"raisonSociale": "FAR LOW", "statutSeveso": "Seveso seuil bas", "latitude": 45.73, "longitude": 4.856},
              {"raisonSociale": "SHOP", "statutSeveso": "Non Seveso", "latitude": 45.703, "longitude": 4.856},
              {"raisonSociale": "KEM ONE", "statutSeveso": "Seveso seuil haut", "latitude": 45.71, "longitude": 4.856}]
    found = surroundings.soil_found([{"nom_etablissement": "Garage"}], [{"nom_site": "Ancienne usine"}], plants,
                                    45.703, 4.856)
    assert [p["name"] for p in found["seveso"]] == ["HUNTSMAN", "KEM ONE"]     # a low plant 3 km off is too far
    lines = surroundings._soil_note(found)
    assert lines[0] == "a site with known soil pollution within 300 m (Ancienne usine) (Géorisques, BASOL/SIS)"
    assert lines[1].startswith("1 former industrial or service site within 100 m (Garage): the ground may be")
    assert lines[2] == ("Seveso high-threshold plant 0.7 km away (HUNTSMAN); 2 Seveso plants within 3 km: an "
                        "accident plan and possibly a PPRT apply (Géorisques)")
    assert surroundings._soil_note(surroundings.soil_found([], [], [], 45.0, 1.5)) == []
