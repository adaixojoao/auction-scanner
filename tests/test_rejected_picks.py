"""Homes that scored 60+ as both a home and an investment, opened and rejected by hand (2026-10)."""
import json

from common import has_term
from scoring import (UNCHECKED_CAP, condition, energy_class, property_kind, score, score_detail)


def item(**kw):
    base = {"source": "fotocasa", "country": "ES", "title": "", "description": "", "tipo": "vivienda"}
    base.update(kw)
    return base


def test_ad_saying_it_is_not_ready_needs_work():
    ad = item(title="Vivienda en Ponferrada",
              description="se vende piso 25.000 no está acto para entrar a vivir todavía por falta de luz y agua")
    assert condition(ad) == "some"


def test_french_full_renovation_needs_heavy_work():
    ad = item(country="FR", source="bienici", tipo="maison", title="Maison à Varzy (58210)",
              description="Cette maison, qui nécessite une rénovation totale, présente une superficie de 125 m². "
                          "Le gros œuvre et la toiture sont en bon état.")
    assert condition(ad) == "heavy"
    done = item(country="FR", tipo="maison", title="Maison",
                description="Maison ayant fait l'objet d'une rénovation totale en 2020, en bon état.")
    assert condition(done) == "good"


def test_uninhabitable_building_but_not_an_uninhabitable_attic():
    assert condition(item(country="FR", source="notaires", title="Maison à Beuvry (62660)",
                          description="BEUVRY – Immeuble non-habitable · Surface 153 m²")) == "heavy"
    assert condition(item(country="FR", title="Maison",
                          description="Maison en bon état, combles non habitables.")) == "good"


def test_partly_done_is_not_done():
    assert condition(item(source="imovirtual", country="PT", title="Casa de Aldeia parcialmente remodelada")) == "some"
    assert condition(item(source="imovirtual", country="PT", title="CASA pra reformar ALDEIA")) == "heavy"


def test_french_works_phrased_around_the_verb():
    for desc in ("Des travaux sont à prévoir, notamment sur la partie arrière.",
                 "Double vitrage existant et toiture en bon état. Travaux de rénovation à prévoir.",
                 "Le gros oeuvre est en bon état, prévoir des travaux d'isolation."):
        assert condition(item(country="FR", title="Maison", description=desc)) == "some", desc


def test_no_works_in_french_is_good():
    ad = item(country="FR", title="Maison", description="Maison en bon état, aucun travaux à prévoir.")
    assert condition(ad) == "good"
    assert not has_term("Maison sans travaux à prévoir", ["travaux à prévoir"])


def test_two_rustic_plots_under_a_house_title_are_land():
    ad = item(title="Casa en Murcia, Sangonera la Seca", area_m2=1000,
              description="MOLINO DE LA VEREDA ¡Oportunidad única para los amantes de la naturaleza! Se venden "
                          "dos parcelas rústicas de 1000 m2 cada una, ubicadas cerca del núcleo. Aunque no cuentan "
                          "con suministro de agua ni luz, las viviendas cercanas utilizan aljibes.")
    assert property_kind(ad) == "rural_plot"
    assert property_kind(item(title="Casa en Cabana", description="Se vende casa con terreno y dos hórreos")) == "home"


def test_down_payment_price_is_skipped():
    ad = item(country="FR", source="bienici", tipo="maison", title="Maison à Saint-Pardoux-la-Rivière",
              price=22500, area_m2=99,
              description="Maison de village en pierre · Vente à terme libre. Maison rénovée. 500 euros par mois "
                          "pendant 10 ans.")
    for mode in ("home", "invest"):
        sc, reasons = score(ad, mode=mode)
        assert sc == 0 and "down payment" in reasons[0], mode
    assert score(item(country="FR", title="Maison", price=30000,
                      description="Vente classique, pas de viager."), mode="home")[0] > 0


def test_resold_auction_award_without_possession_is_skipped():
    ad = item(title="Vivienda en Sevilla, La Oliva", price=21850, area_m2=72,
              description="- CESIÓN de REMATE- La adquisición se llevará a cabo mediante cesión de remate (ii) no se "
                          "contará con la posesión del inmueble en el momento de la adquisición.")
    sc, reasons = score_detail(ad)
    assert sc == 0 and "possession" in reasons[0]


def test_home_outside_the_land_registry_is_capped():
    ad = item(title="Casa en Mieres", price=33900, area_m2=84,
              description="Casa de aldea reformada con vistas. La vivienda no está registrada, por lo que no es "
                          "apta para hipoteca.")
    for mode in ("home", "invest"):
        sc, reasons = score(ad, mode=mode)
        assert sc <= UNCHECKED_CAP and any("land registry" in r for r in reasons), mode


def test_french_energy_class():
    assert energy_class(item(country="FR", raw_json=json.dumps({"dpe": "G"}))) == "G"
    assert energy_class(item(country="FR", description="Classe énergie : D, classe climat B")) == "D"
    assert energy_class(item(country="FR", description="Logement à consommation énergétique excessive")) == "F/G"
    assert energy_class(item(country="FR", description="DPE à venir")) is None


def test_g_rated_french_home_scores_lower_to_let_but_not_to_live_in():
    base = item(country="FR", source="bienici", tipo="maison", title="Maison à Sainte-Sévère-sur-Indre",
                price=36950, area_m2=78, description="Rénové à un niveau élevé, cuisine équipée.")
    g = {**base, "raw_json": json.dumps({"dpe": "G"})}
    d = {**base, "raw_json": json.dumps({"dpe": "D"})}
    inv_g, why = score(g, mode="invest")
    assert inv_g < score(d, mode="invest")[0] and any("energy class G" in r for r in why)
    assert score(g, mode="home")[0] == score(d, mode="home")[0]
    assert not any("energy class" in r for r in score({**g, "country": "ES"}, mode="invest")[1])
