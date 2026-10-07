"""Each goal ranked on its own (scoring.MODES): home, invest, land, forest."""
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
    assert score_detail(land, mode="invest") == (0.0, ["not a home — see Investment land"])
    assert score_detail(home, mode="forest") == (0.0, ["not land"])


def test_urbanizable_and_high_mountain_are_not_forestry():
    sector = listing(title="Terreno en Turre", tipo="terreno", area_m2=200000, price=40000,
                     description="Suelo urbanizable. Futuros desarrollos residenciales.")
    assert score_detail(sector, mode="forest") == (
        0.0, ["building land (urbanizable or a development sector) — not a forestry plot"])
    rustic = listing(title="Terreno rústico", tipo="terreno", area_m2=200000, price=40000,
                     description="Suelo no urbanizable. Pinhal.")
    assert score_detail(rustic, mode="forest")[0] > 0
    park = listing(title="Finca en Laujar de Andarax", tipo="terreno", area_m2=300000, price=30000,
                   description="Finca rústica en el Parque Nacional de Sierra Nevada, altitud 2.000-2.370 m.")
    assert score_detail(park, mode="forest")[0] == 0
    assert any("national park" in r or "high mountain" in r for r in score_detail(park, mode="forest")[1])


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


def test_an_earlier_cheap_plot_ranks_above_a_later_one():
    """Sooner is better. A date next year is still a buy, and a cheap hectare
    outweighs a sale that merely ends soon."""
    from datetime import datetime, timezone
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    base = dict(title="Terreno florestal", tipo="terreno", area_m2=200000, price=40000)
    soon, r_soon = score_detail(listing(**base, date_end="2026-11-01T12:00:00"), now=now, mode="forest")
    later, r_later = score_detail(listing(**base, date_end="2027-03-01T12:00:00"), now=now, mode="forest")
    far = score_detail(listing(**base, date_end="2028-06-01T12:00:00"), now=now, mode="forest")[0]
    assert soon > later > far
    assert any(r.endswith("— soon") for r in r_soon)
    assert any("before long" in r for r in r_later)
    assert not any("not this year" in r for r in r_later)
    private = score_detail(listing(**base), now=now, mode="forest")[1]
    assert any("private sale" in r for r in private)
    # €1,000/ha closing in 2028 beats €4,000/ha closing next month.
    cheap_later = score_detail(listing(title="Finca rústica forestal", tipo="terreno",
                                       area_m2=300000, price=30000, date_end="2028-06-01T12:00:00"),
                               now=now, mode="forest")[0]
    dear_soon = score_detail(listing(title="Finca rústica", tipo="terreno",
                                     area_m2=120000, price=48000, date_end="2026-11-01T12:00:00"),
                             now=now, mode="forest")[0]
    assert cheap_later > dear_soon
    pt = listing(**base, country="PT", source="imovirtual")
    assert score_detail(pt, now=now, mode="forest")[0] > score_detail(listing(**base), now=now, mode="forest")[0]
    assert any("you can visit" in r for r in score_detail(pt, now=now, mode="forest")[1])
    azores = listing(**base, country="PT", source="imovirtual", description="Ilha de São Miguel")
    assert not any("you can visit" in r for r in score_detail(azores, now=now, mode="forest")[1])


def test_five_hectares_is_a_first_plot_and_stays_under_a_real_project():
    first, reasons = score_detail(listing(title="Terreno rústico", tipo="terreno", area_m2=70000, price=14000),
                                  mode="forest")
    project = score_detail(listing(title="Terreno rústico", tipo="terreno", area_m2=200000, price=40000),
                           mode="forest")[0]
    assert 0 < first <= 64 and any("under 10 ha" in r for r in reasons)
    assert project > first
    tiny = score_detail(listing(title="Terreno rústico", tipo="terreno", area_m2=20000, price=4000), mode="forest")
    assert tiny[0] == 0 and tiny[1][0].endswith("under 5 ha")


def test_a_share_is_skipped_in_every_mode():
    share = listing(title="1/2 de terreno rústico", tipo="terreno", area_m2=300000, price=10000)
    for mode in ("home", "invest", "land", "forest"):
        assert score_detail(share, mode=mode)[0] == 0


# ─── A plot as an investment (the "land" goal) ───────────────────────

def plot(**kw):
    base = {"source": "fotocasa", "country": "ES", "district": "Lugo", "title": "Finca rústica",
            "description": "", "tipo": "terreno", "area_m2": 50000, "price": 15000}
    base.update(kw)
    return base


MARKET = {"ES:Lugo": {"eur_ha": 6000, "plots": 18}, "ES": {"eur_ha": 9000, "plots": 60}}


