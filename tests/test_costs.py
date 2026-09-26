"""What a property really costs on top of the price (costs.py)."""
import pytest

import costs
import dashboard


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def home(**over):
    item = {"title": "Moradia T3", "description": "", "kind": "home", "country": "PT",
            "source": "citius", "price": 20000, "area_m2": 100}
    item.update(over)
    return item


def labels(est):
    return {line["label"]: round(line["amount"], 2) for line in est["lines"]}


def test_portuguese_home_pays_imt_stamp_duty_and_the_registry():
    est = costs.estimate(home())
    assert labels(est) == {"IMT": 200.0, "Imposto do selo": 160.0, "Land registry": 250.0}
    assert (est["fees"], est["total"], est["basis"]) == (610.0, 20610.0, "the base value")


@pytest.mark.parametrize("value, expected", [
    (104_261, 1042.61),                       # top of the first bracket
    (150_000, 150_000 * 0.05 - 5321.15),      # third bracket, with its deduction
    (2_000_000, 2_000_000 * 0.075),           # flat rate at the top
])
def test_imt_follows_the_published_brackets(value, expected):
    assert costs.imt(value, "home")[0] == pytest.approx(expected, abs=0.5)


def test_a_home_to_live_in_pays_less_imt_than_one_bought_to_let():
    assert costs.imt(100_000, "home", own_home=True)[0] == 0
    assert costs.imt(100_000, "home")[0] == 1000
    # A 0 € line is not shown at all.
    assert "IMT" not in labels(costs.estimate(home(price=100_000), own_home=True))


def test_land_and_shops_pay_their_own_rate():
    assert labels(costs.estimate(home(kind="rural_plot", price=30000)))["IMT"] == 1500.0
    assert labels(costs.estimate(home(kind="other", price=30000)))["IMT"] == 1950.0


def test_a_court_sale_has_no_deed_but_a_bank_sale_does():
    assert "Deed" not in labels(costs.estimate(home()))
    assert labels(costs.estimate(home(source="novobanco")))["Deed"] == float(costs.PT_DEED_EUR)


def test_other_countries_use_one_typical_rate_each():
    spain = labels(costs.estimate(home(country="ES", price=50000)))
    assert (spain["Transfer tax"], spain["Notary and registry"]) == (4000.0, 750.0)
    # Italy's registration tax has a floor, whatever the price.
    assert labels(costs.estimate(home(country="IT", price=5000)))["Transfer tax"] == costs.IT_REGISTRO_MIN
    unknown = costs.estimate(home(country="XX", price=10000))
    assert unknown["fees"] == pytest.approx(10000 * (0.070 + 0.015))


def test_the_price_used_is_the_one_you_would_really_pay():
    assert costs.estimate(home(current_bid=12000))["base"] == 12000
    assert costs.estimate(home(min_price=15000))["base"] == 15000
    assert costs.estimate(home(current_bid=12000), bid=9000)["basis"] == "your bid"
    assert costs.estimate(home(price=0)) is None


@pytest.mark.parametrize("title, low, high", [
    ("Moradia em ruínas", 70000, 120000),
    ("Moradia que necessita de obras", 30000, 60000),
    ("Moradia em bom estado", 0, 15000),
    ("Moradia T3", 20000, 50000),              # condition not stated
])
def test_renovation_is_banded_by_what_the_listing_admits_to(title, low, high):
    work = costs.estimate(home(title=title))["renovation"]
    assert (work["low"], work["high"]) == (low, high)


def test_renovation_only_for_a_home_whose_size_is_known():
    assert costs.renovation(home(kind="rural_plot", title="Terreno rústico")) is None
    assert costs.renovation(home(area_m2=0)) is None
    assert costs.renovation(home(area_m2=5000)) is None      # that is the plot, not the building
    assert costs.estimate(home(area_m2=0))["all_in"] is None


def test_all_in_adds_the_work_to_the_price_and_the_fees():
    est = costs.estimate(home(title="Moradia em ruínas"))
    assert est["all_in"] == {"low": 20610.0 + 70000, "high": 20610.0 + 120000}


def test_as_text_is_one_line_per_cost_and_says_it_is_an_estimate():
    text = costs.as_text(costs.estimate(home()))
    assert "IMT: €200" in text and "to own it: €20,610" in text and "Estimate." in text
    assert costs.as_text(None) == ""


def test_the_bands_the_ai_check_is_told_are_the_ones_used():
    for state, (low, high) in costs.RENOVATION_EUR_M2.items():
        assert f"€{low}–{high}/m²" in costs.bands_text()
        assert costs.CONDITION_LABEL[state] in costs.bands_text()


def test_the_listing_panel_carries_the_estimate(client, add):
    add("citius", "c1", title="Moradia T3 em ruínas", tipo="moradia", price=20000, area_m2=100)
    est = client.get("/api/listing?id=citius:c1").get_json()["costs"]
    assert est["total"] == 20610 and est["all_in"]["low"] == 90610


def test_a_listing_without_a_price_has_no_estimate(client, add):
    add("citius", "c2", title="Moradia", tipo="moradia", price=0)
    assert client.get("/api/listing?id=citius:c2").get_json()["costs"] is None


def test_a_car_pays_no_imt(client, add):
    assert costs.estimate({"title": "Veículo ligeiro Opel Corsa", "price": 2000, "country": "PT"}) is None
    add("eleiloes", "c3", title="Automóvel Opel Corsa", tipo="veiculo", price=2000)
    assert client.get("/api/listing?id=eleiloes:c3").get_json()["costs"] is None


