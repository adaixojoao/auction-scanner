"""A forestry project planned on the land budget (forestry.project_plan)."""
import forestry

MILD = {"heat": {"today": 28, "ssp245_2061-2080": 30, "ssp245_2081-2100": 32}}


def plot(**kw):
    base = {"source": "imovirtual", "country": "PT", "title": "Terreno florestal", "description": "Pinhal",
            "tipo": "terreno", "area_m2": 300000, "price": 40000, "climate": MILD}
    base.update(kw)
    return base


def test_the_plan_spends_the_budget_on_the_land_first_and_plants_what_is_left():
    plan = forestry.project_plan(plot(), 100_000)
    assert plan["fundable"] is True
    assert plan["acquire"] < 100_000
    assert plan["crop"]
    assert 0 < plan["hectares_now"] <= plan["hectares"]
    assert "Land budget" in plan["headline"] and "leaving" in plan["headline"]
    assert any("Buy the" in s for s in plan["steps"])


def test_a_plot_over_the_land_budget_is_not_a_project_you_can_start():
    plan = forestry.project_plan(plot(price=200_000), 100_000)
    assert plan["fundable"] is False
    assert plan["hectares_now"] == 0
    assert any("Do not buy" in s for s in plan["steps"])
    assert "over the money" in plan["headline"]


def test_without_a_climate_the_money_is_still_planned_and_no_crop_is_invented():
    plan = forestry.project_plan(plot(climate=None), 100_000)
    assert plan["fundable"] is True and plan["crop"] is None
    assert any("climate" in u for u in plan["unknowns"])


def test_a_home_and_a_plot_outside_the_forestry_countries_have_no_plan():
    assert forestry.project_plan(plot(title="Moradia T2", tipo="moradia", area_m2=120), 100_000) is None
    assert forestry.project_plan(plot(country="BG"), 100_000) is None


def test_five_hectares_of_eucalyptus_is_a_conversion_not_a_new_crop():
    gum = plot(area_m2=80_000, price=15000, title="Eucaliptal", description="Povoamento de eucalipto")
    plan = forestry.project_plan(gum, 100_000)
    assert plan["fundable"] is True
    assert any("Do not replant eucalyptus" in s for s in plan["steps"])
