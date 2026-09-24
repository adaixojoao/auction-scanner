from datetime import datetime, timedelta, timezone

from scoring import categorize, market_value_estimate, score


def item(**kw):
    base = {"source": "eleiloes", "country": "PT", "title": "", "description": ""}
    base.update(kw)
    return base


def test_fractional_share_with_spaces_scores_zero():
    # This listing was #2 in the old report with a score of 95.
    sc, reasons = score(item(title="1 / 2 (Um Meio) Prédio Urbano, casas baixas",
                             price=30900, current_bid=15450))
    assert sc == 0 and "fractional" in reasons[0]


def test_dates_in_titles_are_not_fractions():
    sc, _ = score(item(title="Moradia penhorada em 11/2023", price=20000))
    assert sc > 0


def test_vacant_is_not_occupied():
    vacant, r1 = score(item(title="Moradia", description="Imóvel devoluto e desocupado", price=20000))
    occupied, r2 = score(item(title="Moradia", description="Imóvel arrendado", price=20000))
    negated, r3 = score(item(title="Moradia", description="O imóvel não se encontra arrendado", price=20000))
    assert "vacant (devoluto)" in r1 and "occupied/tenanted" not in r1
    assert "occupied/tenanted" in r2
    assert "occupied/tenanted" not in r3
    assert vacant > negated > occupied


def test_casal_is_not_a_house():
    _, reasons = score(item(title="Prédio rústico denominado Fontainhas - Casal do Mato",
                            source="citius"))
    assert "full dwelling" not in reasons


def test_no_minimum_bonus_counted_once():
    _, reasons = score(item(title="Prédio", source="citius", price=None))
    assert "Citius no-minimum court sale" in reasons
    assert "no minimum bid" not in reasons


def test_urgency_works_with_naive_dates():
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat()
    _, reasons = score(item(title="Moradia", price=20000, date_end=soon))
    assert any("urgent" in r for r in reasons)


def test_price_drop_bonus():
    base, _ = score(item(title="Moradia", price=20000))
    dropped, reasons = score(item(title="Moradia", price=20000, price_drop_pct=30))
    assert dropped == base + 10 and any("price cut" in r for r in reasons)


def test_market_value_matches_place_exactly():
    assert market_value_estimate(item(area_m2=100, concelho="Porto")) == 310000
    assert market_value_estimate(item(area_m2=100, concelho="Porto de Mós")) is None
    assert market_value_estimate(item(area_m2=100, concelho="Setúbal")) == 220000
    assert market_value_estimate(item(area_m2=100, concelho="Lisboa (Santa Maria Maior)")) == 420000
    assert market_value_estimate(item(area_m2=100, concelho="a")) is None


def test_categorize():
    assert categorize(item(title="Prédio com auditório")) == "imoveis"
    assert categorize(item(title="Audi A4 2015")) == "outros"
    assert categorize(item(title="Anel em ouro")) == "ouro_joias"
    assert categorize(item(title="Loja", tipo="loja")) == "imoveis"
    assert categorize(item(title="Vivienda en Loja", tipo="inmueble")) == "imoveis"
    assert categorize(item(title="3 dormitorios en Valencia", tipo="Piso")) == "imoveis"
    assert categorize(item(title="Une maison avec jardin", tipo="")) == "imoveis"
    assert categorize(item(title="Terrain à bâtir", tipo="")) == "imoveis"


def test_occupancy_from_the_detail_page_wins():
    import json
    raw = lambda occ: json.dumps({"occupation": occ})
    _, occupied = score(item(title="Vivienda", raw_json=raw("occupied")))
    assert "occupied/tenanted" in occupied
    # the page says vacant, so an "ocupado" elsewhere in the text does not count
    _, vacant = score(item(title="Vivienda", description="antes ocupado", raw_json=raw("vacant")))
    assert "occupied/tenanted" not in vacant and "vacant (devoluto)" in vacant
    _, french = score(item(title="Appartement occupé par le locataire"))
    assert "occupied/tenanted" in french
    _, libre = score(item(title="Maison libre de toute occupation"))
    assert "occupied/tenanted" not in libre and "vacant (devoluto)" in libre


def test_score_bounds():
    sc, _ = score(item(title="Moradia devoluta", source="citius", price=5000, area_m2=200,
                       description="venda por propostas em carta fechada"))
    assert 0 <= sc <= 100


# ─── What we are looking for: homes and plots at ridiculous prices ───────────

def test_what_a_listing_is():
    from scoring import property_kind as kind
    assert kind(item(title="Moradia T3 em Seia")) == "home"
    assert kind(item(title="Apartamento T2")) == "home"
    assert kind(item(title="Loja no rés-do-chão")) == "other"          # not a home because of "rés-do-chão"
    assert kind(item(title="Garagem no piso -1")) == "other"
    assert kind(item(title="Prédio rústico denominado Fontainhas", area_m2=800)) == "rural_plot"
    assert kind(item(title="Terreno para construção")) == "urban_plot"
    assert kind(item(title="Lote 3 - Imóvel")) is None                   # an auction lot, not a building plot
    assert kind(item(title="Terreno", area_m2=20000)) == "rural_plot"
    assert kind(item(title="Terreno", area_m2=600)) == "urban_plot"
    assert kind(item(title="Solar do século XVIII")) is None             # a manor, in Portugal
    assert kind(item(title="Solar en Sevilla", country="ES")) == "urban_plot"
    assert kind(item(title="Prédio urbano", tipo="moradia")) == "home"
    assert kind(item(title="Prédio urbano", description="destinado a habitação")) == "home"
    assert kind(item(title="Imóvel", tipo="loja/escritorio")) == "other"


