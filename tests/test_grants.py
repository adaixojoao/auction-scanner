"""Planting grants (grants.py) and today's money at the owner's 3.5% inflation (forestry.py)."""
import csv
import re
from datetime import date

import pytest

import forestry
import grants

MILD = {"heat": {"today": 28, "ssp245_2061-2080": 30, "ssp245_2081-2100": 32}}


def write(path, *rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=grants.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: "" for k in grants.FIELDS} | r)
    return str(path)


def row(**kw):
    base = {"country": "PT", "measure": "X.1", "name": "Test grant", "case": "planting", "species": "any",
            "rate": "0.75", "legal_text": "Portaria test", "source_url": "https://example.org/law",
            "checked_on": "2026-10-21", "status": "partial"}
    return base | kw


# ─── the shipped table ──────────────────────────────────────────────────────

def test_every_shipped_grant_row_is_sourced_and_well_formed():
    table = grants.rows()
    assert table, "data/forest_grants.csv is shipped"
    for r in table:
        assert set(r) == set(grants.FIELDS)
        assert r["status"] in grants.STATUSES and r["case"] in grants.CASES
        assert r["country"] in {"PT", "ES", "FR", "BE", "NL", "LU"}
        assert r["legal_text"] and r["source_url"].startswith("https://")
        assert date.fromisoformat(r["checked_on"]) <= date.today()
        for key in ("rate", "bonus_max"):
            if r[key]:
                assert 0 < float(r[key]) <= 1
        for key in ("cost_cap_eur_ha", "premium_eur_ha_year", "premium_years", "min_ha", "max_timber_x_cost"):
            if r[key]:
                assert float(r[key]) > 0
        assert r["species"] in ("any", "favoured_broadleaf", "other")
        if r["status"] == "partial":
            # no amount is shown before it has been read in the official text
            assert not r["cost_cap_eur_ha"] and not r["premium_eur_ha_year"]


def test_portugal_names_pepac_for_a_native_planting_without_inventing_an_amount():
    out = grants.for_project("PT", "cork oak", "planting", hectares=20, plant_eur_ha=2500)
    [g] = out["grants"]
    assert g["measure"] == "C.3.2.1" and g["status"] == "partial" and g["eur_ha_year"] is None
    assert "75% of eligible planting cost" in g["what"] and "not yet checked" in g["what"]
    assert "20 years" in g["what"]                             # favoured broadleaves: the longer premium
    assert "124/2025/1" in g["legal_text"]
    other = grants.for_project("PT", "maritime pine", "planting", hectares=20, plant_eur_ha=2000)
    assert "15 years" in other["grants"][0]["what"]


def test_no_grant_for_keeping_a_stand_eucalyptus_or_an_orchard():
    assert grants.case_for("cork oak", established=True, wooded=True) is None
    assert grants.for_project("PT", "cork oak", None)["grants"] == []
    assert grants.for_project("PT", "eucalyptus", "planting", hectares=20)["grants"] == []
    walnut = grants.for_project("PT", "walnut", "planting", hectares=20)
    assert walnut["grants"] == [] and "farm investment" in walnut["text"]


def test_spain_and_unlisted_cases_say_so_rather_than_guess():
    assert "each region" in grants.for_project("ES", "cork oak", "planting", hectares=20)["text"]
    replant = grants.for_project("PT", "cork oak", "replanting", hectares=20)
    assert replant["grants"] == [] and "No replanting grant" in replant["text"]


def test_france_refuses_replanting_a_stand_worth_three_times_the_work(tmp_path):
    poor = grants.for_project("FR", "Douglas fir", "replanting", hectares=10, plant_eur_ha=3000, timber_eur=20_000)
    assert poor["grants"] and poor["grants"][0]["measure"] == "Renouvellement forestier"
    rich = grants.for_project("FR", "Douglas fir", "replanting", hectares=10, plant_eur_ha=3000, timber_eur=90_000)
    assert rich["grants"] == [] and "3×" in rich["refused"][0]


def test_a_minimum_area_is_respected(tmp_path):
    path = write(tmp_path / "g.csv", row(min_ha="4"))
    assert grants.for_project("PT", "cork oak", "planting", hectares=3, path=path)["grants"] == []
    assert grants.for_project("PT", "cork oak", "planting", hectares=5, path=path)["grants"]


# ─── a confirmed row is valued in today's money ────────────────────────────

