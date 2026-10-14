"""The exact position of a plot from the land cadastre (cadastre.py)."""
import json

import cadastre
from conftest import FakeResponse


def test_the_spanish_cadastral_reference_is_read_from_the_text():
    assert cadastre.referencia_catastral("SITUACIÓN: Paraje REFERENCIA CATASTRAL: 24078A014000720000OE VIVIENDA") \
        == "24078A01400072"
    assert cadastre.referencia_catastral("Referencia catastral 3589701UK6938N0001JG") == "3589701UK6938N"
    assert cadastre.referencia_catastral("REFERENCIA CATASTRAL: no consta") is None


def test_only_measured_areas_are_matched():
    assert cadastre.is_measured(82750) and cadastre.is_measured(46905)
    assert not cadastre.is_measured(83000) and not cadastre.is_measured(3000) and not cadastre.is_measured(None)


class Session:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        return FakeResponse(json_data=self.answers(url, params or {}))


def parcel(lat, lon, area, unit):
    return {"properties": {"label": "AAA 000 796 411", "areavalue": area, "administrativeunit": unit,
                           "referencepoint": {"type": "Point", "coordinates": [lon, lat]}}}


def test_a_portuguese_plot_is_found_by_its_area_in_its_own_municipality():
    def answers(url, params):
        if params.get("propertyName") == "administrativeunit":           # the town's municipality
            return {"features": [{"properties": {"administrativeunit": "070105"}}]}
        return {"features": [parcel(38.624, -7.411, 82726, "070105"), parcel(38.854, -7.497, 82758, "071001")]}
    got = cadastre.dgt_position(Session(answers), {"lat": 38.70, "lon": -7.40}, 82750)
    assert got["precision"] == "cadastre" and (got["lat"], got["lon"]) == (38.624, -7.411)
    assert "82 726 m²" in got["query"]
    # Two parcels of that size in the same municipality: not sure which, so none.
    both = lambda url, params: {"features": [parcel(38.62, -7.41, 82726, "070105"),  # noqa: E731
                                             parcel(38.63, -7.42, 82760, "070103")]} \
        if params.get("propertyName") != "administrativeunit" else \
        {"features": [{"properties": {"administrativeunit": "070105"}}]}
    assert cadastre.dgt_position(Session(both), {"lat": 38.70, "lon": -7.40}, 82750) is None


def test_the_best_plots_get_their_cadastre_position_once(db, add):
    import geo
    add("citius", "p1", title="Prédio rústico", tipo="terreno", area_m2=82750, price=9000, concelho="Alandroal",
        description="Prédio rústico, freguesia de Santiago Maior, concelho de Alandroal")
    add("spain", "s1", country="ES", title="Finca rústica", price=9000,
        description="REFERENCIA CATASTRAL: 24078A014000720000OE")
    add("spain", "s2", country="ES", title="Vivienda", price=12000, description="Piso",
        raw_json=json.dumps({"referencia_catastral": "3589701UK6938N0001JG"}))
    add("citius", "p2", title="Prédio rústico", tipo="terreno", area_m2=83000, price=9000, concelho="Alandroal")

    def answers(url, params):
        if "catastro" in url:
            return {"Consulta_CPMRCResult": {"coordenadas": {"coord": [
                {"geo": {"xcen": "-5.5282", "ycen": "42.7296"}, "ldt": "Polígono 14 Parcela 72"}]}}}
        return {"features": [parcel(38.624, -7.411, 82726, "070105")]}
    session = Session(answers)
    items = [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]
    towns = {geo.town_key("PT", "Alandroal"): {"name": "Alandroal", "lat": 38.70, "lon": -7.40}}
    assert cadastre.locate_pending(db, session, items, towns) == 3
    raws = {r["id"]: json.loads(r["raw_json"] or "{}") for r in db.execute("SELECT id, raw_json FROM listings")}
    assert raws["citius:p1"]["geo"]["precision"] == "cadastre" and raws["citius:p1"]["cadastre_checked"]
    assert raws["spain:s1"]["geo"]["lat"] == 42.7296
    assert raws["spain:s2"]["geo"]["precision"] == "cadastre"
    assert raws["spain:s2"]["geo"]["query"] == "Catastro 3589701UK6938N"
    assert raws["spain:s2"]["climate"] is None
    assert "geo" not in raws["citius:p2"]                    # a round area is not matched
    items = [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]
    calls = len(session.calls)
    assert cadastre.locate_pending(db, session, items, towns) == 0 and len(session.calls) == calls   # once
    assert geo.position(items[0])["precision"] == "cadastre"


SQUARE = {"type": "MultiPolygon", "coordinates": [[
    [[2.0, 48.0], [2.002, 48.0], [2.002, 48.002], [2.0, 48.002], [2.0, 48.0]]]]}


def _french_answers(buildings=1, features=1):
    def answers(url, params):
        if "geo.api.gouv.fr" in url:
            return [{"nom": "Plémet", "code": "22183"}]
        if "apicarto" in url:
            feat = {"properties": {
                "code_insee": "22183", "section": "0A", "numero": "0654", "idu": "221830000A0654",
                "nom_com": "Plémet", "contenance": 3400}, "geometry": SQUARE}
            return {"features": [feat] * features}
        if "rel_batiment" in url:
            return [{"batiment_groupe_id": f"bg-{i}"} for i in range(buildings)]
        if "ffo_bat" in url:
            return [{"annee_construction": 1978, "usage_niveau_1_txt": "Résidentiel individuel"}]
        return {}
    return answers


