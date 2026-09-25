import pytest

from datetime import datetime, timedelta, timezone

from scoring import categorize, market_value_estimate, score, score_detail


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


def test_any_share_is_a_fraction_but_not_dates_or_case_numbers():
    from scoring import is_fractional_share as share
    assert share("29/84 DE IMOVEL EM GASIELAS, TOURIM")
    assert share("Venda de parte de bem(4986/100000)-prédio urbano")
    assert share("Direito de 3/32 em três prédios")
    assert not share("Moradia penhorada em 01/2025")
    assert not share("Online Auction Loja · Rio de Mouro, Sintra - 115.000,00 € Portugal DMI-1039/2026")
    assert not share("Citius judicial sale 1436/19.9T8VRL.1")
    assert not share("Processo 12/18.0T8PRT, moradia")
    assert not share("Moradia T3 no nº 12/14 da Rua Direita")          # house numbers
    assert not share("Loja na Rua do Sol, n.º 3/5")
    assert share("1 / 2 (Um Meio) Prédio Urbano")


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
    _, reasons = score(item(title="Prédio", source="citius", price=12000))
    assert reasons.count("no minimum bid") == 1
    _, no_price = score(item(title="Prédio", source="citius", price=None))
    assert "no price — you set your offer" in no_price and "no minimum bid" not in no_price
    _, low_min = score(item(title="Prédio", source="citius", price=12000, min_price=200))
    assert "min bid only €200" in low_min and "no minimum bid" not in low_min


def test_no_price_on_an_offer_sale_is_a_chance():
    # Carta fechada / negociação particular: you name the price, so a missing
    # price is an opening, not a gap.
    home = "Prédio urbano, casa de habitação"
    sealed, r_sealed = score(item(title=home, price=0, source="citius",
                                  description="Venda mediante proposta em carta fechada"))
    private, r_private = score(item(title=home, price=None, source="citius",
                                    description="Venda por negociação particular"))
    assert "no price — you set your offer" in r_sealed and "sealed-bid (carta fechada)" in r_sealed
    assert "no price — you set your offer" in r_private
    assert sealed == 100 and private >= 80
    # an online listing whose price we simply did not read gets no such bonus
    _, r_online = score(item(title="Moradia", source="leilosoc"))
    assert "no price — you set your offer" not in r_online


def test_price_outweighs_how_the_court_sells():
    court = dict(source="citius", description="Venda mediante proposta em carta fechada")
    dear_court, r_dear = score(item(title="Fracção - habitação no 3º andar", price=97500, **court))
    cheap_online, _ = score(item(title="Moradia", price=12000, concelho="Guarda"))
    assert "€97,500 — not a low price" in r_dear
    assert dear_court < 85 < cheap_online


def test_half_shares_and_furniture():
    for title in ("Metade da fracção autónoma identificada pela letra A",
                  "Metade ( da habitação do 2º andar direito"):
        sc, reasons = score(item(title=title, price=46750))
        assert sc == 0 and "fractional" in reasons[0], title
    from scoring import property_kind as kind
    assert kind(item(title="Mobiliário de habitação", price=1270)) == "other"
    assert kind(item(title="Móveis de Habitação")) == "other"
    assert kind(item(title="Mobília de casa")) == "other"
    assert kind(item(title="Moradia T3 com mobiliário")) == "home"   # names the building first
    assert kind(item(title="Casa com mobília")) == "home"


def test_what_the_portal_says_is_not_property():
    # e-leilões typed this machine as a flat (subtype 22 of equipment, type 5).
    from scoring import property_kind as kind
    machine = item(title="Máquina de calcanheiras", tipo="equipamento", price=4250)
    assert categorize(machine) == "outros" and kind(machine) == "other"
    assert categorize(item(title="Mobília de casa", tipo="mobiliario")) == "outros"