def test_rural_plots_must_be_big_and_cheap():
    small, r_small = score(item(title="Prédio rústico", area_m2=3000, price=1500))
    big_cheap, r_big = score(item(title="Prédio rústico", area_m2=30000, price=4500))    # €0.15/m²
    big_dear, r_dear = score(item(title="Prédio rústico", area_m2=30000, price=45000))   # €1.50/m²
    unknown, r_unknown = score(item(title="Prédio rústico", price=4500))
    assert "rural plot too small (3 000 m² < 1.0 ha)" in r_small
    assert "big rural plot (3.0 ha)" in r_big and "very cheap land (€0.15/m²)" in r_big
    assert "dear for rural land (€1.50/m²)" in r_dear
    assert "rural plot, size unknown" in r_unknown
    assert big_cheap > unknown > small and big_cheap > big_dear
    # the limits come from Settings (config filters)
    _, relaxed = score(item(title="Prédio rústico", area_m2=3000, price=1500),
                       targets={"rural_min_m2": 2000, "rural_max_eur_m2": 1})
    assert "big rural plot (3 000 m²)" in relaxed and "very cheap land (€0.50/m²)" in relaxed


def test_homes_in_good_places_without_heavy_work():
    base = dict(title="Moradia", price=20000, area_m2=100)
    ruin, r_ruin = score(item(**base, description="Moradia em ruínas, para recuperar"))
    works, r_works = score(item(**base, description="Necessita de obras"))
    good, r_good = score(item(**base, description="Em bom estado de conservação"))
    plain, _ = score(item(**base))
    negated, r_negated = score(item(**base, description="Não necessita de obras"))
    assert "needs heavy work (ruin / full rebuild)" in r_ruin and "needs some work" in r_works
    assert "good condition" in r_good and "needs some work" not in r_negated
    assert good > plain > works > ruin

    isolated, r_iso = score(item(**base, description="Casa em lugar isolado"))
    central, r_central = score(item(**base, description="No centro histórico"))
    town, r_town = score(item(**base, concelho="Guarda"))
    village, r_village = score(item(**base, district="Guarda"))   # a village in the Guarda district
    assert "isolated location" in r_iso and "good location (centro histórico)" in r_central
    assert any(r.startswith("in Guarda") for r in r_town)
    assert not any(r.startswith("in Guarda") for r in r_village)
    assert central > plain > isolated and town > village


def test_land_is_not_priced_like_buildings():
    # A 2 000 m² plot in Porto is not "99% below the local price per m²" of flats.
    _, plot = score(item(title="Terreno para construção", area_m2=2000, concelho="Porto", price=50000))
    _, home = score(item(title="Apartamento", area_m2=80, concelho="Porto", price=50000))
    assert not any("below local prices" in r for r in plot)
    assert any("below local prices" in r for r in home)


def test_shops_and_garages_are_not_the_goal():
    shop, r_shop = score(item(title="Loja comercial", price=10000))
    home, _ = score(item(title="Moradia", price=10000))
    assert "not a home or plot" in r_shop and home - shop >= 30


def test_cheaper_is_better():
    very, r_very = score(item(title="Moradia", price=4000))
    dear, _ = score(item(title="Moradia", price=55000))
    assert "very cheap: €4,000" in r_very and very > dear
    # what you would pay: the current bid, not the base value
    _, r_bid = score(item(title="Moradia", price=40000, current_bid=4500))
    assert "very cheap: €4,500" in r_bid


def test_what_is_not_the_goal_stays_under_the_default_minimum_score():
    # Court-sale bonuses (forced sale, no minimum, sealed bid) used to lift these to 90–100.
    court = dict(source="citius", description="venda por propostas em carta fechada")
    shop, _ = score(item(title="Loja comercial", price=12000, **court))
    small_rural, _ = score(item(title="Prédio rústico - pinhal", price=1500, area_m2=2500, **court))
    ruin, _ = score(item(title="Casa em ruínas", price=4000, area_m2=80, **court))
    assert shop <= 35 and small_rural <= 35 and ruin <= 40
    good, _ = score(item(title="Moradia em bom estado", concelho="Guarda", price=15000, area_m2=90, **court))
    assert good >= 90


def test_the_ai_check_is_told_the_same_goal():
    from scoring import buyer_priorities
    text = buyer_priorities({"rural_min_m2": 20000, "rural_max_eur_m2": 0.3})
    assert "at least 20,000 m²" in text and "€0.30/m²" in text and "heavy work" in text


def test_listings_that_all_reach_100_are_still_ordered():
    from scoring import score_detail
    court = dict(source="citius", description="venda por propostas em carta fechada, em bom estado")
    better = item(title="Moradia", concelho="Guarda", price=4000, area_m2=90, **court)
    good = item(title="Moradia", concelho="Guarda", price=25000, area_m2=90, **court)
    assert score(better)[0] == score(good)[0] == 100
    assert score_detail(better)[0] > score_detail(good)[0] > 100
