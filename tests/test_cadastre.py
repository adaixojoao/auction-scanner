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
    add("citius", "p2", title="Prédio rústico", tipo="terreno", area_m2=83000, price=9000, concelho="Alandroal")

    def answers(url, params):
        if "catastro" in url:
            return {"Consulta_CPMRCResult": {"coordenadas": {"coord": [
                {"geo": {"xcen": "-5.5282", "ycen": "42.7296"}, "ldt": "Polígono 14 Parcela 72"}]}}}
        return {"features": [parcel(38.624, -7.411, 82726, "070105")]}
    session = Session(answers)
    items = [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]
    towns = {geo.town_key("PT", "Alandroal"): {"name": "Alandroal", "lat": 38.70, "lon": -7.40}}
    assert cadastre.locate_pending(db, session, items, towns) == 2
    raws = {r["id"]: json.loads(r["raw_json"] or "{}") for r in db.execute("SELECT id, raw_json FROM listings")}
    assert raws["citius:p1"]["geo"]["precision"] == "cadastre" and raws["citius:p1"]["cadastre_checked"]
    assert raws["spain:s1"]["geo"]["lat"] == 42.7296
    assert "geo" not in raws["citius:p2"]                    # a round area is not matched
    items = [dict(r) for r in db.execute("SELECT * FROM listings ORDER BY id")]
    calls = len(session.calls)
    assert cadastre.locate_pending(db, session, items, towns) == 0 and len(session.calls) == calls   # once
    assert geo.position(items[0])["precision"] == "cadastre"
