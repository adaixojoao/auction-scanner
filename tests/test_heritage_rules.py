"""Protected heritage (France's planning servitudes, Portugal's DGPC atlas),
French letting rules by commune, and the nearest hospital, 2026-10-31."""
import json

import geo
import rent_rules
import scoring
import surroundings


def _sup(suptype, typeass, name):
    return {"properties": {"suptype": suptype, "typeass": typeass, "nomsuplitt": name}}


def test_french_protections_from_the_servitudes():
    found = surroundings.fr_protections([
        _sup("ac1", "Monument historique", "Cathédrale Notre-Dame"),
        _sup("ac1", "Périmètre des abords", "Cathédrale Notre-Dame"),
        _sup("ac1", "Périmètre des abords", "HÃ´tel de Ville"),
        _sup("ac1", "Périmètre des abords", "HÃ´tel de Ville"),
        _sup("ac2", "Enceinte du site", "Ensemble urbain à Paris"),
        _sup("ac4", "Périmètre", "SPR d'Avignon"),
        _sup("pm1", "Enveloppe", "PPRi"),
    ])
    assert found == {"monument": ["Cathédrale Notre-Dame"], "near": ["Hôtel de Ville"],
                     "site": ["Ensemble urbain à Paris"], "spr": ["SPR d'Avignon"], "ppr": ["PPRi"], "pprt": []}
    lines = surroundings._heritage_notes({"fr_heritage": found})
    assert lines[0].startswith("on a monument historique (Cathédrale Notre-Dame)")
    assert any(x.startswith("within the protected surroundings of a monument historique (Hôtel de Ville): "
                            "outside works need the Architecte des Bâtiments de France's agreement") for x in lines)
    assert any("site patrimonial remarquable" in x for x in lines)
    assert any(x.startswith("in a listed site (Ensemble urbain à Paris)") for x in lines)


def test_many_monuments_are_counted_and_none_is_said():
    found = surroundings.fr_protections([_sup("ac1", "Périmètre des abords", f"Immeuble {i}") for i in range(5)])
    line = surroundings._heritage_notes({"fr_heritage": found})[0]
    assert "of 5 monuments historiques (Immeuble 0, Immeuble 1 and 3 more)" in line
    assert surroundings._heritage_notes({"fr_heritage": surroundings.fr_protections([])}) == \
        ["no heritage protection registered at the point (Géoportail de l'urbanisme)"]


def test_portuguese_classified_heritage():
    found = surroundings.pt_protections([
        {"layerName": "PatrimonioImovel", "attributes": {"Designacao": "Zona histórica do Porto",
                                                         "Categoria": "IIP - imóvel de interesse público",
                                                         "Situacao_A": "Classificado"}},
        {"layerName": "ZEP", "attributes": {"Designacao": "Zona histórica do Porto"}},
        {"layerName": "ZGP", "attributes": {"Designacao": "Igreja dos Grilos"}},
        {"layerName": "ZEP", "attributes": {"Designacao": "Igreja dos Grilos"}},
    ])
    assert found == {"inside": [["Zona histórica do Porto", "IIP - imóvel de interesse público", "Classificado"]],
                     "zep": ["Igreja dos Grilos"], "zgp": []}
    lines = surroundings._heritage_notes({"pt_heritage": found})
    assert lines == ["inside Zona histórica do Porto, imóvel de interesse público: works need the heritage "
                     "office's approval (DGPC atlas)",
                     "in the protection zone of Igreja dos Grilos: works need the heritage office's opinion "
                     "(DGPC atlas)"]
    pending = surroundings.pt_protections([{"layerName": "PatrimonioImovel", "attributes": {
        "Designacao": "Casa X", "Categoria": "", "Situacao_A": "Em Vias de Classificação"}}])
    assert "(being classified)" in surroundings._heritage_notes({"pt_heritage": pending})[0]


def test_heritage_sources_ask_building_pins_only():
    item = {"id": "x", "country": "FR", "title": "Maison à Paris", "raw_json": "{}"}
    pos = {"lat": 48.85, "lon": 2.35, "precision": "sale"}
    assert "fr_heritage" in surroundings.applicable(item, pos)
    assert "fr_heritage" not in surroundings.applicable(item, {**pos, "precision": "municipality"})


def _rules(tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text("insee,name,zone,rent_cap,cap_until,territory\n"
                    "75056,Paris,tendue,all,2026-11-24,Paris\n"
                    "64009,Ahetze,tendue,all,,Pays basque (24 communes)\n"
                    "05061,Gap,,,,\n"
                    "65286,Lourdes,touristique,,,\n"
                    "38485,Seyssinet-Pariset,tendue,part,2026-11-24,Grenoble-Alpes Métropole (21 communes)\n",
                    encoding="utf-8")
    rent_rules._table.cache_clear()
    return str(path)


def test_french_letting_rules(tmp_path):
    path = _rules(tmp_path)
    paris = rent_rules.note({"country": "FR", "concelho": "Paris"}, path)
    assert paris.startswith("Paris: zone tendue: a new tenant's rent cannot rise beyond the rent index")
    assert "rents are capped (Paris): a new lease cannot exceed the prefect's reference rent plus 20%; " \
           "the current order runs to 2026-11-24" in paris
    assert "rents are capped (Pays basque)" in rent_rules.note({"country": "FR", "concelho": "Ahetze"}, path)
    assert "rents are capped (in part of the commune)" in \
        rent_rules.note({"country": "FR", "concelho": "Seyssinet-Pariset"}, path)
    assert rent_rules.note({"country": "FR", "concelho": "Lourdes"}, path).startswith(
        "Lourdes: tourist and tight-market commune")
    assert rent_rules.note({"country": "FR", "concelho": "Gap"}, path) is None
    assert rent_rules.note({"country": "PT", "concelho": "Paris"}, path) is None
    rent_rules._table.cache_clear()


def test_nearest_hospital_and_emergency_department(tmp_path):
    path = tmp_path / "hospitals.csv"
    path.write_text("country,lat,lon,name,city,kind\n"
                    "PT,41.150,-8.610,H. Santo António,Porto,emergency\n"
                    "PT,41.180,-8.600,Clínica Norte,Porto,hospital\n", encoding="utf-8")
    item = {"country": "PT", "raw_json": json.dumps({"lat": 41.181, "lon": -8.601})}
    found = geo.nearest_hospital(item, path=str(path))
    assert found["name"] == "Clínica Norte" and found["km"] < 1
    assert found["emergency"]["name"] == "H. Santo António" and 3 < found["emergency"]["km"] < 4
    reasons = []
    scoring._surroundings({**item, "hospital": found}, reasons)
    assert any(r.startswith("0.1 km from a hospital (Clínica Norte); 3.") and "from an emergency department "
               "(H. Santo António) (straight line; GISCO)" in r for r in reasons), reasons
    reasons = []
    scoring._surroundings({**item, "hospital": {**found, "emergency": {**found["emergency"], "km": found["km"]}}},
                          reasons)
    assert any("from an emergency department (H. Santo António) (straight line; GISCO)" in r and
               "from a hospital" not in r for r in reasons)
    assert geo.nearest_hospital({"country": "PT"}, path=str(path)) is None
    german = {"country": "DE", "raw_json": json.dumps({"lat": 51.15, "lon": 14.97})}
    assert geo.nearest_hospital(german, path=str(path)) is None        # not in the file: not Poland's