def test_investment_land_takes_plots_and_sends_homes_elsewhere():
    home = listing(area_m2=100, price=30000)
    sc, reasons = score_detail(home, mode="land")
    assert sc <= 40 and reasons[0] == "a home, not land — see My home or Investment home"
    assert score_detail(plot(), mode="land")[0] > 40


def test_investment_land_ranks_on_the_price_per_hectare_where_it_is():
    """€/ha against what land actually goes for in that district is the point;
    the same plot in a dearer district is the better buy."""
    cheap = plot(area_m2=100000, price=20000, land_market=MARKET)        # €2,000/ha against €6,000
    dear = plot(area_m2=100000, price=60000, land_market=MARKET)         # €6,000/ha: not below it
    s_cheap, r_cheap = score_detail(cheap, mode="land")
    s_dear, r_dear = score_detail(dear, mode="land")
    assert s_cheap > s_dear
    assert any("land here sells for €6,000/ha" in r and "67% below that" in r for r in r_cheap)
    assert any("not below that" in r for r in r_dear)
    # No district figure falls back to the country's; with neither, nothing is
    # claimed rather than compared with the wrong kind of land.
    assert any("plots in Spain" in r for r in score_detail(plot(district="Nowhere", land_market=MARKET),
                                                           mode="land")[1])
    assert not any("land here sells for" in r for r in
                   score_detail(plot(country="PT", district="Guarda", land_market=MARKET), mode="land")[1])


def test_investment_land_does_not_believe_an_impossible_gap():
    absurd = plot(area_m2=100000, price=1000, land_market=MARKET)        # €100/ha against €6,000
    reasons = score_detail(absurd, mode="land")[1]
    assert any("check why" in r for r in reasons)
    assert not any("check why" in r for r in score_detail(plot(area_m2=100000, price=20000,
                                                               land_market=MARKET), mode="land")[1])


def test_investment_land_wants_it_reachable_and_resellable():
    base = plot(area_m2=100000, price=20000, land_market=MARKET)
    near = {**base, "town_distance": {"km": 1.5, "text": "1 km from Lugo"}}
    far = {**base, "town_distance": {"km": 45, "text": "45 km from Lugo"}}
    assert score_detail(near, mode="land")[0] > score_detail(far, mode="land")[0]
    landlocked = {**base, "description": "Parcela sin acceso, enclavada."}
    assert score_detail(landlocked, mode="land")[0] < score_detail(base, mode="land")[0]
    building = plot(title="Terreno urbano en Lugo", tipo="suelo", area_m2=2000, price=20000,
                    land_market=MARKET)
    assert any("building land — resells to anyone" in r for r in score_detail(building, mode="land")[1])


def test_forestry_is_upside_on_a_plot_not_the_only_way_it_can_rank():
    """A 2 ha plot is too small for forestry but can still be a good buy; a big
    one says what a project on it would return, and scores a little higher."""
    small = plot(country="PT", district="Guarda", area_m2=20000, price=2000, land_market=MARKET)
    assert score_detail(small, mode="land")[0] > 40
    assert score_detail(small, mode="forest")[0] == 0

    big = plot(country="PT", district="Guarda", title="Terreno rústico florestal", area_m2=400000,
               price=40000, land_market=MARKET,
               climate={"heat": {"today": 24, "ssp245_2081-2100": 28}, "water_km": 0.2})
    sc, reasons = score_detail(big, mode="land")
    assert any("forestry on it would return" in r and "see Forestry" in r for r in reasons)
    assert sc > score_detail({**big, "climate": {"heat": {"today": 33, "ssp245_2081-2100": 37},
                                                 "stress": {"stress_2080": 4}}}, mode="land")[0]


def test_what_land_goes_for_is_the_scanners_own_asking_prices():
    import land_prices
    rows = [("ES", "Lugo", "Finca rústica", "terreno", 100000, 60000),      # €6,000/ha
            ("ES", "Lugo", "Finca rústica", "terreno", 200000, 80000),      # €4,000/ha
            ("ES", "Lugo", "Finca rústica", "terreno", 100000, 50000),      # €5,000/ha
            ("ES", "Lugo", "Finca rústica", "terreno", 100000, 30000),      # €3,000/ha
            ("ES", "Lugo", "Finca rústica", "terreno", 100000, 70000),      # €7,000/ha
            ("ES", "Ourense", "Finca rústica", "terreno", 100000, 10000),
            ("ES", "Lugo", "Casa en Foz", "moradia", 120, 90000),           # a home: not land
            ("ES", "Lugo", "Parcela", "terreno", 400, 20000)]               # too small to price by the hectare
    index = land_prices.observed_index(rows)
    assert index["ES:Lugo"] == {"eur_ha": 5000, "plots": 5}
    assert "ES:Ourense" not in index                                        # one plot is not a market
    assert index["ES"]["plots"] == 6                                        # the country has enough
    found = land_prices.observed_value({"country": "ES", "district": "Lugo", "land_market": index})
    assert found["eur_ha"] == 5000 and "the median of 5 plots in Lugo" in found["label"]
    # No district figure: the country's, said as such.
    country = land_prices.observed_value({"country": "ES", "district": "Nowhere", "land_market": index})
    assert "plots in Spain" in country["label"]
    assert land_prices.observed_value({"country": "PT", "land_market": index}) is None


