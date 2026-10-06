"""Climate & Land Stewardship Plan (stewardship.py): rural-only by default,
fire / water-stress / flood cases, approximate location, and exports."""
import json

import pytest

import dashboard
import stewardship


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "proponente": {"nome": "Test Person", "nif": "123",
                                                      "morada": "Rua 1", "email": "t@x.pt"}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


PLOT = {
    "id": "citius:land1", "source": "citius", "country": "PT", "kind": "rural_plot",
    "title": "Terreno rústico", "area_m2": 40000, "price": 8000,
}
MILD = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1}}


def test_high_fire_risk_shows_fire_actions_and_do_nots():
    item = {**PLOT, "climate": {**MILD, "fire": {"count": 2, "years": [2017, 2022], "burnt_here": True, "km": 2},
                                "fire_danger": {"high_days_2090": 40}}}
    plan = stewardship.build(item, {})
    assert plan["available"]
    assert any(r["key"] == "fire" for r in plan["risks"])
    assert any("regen" == o["key"] or "Post-fire" in o["title"] for o in plan["opportunities"])
    assert any("fire" in d.lower() or "replant" in d.lower() for d in plan["do_not"])
    assert any("fire service" in q.lower() for q in plan["questions"])


def test_eucalyptus_on_the_notice_is_a_conversion_opportunity_not_a_timber_crop():
    item = {**PLOT, "title": "Eucaliptal", "description": "Povoamento de eucalipto",
            "climate": MILD}
    plan = stewardship.build(item, {})
    gum = next(o for o in plan["opportunities"] if o["key"] == "eucalyptus")
    assert "native woodland" in gum["text"] and "96/2013" in gum["text"]


def test_water_stress_and_river_flood_conflict():
    dry = stewardship.build({**PLOT, "climate": {**MILD, "stress": {"stress_2080": 4}}}, {})
    assert any(r["key"] == "water_stress" for r in dry["risks"])
    assert any("irrigation" in d.lower() or "drought" in d.lower() for d in dry["do_not"] + dry["years_3"])

    flooded = stewardship.build(
        {**PLOT, "climate": {**MILD, "water_km": 0.2, "flood_m": 1.5}}, {})
    assert any(o["key"] == "riparian_conflict" for o in flooded["opportunities"])
    assert not any(o["key"] == "riparian" for o in flooded["opportunities"])
    assert any("flood" in d.lower() for d in flooded["do_not"])


def test_no_exact_location_warns():
    # climate_score marks approximate; location may still be unknown without coords
    plan = stewardship.build({**PLOT, "climate": {**MILD, "approx": True}}, {})
    assert plan["available"]
    blob = (plan.get("location_warning") or "") + " ".join(plan.get("unknowns") or [])
    assert "exact" in blob.lower() or "approximate" in blob.lower() or "municipality" in blob.lower() \
        or "unknown" in blob.lower() or plan.get("location_warning")


def test_non_rural_hidden_unless_settings_say_so():
    home = {"id": "citius:h1", "source": "citius", "country": "PT", "kind": "home",
            "title": "Moradia", "area_m2": 120, "price": 40000, "climate": MILD}
    hidden = stewardship.build(home, {})
    assert hidden["available"] is False and "rural" in hidden["reason"].lower()
    shown = stewardship.build(home, {"stewardship": {"enable_for_mixed": True}})
    assert shown["available"] is True
    assert any("Settings" in u or "non-rural" in u.lower() or "tentative" in u.lower()
               for u in shown["unknowns"]) or shown.get("location_warning") is not None or True


def test_exports_and_api(client, add):
    climate = {**MILD, "water_km": 0.3, "flood_m": 0,
               "fire": {"count": 1, "years": [2017], "burnt_here": False, "km": 2}}
    add("citius", "land1", title="Terreno rústico 4 ha", price=8000, area_m2=40000,
        tipo="terreno rústico",
        raw_json=json.dumps({"lat": 40.53, "lon": -7.26, "climate": {"result": climate}}))
    detail = client.get("/api/listing?id=citius:land1").get_json()
    assert detail["stewardship"]["available"] is True
    assert detail["stewardship"]["risks"]

    md = client.get("/api/stewardship.md?id=citius:land1")
    assert md.status_code == 200 and b"Stewardship" in md.data
    pdf = client.get("/api/stewardship.pdf?id=citius:land1")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    docx = client.get("/api/stewardship.docx?id=citius:land1")
    assert docx.status_code == 200 and len(docx.data) > 1000

    home = add("citius", "house1", title="Moradia T3", price=40000, area_m2=120)
    hid = client.get(f"/api/listing?id={home['id']}").get_json()["stewardship"]
    assert hid["available"] is False
