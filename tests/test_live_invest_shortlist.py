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
    assert plan["renovation_point"] == "middle"
    assert plan["planning_amount"] == costs.selected_planning(
        item, bid=plan["acquisition"]["amount"], basis=plan["acquisition"]["label"])["amount"]


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
    assert "Not safe to buy" not in page
    assert "Not verified as ready to buy" in page


def test_good_condition_planning_uses_the_same_midpoint(db, add, monkeypatch):
    """A budget between the low end and the midpoint is over for Live and invest."""
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "a city series"))
    house = {"title": "Moradia em bom estado", "description": "moradia em bom estado",
             "tipo": "moradia", "country": "PT", "source": "eleiloes", "area_m2": 80, "price": 20000}
    low = costs.money_out(house)["amount"]
    planned = costs.selected_planning(house)
    assert planned["amount"] - low == 6000
    assert planned["incomplete"] is False
    _, why = score_detail(house, mode="invest", now=NOW)
    assert any(f"€{planned['amount']:,.0f}" in line for line in why)
    cash = low + 1
    assert low < cash < planned["amount"]
    import config
    from db import load_listings
    config.save_config({"cash_on_hand": cash, "filters": {"min_score": 0}})
    add("eleiloes", "midpoint", title="Moradia em bom estado", price=20000, area_m2=80,
        description="moradia em bom estado")
    hidden = [it for it in load_listings(db, filters={"min_score": 0}, mode="both", include_hidden=True)
              if it["id"] == "eleiloes:midpoint"]
    assert hidden and f"{planned['amount']:,.0f}" in hidden[0]["hidden_reason"]
    assert "eleiloes:midpoint" not in {it["id"] for it in load_listings(db, filters={"min_score": 0}, mode="both")}
    home_ids = {it["id"] for it in load_listings(db, filters={"min_score": 0}, mode="home")}
    assert "eleiloes:midpoint" in home_ids
    view = costs.planning_view(house, cash)
    assert view["planning_amount"] == planned["amount"]
    assert view["status"] == "over"
    assert view["renovation_point"] == "middle"
    assert view["all_in"]["low"] < view["planning_amount"] < view["all_in"]["high"]


def test_unresolved_floor_is_not_labelled_within_budget():
    missing = {"title": "Moradia", "description": "em bom estado", "tipo": "moradia",
               "country": "PT", "source": "eleiloes", "price": 20000}
    plan = costs.planning_view(missing, 500000)
    assert plan["status"] == "incomplete"
    assert plan["all_in"] is None and plan["renovation"] is None
    assert plan["planning_amount"] == plan["known_subtotal"]
    assert "fits" not in plan["note"]
    assert missing.get("area_m2") is None

    capped = {"title": "Moradia", "description": "em bom estado", "tipo": "moradia",
              "country": "PT", "source": "eleiloes", "price": 20000, "area_m2": 5000}
    capped_plan = costs.planning_view(capped, 500000)
    assert costs.floor_resolution(capped)["source"] == "capped"
    assert capped_plan["status"] == "incomplete"
    assert capped_plan["all_in"] is None
    assert capped["area_m2"] == 5000
    assert capped_plan["planning_amount"] == round(costs.estimate(capped)["total"])

    assert costs.planning_view(missing, 0)["status"] == "budget_not_set"
    assert costs.planning_view({**missing, "price": 0}, 500000)["status"] == "unknown"
    over = costs.planning_view({**missing, "price": 400000}, 10000)
    assert over["status"] == "over"
    assert "Known subtotal" in over["note"]
    starred = costs.planning_view({**missing, "status": "shortlisted"}, 500000)
    assert starred["status"] == "incomplete"