def test_eleiloes_types_depend_on_the_main_type():
    from sources.pt import eleiloes_tipo
    assert eleiloes_tipo({"tipoId": 1, "subtipoId": 2}) == "moradia"       # was "loja/escritorio"
    assert eleiloes_tipo({"tipoId": 1, "subtipoId": 1}) == "apartamento"
    assert eleiloes_tipo({"tipoId": 1, "subtipoId": 27}) == "terreno_rustico"
    assert eleiloes_tipo({"tipoId": 5, "subtipoId": 22}) == "equipamento"  # not an apartment
    assert eleiloes_tipo({"tipoId": 4, "subtipoId": 16}) == "mobiliario"
    assert eleiloes_tipo({"tipoId": 6, "subtipoId": 37}) == "direitos"
    assert eleiloes_tipo({"subtipoId": 21}) == "moradia"                   # no tipoId: as before


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
    assert "medium rural plot (3.0 ha)" in r_big and "very cheap land (€0.15/m²)" in r_big
    assert "dear for rural land (€1.50/m²)" in r_dear
    assert "rural plot, size unknown" in r_unknown
    assert big_cheap > unknown > small and big_cheap > big_dear
    # the limits come from Settings (config filters)
    _, relaxed = score(item(title="Prédio rústico", area_m2=3000, price=1500),
                       targets={"rural_min_m2": 2000, "rural_max_eur_m2": 1})
    assert "medium rural plot (3 000 m²)" in relaxed and "very cheap land (€0.50/m²)" in relaxed


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


# ─── The owner's own examples (Sept 2026): this order, and the rest below it ───

def _ex(**kw):
    return item(**kw)


OWNER_WANTS = [   # best first
    _ex(title="Moradia T3 em bom estado", description="Remodelada, no centro da vila.",
        concelho="Guarda", area_m2=120, price=45000),                  # pristine, great place, well under market
    _ex(title="Prédio rústico com 8 ha", description="Terreno agrícola que confronta com o rio.",
        area_m2=80000, price=20000),                                  # large farm plot by water, very cheap
    _ex(title="Moradia T2", description="Necessita de obras. No centro da vila.",
        concelho="Guarda", area_m2=100, price=8000),                   # some repairs, dirt cheap, great place
    _ex(title="Terreno rústico", description="Terreno de cultura junto à ribeira.",
        area_m2=20000, price=3000),                                   # medium farm plot by water, dirt cheap
    _ex(title="Moradia em bom estado", description="Casa de habitação.",
        area_m2=100, price=9000),                                     # pristine, dirt cheap, ordinary place
]
OWNER_DOES_NOT_WANT = {
    "small home": _ex(title="Apartamento T0 com 25 m2", description="No centro da vila.",
                      concelho="Guarda", area_m2=25, price=8000),
    "partial home": _ex(title="1/2 de moradia", description="No centro.", area_m2=100, price=8000),
    "heavy repairs": _ex(title="Moradia em ruínas", description="Para reconstruir.", area_m2=100, price=5000),
    "expensive home": _ex(title="Moradia em bom estado", description="No centro da vila.",
                          concelho="Guarda", area_m2=120, price=95000),
    "bad location": _ex(title="Moradia em bom estado", description="Lugar isolado, caminho de terra.",
                        area_m2=100, price=9000),
    "small plot": _ex(title="Terreno rústico", description="Junto à ribeira.", area_m2=2000, price=500),
    "small building plot": _ex(title="Lote de terreno para construção", area_m2=90, price=3000),
}


def test_the_owners_order():
    from scoring import score_detail
    raws = [score_detail(x)[0] for x in OWNER_WANTS]
    assert raws == sorted(raws, reverse=True), raws
    assert len(set(raws)) == len(raws), raws


def test_what_the_owner_does_not_want_stays_under_the_minimum_score():
    from scoring import score_detail
    worst_wanted = min(score_detail(x)[0] for x in OWNER_WANTS)
    for label, x in OWNER_DOES_NOT_WANT.items():
        sc, reasons = score(x)
        assert sc <= 45 and sc < worst_wanted, (label, sc, reasons)