def test_woodland_is_compared_with_the_official_forest_price_where_there_is_one():
    import land_prices
    landes = {"country": "FR", "district": "40", "land_market": {"FR": {"eur_ha": 7000, "plots": 30}}}
    assert land_prices.land_value(landes, "Forêt de pins maritimes")["eur_ha"] == 3310
    # Not woodland, or no official figure: the scanner's own asking prices.
    assert land_prices.land_value(landes, "Terre agricole")["label"].startswith("asking prices")
    assert land_prices.land_value({"country": "ES", "district": "Lugo"}, "Bosque") is None


def test_cheap_rustic_land_is_not_doubtful_for_forestry():
    cheap = listing(title="Finca rústica", tipo="terreno", area_m2=1000000, price=15000)     # €150/ha
    assert not any("doubtful" in r for r in score_detail(cheap, mode="forest")[1])
    absurd = listing(title="Finca rústica", tipo="terreno", area_m2=130000, price=100)        # €8/ha
    assert any("doubtful" in r for r in score_detail(absurd, mode="forest")[1])


# ─── One budget per goal, and alerts that follow the goal ────────────

def test_a_scan_keeps_what_any_goal_could_buy_and_each_tab_caps_its_own():
    from common import invest_max_price, land_max_price, mode_max_price, scrape_max_price
    cfg = {"max_price": 60000, "invest_max_price": 150000, "land_max_price": 120000}
    # A €150,000 house used to be thrown away at scrape time by the home budget.
    assert scrape_max_price(cfg, cfg["max_price"]) == 150000
    assert [mode_max_price(cfg, m, cfg["max_price"]) for m in ("home", "invest", "land", "forest")] \
        == [60000, 150000, 120000, 120000]
    # Never below the house budget, whatever is configured.
    assert land_max_price({"land_max_price": 10000}, 60000) == 60000
    assert invest_max_price({}, 200000) == 200000


def test_the_scan_asks_the_sources_for_the_highest_of_the_budgets(monkeypatch, db):
    """A plot or a buy-to-let above the home budget must still be scraped."""
    import pipeline
    import sources
    asked = []
    source = sources.Source("fake", "PT", lambda db, max_price=0, **kw: asked.append(max_price) or 0)
    monkeypatch.setattr(sources, "REGISTRY", {"fake": source})
    monkeypatch.setattr(sources, "load_all", lambda: {"fake": source})
    monkeypatch.setattr(pipeline, "build_report", lambda db, cfg: None)
    pipeline.run_scan(source_names=["fake"], cfg={"max_price": 40000, "invest_max_price": 150000,
                                                  "land_max_price": 100000},
                      report=False, alerts=False, db=db)
    assert asked == [150000]


def test_alerts_judge_a_listing_by_the_goal_it_suits(db, add, monkeypatch):
    """A flat worth letting and a cheap plot used to be scored as somewhere to
    live, fall under the minimum score, and never be alerted."""
    import config
    from db import load_best
    monkeypatch.setattr(config, "load_config", lambda: config.DEFAULTS)
    add("eleiloes", "p1", title="Prédio rústico com olival", tipo="terreno_rustico",
        description="Terreno agrícola que confronta com o rio.", area_m2=80000, price=12000)
    add("eleiloes", "h1", title="Moradia T3", tipo="moradia", description="Em bom estado. No centro da vila.",
        concelho="Guarda", area_m2=120, price=30000)
    best = {it["id"]: it for it in load_best(db, filters={"min_score": 45})}
    assert best["eleiloes:p1"]["mode"] == "land" and best["eleiloes:p1"]["mode_label"] == "Investment land"
    assert best["eleiloes:h1"]["mode"] == "home"
    # On the home goal alone the plot is not shown at all.
    from db import load_listings
    assert "eleiloes:p1" not in {it["id"] for it in load_listings(db, filters={"min_score": 45})}


