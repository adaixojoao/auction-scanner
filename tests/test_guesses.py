"""guesses.py: what a court notice leaves out may be guessed, conservatively,
named, and at a cost to the rank — and never to set a bid."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import bidcap
import costs
import guesses
from db import load_listings

MILD = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1},
        "flood_m": 0.0, "fire_danger": {"high_days_2090": 0}}
PARISH = {"lat": 41.815, "lon": -8.857, "precision": "parish"}      # Vila Praia de Âncora
SEEN = [88, 95, 100, 104, 110, 115, 120, 126, 132, 140]            # T3 houses in Viana do Castelo


@pytest.mark.parametrize("title, description, n", [
    ("Moradia T3", "", 3),
    ("Moradia", "Vivenda V2 com quintal", 2),
    ("Casa", "casa com 3 quartos e cozinha", 3),
    ("Apartamento T2+1", "", 2),
    ("Moradia", "Processo 123/24.0T8VCT, prédio urbano", None),     # a case number is not a type
    ("Lote", "fração T1 e fração T3", None),                        # two homes: no one type
])
def test_the_type_of_home(title, description, n):
    assert guesses.typology({"title": title, "description": description}) == n


def _row(area, district="Viana do Castelo", title="Moradia T3", description=None, country="PT", tipo="moradia"):
    return {"country": country, "district": district, "title": title, "tipo": tipo,
            "description": description if description is not None else f"{title} com {area} m2",
            "area_m2": area}


def test_the_lower_quartile_of_what_has_been_seen():
    index = guesses.observed_floors([_row(a) for a in SEEN] + [_row(2000)]          # a plot, out of range
                                    + [_row(90, description="terreno com 90 m2")]  # land, not a floor
                                    + [_row(60, title="Apartamento T3", tipo="apartamento") for _ in range(3)])
    q1, count = index[("PT", "viana do castelo", "house", 3)]
    assert count == 10 and 98 <= q1 <= 104
    assert ("PT", "", "house", 3) in index                        # the country, for districts with few
    assert ("PT", "viana do castelo", "flat", 3) not in index     # three flats are too few


def _home(**kw):
    item = {"country": "PT", "district": "Viana do Castelo", "title": "Moradia", "tipo": "moradia",
            "description": "Moradia T3 de rés-do-chão e 1.º andar", "raw_json": json.dumps({"geo": PARISH})}
    item.update(kw)
    return item


def test_which_guess_comes_first():
    index = guesses.observed_floors([_row(a) for a in SEEN] + [_row(a, district="Faro") for a in (60,) * 8])
    item = _home(floor_market=index)
    g = guesses.floor_guess(item)
    assert 98 <= g["m2"] <= 104 and "T3 houses in Viana do Castelo" in g["basis"] and "10 floors" in g["basis"]

    # A district with too few falls back to the whole country.
    elsewhere = guesses.floor_guess(_home(floor_market=index, district="Braga"))
    assert "T3 houses in PT" in elsewhere["basis"]

    # No bedrooms named: every house seen in the district, whatever its size (2026-10-26).
    any_size = guesses.floor_guess(_home(floor_market=index, description="prédio urbano"))
    assert "houses of any size (no bedrooms stated) in Viana do Castelo" in any_size["basis"]

    # Nothing to go on: no guess.
    assert guesses.floor_guess(_home(floor_market={})) is None

    # The map's outline with its levels beats what was seen elsewhere.
    pin = {"lat": 41.0, "lon": -8.6}
    drawn = {"v": 1, "pin": [41.0, -8.6], "house": {"m2": 70, "levels": 2, "floor_m2": 140}}
    exact = _home(floor_market=index, raw_json=json.dumps({**pin, "outline": drawn}))
    assert guesses.floor_guess(exact) == {"m2": 140.0, "basis": "the house's outline on the map, 2 levels"}
    # Without levels the outline is only a floor at least that big, used last.
    one_level = {**drawn, "house": {"m2": 70, "levels": None, "floor_m2": None}}
    no_type = _home(description="prédio urbano", raw_json=json.dumps({**pin, "outline": one_level}))
    assert guesses.floor_guess(no_type)["m2"] == 70


def test_the_sea_at_its_farthest_from_a_parish_pin():
    worst = guesses.beach_worst_case(_home(beach={"km": 0.6}))
    assert worst == {"km": 3.6, "spread": 3.0, "precision": "parish"}
    exact = _home(beach={"km": 0.6}, raw_json=json.dumps({"lat": 41.8, "lon": -8.86}))
    assert guesses.beach_worst_case(exact) is None


# ─── Through the real loader ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def caminha_price(monkeypatch):
    """The tests' fixed city price table has no Caminha: give it one."""
    monkeypatch.setattr("scoring.local_price", lambda item: (2200.0, "a city series"))

