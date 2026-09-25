import csv
import os
import sys

import pytest

import prices
from scoring import score

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import update_prices  # noqa: E402

INE_ANSWER = [{
    "IndicadorCod": "0012009",
    "IndicadorDsg": "Valor mediano das vendas por m2 de alojamentos familiares (€) por Localização geográfica",
    "UltimoPref": "2.º Trimestre de 2026",
    "Dados": {
        "1.º Trimestre de 2026": [{"geocod": "0909", "geodsg": "Guarda", "valor": "700"}],
        "2.º Trimestre de 2026": [
            {"geocod": "PT", "geodsg": "Portugal", "dim_3": "T", "dim_3_t": "Total", "valor": "1832"},
            {"geocod": "16G", "geodsg": "Beiras e Serra da Estrela", "dim_3": "T", "dim_3_t": "Total", "valor": "605"},
            {"geocod": "1690907", "geodsg": "Guarda", "dim_3": "T", "dim_3_t": "Total", "valor": "742"},
            {"geocod": "1690907", "geodsg": "Guarda", "dim_3": "1", "dim_3_t": "Novos", "valor": "1100"},
            {"geocod": "1690914", "geodsg": "Sabugal", "dim_3": "T", "dim_3_t": "Total", "valor": "310"},
            {"geocod": "1690915", "geodsg": "Seia", "dim_3": "T", "dim_3_t": "Total", "valor": "x"},
            # NUTS 2024 codes can hold letters; "-" is INE's "no figure"
            {"geocod": "11D1818", "geodsg": "Sernancelhe", "dim_3": "H1", "dim_3_t": "Total", "valor": "275"},
            {"geocod": "1C20204", "geodsg": "Barrancos", "dim_3": "H1", "dim_3_t": "Total",
             "sinal_conv": "-", "ind_string": "-"},
            {"geocod": "11D1818", "geodsg": "Sernancelhe", "dim_3": "H3", "dim_3_t": "Existentes", "valor": "260"},
            {"geocod": "11D18", "geodsg": "Viseu Dão Lafões", "dim_3": "H1", "dim_3_t": "Total", "valor": "600"},
        ],
    },
}]


def test_ine_answer_is_read_for_municipalities_only():
    rows, period, title = update_prices.parse_ine(INE_ANSWER)
    assert period == "2.º Trimestre de 2026" and "mediano" in title
    assert [(r["municipality"], r["eur_m2"]) for r in rows] == [("Guarda", 742), ("Sabugal", 310),
                                                                ("Sernancelhe", 275)]
    with pytest.raises(ValueError):
        update_prices.parse_ine([{"Dados": {}}])


@pytest.fixture
def pt_prices(tmp_path, monkeypatch):
    path = tmp_path / "pt_home_prices.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=prices.COLUMNS)
        w.writeheader()
        w.writerows([{"municipality": "Sabugal", "eur_m2": 310, "period": "2.º Trimestre de 2026", "source": "INE"},
                     {"municipality": "Guarda", "eur_m2": 742, "period": "2.º Trimestre de 2026", "source": "INE"}])
    monkeypatch.setattr(prices, "PT_FILE", str(path))
    return path


def test_every_municipality_has_a_local_price(pt_prices):
    fallback = {"ES": {"sevilla": 1900}}
    assert prices.local_price("PT", "SABUGAL", fallback) == (310, "INE 2.º Trimestre de 2026")
    assert prices.local_price("PT", "Guarda (Sé)", fallback)[0] == 742
    assert prices.local_price("ES", "Sevilla", fallback) == (1900, "city estimate")
    assert prices.local_price("PT", "Nowhere", fallback) is None and prices.local_price("PT", "", fallback) is None


def test_a_home_in_a_small_municipality_is_compared_with_local_prices(pt_prices):
    home = {"source": "eleiloes", "country": "PT", "title": "Moradia em bom estado", "description": "",
            "concelho": "Sabugal", "area_m2": 100, "price": 9000}
    _, reasons = score(home)
    assert any(r.startswith("71% below local prices (INE 2.º Trimestre de 2026)") for r in reasons)


def test_without_the_file_the_city_table_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "PT_FILE", str(tmp_path / "missing.csv"))
    _, reasons = score({"source": "eleiloes", "country": "PT", "title": "Moradia", "description": "",
                        "concelho": "Guarda", "area_m2": 90, "price": 25000})
    assert any("below local prices (city estimate; counted at 75%: condition not stated)" in r for r in reasons)