def test_a_listing_over_its_goals_budget_is_not_alerted(db, add, monkeypatch):
    import config
    from db import load_best
    cfg = {**config.DEFAULTS, "max_price": 50000, "invest_max_price": 50000, "land_max_price": 50000}
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    add("eleiloes", "dear", title="Moradia T3", tipo="moradia", description="Em bom estado. No centro da vila.",
        concelho="Guarda", area_m2=120, price=80000)
    assert [it["id"] for it in load_best(db, filters={"min_score": 0})] == []


# ─── A home as an investment: the money, after what it costs ─────────

def test_investment_counts_the_taxes_fees_and_work_not_the_bare_price():
    """A €30,000 flat needing a rebuild is not €30,000: the gap to the local
    price is measured on what it really takes to own it."""
    sound = listing(concelho="Lugo", district="Lugo", area_m2=100, price=30000,
                    description="Piso reformado, en buen estado")
    ruin = listing(concelho="Lugo", district="Lugo", area_m2=100, price=30000,
                   description="Piso para reforma integral, en mal estado")
    s_sound, r_sound = score_detail(sound, mode="invest")
    s_ruin, r_ruin = score_detail(ruin, mode="invest")
    assert any("all-in (price, taxes and fees, and the work it needs)" in r for r in r_sound)
    assert any("below local prices all-in" in r for r in r_sound)
    assert s_sound > s_ruin


def test_investment_yield_is_net_of_running_costs_and_empty_months():
    import costs
    flat = {"source": "fotocasa", "country": "ES", "concelho": "Lugo", "district": "Lugo", "kind": "home",
            "title": "Piso", "description": "Piso reformado", "area_m2": 80, "price": 40000}
    rent = costs.estimate(flat)["rent"]
    assert rent["net_yield_pct"] < rent["yield_pct"]
    assert round(rent["yield_pct"] * costs.NET_RENT_SHARE, 1) == rent["net_yield_pct"]
    assert "empty months" in rent["note"]
    reasons = score_detail(flat, mode="invest")[1]
    assert any("a year net of running costs and empty months" in r for r in reasons)


def test_a_tenant_costs_less_when_the_rent_can_be_worked_out():
    """Letting it is the point: a tenant paying is not the same problem as a
    listing that only says it is occupied."""
    from scoring import INVEST_TENANT_UNKNOWN, INVEST_TENANT_WITH_RENT
    assert INVEST_TENANT_WITH_RENT > INVEST_TENANT_UNKNOWN
    known = listing(concelho="Lugo", district="Lugo", area_m2=80, price=40000,
                    description="Piso reformado, actualmente arrendado.")
    unknown = listing(concelho="Nowhere", district="Nowhere", area_m2=80, price=40000,
                      description="Piso reformado, actualmente arrendado.")
    assert any("rent from day one" in r for r in score_detail(known, mode="invest")[1])
    assert any("no rent can be worked out" in r for r in score_detail(unknown, mode="invest")[1])


def test_each_goal_tells_the_ai_check_its_own_wish():
    from scoring import MODES, buyer_priorities
    targets = {"rural_min_m2": 20000, "rural_max_eur_m2": 0.3}
    home, invest, land, forest = (buyer_priorities(targets, m) for m in MODES)
    assert "to live in" in home and "not a plot" in home and "swim" in home
    assert "all-in" in invest or "transfer tax" in invest
    assert "net rental yield" in invest and "live in" not in invest.split(",")[0]
    assert "at least 20,000 m²" in land and "€0.30/m²" in land and "upside" in land
    assert "forestry project" in forest and "10 ha" in forest
    assert len({home, invest, land, forest}) == 4


def test_investment_does_not_believe_an_impossible_discount(monkeypatch):
    plausible = listing(concelho="Ourense", district="Ourense", area_m2=100, price=60000,
                        description="Casa en buen estado")
    absurd = listing(concelho="Ourense", district="Ourense", area_m2=1000, price=5000,
                     description="Casa en buen estado")
    reasons = score_detail(absurd, mode="invest")[1]
    assert any("check why" in r for r in reasons)
    assert not any("check why" in r for r in score_detail(plausible, mode="invest")[1])
    import scoring
    penalised = score_detail(absurd, mode="invest")[0]
    monkeypatch.setattr(scoring, "INVEST_TOO_CHEAP", 0)
    assert penalised == score_detail(absurd, mode="invest")[0] - 15


def test_a_cheap_home_on_the_croatian_coast_is_doubtful():
    from scoring import score
    villa = {"source": "indexoglasi", "country": "HR", "district": "Dubrovačko-neretvanska", "tipo": "house",
             "title": "Vila prvi red uz more", "description": "", "area_m2": 200, "price": 10000}
    assert any("price doubtful" in r for r in score(villa)[1])
    inland = {**villa, "district": "Osječko-baranjska", "title": "Kuća"}
    assert not any("price doubtful" in r for r in score(inland)[1])


