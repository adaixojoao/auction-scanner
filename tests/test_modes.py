"""Three goals ranked on their own (scoring.MODES): home, invest, forest."""
from scoring import score_detail


def listing(**kw):
    base = {"source": "fotocasa", "country": "ES", "title": "Casa en Foz", "description": "", "price": 30000}
    base.update(kw)
    return base


def test_investment_ranks_by_the_beach_and_far_below_market_above_inland():
    beach = listing(area_m2=100, beach={"km": 0.4, "text": "0.4 km from the beach"}, description="Casa en buen estado")
    inland = listing(area_m2=100, beach={"km": 25}, description="Casa en buen estado")
    assert score_detail(beach, mode="invest")[0] > score_detail(inland, mode="invest")[0]


def test_investment_takes_only_homes_and_forestry_only_land():
    land = listing(title="Terreno rústico", tipo="terreno", area_m2=200000, price=40000)
    home = listing(area_m2=100)
    assert score_detail(land, mode="invest") == (0.0, ["not a home — see Forestry for land"])
    assert score_detail(home, mode="forest") == (0.0, ["not land"])


def test_forestry_wants_10_ha_cheap_per_hectare_and_rustic():
    small = listing(title="Terreno rústico", tipo="terreno", area_m2=50000, price=5000)
    assert score_detail(small, mode="forest")[1][0].endswith("under 10 ha")
    cheap = listing(title="Finca rústica forestal", tipo="terreno", area_m2=300000, price=30000)    # €1,000/ha
    dear = listing(title="Finca rústica", tipo="terreno", area_m2=120000, price=48000)              # €4,000/ha
    assert score_detail(cheap, mode="forest")[0] > score_detail(dear, mode="forest")[0]
    wet = {**dear, "description": "Finca junto al río"}
    assert score_detail(wet, mode="forest")[0] > score_detail(dear, mode="forest")[0]
    building = listing(title="Suelo urbano", tipo="terreno", area_m2=120000, price=48000,
                       description="Solar urbano edificable")
    rustic, urban = score_detail(dear, mode="forest")[0], score_detail(building, mode="forest")[0]
    assert urban <= rustic


def test_a_share_is_skipped_in_every_mode():
    share = listing(title="1/2 de terreno rústico", tipo="terreno", area_m2=300000, price=10000)
    for mode in ("home", "invest", "forest"):
        assert score_detail(share, mode=mode)[0] == 0


def test_cheap_rustic_land_is_not_doubtful_for_forestry():
    cheap = listing(title="Finca rústica", tipo="terreno", area_m2=1000000, price=15000)     # €150/ha
    assert not any("doubtful" in r for r in score_detail(cheap, mode="forest")[1])
    absurd = listing(title="Finca rústica", tipo="terreno", area_m2=130000, price=100)        # €8/ha
    assert any("doubtful" in r for r in score_detail(absurd, mode="forest")[1])


def test_investment_does_not_believe_an_impossible_discount():
    plausible = listing(concelho="Ourense", district="Ourense", area_m2=100, price=60000,
                        description="Casa en buen estado")
    absurd = listing(concelho="Ourense", district="Ourense", area_m2=1000, price=5000,
                     description="Casa en buen estado")
    reasons = score_detail(absurd, mode="invest")[1]
    assert any("check why" in r for r in reasons)
    assert score_detail(absurd, mode="invest")[0] < score_detail({**absurd, "area_m2": 60, "price": 40000}, mode="invest")[0]
    assert not any("check why" in r for r in score_detail(plausible, mode="invest")[1])
