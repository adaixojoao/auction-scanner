"""The Live and invest shortlist uses one floor, one planning price, and
research candidates rather than a verified purchase list."""
import json
from datetime import datetime, timezone

import costs
import dashboard
import scoring
from scoring import score_detail

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def _house(**kw):
    item = {
        "id": "eleiloes:one", "source": "eleiloes", "country": "PT",
        "title": "Moradia T3", "tipo": "moradia", "area_m2": 80, "price": 20000,
        "description": "moradia em bom estado",
        "beach": {"km": 0.4, "approx": False, "name": "Praia", "text": "0.4 km"},
        "climate": {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1}},
        "raw_json": json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}),
        "live_score": 70, "invest_score": 60, "score": 60, "status": "",
    }
    item.update(kw)
    return item


def test_predicted_acquisition_is_the_planning_price():
    item = _house(predicted_final={"price": 80000, "text": "likely to close around €80,000"})
    current = costs.money_out(item)["amount"]
    plan = costs.planning_view(item, current, typical=False)
    assert plan["acquisition"]["kind"] == "predicted"
    assert plan["acquisition"]["amount"] == 80000
    assert plan["status"] == "over"
    assert plan["note"] != "Within the planning budget"
    assert "middle" in (plan["renovation_point"] or "") or plan["renovation_point"] == "low"


def test_shortlist_does_not_clear_the_over_budget_label():
    item = _house(status="shortlisted",
                  predicted_final={"price": 80000, "text": "likely to close around €80,000"})
    plan = costs.planning_view(item, 1000, typical=True)
    assert plan["status"] == "over"
    assert "Shortlist" not in plan["note"]


def test_land_figure_is_not_the_floor(monkeypatch):
    monkeypatch.setattr(scoring, "local_price", lambda item: (1000.0, "a city series"))
    house = _house(area_m2=150, description="área total do terreno de 150m2 e habitação de 80 m2")
    resolved = costs.floor_resolution(house)
    assert resolved["m2"] == 80 and resolved["source"] == "notice"
    assert house["area_m2"] == 150
    assert scoring.market_value_estimate(house) == 80_000
    raw, why = score_detail(house, mode="invest", now=NOW)
    assert raw > 0
    assert any("80" in line and "notice" in line for line in why)


def test_research_candidates_keep_unvalued_separate():
    priced = _house()
    unvalued = _house(id="eleiloes:two", title="No price series", research_only=True,
                      invest_score=None, score=0)
    research = dashboard._research_candidates([unvalued, priced], 100000)
    assert research["heading"] == "Research candidates — diligence incomplete"
    assert [it["id"] for it in research["valued"]] == ["eleiloes:one"]
    assert [it["id"] for it in research["unvalued"]] == ["eleiloes:two"]
    assert research["unvalued"][0]["valuation"] == "Investment value not established"
    assert "Safe to buy" not in research["heading"]


def test_listings_page_names_the_research_group(db, add, monkeypatch):
    monkeypatch.setattr(scoring, "local_price", lambda item: (1200.0, "a city series"))
    add("eleiloes", "lead", title="Moradia T3", price=22000, area_m2=90,
        description="moradia em bom estado", url="https://example.test/lead",
        raw_json=json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"}))
    dashboard._LISTS.clear()
    dashboard.app.config["TESTING"] = True
    body = dashboard.app.test_client().get("/api/listings?mode=both&min_score=0").get_json()
    assert body["research"]["heading"] == "Research candidates — diligence incomplete"
    assert len(body["research"]["valued"]) <= 3
    page = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "research-candidates" in page
    assert "Safe to buy" not in page
