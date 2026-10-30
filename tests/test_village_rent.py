"""A home well outside its municipality's town (Cinco Casas, 24 km from
Alcázar de San Juan): the town's rent is cut like its price, and the invest
reasons say the town's figures may not apply."""
import costs
import scoring

HOME = {"title": "Casa", "tipo": "vivienda", "country": "ES", "source": "solvia", "concelho": "Lleida",
        "district": "Lleida", "area_m2": 80, "price": 30000, "description": "Casa en buen estado",
        "raw_json": "{}"}


def _far(km, approx=False):
    return dict(HOME, town_distance={"km": km, "town": "LLEIDA", "approx": approx,
                                     "text": f"{km:.0f} km from LLEIDA"})


def test_the_town_rent_is_cut_far_from_town():
    near, far = costs.rent(dict(HOME), 40000), costs.rent(_far(24), 40000)
    assert near and far and "town_cut" not in near
    assert far["town_cut"] == (0.7, 24)
    assert abs(far["monthly"] - near["monthly"] * 0.7) <= 1
    assert "counted at 70%" in far["note"]


def test_close_to_town_keeps_the_full_rent():
    assert "town_cut" not in costs.rent(_far(1.5), 40000)


def test_the_reasons_say_the_figures_are_the_town_s():
    _, reasons = scoring.score(_far(24), mode="invest")
    assert any("24 km from LLEIDA: the local price and rent are the town's" in r for r in reasons)
    _, reasons = scoring.score(_far(6), mode="invest")
    assert not any("are the town's" in r for r in reasons)
    _, reasons = scoring.score(_far(24, approx=True), mode="invest")   # a parish pin is not precise enough
    assert not any("are the town's" in r for r in reasons)
