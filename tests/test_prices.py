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
    fallback = {"GR": {"athina": 1900}}
    assert prices.local_price("PT", "SABUGAL", fallback) == (310, "INE 2.º Trimestre de 2026")
    assert prices.local_price("PT", "Guarda (Sé)", fallback)[0] == 742
    assert prices.local_price("GR", "Athina", fallback) == (1900, "city estimate")      # no official table there
    assert prices.local_price("PT", "Nowhere", fallback) is None and prices.local_price("PT", "", fallback) is None


def test_a_home_in_a_small_municipality_is_compared_with_local_prices(pt_prices):
    home = {"source": "eleiloes", "country": "PT", "title": "Moradia em bom estado", "description": "",
            "concelho": "Sabugal", "area_m2": 100, "price": 9000}
    _, reasons = score(home, mode="invest")
    assert any("below local prices all-in" in r and "INE 2.º Trimestre de 2026" in r for r in reasons)
    assert not any("below local prices" in r for r in score(home)[1])


def test_without_the_file_the_city_table_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "PT_FILE", str(tmp_path / "missing.csv"))
    from scoring import local_price
    found = local_price({"country": "PT", "concelho": "Guarda"})
    assert found[1] == "city estimate" and found[0] > 0


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
    from scoring import local_value_factor
    sound_factor, _ = local_value_factor(sound)
    factor, why = local_value_factor(old)
    assert sound_factor == 1 and factor < sound_factor
    assert "needs work" in why and "built 1937" in why and any(w.startswith("8 km") for w in why)
    assert "needs some work" in score(old, mode="invest")[1]



def test_a_parish_price_beats_the_municipality_where_ine_has_one(pt_prices, tmp_path, monkeypatch):
    parish = tmp_path / "pt_parish_prices.csv"
    parish.write_text("municipality,parish,eur_m2,period,source\n"
                      "Sabugal,União das freguesias de Sortelha e Malcata,150,2.º Trimestre de 2026,INE\n",
                      encoding="utf-8")
    monkeypatch.setattr(prices, "PT_PARISH_FILE", str(parish))
    assert prices.parish_names("União das freguesias de Vila do Bispo e Raposeira") == ["vila do bispo", "raposeira"]
    assert "parish" in prices.local_price("PT", "Sabugal", {}, parish="Malcata")[1]
    assert "parish" not in prices.local_price("PT", "Sabugal", {}, parish="Soito")[1]


def test_ine_keeps_the_previous_quarter_from_the_same_answer():
    """The earlier period is the one just before the latest, not whichever key
    happens to sit next to it."""
    payload = [{**INE_ANSWER[0], "Dados": {
        "2.º Trimestre de 2026": [{"geocod": "1690907", "geodsg": "Guarda", "dim_3": "T",
                                   "dim_3_t": "Total", "valor": "742"}],
        "4.º Trimestre de 2025": [{"geocod": "1690907", "geodsg": "Guarda", "dim_3": "T",
                                   "dim_3_t": "Total", "valor": "600"}],
        "1.º Trimestre de 2026": [{"geocod": "1690907", "geodsg": "Guarda", "dim_3": "T",
                                   "dim_3_t": "Total", "valor": "700"}],
    }}]
    rows, period, _ = update_prices.parse_ine(payload)
    assert period == "2.º Trimestre de 2026"
    assert rows[0]["prev_eur_m2"] == 700 and rows[0]["prev_period"] == "1.º Trimestre de 2026"