def test_the_description_can_say_the_house_is_elsewhere():
    import geo
    house = {"country": "ES", "concelho": "Grado", "district": "Asturias", "title": "Casa en Grado",
             "description": "tejado parte caida, buena oportunidad esta em horcajo medianero"}
    assert geo.stated_town(house) == "Horcajo Medianero"
    towns = {"ES:grado": {"name": "Grado", "lat": 43.39, "lon": -6.07},
             "ES:horcajo medianero": {"name": "Horcajo Medianero", "lat": 40.62, "lon": -5.41}}
    conflict = geo.title_town_conflict(house, towns)
    assert conflict and conflict["where"] == "description" and conflict["km"] > 200
    assert geo.stated_town({"description": "situada en el centro del pueblo"}) is None


def test_the_photo_model_also_reads_the_description(monkeypatch):
    import photos

    class Looker:
        name, model = "ollama", "test"

        def look(self, urls, prompt):
            self.prompt = prompt
            return {"condition": "heavy", "confidence": "high", "notes": "", "shows_house": True}

    monkeypatch.setattr(photos, "photo_urls", lambda item: ["http://x/1.jpg"])
    looker = Looker()
    photos.check_photos(looker, {"title": "Casa", "description": "tejado parte caida"})
    assert "tejado parte caida" in looker.prompt


def test_forestry_values_the_crops_the_climate_allows():
    import forestry
    atlantic = {"heat": {"today": 24, "ssp245_2081-2100": 28}, "water_km": 0.2}
    crops = [o["crop"] for o in forestry.options(atlantic, "FR", 20)]
    assert "poplar" in crops and "Douglas fir" in crops
    hot = {"heat": {"today": 33, "ssp245_2081-2100": 37}, "stress": {"stress_2080": 4}}
    names = [o["crop"] for o in forestry.options(hot, "ES", 20)]
    assert "Douglas fir" not in names and "chestnut" not in names
    assert forestry.growing("Herdade com montado de sobro") == {"cork oak"}
    planted = forestry.options(hot, "PT", 20)
    owned = forestry.options(hot, "PT", 20, existing={"cork oak"})
    value = lambda opts: next(o["eur_ha_year"] for o in opts if o["crop"] == "cork oak")  # noqa: E731
    assert value(owned) > value(planted)


def test_a_crop_stops_earning_when_the_climate_passes_its_limit_and_fire_adds_up():
    import forestry
    warming = {"today": 29, "ssp245_2061-2080": 33, "ssp245_2081-2100": 35}
    assert 2030 <= forestry.heat_limit_year(warming, 31) <= 2035
    assert forestry.heat_limit_year({"today": 24, "ssp245_2081-2100": 27}, 30) is None
    pine = lambda c: next(o for o in forestry.options(c, "PT", 20) if o["crop"] == "maritime pine")  # noqa: E731
    calm = {"heat": {"today": 24, "ssp245_2081-2100": 28}}
    burning = {**calm, "fire_danger": {"high_days_2090": 70}, "fire": {"count": 3}}
    assert pine(burning)["eur_ha_year"] < pine(calm)["eur_ha_year"]
    assert any("chance of losing it" in x for x in pine(burning)["limits"])
    assert "maritime pine" not in [o["crop"] for o in forestry.options({"heat": warming}, "PT", 20)]


def test_standing_timber_worth_more_than_the_price_is_flagged():
    import forestry
    assert forestry.standing_volume("Forêt de 20 ha, volume sur pied estimé à 2 400 m3") == 2400
    assert forestry.standing_volume("Finca con 1.500 metros cúbicos de madera") == 1500
    assert forestry.standing_volume("casa de 120 m3 de volumen") is None
    timber = forestry.standing_timber("Kopējais mežaudzes krājas apjoms ir 1632 m³.", "LV", 12.35)
    assert timber and 75000 < timber["eur"] < 80000 and "LVM" in timber["label"]
    from scoring import score_detail
    plot = {"source": "safer", "country": "FR", "district": "40", "tipo": "terreno", "title": "Forêt 20 ha",
            "description": "Forêt de pins, volume sur pied 2 000 m3.", "area_m2": 200000, "price": 60000}
    _, reasons = score_detail(plot, mode="forest")
    assert any("land comes free" in r for r in reasons)


