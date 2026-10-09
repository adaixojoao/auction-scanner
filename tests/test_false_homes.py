"""Listings that reached the top as "homes" but were not (checked by hand, 2026-10)."""
from geo import title_town_conflict
from scoring import UNCHECKED_CAP, property_kind, score_detail


def item(**kw):
    base = {"source": "fotocasa", "country": "ES", "title": "", "description": ""}
    base.update(kw)
    return base


def test_barn_under_a_house_title_is_not_a_home():
    for desc in ("GRANGE pour stockage ou ATELIER – GRANGE, toute en pierre, de 89 m2",
                 "Grange de 170m2 avec dépendance de 20m2",
                 "Se vende panera situada en zona soleada"):
        assert property_kind(item(country="FR", title="Maison à Balledent (87290)", tipo="house",
                                  description=desc)) == "other", desc


def test_bare_finca_is_land_but_finca_with_house_stays_home():
    assert property_kind(item(title="Casa en Cambre", tipo="house", area_m2=3774,
                              description="Finca rústica en el ayuntamiento de Cambre.")) == "rural_plot"
    assert property_kind(item(title="Casa en Neda", tipo="house", area_m2=251,
                              description="¡Gran oportunidad! Finca rústica con una vivienda de dos plantas")) == "home"
    assert property_kind(item(title="Casa en Cabana", tipo="house",
                              description="Se vende casa con terreno y dos hórreos")) == "home"


def test_home_for_a_rent_sized_price_is_capped():
    sc, reasons = score_detail(item(source="imovirtual", country="PT", title="Moradia T4 em Porto",
                                    price=1190, area_m2=841))
    assert sc <= UNCHECKED_CAP and any("price doubtful" in r for r in reasons)


def test_land_for_a_few_cents_per_m2_is_capped():
    sc, reasons = score_detail(item(source="aliseda", title="Terreno en Coristanco", tipo="land",
                                    price=3915, area_m2=640000))
    assert sc <= UNCHECKED_CAP and any("price doubtful" in r for r in reasons)


def test_title_town_far_from_the_pin_is_flagged():
    towns = {"PT:covilha": {"name": "Covilhã", "lat": 40.28, "lon": -7.50},
             "PT:povoa de varzim": {"name": "Póvoa de Varzim", "lat": 41.38, "lon": -8.76}}
    listing = {"country": "PT", "title": "2 moradias em Tortosendo - Covilhã",
               "concelho": "Póvoa de Varzim", "lat": 41.38, "lon": -8.76}
    found = title_town_conflict(listing, towns)
    assert found and found["town"] == "Covilhã" and found["km"] > 100
    assert title_town_conflict({**listing, "title": "Moradia na Póvoa de Varzim"}, towns) is None
    sc, reasons = score_detail({**item(country="PT", price=17500, area_m2=84,
                                       title=listing["title"]), "place_conflict": found})
    assert sc <= UNCHECKED_CAP and any("check the location" in r for r in reasons)


def test_share_in_the_description_is_skipped():
    for desc in ("A la venta el 12,5 del pleno dominio de un piso de 87 m² ubicado en Ferrol",
                 "PROINDIVISO: 66,67% de titularidad. A Pedreira-O Val, Narón"):
        sc, reasons = score_detail(item(title="Vivienda en Ferrol", price=19000, area_m2=87, description=desc))
        assert sc == 0 and "fractional" in reasons[0], desc


def test_mobile_home_is_skipped():
    sc, reasons = score_detail(item(source="imovirtual", country="PT", title="Casa movel T3 mais fossa 2000lt",
                                    price=16000, area_m2=80))
    assert sc == 0 and "mobile home" in reasons[0]


def test_unfinished_house_and_blind_auction_are_capped():
    sc, reasons = score_detail(item(title="Casa en Cariño", price=17000, area_m2=190,
                                    description="- - VIVIENDA EN CONSTRUCCIÓN - - . Vivienda en construcción."))
    assert sc <= UNCHECKED_CAP and any("under construction" in r for r in reasons)
    sc, reasons = score_detail(item(title="Vivienda en Vigo", price=28000, area_m2=84,
                                    description="La compra de inmuebles en subasta se realiza SIN VISITAS PREVIAS"))
    assert sc <= UNCHECKED_CAP and any("middleman" in r for r in reasons)


def test_plot_and_stable_under_a_house_title_are_not_homes():
    assert property_kind(item(title="Casa en Santoña", tipo="house",
                              description="¡Tu refugio privado en el Monte de Santoña! Parcela con infinitas "
                                          "posibilidades. Se vende preciosa parcela rústica de 1.747 m²")) == "rural_plot"
    assert property_kind(item(title="Casa en Corvera", tipo="house", area_m2=2320,
                              description="Finca de recreo en Cancienes, con 2320 m²")) == "rural_plot"
    assert property_kind(item(title="Vivienda en Aller", tipo="house",
                              description="Se vende finca con cuadra de dos plantas")) == "other"
    # A traditional house that also has a stable and a hórreo stays a home.
    assert property_kind(item(title="Casa en Valdés - Luarca", tipo="house",
                              description="Se vende casa tradicional asturiana con cuadra, pajar grande, hórreo, "
                                          "finca de unos 1000 metros cuadrados y manantial propio.")) == "home"


