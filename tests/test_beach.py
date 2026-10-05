"""A big, gradual bonus for homes near the beach (geo.nearest_beach, scoring)."""
import json

import pytest

import geo
from scoring import condition, score_detail


@pytest.fixture
def beach_file(tmp_path):
    path = tmp_path / "beaches.csv"
    path.write_text("country,lat,lon,name\nPT,37.0890,-8.2480,Praia da Falésia\nIT,44.0600,12.5800,Rimini\n",
                    encoding="utf-8")
    return str(path)


def at(lat, lon, **extra):
    return {"country": "PT", "raw_json": json.dumps({"lat": lat, "lon": lon}), **extra}


def test_the_nearest_beach_in_any_country(beach_file):
    near = geo.nearest_beach(at(37.0950, -8.2480), beach_file)
    assert near["name"] == "Praia da Falésia" and 0.6 <= near["km"] <= 0.7 and not near["approx"]
    assert "from the beach (Praia da Falésia)" in near["text"]
    assert geo.nearest_beach(at(44.07, 12.58, country="IT"), beach_file)["name"] == "Rimini"
    assert geo.nearest_beach(at(40.0, -7.5), beach_file) is None          # inland, far away
    assert geo.nearest_beach({"country": "PT"}, beach_file) is None       # no position


def home(km):
    return {"source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia", "area_m2": 120,
            "price": 25000, "beach": {"km": km, "name": "X", "approx": False, "text": f"{km} km from the beach (X)"}}


def test_the_beach_bonus_is_big_and_fades_with_distance():
    ranks = [score_detail(home(km))[0] for km in (0.5, 2, 5, 10, 30)]
    assert ranks == sorted(ranks, reverse=True) and ranks[0] - ranks[-1] >= 24
    assert "0.5 km from the beach (X)" in score_detail(home(0.5))[1]
    assert not any("beach" in r for r in score_detail(home(30))[1])
    approx = {**home(0.5), "beach": {**home(0.5)["beach"], "approx": True}}
    assert score_detail(home(30))[0] < score_detail(approx)[0] < score_detail(home(0.5))[0]


def test_an_abandoned_house_needs_heavy_work_but_an_empty_one_does_not():
    assert condition({"title": "Moradia", "description": "Casa devoluta e ao abandono há anos"}) == "heavy"
    assert condition({"title": "Vivienda", "description": "vivienda abandonada"}) == "heavy"
    assert condition({"title": "Casa", "description": "casale abbandonato"}) == "heavy"
    assert condition({"title": "Moradia", "description": "Moradia devoluta, pronta a habitar"}) != "heavy"


def test_water_words_in_other_languages():
    from scoring import water_nearby
    assert water_nearby("terreno vicino al fiume Po") == "vicino al fiume"
    assert water_nearby("Woonhuis aan het water") == "aan het water"
    assert water_nearby("Grundstück am See") == "am See"
    assert water_nearby("kuća uz more") == "uz more"
    assert water_nearby("casa em Rio Maior") is None and water_nearby("Via Mare 3") is None


def test_without_its_own_position_a_home_is_placed_at_its_town(beach_file):
    import geo
    towns = {geo.town_key("PT", "Albufeira"): {"name": "Albufeira", "lat": 37.0890, "lon": -8.2500}}
    item = {"country": "PT", "concelho": "Albufeira", "raw_json": "{}"}
    near = geo.nearest_beach(item, beach_file, towns=towns)
    assert near["approx"] and near["text"].startswith("about ") and near["km"] < 1
    assert geo.nearest_beach(item, beach_file) is None                   # no town known yet


