"""Heavy industry next to a home hides it; lighter pollution nearby lowers its score (geo.pollution)."""
import json

import pytest

import geo
from scoring import pollution_penalty, score_detail


@pytest.fixture
def polluters(tmp_path, monkeypatch):
    path = tmp_path / "polluters.csv"
    path.write_text("country,kind,lat,lon,name\n"
                    "PT,heavy,38.6600,-8.8300,Refinaria (refinery)\n"
                    "PT,light,40.2000,-8.4000,Aterro (landfill)\n", encoding="utf-8")
    monkeypatch.setattr(geo, "POLLUTION_FILE", str(path))
    return str(path)


def at(lat, lon, precision=None):
    raw = {"lat": lat, "lon": lon}
    if precision:
        raw = {"geo": {"lat": lat, "lon": lon, "precision": precision}}
    return {"country": "PT", "raw_json": json.dumps(raw)}


def test_heavy_industry_next_to_the_house_is_heavy(polluters):
    found = geo.pollution(at(38.6700, -8.8300))                 # about 1 km
    assert found["level"] == "heavy" and "heavy industry (Refinaria (refinery))" in found["text"]


def test_heavy_industry_a_few_km_away_or_a_landfill_close_by_is_light(polluters):
    assert geo.pollution(at(38.7100, -8.8300))["level"] == "light"   # about 5.5 km
    assert geo.pollution(at(40.2100, -8.4000))["level"] == "light"   # landfill 1 km
    assert geo.pollution(at(40.2500, -8.4000)) is None               # landfill 5.5 km
    assert geo.pollution({"country": "PT"}) is None                  # no position


def test_the_penalty_is_bigger_for_heavy_and_named():
    light = {"level": "light", "text": "about 2.0 km from a pollution source (Aterro)"}
    assert pollution_penalty({"pollution": light}) == (-10, "some pollution nearby: " + light["text"])
    assert pollution_penalty({"pollution": {"level": "heavy", "text": "x"}})[0] == -30
    assert pollution_penalty({}) == (0, None)


def test_a_polluted_home_scores_lower():
    home = {"source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia", "area_m2": 120,
            "price": 25000}
    clean, _ = score_detail(dict(home))
    dirty, reasons = score_detail({**home, "pollution": {"level": "light", "text": "1 km from a landfill"}})
    assert dirty == max(0.0, clean - 10) and any("pollution nearby" in r for r in reasons)


def test_heavy_industry_hides_the_home_unless_turned_off(db, add, polluters):
    from db import load_listings
    add(external_id="dirty", title="Moradia T3", tipo="moradia", price=20000, area_m2=100,
        raw_json={"lat": 38.6700, "lon": -8.8300})
    add(external_id="clean", title="Moradia T3", tipo="moradia", price=20000, area_m2=100,
        raw_json={"lat": 41.5, "lon": -8.0})
    shown = {it["id"]: it for it in load_listings(db, filters={}, include_hidden=True)}
    assert shown["eleiloes:dirty"]["hidden_reason"].startswith("heavy pollution:")
    assert not (shown["eleiloes:clean"].get("hidden_reason") or "").startswith("heavy pollution")
    kept = {it["id"]: it for it in load_listings(db, filters={"avoid_heavy_pollution": False},
                                                 include_hidden=True)}
    assert not (kept["eleiloes:dirty"].get("hidden_reason") or "").startswith("heavy pollution")
    assert any("heavy pollution nearby" in r for r in kept["eleiloes:dirty"]["reasons"]) \
        or kept["eleiloes:dirty"]["score"] == 0