def test_eu_trees4f_decides_which_crops_still_suit_the_place():
    import forestry
    ok = {p: True for p in forestry.PERIODS}
    lost = {**ok, "rcp45_fut2065": False, "rcp45_fut2095": False}
    trees = {"Quercus_suber": lost, "Pinus_pinea": ok, "Quercus_robur": ok, "Quercus_ilex": lost}
    hot = {"heat": {"today": 33, "ssp245_2081-2100": 37}}      # the heat rule alone would drop stone pine
    crops = [o["crop"] for o in forestry.options(hot, "PT", 20, trees=trees)]
    assert "stone pine" in crops and "cork oak" not in crops
    assert "native mixed forest" not in crops                   # only one native species still suits it
    assert forestry.species_fit(trees, "Douglas fir") is None    # not in EU-Trees4F: the heat rule decides


def test_map_and_climate_lookups_take_the_best_of_every_tab_in_turn(monkeypatch):
    import db
    import pipeline
    lists = {"home": [{"id": "h1", "score": 90}, {"id": "both", "score": 80}],
             "invest": [{"id": "both", "score": 70}],
             "land": [{"id": "l1", "score": 75}],
             "forest": [{"id": "f1", "score": 85}, {"id": "f2", "score": 60}]}
    monkeypatch.setattr(db, "load_listings", lambda conn, filters=None, mode="home": lists[mode])
    assert [it["id"] for it in pipeline.enrich_order(None, {})] == ["h1", "both", "l1", "f1", "f2"]


def test_site_check_queue_puts_forestry_ahead_of_other_land(monkeypatch):
    import db
    import pipeline
    lists = {"home": [], "invest": [],
             "land": [{"id": "l1", "score": 90, "rank": 90}, {"id": "both", "score": 70, "rank": 70}],
             "forest": [{"id": "f1", "score": 80, "rank": 80}, {"id": "both", "score": 85, "rank": 85}]}
    monkeypatch.setattr(db, "load_listings", lambda conn, filters=None, mode="home": lists[mode])
    assert [it["id"] for it in pipeline._plots_for_site_check(None, {})] == ["both", "f1", "l1"]


def test_forest_return_counts_timber_once_and_the_crop_every_year():
    from scoring import forest_return
    assert forest_return(100000, 20, {"eur_ha_year": 100}, None) == 0.02
    assert round(forest_return(100000, 20, {"eur_ha_year": 100}, {"eur": 150000}), 3) == 0.17
    assert forest_return(0, 20, {"eur_ha_year": 100}, None) is None


def test_french_forest_land_is_compared_with_the_official_regional_price():
    import land_prices
    from scoring import forest_return
    landes = {"country": "FR", "district": "40"}
    assert land_prices.forest_value(landes)["eur_ha"] == 3310
    assert land_prices.forest_value({"country": "ES", "district": "Lugo"}) is None
    # 30 ha in the Landes for €50,000: worth ~€99,300, a ~€49k gain counted once
    assert round(forest_return(50000, 30, None, None, land_gain=49300), 3) == 0.099


def test_placeholder_land_prices_and_dead_zones_drop_in_forestry():
    from scoring import score_detail
    plot = {"source": "fotocasa", "country": "ES", "tipo": "terreno", "title": "Finca rústica",
            "description": "", "area_m2": 150000, "price": 999}
    _, reasons = score_detail(plot, mode="forest")
    assert any("price doubtful" in r for r in reasons)


def test_latvian_ads_say_how_old_the_forest_is():
    import forestry
    s = forestry.stand("Pārdod mežu, priede un egle, pieaugusi audze, gatava galvenajai cirtei.")
    assert s["mature"] and not s["young"] and s["species"] == ["pine", "spruce"]
    est = forestry.standing_timber("Pieaugusi audze, priede.", "LV", 20)
    assert est["estimated"] and est["m3"] == 20 * forestry.LV_MEAN_M3_HA
    young = forestry.standing_timber("Jaunaudze, krājums 500 m3.", "LV", 10)
    full = forestry.standing_timber("Krājums 500 m3.", "LV", 10)
    assert young["young"] and young["eur"] < full["eur"] * 0.6
    assert forestry.standing_timber("Pārdod cirsmu, krājums 800 m3", "LV", 5)["rights_only"]


def test_mature_cork_is_valued_at_the_official_producer_price():
    import forestry
    assert round(forestry.cork_net_eur_kg(), 2) == 2.38
    atlantic = {"heat": {"today": 24, "ssp245_2081-2100": 28}}
    cork = next(o for o in forestry.options(atlantic, "PT", 20, existing={"cork oak"}) if o["crop"] == "cork oak")
    assert forestry.CORK_SOURCE in cork["sources"]