def test_a_house_to_renovate_completely_is_not_good_condition():
    from scoring import condition
    assert condition(item(description="Es preciso reformarla por completo pero cuenta con una gran lareira "
                                      "con horno en buen estado que se puede conservar.")) == "heavy"
    assert condition(item(description="La casa, que necesita restauración, cuenta con muros de piedra "
                                      "en muy buen estado")) == "heavy"
    assert condition(item(description="Necesita ser restaurada y renovada.")) == "heavy"


def test_a_bank_home_for_a_few_thousand_is_doubtful():
    sc, reasons = score_detail(item(source="aliseda", title="Piso en Torrevieja", price=6270, area_m2=85,
                                    description="Piso en la 5a planta"))
    assert sc <= UNCHECKED_CAP and any("price doubtful" in r for r in reasons)


def test_land_and_a_stable_filed_as_houses():
    for desc in ("Se vende terreno rustico en Figares (Concejo de Salas) Asturias. Terreno llano",
                 "Terreno grande agrario ideal para todo tipo de cultivo",
                 "REF 438379 SE VENDE TERRENO EN FERROL San Xoan de Filgueira",
                 "Chalet independiente de 118 m² a la venta en Langreo. Actualmente es una parcela de terreno."):
        assert property_kind(item(title="Casa en X", tipo="house", area_m2=2000, description=desc)) == "rural_plot", desc
    assert property_kind(item(title="Casa en Peñamellera Alta", tipo="house",
                              description="Venta de cuadra de piedra en buen estado")) == "other"
    assert property_kind(item(title="Casa en Neda", tipo="house", area_m2=251,
                              description="Se vende terreno con casa de piedra")) == "home"


def test_a_preview_line_never_replaces_the_full_text():
    from db import _is_cut_copy
    full = "Busca su lugar de escape, de retiro?, alejado pero cercano?.. Disponemos de esta propiedad. Una casa"
    assert _is_cut_copy("Busca su lugar de escape, de retiro?, alejado pero cercano?. Disponemos de esta...", full)


def test_a_stable_and_hayloft_under_a_house_title_is_not_a_home():
    assert property_kind(item(title="Vivienda en Villaviciosa", tipo="vivienda",
                              description="Antigua cuadra y pajar en el centro del pueblo.")) == "other"
    assert property_kind(item(title="Casa en Llanes", tipo="vivienda",
                              description="Establo de piedra con nave adosada.")) == "other"
    # A house that also has a stable stays a house.
    assert property_kind(item(title="Casa en Valdés", tipo="vivienda",
                              description="Se vende casa con cuadra y pajar.")) == "home"


def test_okupas_are_out_in_every_tab():
    taken = item(source="aliseda", title="Vivienda en Villanueva de la Reina", tipo="vivienda",
                 price=25000, area_m2=90, description="Vivienda en buen estado.",
                 raw_json='{"posesion": "OKUPADO", "occupation": "occupied"}')
    for mode in ("home", "invest", "land", "forest"):
        sc, reasons = score_detail(taken, mode=mode)
        assert sc == 0 and "okupa" in reasons[0], mode
    tenant = item(title="Piso en Lugo", price=40000, area_m2=80, concelho="Lugo", district="Lugo",
                  description="Piso reformado, actualmente arrendado.")
    assert score_detail(tenant, mode="invest")[0] > 0
    assert any("tenant" in r for r in score_detail(tenant, mode="invest")[1])


def test_a_feed_area_much_larger_than_the_ad_is_not_the_plot():
    calella = item(title="Terreno en Calella", tipo="terreno", area_m2=1_350_000, price=90000,
                   description="Parcelas de 0,8 a 2,8 ha. Suelo urbanizable para futuros desarrollos.")
    sc, reasons = score_detail(calella, mode="forest")
    assert sc == 0 and any("ad states" in r and "2.8 ha" in r for r in reasons)
    assert any("building land" in r for r in reasons)
    tordesillas = item(title="Terreno en Tordesillas", tipo="terreno", area_m2=480_000, price=40000,
                       description="Parcela de 15,7 ha en el sector SUED-10.")
    assert score_detail(tordesillas, mode="forest")[0] == 0
    assert tordesillas["area_m2"] == 157_000


def test_a_fibre_cement_roof_and_a_collapsed_barn_raise_the_repair():
    from scoring import condition
    assert condition(item(description="Casa en buen estado. Cubierta de uralita.")) == "some"
    raw = '{"photo_check": {"condition": "good", "confidence": "high", "notes": "collapsed barn roof, asbestos sheets", "shows_house": true}}'
    assert condition(item(description="Casa en buen estado.", raw_json=raw)) == "heavy"


def test_a_share_named_in_words_is_skipped():
    for desc in ("Predmetom dražby je spoluvlastnícky podiel na rodinnom dome",
                 "Prodaje se suvlasnički dio kuće u Gvozdu", "Продава идеална част от къща"):
        sc, reasons = score_detail(item(country="SK", source="nehnutelnosti", title="Rodinný dom", price=3780,
                                        area_m2=124, description=desc))
        assert sc == 0 and "fractional" in reasons[0], desc
