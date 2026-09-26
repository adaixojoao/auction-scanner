"""A top the owner can trust: unchecked listings stay below the minimum, the
score keeps apart the good and the best, and "excellent" means every wish."""
from types import SimpleNamespace

import geo
from scoring import display_score, excellent, score_detail
from sources.es import servihabitat_town

HOME = dict(source="eleiloes", country="PT", title="Moradia T3", tipo="moradia", area_m2=120, price=20000)
PLOT = dict(source="eleiloes", country="PT", title="Prédio rústico", tipo="terreno", area_m2=40000, price=9000)
MILD = {"hot_days": {"rcp45_2071-2100": 3, "today": 0}, "water_km": 0.3}


def test_top_is_squeezed_not_clamped():
    assert display_score(60) == 60
    assert display_score(100) < display_score(120) < display_score(160) < 100


def test_unlocated_and_unmeasured_stay_below_the_minimum():
    s, reasons = score_detail({**HOME, "unlocated": True})
    assert s <= 65 and "location unknown — climate not checked" in reasons
    s, reasons = score_detail({**PLOT, "area_m2": None})
    assert s <= 65 and any(r.startswith("size unknown — confirm the area") for r in reasons)


def test_guarda_no_longer_scores():
    near = {**PLOT, "guarda": {"km": 5, "text": "5 km from Guarda"}}
    assert score_detail(near)[0] == score_detail(PLOT)[0]


def test_excellent_needs_every_wish():
    item = {**HOME, "climate": MILD, "airport": {"km": 40, "text": "40 km from the airport"}}
    s, reasons = score_detail(item)
    assert excellent(item, display_score(s), reasons)
    hot = {**item, "climate": {**MILD, "hot_days": {"rcp45_2071-2100": 9}}}
    assert excellent(hot, 90, score_detail(hot)[1]) is None
    dry = {**item, "climate": {"hot_days": {"rcp45_2071-2100": 3}}}
    assert excellent(dry, 90, score_detail(dry)[1]) is None
    remote = {**item, "airport": {"km": 150, "text": "150 km from the airport"}}
    assert excellent(remote, 90, score_detail(remote)[1]) is None
    dear = {**item, "price": 45000}
    assert excellent(dear, 90, score_detail(dear)[1]) is None


def test_servihabitat_town_keeps_its_article():
    assert servihabitat_town("Casa en venta en C. Larga, 26, Campo De Peñaranda, El, Salamanca") == \
        "El Campo De Peñaranda"
    assert servihabitat_town("Piso en venta en Ba. La Herrera-Ijalde, 8, Zalla, Bizkaia") == "Zalla"


def test_a_street_hit_in_another_town_is_ignored(monkeypatch):
    item = dict(country="ES", concelho="El Campo De Peñaranda", title="Casa en venta en C. Larga, 26")
    towns = {geo.town_key("ES", "El Campo De Peñaranda"): {"lat": 40.98, "lon": -5.18}}
    far = {"lat": "41.0", "lon": "0.9", "display_name": "Platja Llarga, Salou"}
    near = {"lat": "40.97", "lon": "-5.19", "display_name": "Calle Larga, El Campo de Peñaranda"}
    assert not geo.in_its_town(far, item, towns)
    assert geo.in_its_town(near, item, towns)
    assert geo.in_its_town(near, item, None)


def test_a_lookup_made_with_a_wrong_town_is_redone():
    item = dict(country="ES", concelho="El Campo De Peñaranda",
                raw_json='{"geo": {"lat": 41.0, "lon": 0.9, "precision": "street", "query": "C. Larga, El"}}')
    assert geo.position(item) is None
    ok = dict(item, raw_json='{"geo": {"lat": 40.97, "lon": -5.19, "precision": "street", '
                             '"query": "C. Larga, El Campo De Peñaranda"}}')
    assert geo.position(ok)["lat"] == 40.97


def test_azores_mild_summer_counts_where_the_day_grid_ends():
    item = {**HOME, "climate": {"heat": {"ssp245_2081-2100": 26.1}, "water_km": 0.2},
            "airport": {"km": 25, "text": "25 km from the airport"}}
    s, reasons = score_detail(item)
    assert excellent(item, display_score(s), reasons)
