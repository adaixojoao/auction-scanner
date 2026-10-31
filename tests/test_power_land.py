"""Power lines, wind turbines and solar farms near a listing, and German
official land values (BORIS-NRW), 2026-10-31. Offline."""
import json

import surroundings


def _item(found, title="Maison à Vierzon", country="FR", area=None):
    raw = {"geo": {"lat": 47.4152, "lon": 2.4493, "precision": "sale"},
           "surroundings": {"v": surroundings.VERSION, "pin": [47.4152, 2.4493], "found": found, "failed": {}}}
    return {"id": "x", "country": country, "title": title, "area_m2": area, "raw_json": json.dumps(raw)}


LINE = {"type": "way", "tags": {"power": "line", "voltage": "400000;225000"},
        "geometry": [{"lat": 47.4132, "lon": 2.4400}, {"lat": 47.4132, "lon": 2.4600}]}
LOW = {"type": "way", "tags": {"power": "line", "voltage": "20000"},
       "geometry": [{"lat": 47.4151, "lon": 2.4400}, {"lat": 47.4151, "lon": 2.4600}]}
TURBINE = {"type": "node", "lat": 47.4252, "lon": 2.4493, "tags": {"power": "generator", "generator:source": "wind"}}
SOLAR = {"type": "way", "tags": {"power": "plant", "plant:source": "solar"},
         "geometry": [{"lat": 47.4100, "lon": 2.4493}, {"lat": 47.4110, "lon": 2.4493}]}


def test_the_nearest_high_voltage_line_and_the_rest():
    found = surroundings.power_nearby([LINE, LOW, TURBINE, SOLAR], 47.4152, 2.4493)
    assert found["line"] == {"kv": 400, "m": 221}           # the 20 kV line next door is not counted
    assert found["turbines"] == 1 and found["turbine_m"] == 1112
    assert found["farms"] == [{"source": "solar", "m": 523}]
    line = surroundings.notes(_item({"power": found}))[0]
    assert line == ("high-voltage line (400 kV) 221 m away; 1 wind turbine within 2 km, nearest 1.1 km; solar farm "
                    "523 m away — buyers pay less near pylons and turbines (OpenStreetMap)")


def test_nothing_nearby_is_said_too():
    found = surroundings.power_nearby([LOW], 47.4152, 2.4493)
    assert surroundings.notes(_item({"power": found})) == [
        "no high-voltage line within 1 km, nor wind turbine or solar farm within 2 km (OpenStreetMap)"]


BORIS = {"features": [
    {"layerName": "brw_aussenbereich", "properties": {"BRW": "40", "STAG": "2026-01-01", "NUTA": "W",
                                                      "ORTST": "Lichtenau-Ortsteile"}},
    {"layerName": "brw_landwirtschaft", "properties": {"BRW": "5,3", "STAG": "2026-01-01", "NUTA": "A", "ORTST": " "}},
    {"layerName": "brw_landwirtschaft", "properties": {"BRW": "2,9", "STAG": "2026-01-01", "NUTA": "GR", "ORTST": " "}},
    {"layerName": "brw_forstwirtschaft", "properties": {"BRW": "0,8", "STAG": "2026-01-01", "NUTA": "F", "ORTST": " "}},
    {"layerName": "verfuegbarkeit", "properties": {"STATUS": "Richtwerte verfuegbar"}}]}


def test_land_values_for_the_kind_of_listing():
    zones = {"state": "NRW", "zones": surroundings.boris_zones(BORIS)}
    assert [z["eur_m2"] for z in zones["zones"]] == [40.0, 5.3, 2.9, 0.8]
    house = surroundings.notes(_item({"de_land": zones}, "Einfamilienhaus in Lichtenau", "DE", 140))
    assert house == ["official land value €40/m² (building land outside the village, residential, "
                     "Lichtenau-Ortsteile, 2026) — land only, without buildings (Bodenrichtwert, BORIS-NRW)"]
    field = surroundings.notes(_item({"de_land": zones}, "Ackerland in Lichtenau", "DE", 15000))
    assert field == ["official land value €5.30/m² (farmland, arable, 2026) — this 15,000 m² about €79,500 — "
                     "land only, without buildings (Bodenrichtwert, BORIS-NRW)"]
    meadow = surroundings.notes(_item({"de_land": zones}, "Grünland Wiese bei Lichtenau", "DE", 15000))
    assert meadow[0].startswith("official land value €2.90/m² (farmland, grassland, 2026)")


def test_outside_an_open_state_nothing_is_asked():
    class NoCall:
        def get(self, *a, **k):
            raise AssertionError("no service covers Munich")
    assert surroundings.de_land(NoCall(), 48.137, 11.575, {}) == {"state": None, "zones": []}