def test_a_french_notice_names_one_parcel_and_not_a_phone_number():
    assert cadastre.french_parcels("Maison cadastrée section A n° 654, commune de Plémet") == [
        {"insee": None, "section": "0A", "numero": "0654"}]
    assert len(cadastre.french_parcels("cadastré section ZH nos 12, 13 et 14")) == 3
    one = cadastre.french_parcels("parcelle 221830000A0654, section A n° 654")
    assert one == [{"insee": "22183", "section": "0A", "numero": "0654"}]
    assert cadastre.french_parcels("221830000A0654") == []          # an id with no cadastral word
    assert cadastre.french_parcels("section 2 n° 3 du règlement") == []
    assert cadastre.french_parcels("appeler le 0612345678") == []


def test_one_french_parcel_is_placed_and_a_high_score_reads_the_one_building(db, add, monkeypatch):
    import listing_info
    monkeypatch.setattr(cadastre.time, "sleep", lambda *_: None)
    add("france", "p", "FR", title="Une maison", concelho="Plémet", price=30000,
        description="Maison cadastrée section A n° 654, commune de Plémet",
        raw_json=json.dumps({"department": "22", "geo": {"lat": 48.4, "lon": -2.6, "precision": "advertised"},
                             "climate": {"v": 1}}))
    session = Session(_french_answers())
    items = [dict(r) for r in db.execute("SELECT * FROM listings")]
    items[0]["best_rank"] = 90
    assert cadastre.locate_pending(db, session, items, {}) == 1
    raw = json.loads(db.execute("SELECT raw_json FROM listings").fetchone()[0])
    assert raw["geo"]["precision"] == "cadastre" and raw["geo"]["lat"] == raw["fr_cadastre"]["lat"]
    assert abs(raw["geo"]["lon"] - 2.001) < 1e-6 and "3 400 m²" in raw["geo"]["query"]
    assert raw["climate"] is None and raw["fr_cadastre"]["plot_m2"] == 3400
    assert raw["fr_cadastre"]["bdnb"] == {"buildings": 1, "year": 1978, "use": "Résidentiel individuel"}
    rec = listing_info.official_records({"country": "FR", "title": "Une maison", "description": "",
                                         "raw_json": json.dumps(raw)})
    assert rec["registry"].startswith("Cadastre") and any(f["label"] == "Year built" for f in rec["facts"])
    assert "not used in the score" in rec["check"] and "geoportail" in rec["links"][0]["url"]
    calls = len(session.calls)
    assert cadastre.locate_pending(db, session, [dict(r) for r in db.execute("SELECT * FROM listings")], {}) == 0
    assert len(session.calls) == calls


def test_a_lower_score_does_not_ask_the_building_file_and_a_sale_pin_stays(db, add, monkeypatch):
    monkeypatch.setattr(cadastre.time, "sleep", lambda *_: None)
    add("france", "low", "FR", title="Une maison", concelho="Plémet",
        description="cadastrée section A n° 654",
        raw_json=json.dumps({"department": "22"}))
    add("france", "sale", "FR", title="Une maison", concelho="Plémet",
        description="cadastrée section A n° 654",
        raw_json=json.dumps({"department": "22", "geo": {"lat": 48.1, "lon": -2.1, "precision": "sale"},
                             "climate": {"v": 1}}))
    session = Session(_french_answers())
    items = [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]
    for it in items:
        it["best_rank"] = 84
    assert cadastre.locate_pending(db, session, items, {}) == 1          # the advertised one; the sale pin stays
    assert not any("bdnb" in url for url, _ in session.calls)
    rows = {r["id"]: json.loads(r["raw_json"]) for r in db.execute("SELECT id, raw_json FROM listings")}
    assert rows["france:low"]["geo"]["precision"] == "cadastre" and "bdnb" not in rows["france:low"]["fr_cadastre"]
    assert rows["france:sale"]["geo"]["precision"] == "sale" and rows["france:sale"]["climate"]["v"] == 1
    assert rows["france:sale"]["fr_cadastre"]["plot_m2"] == 3400


def test_several_buildings_or_parcels_are_not_given_one_answer(db, add, monkeypatch):
    monkeypatch.setattr(cadastre.time, "sleep", lambda *_: None)
    add("france", "many", "FR", title="Une maison", concelho="Plémet", price=80000,
        description="cadastrée section A n° 654",
        raw_json=json.dumps({"department": "22"}))
    add("france", "lots", "FR", title="Des parcelles", concelho="Plémet",
        description="cadastré section ZH nos 12 et 13",
        raw_json=json.dumps({"department": "22", "geo": {"lat": 1, "lon": 2, "precision": "municipality"}}))
    session = Session(_french_answers(buildings=2))
    items = [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]
    for it in items:
        it["best_rank"] = 90
    cadastre.locate_pending(db, session, items, {})
    rows = {r["id"]: json.loads(r["raw_json"]) for r in db.execute("SELECT id, raw_json FROM listings")}
    assert rows["france:many"]["fr_cadastre"]["bdnb"] == {"buildings": 2}
    assert "year" not in rows["france:many"]["fr_cadastre"]["bdnb"]
    assert rows["france:lots"]["geo"]["precision"] == "municipality"
    assert "not placed as one pin" in rows["france:lots"]["fr_cadastre"]["note"]
    assert len(rows["france:lots"]["fr_cadastre"]["parcels"]) == 2
