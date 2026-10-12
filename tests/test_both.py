"""Live and invest: a home that passes as a place to live, ranked by the worse score."""
import json

from scoring import score_detail

MILD = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1}}
SALE = json.dumps({"lat": 41.18, "lon": -8.69})


def beach_home(**kw):
    item = {
        "source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia",
        "area_m2": 90, "price": 22000, "description": "moradia em bom estado",
        "beach": {"km": 0.4, "approx": False, "name": "Praia", "text": "0.4 km from Praia"},
        "climate": MILD, "raw_json": SALE,
    }
    item.update(kw)
    return item


def test_a_house_by_the_sea_in_a_good_climate_is_the_worse_of_the_two_scores(monkeypatch):
    monkeypatch.setattr("scoring.local_price", lambda item: (1200.0, "a city series"))
    house = beach_home()
    both, why = score_detail(house, mode="both")
    home = score_detail(house, mode="home")[0]
    invest = score_detail(house, mode="invest")[0]
    assert both == min(home, invest) > 0
    assert why[0].startswith("the worse of the two")


def test_the_gates_keep_off_what_is_not_a_home_to_live_in():
    town = beach_home(raw_json=json.dumps(
        {"geo": {"lat": 41.1, "lon": -8.6, "precision": "municipality"}}))
    assert score_detail(town, mode="both")[0] == 0
    assert "town, not the house" in score_detail(town, mode="both")[1][0]

    far = beach_home(beach={"km": 8.2, "approx": False, "text": "8.2 km from the beach"})
    assert score_detail(far, mode="both")[0] == 0
    assert score_detail(far, mode="both")[1][0].startswith("not by the beach")
    assert score_detail(far, mode="invest")[0] > 0

    guessed = beach_home(beach={"km": 0.4, "approx": True, "text": "about 0.4 km from the beach"})
    assert score_detail(guessed, mode="both")[0] == 0
    assert "not the house" in score_detail(guessed, mode="both")[1][0]

    hot = beach_home(climate={"hot_days": {"rcp45_2071-2100": 30}, "stress": {"stress_2080": 1}})
    assert score_detail(hot, mode="both")[0] == 0
    assert "climate poor" in score_detail(hot, mode="both")[1][0]

    unknown = beach_home(climate=None)
    assert score_detail(unknown, mode="both")[0] == 0
    assert "climate unknown" in score_detail(unknown, mode="both")[1][0]

    caution = beach_home(climate={"hot_days": {"rcp45_2071-2100": 10}, "stress": {"stress_2080": 1}})
    assert score_detail(caution, mode="both")[0] == 0
    assert "climate caution" in score_detail(caution, mode="both")[1][0]

    dry = beach_home()
    dry.pop("beach")
    assert score_detail(dry, mode="both")[0] == 0
    assert "not by the beach" in score_detail(dry, mode="both")[1][0]


def test_the_floor_has_to_be_a_real_floor(monkeypatch):
    monkeypatch.setattr("scoring.local_price", lambda item: (1200.0, "a city series"))
    missing = beach_home(area_m2=0, title="Moradia", description="em bom estado")
    assert score_detail(missing, mode="both")[0] == 0
    assert "floor area" in score_detail(missing, mode="both")[1][0]

    plot = beach_home(area_m2=5000, title="Casa")
    assert score_detail(plot, mode="both")[0] == 0
    assert "plot" in score_detail(plot, mode="both")[1][0]

    notice = beach_home(area_m2=0, title="Moradia de 80 m²", description="em bom estado")
    both = score_detail(notice, mode="both")[0]
    home = score_detail(notice, mode="home")[0]
    invest = score_detail(notice, mode="invest")[0]
    assert both == min(home, invest) > 0


def test_a_stored_area_the_notice_calls_land_is_not_a_floor(monkeypatch):
    monkeypatch.setattr("scoring.local_price", lambda item: (1200.0, "a city series"))
    land = beach_home(area_m2=150, description="casa de habitação, área total do terreno de 150 m2")
    assert score_detail(land, mode="both")[0] == 0
    assert "floor" in score_detail(land, mode="both")[1][0]
    house = beach_home(area_m2=150, description="área total do terreno de 150m2 e habitação de 80 m2")
    both = score_detail(house, mode="both")[0]
    assert both == min(score_detail(house, mode="home")[0], score_detail(house, mode="invest")[0]) > 0


def test_a_plot_stays_on_its_own_goals():
    land = beach_home(title="Terreno rústico", tipo="terreno", area_m2=200000, price=40000,
                      description="Pinhal")
    assert score_detail(land, mode="both") == (
        0.0, ["not a home — a plot is Investment land or Forestry"])
    assert score_detail(land, mode="forest")[0] > 0


def test_a_climate_read_is_kept_on_the_listing():
    import climate
    item = {"raw_json": json.dumps({"lat": 41.18, "lon": -8.69})}
    climate.remember(item, {"hot_days": {"rcp45_2071-2100": 2}})
    assert item["_climate_write"]
    assert climate.stored(item)["hot_days"]["rcp45_2071-2100"] == 2


def test_a_province_average_is_not_a_price_for_this_house(monkeypatch):
    monkeypatch.setattr("scoring.local_price", lambda item: (900.0, "MIVAU, province average"))
    house = beach_home()
    both, why = score_detail(house, mode="both")
    home = score_detail(house, mode="home")[0]
    assert both == home
    assert why[0].startswith("no local sale price")
    monkeypatch.setattr("scoring.local_price", lambda item: None)
    house = beach_home()
    both, why = score_detail(house, mode="both")
    home = score_detail(house, mode="home")[0]
    assert both == home > 0
    assert why[0].startswith("no local sale price")
    assert score_detail(house, mode="both")[0] > score_detail(house, mode="invest")[0]


def test_live_and_invest_drops_what_the_cash_cannot_cover(db, add, monkeypatch):
    """Over the all-in ceiling the home is hidden here, as on My home."""
    import config
    from db import load_listings
    config.save_config({"cash_on_hand": 40000, "filters": {"min_score": 40}})
    add("eleiloes", "sea", title="Moradia T3", price=20000, area_m2=100)

    def fake(item, now=None, targets=None, mode="home"):
        if mode == "both":
            item["live_score"] = 86.0
            item["invest_score"] = 26.0
            return 60.0, ["the worse of the two (86 to live in, 26 as an investment)"]
        return 80.0, ["home"]

    monkeypatch.setattr("scoring.score_detail", fake)
    shown = {it["id"] for it in load_listings(db, filters={"min_score": 40}, mode="both")}
    assert "eleiloes:sea" not in shown
    hidden = [it for it in load_listings(db, filters={"min_score": 40}, mode="both", include_hidden=True)
              if it["id"] == "eleiloes:sea"]
    assert hidden and hidden[0]["hidden_reason"].startswith("cash ")
    home = {it["id"] for it in load_listings(db, filters={"min_score": 40}, mode="home")}
    assert "eleiloes:sea" not in home