def test_a_refresh_keeps_the_previous_figure_of_the_same_series(tmp_path):
    path = tmp_path / "prices.csv"
    update_prices._write(str(path), [
        {"municipality": "Guarda", "eur_m2": 700, "period": "1.º Trimestre de 2026", "source": "INE"}])
    update_prices._write(str(path), [
        {"municipality": "Guarda", "eur_m2": 742, "period": "2.º Trimestre de 2026", "source": "INE"},
        {"municipality": "Sabugal", "eur_m2": 310, "period": "2.º Trimestre de 2026", "source": "INE"}])
    rows = {r["municipality"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}
    assert rows["Guarda"]["prev_eur_m2"] == "700"
    assert rows["Guarda"]["prev_period"] == "1.º Trimestre de 2026"
    assert rows["Sabugal"]["prev_eur_m2"] == ""
    update_prices._write(str(path), [
        {"municipality": "Guarda", "eur_m2": 742, "period": "2.º Trimestre de 2026", "source": "INE"}])
    again = next(csv.DictReader(path.open(encoding="utf-8")))
    assert again["prev_eur_m2"] == "700"                         # same quarter rewritten: the older one stays
    update_prices._write(str(path), [
        {"municipality": "Guarda", "eur_m2": 800, "period": "2.º Trimestre de 2026", "source": "DVF"}])
    other = next(csv.DictReader(path.open(encoding="utf-8")))
    assert "prev_eur_m2" not in other                            # a different source is not the previous quarter


def test_a_move_in_sold_prices_is_shown_and_does_not_change_the_score(tmp_path, monkeypatch):
    path = tmp_path / "pt.csv"

    def write(prev):
        with path.open("w", encoding="utf-8", newline="") as handle:
            fields = ["municipality", "eur_m2", "period", "source"]
            row = {"municipality": "Sabugal", "eur_m2": 310, "period": "2.º Trimestre de 2026", "source": "INE"}
            if prev:
                fields += ["prev_eur_m2", "prev_period"]
                row["prev_eur_m2"], row["prev_period"] = prev
            w = csv.DictWriter(handle, fieldnames=fields)
            w.writeheader()
            w.writerow(row)

    monkeypatch.setattr(prices, "PT_FILE", str(path))
    home = {"source": "eleiloes", "country": "PT", "title": "Moradia em bom estado", "description": "",
            "concelho": "Sabugal", "area_m2": 100, "price": 9000}

    def scored(mode="home"):
        prices._load.cache_clear()
        return score(home, mode=mode)

    write(None)
    quiet, quiet_why = scored()
    quiet_invest = scored("invest")[0]
    assert not any("sold prices here" in r for r in quiet_why)
    write((280, "1.º Trimestre de 2026"))
    moved, why = scored()
    assert moved == quiet
    assert any(r == "sold prices here up 11% since 1.º Trimestre de 2026 (INE 2.º Trimestre de 2026)"
               for r in why)
    invested, invest_why = scored("invest")
    assert invested == quiet_invest
    assert any("sold prices here up 11%" in r for r in invest_why)
    write((307, "1.º Trimestre de 2026"))          # under 2%: not mentioned
    assert not any("sold prices here" in r for r in scored()[1])
    assert prices.sale_move("PT", "Sabugal")["change_pct"] == pytest.approx((310 - 307) / 307 * 100)


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


def test_spain_and_france_use_their_official_tables_and_the_province_for_villages():
    town = prices.local_price("ES", "Ourense", {}, district="Ourense")
    assert town and "MIVAU" in town[1] and "province" not in town[1]
    village = prices.local_price("ES", "Ourol", {}, district="Lugo")
    assert village and village[1].endswith("province average")
    assert "DVF" in prices.local_price("FR", "Brest", {}, district="29")[1]


def test_italy_reads_table_8_of_an_omi_regional_report():
    import sys
    sys.path.insert(0, "scripts")
    from update_prices import it_table8
    text = ("Nella Tabella 8 sono indicate le quotazioni medie.\nTabella 8: Quotazione media e variazione annua\n"
            "Provincia\nCapoluogo\nResto provincia\nBIELLA\n805\n-0,4%\n498\n0,0%\n"
            "REGGIO CALABRIA\n1.120\n1,0%\n640\n0,5%\nPIEMONTE\n1.827\n1,2%\n988\n-0,1%\n")
    rows = {r["municipality"]: r["eur_m2"] for r in it_table8(text, "2025")}
    assert rows["Biella"] == 805 and rows["prov:Biella"] == 498
    assert rows["prov:Reggio di Calabria"] == 640 and rows["Reggio Calabria"] == 1120
    assert "Piemonte" not in rows                                  # the region's total is not a province


def test_germany_reads_an_immoportal_town_page():
    import sys
    sys.path.insert(0, "scripts")
    from update_prices import de_town_price
    html = "<p>Was kostet eine Immobilie in Achern?</p><div>∅ Median Kaufpreis Haus <b>2.940 €/m²</b></div>"
    assert de_town_price(html) == ("Achern", 2940)


def test_simef_prices_are_weighted_by_volume_over_the_last_years():
    from update_forest_prices import simef_prices, simef_rows
    page = ('<table id="MainContent_GV_TBLPRECOS_SP"><tr><th>Ano</th></tr>'
            "<tr><td>2025</td><td>53</td><td>Pinheiro-bravo</td><td>2T</td><td>15</td><td>63</td><td>50</td><td>1.000</td></tr>"
            "<tr><td>2024</td><td>40</td><td>Pinheiro-bravo</td><td>1T</td><td>10</td><td>60</td><td>40</td><td>3.000</td></tr>"
            "<tr><td>2019</td><td>9</td><td>Pinheiro-bravo</td><td>1T</td><td>10</td><td>60</td><td>99</td><td>9.000</td></tr>"
            "</table>")
    mixed, pine = simef_prices(simef_rows(page))
    assert mixed["crop"] == "mixed" and mixed["eur_m3"] == 42.5
    assert (pine["crop"], pine["eur_m3"], pine["period"]) == ("maritime pine", 42.5, "2023-2025")


def test_the_forestry_model_uses_the_official_timber_price():
    import forestry
    price = forestry.timber_price("PT", "maritime pine")
    assert price and "SIMeF" in price["label"]
    atlantic = {"heat": {"today": 24, "ssp245_2081-2100": 28}}
    pine = next(o for o in forestry.options(atlantic, "PT", 20) if o["crop"] == "maritime pine")
    assert pine["sources"] and "SIMeF" in forestry.describe(pine)
