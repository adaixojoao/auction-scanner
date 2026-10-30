"""A Dutch public body selling a rural plot by tender sets no price: on the
land goals that is "you set your offer", not "price unknown" (2026-10-31)."""
import scoring
from scoring import score

PLOT = {"source": "biedboek", "country": "NL", "title": "Diverse graskavels — Nieuwerbrug aan den Rijn",
        "description": "Agrarisch · openbare inschrijving (Biedboek), geen vraagprijs", "tipo": "terreno",
        "area_m2": 62000, "price": None, "raw_json": "{}"}


def test_a_rural_tender_is_a_sale_where_you_set_the_offer():
    sc, reasons = score(dict(PLOT), mode="land")
    assert "no price — you set your offer" in reasons
    assert sc > scoring.NO_PRICE_CAP


def test_a_building_plot_or_home_by_tender_stays_unpriced():
    _, plot = score({**PLOT, "title": "Perceel grond (bedrijfskavel) — Groningen", "description": "Bedrijventerrein",
                     "area_m2": 3577}, mode="land")
    assert "no price — you set your offer" not in plot
    home, _ = score({**PLOT, "title": "Woonhuis met ondergrond en tuin — Rotterdam", "tipo": "woonhuis",
                     "area_m2": 253}, mode="home")
    assert home <= scoring.NO_PRICE_CAP


def test_the_bid_hint_names_the_benchmark_or_says_nothing(monkeypatch):
    import land_prices
    monkeypatch.setattr(land_prices, "land_value", lambda item: {"eur_ha": 20000, "label": "asking prices seen"})
    assert "about €12,400 for this plot" in scoring.tender_bid_hint(dict(PLOT), 6200)
    monkeypatch.setattr(land_prices, "land_value", lambda item: None)
    assert scoring.tender_bid_hint(dict(PLOT), 6200) is None