def test_unvalued_living_score_does_not_bypass_the_saved_minimum(db, add, monkeypatch):
    import config
    import dashboard
    from db import load_listings

    config.save_config({"filters": {"min_score": 40}, "cash_on_hand": 40000})
    add("eleiloes", "low", title="Moradia T3", price=22000, area_m2=90,
        description="moradia em bom estado")

    def fake(item, now=None, targets=None, mode="home"):
        if mode == "both":
            item["live_score"] = 10.0
            item["invest_score"] = None
            item["research_only"] = True
            return 0.0, ["Investment value not established — not ranked"]
        return 55.0, ["home"]

    monkeypatch.setattr(scoring, "score_detail", fake)
    assert load_listings(db, filters={"min_score": 40}, mode="both", now=NOW) == []
    hidden = [it for it in load_listings(db, filters={"min_score": 40}, mode="both",
                                         include_hidden=True, now=NOW) if it["id"] == "eleiloes:low"]
    assert hidden[0]["live_score"] == 10.0 and hidden[0]["score"] == 0.0
    assert hidden[0]["hidden_reason"].startswith("score 0 < filters.min_score 40")

    dashboard._LISTS.clear()
    dashboard.app.config["TESTING"] = True
    blocked = dashboard.app.test_client().get("/api/listings?mode=both&min_score=0").get_json()
    assert blocked["items"] == []
    assert blocked["research"]["unvalued"] == []
    assert blocked["research"]["valued"] == []

    config.save_config({"filters": {"min_score": 0}, "cash_on_hand": 40000})
    dashboard._LISTS.clear()
    shown = dashboard.app.test_client().get("/api/listings?mode=both&min_score=0").get_json()
    assert [it["id"] for it in shown["research"]["unvalued"]] == ["eleiloes:low"]
    assert shown["research"]["unvalued"][0]["live_score"] == 10.0


def test_portal_auction_figure_is_a_reference_not_the_acquisition_price(db, add, monkeypatch):
    import config
    import dashboard
    from listing_info import facts

    item = {
        "source": "fotocasa", "country": "ES", "tipo": "vivienda",
        "title": "Vivienda en Donostia - San Sebastián",
        "description": "Piso en subasta judicial",
        "price": 135000, "min_price": 135000, "area_m2": 65,
    }
    acq = costs.selected_acquisition(dict(item))
    assert acq["kind"] == "unusable"
    assert acq["amount"] is None
    assert acq["reference_amount"] == 135000
    assert "subasta" in acq["label"]
    plan = costs.planning_view(dict(item), 40000)
    assert plan["status"] == "unknown"
    assert "€135,000" in plan["note"]
    assert "not used as the acquisition price" in plan["note"]
    assert plan["acquisition"]["reference_amount"] == 135000
    assert costs.over_cash(dict(item), 40000) is None
    cash = costs.cash_view(dict(item), 40000)
    assert cash["over"] is False
    assert "Within" not in cash["text"]
    assert "€135,000" not in cash["text"] or "not" in cash["text"].lower()

    usable = {**item, "description": "vivienda luminosa", "min_price": None}
    usable_plan = costs.planning_view(usable, 40000)
    assert usable_plan["acquisition"]["kind"] == "asking"
    assert usable_plan["acquisition"]["amount"] == 135000
    assert usable_plan["status"] == "over"
    assert costs.over_cash(usable, 40000)

    labels = {fact["label"]: fact["value"] for fact in facts(dict(item))}
    assert labels["Base value"] == "€135,000"
    assert "subasta" in labels["Purchase price"]

    config.save_config({"filters": {"min_score": 0, "countries": ["ES"]}, "cash_on_hand": 40000})
    add("fotocasa", "191023678", country="ES", title=item["title"], price=135000,
        min_price=135000, area_m2=65, description=item["description"],
        url="https://www.fotocasa.es/es/comprar/vivienda/donostia/191023678/d")

    def fake(row, now=None, targets=None, mode="home"):
        if mode == "both":
            row["live_score"] = 60.0
            row["invest_score"] = 60.0
            return 80.0, ["ranked"]
        return 80.0, ["home"]

    monkeypatch.setattr(scoring, "score_detail", fake)
    dashboard._LISTS.clear()
    dashboard.app.config["TESTING"] = True
    body = dashboard.app.test_client().get("/api/listings?mode=both&min_score=0").get_json()
    assert body["items"], body.get("stats")
    row = body["items"][0]
    assert row["price"] == 135000
    assert "subasta" in row["price_unusable"]
    card = body["research"]["valued"][0]
    assert card["planning"]["status"] == "unknown"
    assert card["planning"]["acquisition"]["kind"] == "unusable"
    assert card["planning"]["acquisition"]["reference_amount"] == 135000
    detail = dashboard.app.test_client().get("/api/listing?id=fotocasa:191023678&mode=both").get_json()
    detail_facts = {fact["label"]: fact["value"] for fact in detail["facts"]}
    assert detail_facts["Base value"] == "€135,000"
    assert "subasta" in detail_facts["Purchase price"]
