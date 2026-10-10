"""The maximum-bid calculator (bidcap.calculate_bid_cap): homes, ruins, rural
land, missing area, climate at an exact position, and a listing priced above
its estimated value."""
import json

import pytest

import bidcap
import dashboard
from db import connect
from scoring import climate_score


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000,
                        "proponente": {"nome": "Test Person", "nif": "123",
                                       "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"},
                        "bid_cap": {"max_all_in": 0, "margin_pct": 15, "contingency_pct": 10,
                                    "rural_reserve_per_ha": 500, "rural_reserve_fixed": 0,
                                    "require_exact": False, "adviser_reserve_eur": 1500,
                                    "adviser_reserve_by_country": {}},
                        "checklist": {"blocking": {"pt_court": []}}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _home(**over):
    row = {"id": "citius:h1", "source": "citius", "country": "PT", "kind": "home",
           "title": "Moradia em bom estado", "description": "Habitação em bom estado de conservação",
           "concelho": "Porto", "area_m2": 100, "price": 80000, "category": "imoveis"}
    row.update(over)
    return row


def _settings(**over):
    s = {"filters": {}, "bid_cap": {"max_all_in": 0, "margin_pct": 15, "contingency_pct": 10,
                                    "rural_reserve_per_ha": 500, "rural_reserve_fixed": 0,
                                    "require_exact": False, "adviser_reserve_eur": 1500,
                                    "adviser_reserve_by_country": {}}}
    s["bid_cap"].update(over)
    return s


def _exact_evidence(grade="good"):
    clim = climate_score(None, "home")
    clim = {**clim, "grade": grade, "confidence": "exact",
            "bid_multiplier": {"excellent": 1.0, "good": 1.0, "caution": 0.85, "poor": 0.6}.get(grade, 1.0)}
    return {"climate": clim, "location": {"level": "exact", "label": "Exact location"}}


def test_a_sound_home_has_recommended_below_absolute_max():
    cap = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    assert cap["estimated_market_value"] and cap["market_value_confidence"] in ("low", "medium")
    assert cap["recommended_bid"] is not None and cap["absolute_max_bid"] is not None
    assert 0 < cap["recommended_bid"] < cap["absolute_max_bid"]
    assert cap["all_in_low"] <= cap["all_in_high"]
    labels = [r["label"] for r in cap["waterfall"]]
    assert labels[0] == "Estimated value" and labels[-1] == "Bid cap (recommended)"
    assert "Renovation allowance" in labels and "Taxes and fees" in labels and "Risk reserve" in labels
    assert "Your margin" in labels
    assert "not a valuation" in cap["note"].lower()


def test_a_ruin_allows_for_more_work_than_a_sound_home():
    ruin = bidcap.calculate_bid_cap(
        _home(title="Moradia em ruínas", description="Em ruínas, precisa de reconstrução total"),
        _settings(), _exact_evidence())
    sound = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence())
    assert ruin["renovation_high"] > sound["renovation_high"]
    assert ruin["recommended_bid"] < sound["recommended_bid"]


def test_rural_land_uses_the_buyer_target_not_a_market_price():
    plot = {"id": "citius:p1", "source": "citius", "country": "PT", "kind": "rural_plot",
            "title": "Terreno rústico", "area_m2": 20000, "price": 5000, "category": "imoveis"}
    settings = _settings()
    settings["filters"] = {"rural_max_eur_m2": 0.5}
    cap = bidcap.calculate_bid_cap(plot, settings, _exact_evidence())
    assert cap["basis"]["basis"] == "target" and cap["basis"]["amount"] == 10000
    assert cap["estimated_market_value"] is None
    assert cap["market_value_confidence"] == "policy"
    assert any("land" in (r["note"] or "").lower() for r in cap["waterfall"] if r["label"] == "Risk reserve")
    assert cap["recommended_bid"] is not None and cap["recommended_bid"] < cap["absolute_max_bid"]


def test_unknown_area_leaves_no_bid_and_says_what_is_missing():
    cap = bidcap.calculate_bid_cap(_home(area_m2=0), _settings(), _exact_evidence())
    assert cap["recommended_bid"] is None and cap["absolute_max_bid"] is None
    assert "the floor area" in cap["unknowns"]
    assert any("nothing to work from" in r.lower() or "floor area" in r.lower() for r in cap["reasons"])