@pytest.fixture
def pt_rents(tmp_path, monkeypatch):
    import prices
    path = tmp_path / "pt_rents.csv"
    path.write_text("municipality,eur_m2,period,source\n"
                    "Reguengos de Monsaraz,5.2,1.º Semestre de 2026,INE\n", encoding="utf-8")
    monkeypatch.setattr(prices, "PT_RENT_FILE", str(path))


def test_a_home_shows_what_it_would_rent_for_and_the_yield(pt_rents):
    house = {"title": "Moradia T3", "tipo": "moradia", "country": "PT", "source": "financas",
             "concelho": "Reguengos de Monsaraz", "district": "Évora", "area_m2": 100,
             "price": 28211, "description": "Moradia em bom estado"}
    est = costs.estimate(house)
    rent = est["rent"]
    assert rent["monthly"] == 520 and rent["eur_m2"] == 5.2
    cost = (est["all_in"]["low"] + est["all_in"]["high"]) / 2 if est["all_in"] else est["total"]
    assert rent["yield_pct"] == round(1200 * 520 / cost, 1)
    assert rent["payback_years"] == round(cost / (12 * 520), 1)
    assert "Reguengos de Monsaraz" in rent["note"] and "Rent: about €520" in costs.as_text(est)
    from telegram_alert import _cost_line
    assert "rents ~€520/month" in _cost_line(house)
    # A mansion rents like a 200 m² house; a plot, another country or an unknown town: no figure.
    assert costs.estimate({**house, "area_m2": 400})["rent"]["monthly"] == 1040
    assert costs.estimate({**house, "title": "Terreno rústico", "tipo": "terreno"})["rent"] is None
    assert costs.estimate({**house, "country": "ES"})["rent"] is None
    assert costs.estimate({**house, "concelho": "Nowhere"})["rent"] is None


def test_rents_keep_their_cents():
    import sys
    sys.path.insert(0, "scripts")
    import update_prices
    answer = [{"IndicadorDsg": "rendas", "UltimoPref": "S1", "Dados": {"S1": [
        {"geocod": "1870705", "geodsg": "Reguengos de Monsaraz", "valor": "5.23"}]}}]
    rows, _, _ = update_prices.parse_ine(answer, digits=2)
    assert rows[0]["eur_m2"] == 5.23
    assert update_prices.parse_ine(answer)[0][0]["eur_m2"] == 5


def test_a_french_house_rents_at_the_communes_carte_des_loyers(tmp_path, monkeypatch):
    import prices
    path = tmp_path / "fr_rents.csv"
    path.write_text("municipality,eur_m2,period,source\nAast,9.74,2025,Carte des loyers\n", encoding="utf-8")
    monkeypatch.setitem(prices.RENT_FILES, "FR", str(path))
    house = {"title": "Maison avec jardin", "tipo": "maison", "country": "FR", "source": "france",
             "district": "Aast", "area_m2": 100, "price": 20000}
    rent = costs.estimate(house)["rent"]
    assert rent["monthly"] == 974 and "Aast (Carte des loyers 2025)" in rent["note"]
    assert costs.estimate({**house, "country": "IT"})["rent"] is None       # no table for Italy


def test_the_carte_des_loyers_csv_is_read():
    import sys
    sys.path.insert(0, "scripts")
    import update_prices
    text = ('"id_zone";"INSEE_C";"LIBGEO";"loypredm2"\n"1";"37099";"Druye";8,9564\n'
            '"2";"99999";"Druye";12,1\n"3";"1";"";5\n')
    assert update_prices.parse_fr_rents(text, "2025") == [
        {"municipality": "Druye", "eur_m2": 8.96, "period": "2025", "source": "Carte des loyers"}]


def test_a_spanish_village_falls_back_on_its_provinces_rent(tmp_path, monkeypatch):
    import prices
    path = tmp_path / "es_rents.csv"
    path.write_text("municipality,eur_m2,period,source\nTabernas,3.6,2024,SERPAVI\n"
                    "prov:Ciudad Real,5.06,2024,SERPAVI\n", encoding="utf-8")
    monkeypatch.setitem(prices.RENT_FILES, "ES", str(path))
    home = {"title": "Vivienda unifamiliar", "tipo": "Casa", "country": "ES", "source": "servihabitat",
            "concelho": "Villamayor De Calatrava", "district": "ciudadreal", "area_m2": 75, "price": 9500}
    rent = costs.estimate(home)["rent"]
    assert rent["monthly"] == 379 and rent["source"] == "SERPAVI 2024, province"
    assert costs.estimate({**home, "concelho": "Tabernas", "district": "Almería"})["rent"]["monthly"] == 270


def test_serpavi_takes_the_latest_year_flats_then_houses():
    import sys
    sys.path.insert(0, "scripts")
    import update_prices
    header = ["NMUN", "ALQM2_LV_M_VC_23", "ALQM2_LV_M_VU_23", "ALQM2_LV_M_VC_24", "ALQM2_LV_M_VU_24"]
    rows = [("Tabernas", 3.1, 2.0, None, 3.6), ("Nada", None, None, None, None), ("Old", 4.0, None, None, None)]
    assert update_prices.serpavi_rows(header, rows, "NMUN") == [
        {"municipality": "Tabernas", "eur_m2": 3.6, "period": "2024", "source": "SERPAVI"},
        {"municipality": "Old", "eur_m2": 4.0, "period": "2023", "source": "SERPAVI"}]