def test_a_ruin_on_a_big_farm_is_valued_as_land():
    sc, reasons = score(item(title="Quinta com casa em ruínas",
                             description="Prédio misto com 6 ha de terreno agrícola.",
                             area_m2=60000, price=25000))
    assert "ruin on a farm — valued as land" in reasons and "large rural plot (6.0 ha)" in reasons
    assert sc >= 80


def test_water_is_next_to_the_plot_not_a_place_name():
    from scoring import water_nearby
    assert water_nearby("Terreno que confronta com o rio Mondego")
    assert water_nearby("Olival junto à ribeira, com poço")
    assert water_nearby("atravessado por uma linha de água")
    assert water_nearby("Finca rústica junto al río")
    assert not water_nearby("Prédio rústico em Rio Maior")
    assert not water_nearby("Terreno no concelho de Albufeira")
    assert not water_nearby("Moradia na Lagoa, Algarve")


def test_amounts_score_smoothly_not_in_steps():
    from scoring import score_detail

    def raw(**kw):
        return score_detail(item(title="Moradia", concelho="Guarda", **kw))[0]
    # €20,000 scores a little more than €20,001: no ties, no jumps
    assert 0 < raw(price=20000, area_m2=100) - raw(price=20001, area_m2=100) < 0.01
    # one more euro, m², hectare or percent never moves the score by more than a hair
    for lo in range(1000, 99000, 997):
        assert abs(raw(price=lo, area_m2=100) - raw(price=lo + 1, area_m2=100)) < 0.05, lo
    for m2 in range(20, 300, 7):
        assert abs(raw(price=20000, area_m2=m2) - raw(price=20000, area_m2=m2 + 1)) < 3, m2

    def rural(area, price):
        return score_detail(item(title="Prédio rústico", area_m2=area, price=price))[0]
    for ha in range(2000, 120000, 1990):
        assert abs(rural(ha, 10000) - rural(ha + 10, 10000)) < 0.1, ha
    # and cheaper, bigger, further below market is always at least as good
    prices = [raw(price=p, area_m2=100) for p in range(1000, 100000, 500)]
    assert all(a >= b for a, b in zip(prices, prices[1:]))


def test_curve_passes_through_its_points():
    from scoring import curve
    pts = [(0, 10), (10, 0)]
    assert curve(-5, pts) == 10 and curve(5, pts) == 5 and curve(99, pts) == 0


def test_timeshares_are_skipped():
    # Court sales in Benalmádena (Sept 2026): cheap "homes" that were one week a year.
    for title, desc in [
        ("Finca nº 13.623 del Registro de la Propiedad Nº 2 de Benalmádena",
         "que se concreta en el uso y disfrute de forma exclusiva y excluyente de esa finca "
         "(apartamento 501) durante la semana 37 de cada año"),
        ("Finca registral nº 13.649/37 inscrita en el Registro de la Propiedad",
         "Benalmádena, SEMANA SEIS DE CADA AÑO"),
        ("Direito real de habitação periódica - apartamento T1 em Albufeira", ""),
        ("Apartamento em regime de multipropriedade", ""),
        ("Appartement en multipropriété, semaine 12 chaque année", ""),
    ]:
        sc, reasons = score(item(title=title, description=desc, price=4400, source="spain", country="ES"))
        assert sc == 0 and reasons == ["timeshare (some weeks a year) — skip"], title
    from scoring import is_timeshare
    assert not is_timeshare("Moradia T3, visitas durante a semana, das 10h às 12h")
    assert not is_timeshare("Obras de 3 semanas concluídas em 2024")
    assert not is_timeshare("Vivienda en venta, 3 dormitorios, visitas cada semana")


def test_size_written_in_the_text():
    from common import find_area
    assert find_area("Une maison à usage d'habitation d'environ 35,50 m², comprenant") == 35.5
    assert find_area("Prédio com 1.250 m2 de terreno") == 1250
    assert find_area("Herdade com 8 ha") == 80000
    assert find_area("T2 em Lisboa, 3º andar") is None
    # the Le Mans house from the real list: 35 m² is a small home, however cheap
    sc, reasons = score(item(source="france", country="FR", price=6000,
                             title="72Le MansUne maison à usage d'habitationd'environ 35,50 m², comprenant : entrée"))
    assert any(r.startswith("small home") for r in reasons) and sc <= 45