def test_airports_and_long_distance_stations_add_a_smaller_bonus(tmp_path):
    import geo
    from scoring import score_detail
    path = tmp_path / "transport.csv"
    path.write_text("country,kind,lat,lon,name\nPT,airport,38.7742,-9.1342,Aeroporto de Lisboa\n"
                    "PT,station,38.7680,-9.0990,Lisboa Oriente\n", encoding="utf-8")
    home = at(38.7700, -9.1000)
    assert geo.nearest_hub(home, "airport", str(path))["name"] == "Aeroporto de Lisboa"
    assert geo.nearest_hub(home, "station", str(path))["km"] < 0.5
    assert geo.nearest_hub(at(41.15, -8.61), "station", str(path)) is None     # Porto: 270 km away
    base = {"source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia", "area_m2": 120,
            "price": 25000}
    near = {**base, "airport": {"km": 10, "approx": False, "text": "10 km from the airport (X)"},
            "station": {"km": 1, "approx": False, "text": "1.0 km from the station (Y)"}}
    plain, _ = score_detail(base)
    better, reasons = score_detail(near)
    assert 12 <= better - plain <= 16 and "10 km from the airport (X)" in reasons
    by_the_beach, _ = score_detail({**base, "beach": {"km": 0.5, "approx": False, "text": "b"}})
    assert better - plain < by_the_beach - plain                  # the beach still counts most


def test_the_transport_list_leaves_out_airstrips():
    import sys
    sys.path.insert(0, "scripts")
    import update_transport
    payload = {"elements": [
        {"type": "way", "center": {"lat": 38.77, "lon": -9.13}, "tags": {"name": "Aeroporto Humberto Delgado"}},
        {"type": "way", "center": {"lat": 38.72, "lon": -9.35}, "tags": {"name": "Aeródromo Municipal de Cascais"}},
    ]}
    assert [r["name"] for r in update_transport.parse("PT", "airport", payload)] == ["Aeroporto Humberto Delgado"]
    stops = {"elements": [{"type": "node", "lat": 38.7680, "lon": -9.0990, "tags": {"name": "Lisboa Oriente"}},
                          {"type": "node", "lat": 38.7681, "lon": -9.0992, "tags": {"name": "Lisboa Oriente"}}]}
    assert len(update_transport.parse("PT", "station", stops)) == 1          # one station, many tracks


def test_distance_to_guarda():
    import geo
    from scoring import GUARDA
    got = geo.distance_to_place(at(40.60, -7.30), *GUARDA, "Guarda")
    assert 7 < got["km"] < 8 and got["text"].endswith("km from Guarda") and not got["approx"]
    assert geo.distance_to_place(at(37.0, -8.0), *GUARDA, "Guarda") is None          # Algarve: too far


def test_citius_land_names_its_municipality_under_localizacao():
    import geo
    item = {"country": "PT", "description": "Prédio rústico localização : Rojanda, Freixedas, Pinhel ano de "
                                            "inscrição na matriz: 1969 área total: 4,6905 ha"}
    assert geo.municipality(item) == "Pinhel"


def test_somewhere_to_swim_within_1_5_km_is_a_must():
    from scoring import SWIM_MAX_KM
    by_sea, reasons = score_detail(home(0.5))
    assert "somewhere to swim 0.5 km away (the sea)" in reasons
    lake, reasons = score_detail({**home(30), "climate": {"water_km": 1.2}})
    assert "somewhere to swim 1.2 km away (river)" in reasons
    dry, reasons = score_detail({**home(8), "climate": {}})
    assert f"nowhere to swim within {SWIM_MAX_KM:g} km (nearest: the sea 8.0 km)" in reasons
    assert dry < lake and dry < by_sea
    stream = {**home(8), "climate": {}, "raw_json": '{"water_check": {"radius_m": 300, "found": [{"kind": "stream"}]}}'}
    assert any(r.startswith("nowhere to swim") for r in score_detail(stream)[1])     # a stream is not a swim


def test_sea_beats_lake_beats_river():
    def at(kind):
        if kind == "the sea":
            return {**home(0.5), "climate": {}}
        raw = '{"water_check": {"radius_m": 500, "found": [{"kind": "%s"}]}}' % kind
        return {**home(40), "climate": {}, "raw_json": raw}
    sea, lake, river = (score_detail(at(k))[0] for k in ("the sea", "lake", "river"))
    assert sea > lake > river


def test_water_by_the_open_sea_is_a_ria_not_a_river():
    ria = {**home(2.4), "climate": {"water_km": 0.4}}
    inland = {**home(20), "climate": {"water_km": 0.4}}
    assert "somewhere to swim 0.4 km away (sea inlet (ría))" in score_detail(ria)[1]
    assert "somewhere to swim 0.4 km away (river)" in score_detail(inland)[1]
