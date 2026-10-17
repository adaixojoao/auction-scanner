import copy
import csv

import pytest

import prices


@pytest.fixture
def isolated_tables(tmp_path, monkeypatch):
    municipality = tmp_path / "municipality.csv"
    parish = tmp_path / "parish.csv"
    monkeypatch.setattr(prices, "PT_FILE", str(municipality))
    monkeypatch.setattr(prices, "PT_PARISH_FILE", str(parish))
    monkeypatch.setattr(prices, "PRICE_FILES", {})
    prices._load.cache_clear()
    prices._load_parishes.cache_clear()
    yield municipality, parish
    prices._load.cache_clear()
    prices._load_parishes.cache_clear()


def write_table(path, rows):
    fields = ["municipality", "eur_m2", "period", "source", "prev_eur_m2", "prev_period"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_municipal_provenance_preserves_source_period_and_previous_series(isolated_tables):
    municipality, _ = isolated_tables
    write_table(municipality, [{
        "municipality": "Guarda", "eur_m2": 742, "source": "Fixture series",
        "period": "2026 Q2", "prev_eur_m2": 700, "prev_period": "2026 Q1",
    }])
    record = prices.local_price_record("PT", "Guarda", {}, district="Guarda")
    assert record["eur_m2"] == 742
    assert record["label"] == "Fixture series 2026 Q2"
    assert record["previous_eur_m2"] == 700
    assert record["previous_period"] == "2026 Q1"
    assert record["lookup_key"] == "guarda|continente"
    assert record["dataset_path"] == str(municipality)
    assert record["geographic_scope"] == "municipality"
    assert record["property_specific"] is False
    assert record["fallback"] is False
    assert record["comparability"] == "aggregate_only"
    assert prices.local_price("PT", "Guarda", {}, district="Guarda") == (742, record["label"])
    assert prices.sale_move("PT", "Guarda", district="Guarda")["change_pct"] == pytest.approx(6)


def test_parish_selection_remains_ahead_of_municipality(isolated_tables):
    municipality, parish = isolated_tables
    write_table(municipality, [{"municipality": "Sabugal", "eur_m2": 310}])
    parish.write_text(
        "municipality,parish,eur_m2,period,source\n"
        "Sabugal,Malcata,150,2026 Q2,Fixture parish series\n", encoding="utf-8",
    )
    record = prices.local_price_record("PT", "Sabugal", {}, parish="Malcata")
    assert record["eur_m2"] == 150
    assert record["geographic_scope"] == "parish"
    assert record["dataset_path"] == str(parish)
    assert record["lookup_key"] is None
    assert record["property_specific"] is False
    assert prices.local_price("PT", "Sabugal", {}, parish="Malcata")[0] == 150


def test_missing_parish_keeps_municipality_selection(isolated_tables):
    municipality, _ = isolated_tables
    write_table(municipality, [{"municipality": "Sabugal", "eur_m2": 310}])
    record = prices.local_price_record("PT", "Sabugal", {}, parish="No fixture parish")
    assert record["geographic_scope"] == "municipality"
    assert record["requested_parish"] == "No fixture parish"


def test_city_estimate_is_explicitly_unverified(isolated_tables):
    fallback = {"GR": {"athina": 1900}}
    before = copy.deepcopy(fallback)
    record = prices.local_price_record("GR", "Athina", fallback)
    assert record["fallback"] is True
    assert record["dataset_path"] is None
    assert record["label"] == "city estimate"
    assert record["geographic_scope"] == "city_estimate"
    assert record["comparability"] == "unverified_city_estimate"
    assert record["previous_period"] is None
    assert fallback == before
    assert prices.local_price("GR", "Athina", fallback) == (1900, "city estimate")


def test_province_selection_is_not_property_specific(isolated_tables, tmp_path, monkeypatch):
    path = tmp_path / "es.csv"
    write_table(path, [{"municipality": "prov:Lugo", "eur_m2": 1000,
                        "source": "Fixture series", "period": "2026"}])
    monkeypatch.setattr(prices, "PRICE_FILES", {"ES": str(path)})
    record = prices.local_price_record("ES", "Fixture village", {}, district="Lugo")
    assert record["geographic_scope"] == "province"
    assert record["lookup_key"] == "prov:lugo"
    assert record["label"] == "Fixture series 2026, province average"
    assert any("Province-level" in text for text in record["limitations"])
    assert record["property_specific"] is False
    assert prices.local_price("ES", "Fixture village", {}, district="Lugo") == (
        1000, "Fixture series 2026, province average"
    )


def test_other_country_qualified_key_precedence(isolated_tables, tmp_path, monkeypatch):
    path = tmp_path / "fr.csv"
    write_table(path, [
        {"municipality": "Fixture town", "eur_m2": 1000},
        {"municipality": "Fixture town|29", "eur_m2": 1500},
        {"municipality": "prov:29", "eur_m2": 900},
    ])
    monkeypatch.setattr(prices, "PRICE_FILES", {"FR": str(path)})
    record = prices.local_price_record("FR", "Fixture town", {}, district="29")
    assert record["lookup_key"] == "fixture town|29"
    assert record["eur_m2"] == 1500
    assert record["geographic_scope"] == "municipality"


def test_unqualified_name_is_not_claimed_region_verified(isolated_tables):
    municipality, _ = isolated_tables
    write_table(municipality, [{"municipality": "Calheta (R.A.A.)", "eur_m2": 697}])
    record = prices.local_price_record("PT", "Calheta", {})
    assert record["lookup_key"] == "calheta"
    assert any("disambiguation is not established" in text for text in record["limitations"])
    assert prices.local_price("PT", "Calheta", {})[0] == 697


def test_qualified_region_selection_remains_intact(isolated_tables):
    municipality, _ = isolated_tables
    write_table(municipality, [
        {"municipality": "Calheta (R.A.A.)", "eur_m2": 697},
        {"municipality": "Calheta (R.A.M.)", "eur_m2": 1722},
    ])
    record = prices.local_price_record("PT", "Calheta", {}, district="Ilha da Madeira")
    assert record["lookup_key"] == "calheta|madeira"
    assert record["eur_m2"] == 1722


@pytest.mark.parametrize("place", [None, "", "No fixture match"])
def test_no_evidence_stays_missing(isolated_tables, place):
    assert prices.local_price_record("PT", place, {}) is None
    assert prices.local_price("PT", place, {}) is None


def test_record_mutation_does_not_corrupt_cached_series(isolated_tables):
    municipality, _ = isolated_tables
    write_table(municipality, [{"municipality": "Guarda", "eur_m2": 742}])
    first = prices.local_price_record("PT", "Guarda", {})
    first["limitations"].append("consumer mutation")
    first["eur_m2"] = 1
    second = prices.local_price_record("PT", "Guarda", {})
    assert second["eur_m2"] == 742
    assert "consumer mutation" not in second["limitations"]