def test_a_confirmed_grant_is_valued_with_fixed_premiums_losing_value(tmp_path):
    path = write(tmp_path / "g.csv", row(status="confirmed", cost_cap_eur_ha="2000",
                                         premium_eur_ha_year="125", premium_years="20"))
    [g] = grants.for_project("PT", "cork oak", "planting", hectares=20, plant_eur_ha=3000, path=path)["grants"]
    flows = {0: 0.75 * 2000} | {y: 125 / 1.035 ** y for y in range(1, 21)}     # the cap wins over the cost
    assert g["eur_ha_year"] == pytest.approx(forestry._annuity(forestry._npv(flows)))
    unindexed = {0: 0.75 * 2000} | {y: 125 for y in range(1, 21)}
    assert g["eur_ha_year"] < forestry._annuity(forestry._npv(unindexed))
    assert "up to €2,000/ha" in g["what"] and "fixed in euros" in g["what"]


def test_a_partial_row_never_gets_a_figure_even_with_numbers(tmp_path):
    path = write(tmp_path / "g.csv", row(cost_cap_eur_ha="2000", premium_eur_ha_year="125", premium_years="20"))
    [g] = grants.for_project("PT", "cork oak", "planting", hectares=20, plant_eur_ha=3000, path=path)["grants"]
    assert g["eur_ha_year"] is None


# ─── the plan in the details panel, and the score ──────────────────────────

def plot(**kw):
    base = {"source": "imovirtual", "country": "PT", "title": "Terreno rústico", "description": "Terreno com mato",
            "tipo": "terreno", "area_m2": 300000, "price": 40000, "climate": MILD}
    return base | kw


def test_the_plan_names_a_grant_that_may_apply_and_never_counts_it(monkeypatch):
    pine = {"crop": "maritime pine", "eur_ha_year": 100, "carbon_eur_ha_year": 0, "note": "", "limits": [],
            "sources": [], "until": None}
    monkeypatch.setattr(forestry, "options", lambda *a, **k: [pine])
    plan = forestry.project_plan(plot(), 100_000)
    assert plan["crop"] == "maritime pine"
    line = next(l for l in plan["lines"] if l["label"] == "Grants that may apply")
    assert line["value"] == "C.3.2.1" and "not in the score" in line["note"]
    assert any("ICNF" in s and "Count it only once it is approved" in s for s in plan["steps"])
    assert "3.5%" in plan["note"]
    assert plan["acquire"] == forestry.project_plan(plot(), 100_000)["acquire"]


def test_an_orchard_plan_says_why_no_forest_grant_applies():
    plan = forestry.project_plan(plot(), 100_000)            # this climate's best earner is carob
    line = next(l for l in plan["lines"] if l["label"] == "Grants that may apply")
    assert plan["crop"] == "carob" and line["value"] == "none known" and "farm investment" in line["note"]


def test_the_score_does_not_move_with_the_grant_table(monkeypatch, tmp_path):
    import scoring
    item = plot(area_m2=200000)
    before = scoring.score(dict(item), mode="forest")
    monkeypatch.setattr(grants, "GRANTS_FILE", write(tmp_path / "g.csv", row(status="confirmed", cost_cap_eur_ha="9000",
                                                                            premium_eur_ha_year="900", premium_years="20")))
    assert scoring.score(dict(item), mode="forest") == before


# ─── inflation ─────────────────────────────────────────────────────────────

def test_dated_prices_are_brought_to_today_at_three_and_a_half_percent():
    assert forestry.INFLATION == 0.035
    assert forestry.to_today(100, forestry.PRICE_YEAR) == 100
    assert forestry.to_today(100, forestry.PRICE_YEAR - 2) == pytest.approx(100 * 1.035 ** 2)
    assert forestry.fixed_today(100, 10) == pytest.approx(100 / 1.035 ** 10)
    assert forestry.nominal(0.05) == pytest.approx(1.05 * 1.035 - 1)
    assert forestry.period_year("2023-2025") == 2024 and forestry.period_year("2024") == 2024


def test_timber_and_cork_prices_say_they_were_brought_forward():
    price = forestry.timber_price("PT", "maritime pine")
    raw = float(re.search(r"€(\d+)/m³", price["label"]).group(1))
    assert price["eur_m3"] == pytest.approx(raw * 1.035 ** 2, abs=1.1)     # SIMeF 2023-2025 → 2024
    assert "+3.5% a year to 2026" in price["label"]
    assert "+3.5% a year to 2026" in forestry.CORK_SOURCE


def test_the_forestry_return_is_given_in_today_money_and_with_inflation():
    import scoring
    item = {"source": "imovirtual", "country": "PT", "title": "Herdade com montado de sobro",
            "description": "Montado de sobro, 40 ha, cortiça", "tipo": "terreno", "area_m2": 400000,
            "price": 60000, "climate": MILD}
    _s, reasons = scoring.score(item, mode="forest")
    line = next(r for r in reasons if r.startswith("return ≈"))
    assert "today's money" in line and "3.5% inflation" in line and "grants not counted" in line