def test_nut_and_cone_crops_use_official_producer_prices():
    import forestry
    atlantic = {"heat": {"today": 24, "ssp245_2081-2100": 28}, "water_km": 0.2}
    chestnut = next(o for o in forestry.options(atlantic, "PT", 20) if o["crop"] == "chestnut")
    assert any("CCDR-N" in src for src in chestnut["sources"])
    assert "carob" not in [o["crop"] for o in forestry.options(atlantic, "LV", 20)]


def test_no_return_is_claimed_for_a_listing_that_must_be_checked_first():
    from scoring import score_detail
    plot = {"source": "fotocasa", "country": "ES", "tipo": "terreno", "title": "Terreno", "description": "",
            "area_m2": 130000, "price": 100}
    _, reasons = score_detail(plot, mode="forest")
    assert not any(r.startswith("return ≈") for r in reasons)
    import land_prices
    assert land_prices.forest_value({"country": "FR", "district": "2A"}) is None


def test_forestry_only_looks_at_portugal_spain_france_and_benelux():
    from scoring import score_detail
    forest = {"source": "sslv", "country": "LV", "tipo": "terreno", "title": "Mežs", "description": "",
              "area_m2": 300000, "price": 60000}
    score, reasons = score_detail(forest, mode="forest")
    assert score == 0 and "outside the forestry countries" in reasons[0]
    score, _ = score_detail({**forest, "source": "safer", "country": "FR", "district": "40"}, mode="forest")
    assert score > 0


def test_a_bank_selling_only_its_undivided_share_is_skipped():
    from scoring import score_detail
    plot = {"source": "fotocasa", "country": "ES", "tipo": "terreno", "title": "Suelo rústico",
            "description": "La parte vendedora es propietaria de una participación indivisa del Inmueble.",
            "area_m2": 2845361, "price": 99000}
    assert score_detail(plot, mode="forest")[0] == 0


def test_the_ad_itself_says_what_forest_land_is_worth():
    from scoring import score_detail
    base = {"source": "bienici", "country": "FR", "district": "73", "tipo": "terreno", "title": "Terrain boisé – forêt",
            "area_m2": 406000, "price": 50000}
    good = {**base, "description": "40 hectares de forêt de résineux, desserte par route forestière."}
    poor = {**base, "description": "Taillis de feuillus en qualité de bois de chauffage. Pente de 70 à 80 %. "
                                   "Parcelles non délimitées, en zone Natura 2000."}
    s_good, _ = score_detail(good, mode="forest")
    s_poor, reasons = score_detail(poor, mode="forest")
    assert s_poor < s_good - 20
    assert any(r.startswith("steep (80% slope)") for r in reasons)
    assert any("firewood" in r for r in reasons) and any("protected area" in r for r in reasons)


def test_an_orchard_is_not_proposed_where_a_wood_would_have_to_be_cleared():
    import forestry
    atlantic = {"heat": {"today": 24, "ssp245_2081-2100": 28}, "water_km": 0.2}
    assert "chestnut" in [o["crop"] for o in forestry.options(atlantic, "FR", 20)]
    assert "chestnut" not in [o["crop"] for o in forestry.options(atlantic, "FR", 20, wooded=True)]


def test_the_return_is_on_the_price_plus_buying_costs():
    from scoring import buying_costs
    share, label = buying_costs({"country": "FR"}, "Magnifique terrain (9.24 % d'honoraires TTC à la charge de l'acquéreur.)")
    from costs import FR_DMTO_5
    assert abs(share - (FR_DMTO_5 + 0.02 + 0.0924)) < 1e-9 and "agent fee paid by the buyer" in label
    assert buying_costs({"country": "PT"}, "")[0] == 0.058


def test_planting_costs_come_from_the_caof_matrix():
    import forestry
    assert forestry.planting_cost("conifer", 20) == (1464 + 2270) / 2 + 1100 * 0.40
    assert forestry.planting_cost("conifer", 20, slope_pct=5) == 1464 + 1100 * 0.40      # flat: CAOF's easy cost
    assert forestry.planting_cost("conifer", 20, slope_pct=40) == 2270 + 1100 * 0.40     # steep: the hard cost
    assert forestry.planting_cost("conifer", 5) > forestry.planting_cost("conifer", 20)   # +3% a hectare under 10