def test_high_flood_risk_at_an_exact_position_holds_value_back():
    good = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence("good"))
    poor = bidcap.calculate_bid_cap(_home(), _settings(), _exact_evidence("poor"))
    assert poor["climate_adjustment"] > 0
    assert poor["recommended_bid"] < good["recommended_bid"]
    assert any("Climate poor" in r for r in poor["reasons"])
    # Approximate position: climate is noted, not held back
    approx = bidcap.calculate_bid_cap(
        _home(), _settings(),
        {"climate": {**_exact_evidence("poor")["climate"], "confidence": "approximate"},
         "location": {"level": "municipality", "label": "Municipality estimate"}})
    assert approx["climate_adjustment"] == 0
    assert any("approximate" in r for r in approx["reasons"])


def test_priced_above_estimated_value_is_flagged():
    cap = bidcap.calculate_bid_cap(_home(price=500_000), _settings(), _exact_evidence())
    assert any("above its estimated value" in r for r in cap["reasons"])
    assert any("above the" in r for r in cap["reasons"])   # price_to_pay vs recommended/max


def test_require_exact_blocks_a_town_level_listing():
    settings = _settings(require_exact=True)
    cap = bidcap.calculate_bid_cap(
        _home(), settings,
        {"climate": {"grade": "good", "confidence": "approximate", "bid_multiplier": 1.0},
         "location": {"level": "municipality", "label": "Municipality estimate"}})
    assert cap["recommended_bid"] is None
    assert any("exact location" in r.lower() for r in cap["reasons"])


def test_compare_and_record_keep_the_difference(client, db, add):
    raw = {"processo": "1/10.0TBXXX", "tribunal": "Juízo", "modalidade": "Carta fechada",
           "agente_email": "ae@solic.pt"}
    add("citius", "b1", title="Moradia em bom estado", description="Habitação em bom estado",
        # Not Porto: €800/m² there is so far under its price that it scores 0 and leaves the list.
        price=80000, area_m2=100, concelho="Guarda", raw_json=json.dumps(raw))
    lid = "citius:b1"
    detail = client.get(f"/api/listing?id={lid}").get_json()
    assert detail["bid_cap"]["recommended_bid"] and detail["bid_cap"]["waterfall"]
    view = client.get("/api/offers").get_json()["review"][0]
    assert view["bid_cap"]["absolute_max_bid"] >= view["bid_cap"]["recommended_bid"]

    above = view["bid_cap"]["absolute_max_bid"] + 5000
    sent = client.post("/api/offers/sent", json={"id": lid, "bid": f"{above:,.2f}".replace(",", "X")
                                                .replace(".", ",").replace("X", ".")}).get_json()
    row = db.execute("SELECT bid_cap_recommended, bid_cap_absolute, bid_cap_note FROM carta_log WHERE id = ?",
                     (sent["log_id"],)).fetchone()
    assert row["bid_cap_absolute"] == view["bid_cap"]["absolute_max_bid"]
    assert "above the absolute maximum" in (row["bid_cap_note"] or "")
    offer = client.get("/api/offers").get_json()["sent"][0]["offer"]
    assert "above the absolute maximum" in offer["bid_cap_note"]


def test_the_maximum_bid_setting(client):
    import config
    assert config.DEFAULTS["bid_cap"]["margin_pct"] == 15
    assert "bc_margin_pct" in client.get("/settings").get_data(as_text=True)
    assert client.post("/api/settings", json={"bid_cap": {"margin_pct": -1}}).status_code == 400
    assert client.post("/api/settings", json={"bid_cap": {"require_exact": "yes"}}).status_code == 400
    assert client.post("/api/settings", json={"bid_cap": {"margin_pct": 20, "require_exact": True}}
                       ).get_json() == {"ok": True}
    assert config.load_config()["bid_cap"]["margin_pct"] == 20
    assert config.load_config()["bid_cap"]["require_exact"] is True


def test_a_version_10_database_gets_the_bid_cap_columns(tmp_path):
    path = str(tmp_path / "v10.db")
    conn = connect(path)
    for col in ("bid_cap_recommended", "bid_cap_absolute", "bid_cap_note"):
        conn.execute(f"ALTER TABLE carta_log DROP COLUMN {col}")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()
    conn = connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(carta_log)")}
    assert {"bid_cap_recommended", "bid_cap_absolute", "bid_cap_note"} <= cols
    conn.close()
