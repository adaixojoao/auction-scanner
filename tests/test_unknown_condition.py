"""Second round of hand-rejected picks (2026-10-11): 31 portal homes that ranked high on both goals.
24 of them said nothing about their state; the rest said it in words the scanner did not know."""
import json

from common import find_floor_area
from scoring import UNCHECKED_CAP, condition, portal_condition, property_kind, score, score_detail


def item(**kw):
    base = {"source": "fotocasa", "country": "ES", "title": "", "description": "", "tipo": "vivienda"}
    base.update(kw)
    return base


def test_work_in_words_the_scanner_did_not_know():
    cases = {
        "CASA DE   PIEDRA , DOS PLANTAS Y DESVAN , CON  APARCAMIENTO DELANTE,  NECESITA REFORMAR ,  BODEGA": "heavy",
        "Están situadas una frente a la otra y ambas necesitan rehabilitarse.": "heavy",
        "Propiedad con daños ocasionados por terceros o con necesidad de reforma": "heavy",
        "Immeuble sinistré sur une parcelle de 2,70 ares. L'arrière a été détruit par incendie.": "heavy",
        "Les réseaux (eau, électricité) sont à proximité immédiate, raccordements à prévoir.": "heavy",
        "L'eau et l'électricité sont raccordées. Il n'y a pas de fosse septique.": "heavy",
        "ce bien se prête parfaitement à une rénovation totale, vous permettant de façonner chaque détail": "heavy",
        "A propriedade necessita de total reabilitação para atingir o seu potencial máximo.": "heavy",
        "ideal para inversores o aquellos que buscan un proyecto de renovación": "some",
        "L'eau et l'électricité sont raccordées à la maison, mais doivent être remises en service.": "some",
    }
    for text, state in cases.items():
        assert condition(item(description=text)) == state, text


def test_good_condition_of_one_part_is_not_the_house():
    for text in ("Double vitrage, chauffage électrique, toiture en tres bon etat.",
                 "un appartement indépendant, vaste salle de bains rénové avec douche",
                 "Local de 76 m2. Elementos comunes reformados. ITE en vigor.",
                 "Le gros œuvre et la toiture sont en bon état."):
        assert condition(item(country="FR", description=text)) == "unknown", text
    assert condition(item(country="FR", description="Maison en bon état, toiture refaite en 2020.")) == "good"
    assert condition(item(description="Piso reformado, la cocina en buen estado.")) == "good"


def test_the_portal_field_speaks_when_the_text_does_not():
    to_reform = item(raw_json=json.dumps({"conservation": 4}), description="Lista para entrar a vivir.")
    assert portal_condition(to_reform) == "some" and condition(to_reform) == "some"
    assert condition(item(raw_json=json.dumps({"conservation": 2}), description="Piso de 3 habitaciones")) == "good"
    assert condition(item(source="imovirtual", country="PT",
                          raw_json=json.dumps({"construction_status": "to_renovation"}))) == "some"
    assert condition(item(raw_json=json.dumps({"conservation": 2}), description="Necesita reforma integral")) == "heavy"


def test_shops_barns_caves_and_plots_sold_as_homes():
    others = [
        "Se vende local de 54 m2 en Portugalete, local diáfano. En este momento es un almacen. No tiene baño.",
        "Local comercial de 76 m2 con posibilidad de vivienda. Buena orientación sureste.",
        "165 m² construidos, segunda mano/buen estado, situado a pie de calle, 1 escaparates, "
        "actividad comercial: almacén, bajo. Local para adaptar a cualquier situación.",
        "LUSSAULT-SUR-LOIRE · Exclusivité. Venez découvrir cet ensemble de caves troglodytes authentiques de 78 m2.",
        "Maison à vendre Le Langon · Venez visiter ce Garage/Grange. Au RDC un garage, à l'étage un grenier.",
        "Dépendances a usage agricole à Saint Martin De Bonfossé · dépendances construites en masse et deux hangars",
    ]
    for desc in others:
        assert property_kind(item(country="FR", title="Maison à X", tipo="maison", description=desc)) == "other", desc
    assert property_kind(item(country="FR", title="Maison à X", tipo="maison",
                              description="Maison avec grange attenante, 3 chambres, cuisine, salle d'eau.")) == "home"
    plots = [
        item(title="Casa en Güímar", area_m2=1000, description="RED0615.- BAJADA DE PRECIO!!! Zona la Pasada. "
             "Dos finca rústicas de uso agrícola, acceso a través de camino de tierra"),
        item(source="thinkspain", title="Finca/Country House in Poio", area_m2=300,
             description="With 300 m² built, 0 bedrooms, 0 bathrooms, 0 toilets."),
        item(title="Casa en Mos", area_m2=767, raw_json=json.dumps({"rooms": 0, "bathrooms": 0}), description="."),
        item(source="bienici", country="FR", title="Maison à Ceyrat", tipo="maison", area_m2=62,
             description="BOISSEJOUR / Projet de construction neuf, permis validé ! Budget global à moins de 150 000 €"),
    ]
    for plot in plots:
        assert property_kind(plot) in ("urban_plot", "rural_plot"), plot["title"]