def test_a_stored_site_check_moves_the_forestry_score():
    import json
    from scoring import score_detail
    base = {"source": "fotocasa", "country": "PT", "tipo": "terreno", "title": "Terreno rústico", "description": "",
            "area_m2": 300000, "price": 60000}
    steep = {**base, "raw_json": json.dumps({"site_check": {"v": 1, "exact": False, "cable_share": 0.5,
                                                             "winch_share": 0.3, "track_m": 900}})}
    montado = {**base, "raw_json": json.dumps({"site_check": {"v": 1, "exact": False, "cable_share": 0.0,
                                                               "winch_share": 0.0, "montado": 0.8,
                                                               "forest_share": 0.9}})}
    s_base, _ = score_detail(base, mode="forest")
    s_steep, reasons = score_detail(steep, mode="forest")
    assert s_steep <= s_base - 15 and any("cable yarding" in r for r in reasons)
    _, reasons = score_detail(montado, mode="forest")
    assert any("80% montado" in r for r in reasons)


def test_eucalyptus_and_protected_land_cost_the_land_and_forestry_scores():
    """Eucalyptus is fire-prone and new planting is restricted in Portugal;
    Natura from the notice or a stored site check is a permit constraint, not
    a silent miss. A map miss still claims nothing."""
    import json
    pine = plot(title="Terreno florestal de pinhal", description="Pinhal bravo",
                area_m2=300000, price=30000)
    gum = plot(title="Eucaliptal", description="Povoamento de eucalipto",
               area_m2=300000, price=30000)
    s_pine, _ = score_detail(pine, mode="forest")
    s_gum, gum_reasons = score_detail(gum, mode="forest")
    assert s_gum < s_pine
    assert any("eucalyptus" in r for r in gum_reasons)
    assert any("eucalyptus" in r for r in score_detail(gum, mode="land")[1])

    natura = plot(title="Finca rústica", description="Parcela en Rede Natura 2000",
                  area_m2=300000, price=30000)
    assert any("protected area" in r for r in score_detail(natura, mode="land")[1])
    assert any("protected area" in r for r in score_detail(natura, mode="forest")[1])
    ren = plot(title="Terreno rústico", description="Inserido na Reserva Ecológica Nacional",
               area_m2=300000, price=30000)
    assert any("protected area" in r for r in score_detail(ren, mode="land")[1])

    mapped = plot(title="Terreno rústico", description="", area_m2=300000, price=30000,
                  raw_json=json.dumps({"site_check": {"v": 1, "exact": True,
                                                      "protected": ["Natura 2000 PTCON0001: Serra"]}}))
    _, mapped_reasons = score_detail(mapped, mode="forest")
    assert any("Natura 2000 PTCON0001" in r and "site check" in r for r in mapped_reasons)
    assert score_detail(mapped, mode="forest")[0] < score_detail(pine, mode="forest")[0]
    gum_map = plot(title="Terreno rústico", description="Pinhal", area_m2=300000, price=30000,
                   raw_json=json.dumps({"site_check": {"v": 1, "exact": True, "eucalyptus": 0.7}}))
    assert any("70% eucalyptus" in r for r in score_detail(gum_map, mode="land")[1])
    assert score_detail(gum_map, mode="forest")[0] < score_detail(pine, mode="forest")[0]


def test_investment_uses_the_likely_close_not_the_base(monkeypatch):
    """A live auction's base is not what you pay: the likely close drives all-in."""
    import scoring
    monkeypatch.setattr(scoring, "local_price", lambda item: (1000, "test €/m²"))
    house = listing(area_m2=80, price=20000, description="Casa en buen estado")
    dearer, reasons = score_detail(
        {**house, "predicted_final": {"price": 80000, "text": "likely to close around €80,000"}},
        mode="invest")
    assert dearer < score_detail(house, mode="invest")[0]
    assert "likely to close around €80,000" in reasons


def test_generic_words_in_a_title_are_not_read_as_far_away_towns():
    import geo
    item = {"country": "ES", "concelho": "Gizaburuaga", "title": "Terreno en Gizaburuaga, Barrio Arteaga",
            "raw_json": '{"lat": 43.33, "lon": -2.53}'}
    towns = {"ES:gizaburuaga": {"name": "Gizaburuaga", "lat": 43.33, "lon": -2.53},
             "ES:barrio": {"name": "Barrio (Vilarmaior)", "lat": 40.4, "lon": -7.4}}
    assert geo.title_town_conflict(item, towns) is None


def test_unreachable_land_gets_no_timber_return():
    import json
    from scoring import score_detail
    plot = {"source": "bienici", "country": "FR", "district": "73", "tipo": "terreno", "title": "Forêt",
            "description": "", "area_m2": 400000, "price": 50000,
            "raw_json": json.dumps({"site_check": {"v": 1, "exact": False, "cable_share": 0.5, "winch_share": 0.4,
                                                    "inaccessible": 0.88}})}
    _, reasons = score_detail(plot, mode="forest")
    assert not any("meets the 20% goal" in r for r in reasons)