def test_storage_rooms_and_unclear_listings():
    from scoring import property_kind
    storage = item(source="citius", price=9733,
                   title="Fracção Autómoma designada pelas letras ZB, respeitante a arrumos ao nível do sotão")
    assert property_kind(storage) == "other" and score(storage)[0] <= 35
    assert property_kind(item(title="Casa com arrumos e quintal")) == "home"
    sc, reasons = score(item(source="citius", price=1372, title="Artigo urbano 4517, sito no Montoiro"))
    assert "unclear what it is — check" in reasons
    clear, _ = score(item(source="citius", price=1372, title="Moradia sita no Montoiro"))
    assert clear > sc


def test_a_house_sold_with_cheap_land_in_the_same_case(db, add):
    import json
    from db import load_listings
    case = json.dumps({"processo": "366/10.4TBVLN, Juízo de Valença"})
    add("citius", "366104TBVLN", title="Prédio urbano, casa de um pavimento, com área de 142 m2",
        price=7500, raw_json=case)
    add("citius", "366104TBVLN-2", title="Prédio Rústico composto de cultivo, com área de 2760 m2",
        price=500, area_m2=2760, raw_json=case)
    add("citius", "999", title="Moradia com 100 m2", price=7500,
        raw_json=json.dumps({"processo": "9/99.9TBXXX"}))
    add("citius", "999-2", title="Terreno rústico com 5000 m2", price=20000, area_m2=5000,   # not cheap
        raw_json=json.dumps({"processo": "9/99.9TBXXX"}))
    items = {it["id"]: it for it in load_listings(db, include_hidden=True)}
    assert "land in the same case (€500, 2 760 m²)" in items["citius:366104TBVLN"]["reasons"]
    assert not any(r.startswith("land in the same case") for r in items["citius:999"]["reasons"])
    assert items["citius:366104TBVLN"]["rank"] > items["citius:999"]["rank"]


def test_a_home_next_to_water():
    from scoring import score_detail          # both reach 100: compare before the cap
    by_river, reasons = score_detail(item(title="Moradia T2", description="Casa junto ao rio, em bom estado",
                                          price=20000))
    inland, _ = score_detail(item(title="Moradia T2", description="Casa em bom estado", price=20000))
    assert "next to water (junto ao rio)" in reasons and by_river > inland


# ─── Rejected outright (the owner's list of 24 Sept, from real listings) ───────

@pytest.mark.parametrize("title, description, why", [
    ("Prédio urbano (totalmente inacabado) em Paredes", "O imóvel encontra-se totalmente inacabado",
     "rejected: unfinished building"),
    ("Predio rustico, artigo matricial 560, destinado a cultura, pastagem e pinhal",
     "com a área de 1,960000 ha. Não descrio na CRP.", "rejected: not in the land register"),
    ("Nekretnina u vlasništvu ovršenika, kuća i dvorište", "Nekretnina nije slobodna od osoba i stvari.",
     "rejected: occupied"),
    ("Terreno T0, Tabuaço", "Terreno rústico, sito em Tabuaço. Imóvel em venda conjunta com o 331312.",
     "rejected: land only sold together with another lot"),
])
def test_rejected_outright(title, description, why):
    sc, reasons = score(item(title=title, description=description, price=3500, area_m2=900))
    assert why in reasons and sc <= 30, reasons


def test_recovery_is_heavy_work_but_a_house_sold_with_land_is_kept():
    sc, reasons = score(item(title="Moradia em Banda T3", description="Imóvel para recuperação total.", price=23000,
                             area_m2=94))
    assert "needs heavy work (ruin / full rebuild)" in reasons and sc <= 40
    sc, reasons = score(item(title="Moradia Geminada T2", description="Moradia Venda em conjunto com terreno 40725",
                             price=15000, area_m2=144))
    assert "sold together with another lot (its price is not shown)" in reasons and sc > 45


