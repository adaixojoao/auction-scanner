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