def test_squatted_or_deedless_homes_are_out():
    sc, why = score_detail(item(title="Casa en Algeciras", price=29000, area_m2=90,
                                description="El inmueble se encuentra ocupado ilegalmente por terceros."))
    assert sc == 0 and "possession" in why[0]
    for mode in ("home", "invest"):
        sc, why = score(item(title="Vivienda en Telde, Jinamar", price=25000, area_m2=108,
                             description="no tiene titulo de propiedad hay que acomodar los papeles"), mode=mode)
        assert sc <= UNCHECKED_CAP and any("land registry" in r for r in why), mode


def test_a_silent_portal_ad_ranks_under_one_that_states_its_state():
    # At a believable price: much lower, "good condition" is itself too cheap to be true.
    silent = item(title="Casa en Siero", price=60000, area_m2=105, concelho="Siero", district="Asturias",
                  description="Casa de tres plantas con finca, agua y luz, en una zona tranquila.")
    stated = {**silent, "raw_json": json.dumps({"conservation": 2})}
    for mode in ("home", "invest"):
        raw_silent, why = score_detail(silent, mode=mode)
        raw_stated, _ = score_detail(stated, mode=mode)
        assert raw_silent < raw_stated, mode
        assert any("does not say its state" in r for r in why), mode
    # A court sale that says nothing keeps its score: the forced sale is the bargain.
    court = {**silent, "source": "citius", "country": "PT", "title": "Moradia em Siero"}
    assert not any("does not say its state" in r for r in score_detail(court)[1])


def test_a_silent_ad_is_not_a_bigger_bargain_than_a_stated_one():
    """Unknown state counted the house at 75% of the local price, so a price that
    is too far under the done-up value (works included) still read as a bargain."""
    silent = item(title="Casa en Siero", price=10000, area_m2=105, concelho="Siero", district="Asturias",
                  description="Casa de tres plantas con finca, agua y luz.")
    _, why = score_detail(silent, mode="invest")
    assert any("so far below the local price" in r for r in why)
    assert not any(r.startswith("rent about") for r in why)


def test_the_soft_ceiling_keeps_the_order():
    cheap = item(title="Casa en Siero", price=15000, area_m2=105, concelho="Siero", district="Asturias",
                 description="Casa de tres plantas con finca, agua y luz.")
    dearer = {**cheap, "price": 40000}
    a, b = score_detail(cheap, mode="invest")[0], score_detail(dearer, mode="invest")[0]
    assert a > b                     # a hard cap would have tied them


def test_unconfirmed_listing_says_so():
    base = item(title="Casa en Torrelavega", price=25000, area_m2=124, raw_json=json.dumps({"conservation": 2}),
                description="Casa de dos plantas en buen estado.")
    fresh, _ = score_detail(base)
    old, why = score_detail({**base, "unconfirmed_since": "2026-10-05"})
    assert old <= fresh and any("not seen on fotocasa since 2026-10-05" in r for r in why)


def test_load_listings_marks_a_listing_its_site_has_not_shown(db, add):
    from datetime import datetime, timezone
    from db import load_listings
    add("solvia", "t1", "ES", title="Casa en Torrelavega", price=25000, area_m2=124)
    db.execute("UPDATE listings SET last_seen = '2026-10-05T09:44:21+00:00'")
    db.commit()
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    got = load_listings(db, include_hidden=True, now=now, apply_min_score=False)[0]
    assert got["unconfirmed_since"] == "2026-10-05"
    assert any("not seen on solvia" in r for r in got["reasons"])
    fresh = load_listings(db, include_hidden=True, now=datetime(2026, 10, 7, tzinfo=timezone.utc),
                          apply_min_score=False)[0]
    assert not fresh.get("unconfirmed_since")


def test_rent_is_not_worked_out_on_the_land():
    assert find_floor_area("un terrain de 400 m², idéal pour un potager · terrain 479 m²") is None
    assert find_floor_area("Maison de 80 m² sur un terrain de 400 m²") == 80
    assert find_floor_area("Casa en una parcela de 200 m², 80 m² construidos") == 80
    plot_only = item(source="bienici", country="FR", title="Maison à Ménéac (56490)", tipo="maison", price=26600,
                     concelho="Ménéac", district="56",
                     description="Maison NaN pièce(s), MÉNÉAC · À côté de la propriété se trouve un terrain de "
                                 "400 m², idéal pour aménager un jardin · terrain 479 m²")
    _, why = score(plot_only, mode="invest")
    assert not any(r.startswith("rent about") for r in why)


def test_a_portal_home_with_no_description_is_held_back():
    sc, why = score_detail(item(title="Casa en Barbate", price=20000, area_m2=570, description="."))
    assert sc <= UNCHECKED_CAP and any("no description" in r for r in why)
