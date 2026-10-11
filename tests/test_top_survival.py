"""How many of a fixed top 20 still survive the checks.

The number is the floor in tests/fixtures/top_survival_floor.txt. A change
that drops a sound house, or lets a rejected one through, fails here.
The target is 15 of 20.
"""
import json

from tops import top_blockers

FLOOR_FILE = "tests/fixtures/top_survival_floor.txt"

_LOOKED = json.dumps({"photo_check": {"condition": "unknown", "confidence": "low", "shows_house": True}})
_GOOD_STATE = json.dumps({"photo_check": {"condition": "good", "confidence": "high", "shows_house": True},
                          "conservation": 2})


def home(n, **kw):
    base = {"source": "fotocasa", "country": "ES", "title": f"Casa en Siero {n}",
            "description": "Casa de dos plantas en buen estado, habitable.",
            "tipo": "vivienda", "price": 40000, "area_m2": 90, "concelho": "Siero",
            "district": "Asturias", "raw_json": _GOOD_STATE}
    base.update(kw)
    return base


GOOD = [home(i) for i in range(15)]
BAD = [
    home(90, title="Casa en silencio", description="Casa de tres plantas con finca.", raw_json=_LOOKED),
    home(91, title="APARTAMENTO EN SUBASTA", description="APARTAMENTO EN SUBASTA en Cullera."),
    home(92, raw_json=json.dumps({"photo_check": {"condition": "good", "confidence": "high",
                                                  "shows_house": True},
                                  "conservation": 2, "link_check": {"gone": True, "why": "vendido"}})),
    home(93, title="Local comercial", tipo="local", description="Local diáfano. En este momento es un almacen. No tiene baño.",
         raw_json=_LOOKED),
    home(94, title="Casa ocupada", description="El inmueble se encuentra ocupado ilegalmente por terceros.",
         raw_json=_LOOKED),
]


def test_fifteen_of_twenty_survive_and_the_floor_holds():
    survived = [h for h in GOOD + BAD if not top_blockers(h)]
    floor = int(open(FLOOR_FILE, encoding="utf-8").read().strip())
    assert len(survived) >= floor
    assert len(survived) >= 15
    assert len(GOOD) + len(BAD) == 20
    assert all(top_blockers(h) for h in BAD)
    assert not any(top_blockers(h) for h in GOOD)