def test_same_named_municipalities_get_their_own_region(tmp_path, monkeypatch):
    path = tmp_path / "pt.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=prices.COLUMNS)
        w.writeheader()
        for name, eur in (("Calheta (R.A.A.)", 697), ("Calheta (R.A.M.)", 1722), ("Lagoa", 3220),
                          ("Lagoa (R.A.A.)", 1761), ("São João da Madeira", 1836)):
            w.writerow({"municipality": name, "eur_m2": eur, "period": "1.º Trimestre de 2026", "source": "INE"})
    monkeypatch.setattr(prices, "PT_FILE", str(path))
    price = lambda place, district: prices.local_price("PT", place, {}, district=district)[0]  # noqa: E731
    assert price("Calheta", "Ilha da Madeira") == 1722 and price("Calheta", "Ilha de São Jorge") == 697
    assert price("Lagoa", "Faro") == 3220 and price("Lagoa", "Ilha de São Miguel") == 1761
    assert price("São João da Madeira", "Aveiro") == 1836          # mainland, despite the name
    assert price("Calheta", None) == 697                             # no district: the first one


def test_an_old_village_house_is_not_counted_at_the_towns_median_price(pt_prices):
    """INE's median is mostly sound homes in town: a 1937 house needing work
    8 km out is counted at a fraction of it, so its "discount" is honest."""
    base = {"source": "eleiloes", "country": "PT", "concelho": "Sabugal", "area_m2": 100, "price": 9000}
    sound = {**base, "title": "Moradia em bom estado", "description": ""}
    old = {**base, "title": "Moradia", "description": "Moradia para remodelar. Ano de construção: 1937",
           "town_distance": {"km": 8.0, "text": "8.0 km from Sabugal"}}
    _, sound_reasons = score(sound)
    _, old_reasons = score(old)
    assert any(r.startswith("71% below local prices") for r in sound_reasons)
    worn = next(r for r in old_reasons if "below local prices" in r)
    assert "needs work, built 1937, 8 km from town" in worn
    assert int(worn.split("%")[0]) < 71
    assert "needs some work" in old_reasons                 # "para remodelar" is work



def test_a_parish_price_beats_the_municipality_where_ine_has_one(pt_prices, tmp_path, monkeypatch):
    parish = tmp_path / "pt_parish_prices.csv"
    parish.write_text("municipality,parish,eur_m2,period,source\n"
                      "Sabugal,União das freguesias de Sortelha e Malcata,150,2.º Trimestre de 2026,INE\n",
                      encoding="utf-8")
    monkeypatch.setattr(prices, "PT_PARISH_FILE", str(parish))
    assert prices.parish_names("União das freguesias de Vila do Bispo e Raposeira") == ["vila do bispo", "raposeira"]
    village = {"source": "eleiloes", "country": "PT", "title": "Moradia em bom estado", "description": "",
               "concelho": "Sabugal", "freguesia": "Malcata", "area_m2": 100, "price": 9000}
    _, reasons = score(village)
    assert any("40% below local prices (INE 2.º Trimestre de 2026, parish)" in r for r in reasons)
    elsewhere = {**village, "freguesia": "Soito"}                      # no parish figure: the municipality's
    _, reasons = score(elsewhere)
    assert any(r.startswith("71% below local prices (INE 2.º Trimestre de 2026)") for r in reasons)


def test_ine_answer_gives_the_parishes_under_their_municipality():
    answer = [{**INE_ANSWER[0], "Dados": {"2.º Trimestre de 2026": INE_ANSWER[0]["Dados"]["2.º Trimestre de 2026"] + [
        {"geocod": "169091405", "geodsg": "União das freguesias de Sortelha e Malcata", "dim_3": "H1",
         "dim_3_t": "Total", "valor": "150"},
        {"geocod": "169091405", "geodsg": "União das freguesias de Sortelha e Malcata", "dim_3": "H11",
         "dim_3_t": "Novos", "valor": "900"},
        {"geocod": "169091406", "geodsg": "Soito", "dim_3": "H1", "dim_3_t": "Total", "sinal_conv": "-"},
    ]}}]
    assert update_prices.parse_ine_parishes(answer) == [
        {"municipality": "Sabugal", "parish": "União das freguesias de Sortelha e Malcata", "eur_m2": 150,
         "period": "2.º Trimestre de 2026", "source": "INE"}]