def test_plots_are_not_taken_for_houses():
    from scoring import property_kind as kind
    assert kind(item(title="Lote de terreno destinado a construção de moradia unifamiliar", area_m2=200)) == "urban_plot"
    assert kind(item(title="Terreno T0, Tabuaço", area_m2=924)) == "urban_plot"
    assert kind(item(title="Terreno em Paialvo", description="Terra com oliveira com 3000m2", area_m2=3000)) == "rural_plot"
    assert kind(item(title="Terreno com moradia T3")) == "home"          # a house on land is still a house


def test_lote_moradia_is_a_plot_and_detached_is_not_isolated():
    from scoring import property_kind as kind
    # Montepio via Imobancos (Sept 2026): 28 "Lote Moradia" plots scored 100 as houses.
    lote = item(source="imobancos", title="Lote Moradia, ref: 18159LT 43", tipo="terreno p/ moradia", price=34000,
                area_m2=400, description="lote de terreno com 689 m2, para construção de moradia isolada de 2 Pisos")
    assert kind(lote) == "urban_plot"
    sc, reasons = score(lote)
    assert sc < 90 and not any("below local prices" in r for r in reasons)
    assert kind(item(title="Casa T2", tipo="terreno")) == "home"          # the title names a house
    _, detached = score(item(title="Moradia isolada T3 em bom estado", price=20000))
    _, remote = score(item(title="Casa em lugar isolado", price=20000))
    assert "isolated location" not in detached and "isolated location" in remote


def test_a_house_is_scored_on_how_far_it_really_is_from_town():
    house = dict(title="Moradia T3", tipo="moradia", area_m2=110, price=40000, concelho="Moura")

    def at(km):
        return score(item(**house, town_distance={"km": km, "town": "Moura", "approx": False,
                                                  "text": f"{km} km from Moura"}))

    in_town, edge, far = at(0.5)[0], at(6)[0], at(25)[0]
    assert in_town > edge > far
    assert far <= 60 and "25 km from Moura" in at(25)[1]      # far from everything is isolated
    # The words are only a fallback: with a measured distance they add a little, not 15.
    words = dict(house, description="Moradia no centro da vila")
    guessed, _ = score(item(**words))
    measured, reasons = score(item(**words, town_distance={"km": 0.5, "town": "Moura", "approx": False,
                                                           "text": "0.5 km from Moura"}))
    assert measured > guessed and "0.5 km from Moura" in reasons
    # "Isolated" in the text still decides on its own.
    _, remote = score(item(title="Casa em lugar isolado", price=20000, concelho="Moura",
                           town_distance={"km": 0.5, "town": "Moura", "approx": False,
                                          "text": "0.5 km from Moura"}))
    assert "isolated location" in remote and "0.5 km from Moura" not in remote


def test_a_bid_below_the_minimum_accepted_is_judged_at_the_minimum():
    """e-leilões opens at 50% of the base value but accepts from 85%: the €11,750
    opening bid does not buy a €23,500 house, €19,975 does."""
    from common import price_to_pay
    import costs
    house = {"title": "Moradia em Felgueiras", "description": "Moradia de r/c e 1º andar, 100 m2",
             "price": 23500, "min_price": 19975, "current_bid": 11750, "area_m2": 100,
             "source": "eleiloes", "country": "PT", "concelho": "Resende", "category": "imoveis"}
    assert price_to_pay(house) == 19975
    assert price_to_pay({**house, "current_bid": 21000}) == 21000
    assert price_to_pay({**house, "current_bid": None}) == 19975
    at_floor, reasons = score_detail({**house, "current_bid": 19975})
    assert score_detail(house)[0] == at_floor            # the low bid earns nothing extra
    assert not any("50%" in r for r in reasons)
    est = costs.estimate(house)
    assert est["base"] == 19975 and est["basis"].startswith("the minimum accepted")