def _sale(add, eid, *, area=None, geo=PARISH, description="Moradia T3 de rés-do-chão e 1.º andar"):
    from climate import _spot
    end = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    add(source="citius", external_id=eid, title="Moradia", tipo="moradia", price=60000, area_m2=area,
        concelho="Caminha", district="Viana do Castelo", date_end=end, description=description,
        raw_json=json.dumps({"processo": f"{eid}/24.0T8VCT", "geo": geo,
                             "climate": {"result": MILD, "spot": _spot(geo)}}))


def _seen(add):
    end = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    for i, a in enumerate(SEEN):
        add(external_id=f"seen{i}", title="Moradia T3", description=f"Moradia T3 com {a} m2", tipo="moradia",
            price=150000, area_m2=a, district="Viana do Castelo", concelho="Ponte de Lima", date_end=end)


def _scores(db, mode):
    return {it["id"]: it for it in load_listings(db, mode=mode, include_hidden=True, apply_min_score=False)}


def test_a_citius_house_with_no_area_and_a_parish_pin_is_ranked_on_guesses(db, add):
    _seen(add)
    _sale(add, "guessed")
    got = _scores(db, "both")["citius:guessed"]
    assert got["score"] > 0
    named = next(r for r in got["reasons"] if r.startswith("ranked on 2 educated guesses"))
    assert "floor about 99 m²" in named and "T3 houses in Viana do Castelo" in named
    assert "the sea within 3.6 km at worst (the parish pin, ±3 km)" in named
    # The same figures, stated and at the house, rank higher: a guess costs.
    _sale(add, "stated", area=got["floor_guess"]["m2"],
          geo={"lat": 41.815, "lon": -8.857, "precision": "street"}, description="Moradia T3")
    both = _scores(db, "both")
    assert both["citius:stated"]["rank"] > both["citius:guessed"]["rank"]


def test_a_town_pin_is_still_too_vague_for_the_sea(db, add):
    _seen(add)
    _sale(add, "town", area=120, geo={"lat": 41.82, "lon": -8.85, "precision": "municipality"})
    got = _scores(db, "both")["citius:town"]
    assert got["score"] == 0 and "town, not the house" in got["reasons"][0]


def test_a_guessed_floor_prices_the_repairs_and_never_sets_a_bid(db, add):
    _seen(add)
    _sale(add, "guessed")
    item = _scores(db, "invest")["citius:guessed"]
    assert costs.building_m2(item)[1] == "guess"                     # kept with the score
    planned = costs.selected_planning(item)
    assert planned["incomplete"] is False and planned["estimate"]["renovation"]
    assert costs.floor_resolution(item)["established"] is False
    unknowns = []
    assert bidcap._value(item, "home", {}, unknowns) is None
    assert any("only guessed" in u for u in unknowns)


def test_each_guess_costs_its_share_of_the_rank(db, add, monkeypatch):
    import db as dbmod
    _seen(add)
    _sale(add, "guessed")
    charged = _scores(db, "both")["citius:guessed"]["rank"]
    monkeypatch.setattr(guesses, "GUESS_FACTOR", 1.0)
    dbmod.forget_scores(db)
    free = _scores(db, "both")["citius:guessed"]["rank"]
    assert charged == pytest.approx(free * 0.95 ** 2)        # the floor and the sea
