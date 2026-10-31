"""Recorded crime per place against its country, and French energy ratings,
2026-10-31. Offline: small tables and stored answers."""
import json

import crime
import surroundings

TABLE = """country,code,name,level,year,total,burglary,violence,source
PT,PT,Portugal,country,2025,32.00,16.20,5.00,INE/DGPJ
PT,1920111,Mealhada,municipality,2025,28.00,12.20,4.40,INE/DGPJ
PT,1110601,Lisboa,municipality,2025,49.90,30.00,5.40,INE/DGPJ
ES,ES,Spain,country,2025,50.92,1.53,1.93,Ministerio del Interior
ES,09,BURGOS,province,2025,42.10,1.40,1.00,Ministerio del Interior
IT,IT,Italy,country,2024,40.70,2.64,0.49,ISTAT
IT,ITF33,,province,2024,44.89,1.41,1.10,ISTAT
FR,FR,France,country,2025,,5.63,3.16,SSMSI
FR,18279,,commune,2025,,7.67,3.99,SSMSI
FR,77001,,commune,2025,,,,SSMSI
"""


def _path(tmp_path):
    p = tmp_path / "crime.csv"
    p.write_text(TABLE, encoding="utf-8")
    crime._table.cache_clear()
    return str(p)


def test_a_municipality_against_its_country(tmp_path):
    path = _path(tmp_path)
    assert crime.note({"country": "PT", "concelho": "Mealhada"}, path).startswith(
        "recorded crime in Mealhada, 2025: crimes 28.0 per 1,000 (country 32.0); property crimes 12.2")
    assert crime.note({"country": "PT", "concelho": "Mealhada"}, path).endswith(
        "— about average for the country (INE)")
    assert "— high for the country (INE)" in crime.note({"country": "PT", "concelho": "Lisboa"}, path)


def test_the_total_decides_and_a_double_rate_is_named(tmp_path, monkeypatch):
    import prices
    path = _path(tmp_path)
    monkeypatch.setattr(prices, "population_outlook", lambda c, p: {"nuts3": "ITF33", "name": "Napoli"})
    line = crime.note({"country": "IT", "concelho": "Napoli"}, path)
    assert line.startswith("recorded crime in the province of Napoli, 2024")
    assert "about average for the country, but robberies twice the country's or more (ISTAT)" in line


def test_a_small_spanish_town_gets_its_province(tmp_path, monkeypatch):
    path = _path(tmp_path)
    from prices import place_key
    monkeypatch.setattr(crime, "_lau_codes", lambda: {("ES", place_key("Lerma")): ["09196"]})
    assert crime.note({"country": "ES", "concelho": "Lerma"}, path).startswith(
        "recorded crime in the province of Burgos, 2025: offences 42.1 per 1,000")
    monkeypatch.setattr(crime, "_lau_codes", lambda: {("ES", place_key("Lerma")): ["09196", "33001"]})
    assert crime.note({"country": "ES", "concelho": "Lerma"}, path) is None       # two towns of that name


def test_french_communes_by_their_code(tmp_path, monkeypatch):
    path = _path(tmp_path)
    from prices import place_key
    monkeypatch.setattr(crime, "_lau_codes", lambda: {("FR", place_key("Vierzon")): ["18279"],
                                                      ("FR", place_key("Achères-la-Forêt")): ["77001"]})
    line = crime.note({"country": "FR", "concelho": "Vierzon"}, path)
    assert line.startswith("recorded crime in Vierzon, 2025: home burglaries 7.7 per 1,000 homes (country 5.6)")
    quiet = crime.note({"country": "FR", "concelho": "Achères-la-Forêt"}, path)
    assert quiet.startswith("too few crimes recorded to publish in this commune")
    crime._table.cache_clear()


def _home(certs, area):
    raw = {"geo": {"lat": 47.2257, "lon": 2.0710, "precision": "sale"},
           "surroundings": {"v": surroundings.VERSION, "pin": [47.2257, 2.071],
                            "found": {"fr_energy": {"certificates": certs}}, "failed": {}}}
    return {"id": "x", "country": "FR", "title": "Maison à Vierzon", "area_m2": area, "raw_json": json.dumps(raw)}


CERTS = [{"class": "G", "date": "2024-06-10", "area": 63.5, "m": 0, "kind": "maison"},
         {"class": "B", "date": "2025-03-15", "area": 109.3, "m": 10, "kind": "maison"},
         {"class": "E", "date": "2025-05-19", "area": 122, "m": 16, "kind": "maison"}]


def test_the_certificate_of_this_home_and_the_let_ban():
    line = [n for n in surroundings.notes(_home(CERTS, 65)) if n.startswith("energy")]
    assert line == ["energy rating G: a certificate of 2024-06 for 64 m² at this address, probably this home — "
                    "cannot be let on a new lease since 2025 without renovating (DPE, ADEME)"]


def test_without_a_matching_floor_the_neighbours_are_described():
    line = [n for n in surroundings.notes(_home(CERTS, 200)) if "energy certificates" in n]
    assert line[0].startswith("3 energy certificates at or next to this address: B to G, 1 of them F or G")
    assert surroundings.matching_certificate(CERTS, None) is None


def test_energy_is_asked_for_french_homes_with_a_building_pin():
    item = {"id": "x", "country": "FR", "title": "Maison à Vierzon",
            "raw_json": json.dumps({"geo": {"lat": 47.2, "lon": 2.0, "precision": "sale"}})}
    assert "fr_energy" in surroundings.missing(item)
    item["raw_json"] = json.dumps({"geo": {"lat": 47.2, "lon": 2.0, "precision": "village"}})
    assert "fr_energy" not in surroundings.missing(item)
