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
             "forest": [{"id": "f1", "score": 85}, {"id": "f2", "score": 60}]}
    monkeypatch.setattr(db, "load_listings", lambda conn, filters=None, mode="home": lists[mode])
    assert [it["id"] for it in pipeline.enrich_order(None, {})] == ["h1", "both", "f1", "f2"]


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
    assert est["estimated"] and est["m3"] == 20 * forestry.LV_MATURE_M3_HA
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
