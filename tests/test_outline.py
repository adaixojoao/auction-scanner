"""outline.py: the house and its plot from the cadastre and OpenStreetMap.

The answers below are in the services' own formats (DGT's INSPIRE WFS as
GeoJSON, Overpass with `out geom`), built around one plot in Portugal."""
import json
import math

import pytest

import listing_info
import outline
from common import make_listing
from conftest import FakeResponse, FakeSession
from db import upsert_listing

LAT, LON = 41.0, -8.6
M_LAT = 1 / 110_574.0
M_LON = 1 / (111_320.0 * math.cos(math.radians(LAT)))


def rect(east_m: float, north_m: float, w_m: float, h_m: float) -> list[tuple[float, float]]:
    """A closed w × h m rectangle (lon, lat) whose south-west corner is east_m, north_m from the pin."""
    x, y = LON + east_m * M_LON, LAT + north_m * M_LAT
    dx, dy = w_m * M_LON, h_m * M_LAT
    return [(x, y), (x + dx, y), (x + dx, y + dy), (x, y + dy), (x, y)]


PLOT = rect(-15, -20, 30, 40)            # 1 200 m² drawn, 1 180 m² on the register
HOUSE = rect(-5, -6, 10, 12)             # 120 m², the pin inside it
ANNEX = rect(8, 10, 4, 5)                # 20 m², inside the plot
NEIGHBOUR = rect(20, 0, 9, 9)            # next door, outside the plot


def dgt_answer(rings=(PLOT,), area=1180, latitude_first=False):
    feats = []
    for i, ring in enumerate(rings):
        coords = [[p[1], p[0]] if latitude_first else [p[0], p[1]] for p in ring]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]},
                      "properties": {"label": f"1311{i}", "areavalue": area}})
    return {"type": "FeatureCollection", "features": feats}


def way(ring, levels=None, kind="house"):
    tags = {"building": kind}
    if levels:
        tags["building:levels"] = str(levels)
    return {"type": "way", "tags": tags, "geometry": [{"lat": p[1], "lon": p[0]} for p in ring]}


def overpass_answer(*elements):
    return {"elements": list(elements)}


def test_areas_are_right_to_within_a_percent():
    assert outline.ring_area_m2(rect(0, 0, 20, 10)) == pytest.approx(200, rel=0.01)
    hole = rect(2, 2, 5, 4)
    assert outline.polygon_area_m2([rect(0, 0, 20, 10), hole]) == pytest.approx(180, rel=0.01)
    assert outline.in_polygons(LON, LAT, [[HOUSE]])
    assert not outline.in_polygons(LON, LAT, [[rect(-10, -10, 20, 20), rect(-2, -2, 4, 4)]])  # in the hole


def test_the_house_its_annex_and_the_garden_are_told_apart():
    parcel = outline._parcel_at(dgt_answer()["features"], LAT, LON, "DGT cadastre", ("label",), ("areavalue",))
    buildings = outline.parse_buildings(overpass_answer(way(HOUSE, levels=2), way(ANNEX, kind="shed"),
                                                        way(NEIGHBOUR)))
    found = outline.combine(LAT, LON, parcel, buildings, flat=False)
    assert found["plot"]["official_m2"] == 1180 and found["plot"]["m2"] == pytest.approx(1200, rel=0.01)
    assert found["house"]["m2"] == pytest.approx(120, abs=2)
    assert found["house"]["floor_m2"] == pytest.approx(240, abs=4)      # two levels on the map
    assert found["annexes"]["count"] == 1 and found["annexes"]["m2"] == pytest.approx(20, abs=1)
    assert found["open_m2"] == pytest.approx(1180 - 140, abs=3)         # the neighbour is not counted
    assert len(found["shapes"]["buildings"]) == 2 and found["shapes"]["plot"]
    rows = {r["label"]: r["value"] for r in outline.summary(found)}
    assert rows["Plot"].startswith("1 180 m² (DGT cadastre, parcel 13110)")
    assert "2 levels" in rows["House (ground it covers)"]
    assert rows["Yard or garden"].startswith("about 1 04")


def test_a_parcel_given_latitude_first_is_still_found():
    parcel = outline._parcel_at(dgt_answer(latitude_first=True)["features"], LAT, LON, "DGT cadastre",
                                ("label",), ("areavalue",))
    assert parcel and parcel["official_m2"] == 1180


def test_overlapping_parcels_say_nothing():
    feats = dgt_answer(rings=(PLOT, rect(-10, -10, 25, 25)))["features"]
    assert outline._parcel_at(feats, LAT, LON, "DGT cadastre", ("label",), ("areavalue",)) is None


def test_without_a_parcel_only_the_building_at_the_pin_counts():
    buildings = outline.parse_buildings(overpass_answer(way(HOUSE), way(NEIGHBOUR)))
    found = outline.combine(LAT, LON, None, buildings, flat=False)
    assert found["house"]["m2"] == pytest.approx(120, abs=2) and found["house"]["floor_m2"] is None
    assert "plot" not in found and "open_m2" not in found
    assert "levels not on the map" in outline.summary(found)[0]["value"]

    nothing = outline.combine(LAT, LON, None, outline.parse_buildings(overpass_answer(way(NEIGHBOUR))), flat=False)
    assert "house" not in nothing and "at the pin" in nothing["note"]


def test_a_block_drawn_as_one_building_is_not_the_house():
    # The register gives the house's own parcel as 150 m²; the map draws the
    # whole terrace (280 m²) as one building centred on it.
    parcel = outline._parcel_at(dgt_answer(rings=(rect(-6, -8, 20, 14),), area=280)["features"], LAT, LON,
                                "DGT cadastre", ("label",), ("areavalue",))
    terrace = outline.parse_buildings(overpass_answer(way(rect(-6, -8, 20, 14))))
    found = outline.combine(LAT, LON, {**parcel, "official_m2": 150}, terrace, flat=False)
    assert "house" not in found and found["block"]["m2"] == pytest.approx(280, abs=4)
    assert "terrace or block" in found["note"]


def test_a_flat_gets_the_block_and_no_garden():
    parcel = outline._parcel_at(dgt_answer()["features"], LAT, LON, "DGT cadastre", ("label",), ("areavalue",))
    found = outline.combine(LAT, LON, parcel, outline.parse_buildings(overpass_answer(way(HOUSE, levels=5))),
                            flat=True)
    assert "plot" not in found and "house" not in found and "plot" not in found["shapes"]
    assert found["block"]["levels"] == 5 and "whole block" in found["note"]
    assert outline.is_flat({"title": "Apartamento T2, 3.º andar"})
    assert not outline.is_flat({"title": "Moradia T3 com logradouro"})


def test_a_relation_with_a_courtyard_counts_without_it():
    outer = rect(-10, -10, 20, 20)
    court = rect(-3, 4, 6, 4)
    rel = {"type": "relation", "tags": {"building": "yes", "building:levels": "1"}, "members": [
        {"role": "outer", "geometry": [{"lat": p[1], "lon": p[0]} for p in outer]},
        {"role": "inner", "geometry": [{"lat": p[1], "lon": p[0]} for p in court]},
    ]}
    (b,) = outline.parse_buildings(overpass_answer(rel))
    assert b["m2"] == pytest.approx(400 - 24, abs=4) and b["levels"] == 1


@pytest.mark.parametrize("said, expected", [
    (1180, "matches the plot on the cadastre, not the house"),
    (240, "matches the house's floors on the map"),
    (3000, "more than the parcel at the pin"),
    (60, None),
])
def test_the_notice_figure_is_placed(said, expected):
    parcel = outline._parcel_at(dgt_answer()["features"], LAT, LON, "DGT cadastre", ("label",), ("areavalue",))
    found = outline.combine(LAT, LON, parcel, outline.parse_buildings(overpass_answer(way(HOUSE, levels=2))),
                            flat=False)
    said_notes = outline.notes({"area_m2": said}, found)
    if expected:
        assert len(said_notes) == 1 and expected in said_notes[0]
    else:
        assert said_notes == []


def _home(db, raw: dict, title="Moradia T3 com logradouro", eid="1"):
    listing = make_listing("eleiloes", eid, title=title, price=40000, concelho="Porto",
                           raw_json=json.dumps(raw))
    upsert_listing(db, listing)
    db.commit()
    return db.execute("SELECT * FROM listings WHERE id = ?", (f"eleiloes:{eid}",)).fetchone()


def _services(method, url, kw):
    if url == outline.DGT_WFS:
        return FakeResponse(json_data=dgt_answer())
    if "overpass" in url:
        return FakeResponse(json_data=overpass_answer(way(HOUSE, levels=2), way(ANNEX)))
    raise AssertionError(f"unexpected request {method} {url}")


def test_a_scan_looks_up_each_home_once_and_only_at_a_building_pin(db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    at_house = dict(_home(db, {"lat": LAT, "lon": LON}))
    on_street = dict(_home(db, {"geo": {"lat": LAT, "lon": LON, "precision": "street"}}, eid="2"))
    a_plot = dict(_home(db, {"lat": LAT, "lon": LON}, title="Terreno rústico 5000 m2", eid="3"))
    assert outline.wanted(at_house)
    assert not outline.wanted(on_street) and not outline.wanted(a_plot)

    session = FakeSession(_services)
    assert outline.check_pending(db, session, [at_house, on_street, a_plot]) == 1
    row = dict(db.execute("SELECT * FROM listings WHERE id = 'eleiloes:1'").fetchone())
    stored = json.loads(row["raw_json"])["outline"]
    assert stored["house"]["floor_m2"] == pytest.approx(240, abs=4) and stored["annexes"]["count"] == 1
    assert [c[0] for c in session.calls] == ["GET", "POST"]           # the parcel, then the buildings

    assert outline.check_pending(db, session, [row]) == 0                # not asked again
    panel = listing_info.outline_panel(row)
    assert panel["plot"] and len(panel["buildings"]) == 2 and panel["pin"] == [LAT, LON]

    # A pin you verify somewhere else is another building: the old outline is
    # not shown, and the new pin is looked up.
    raw = json.loads(row["raw_json"])
    raw["verified_geo"] = {"lat": LAT + 0.01, "lon": LON, "precision": "verified"}
    moved = {**row, "raw_json": json.dumps(raw)}
    assert listing_info.outline_panel(moved) is None and outline.wanted(moved)


def test_a_failed_lookup_is_tried_next_scan(db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    home = dict(_home(db, {"lat": LAT, "lon": LON}))

    def down(method, url, kw):
        return FakeResponse(status=504)
    assert outline.check_pending(db, FakeSession(down), [home]) == 0
    row = dict(db.execute("SELECT * FROM listings WHERE id = 'eleiloes:1'").fetchone())
    assert "outline" not in json.loads(row["raw_json"]) and outline.wanted(row)


def test_the_listing_panel_gets_the_outline(db, monkeypatch):
    import config
    import dashboard
    monkeypatch.setattr("time.sleep", lambda *_: None)
    config.save_config({"filters": {}})
    home = dict(_home(db, {"lat": LAT, "lon": LON}))
    outline.check_pending(db, FakeSession(_services), [home])
    dashboard.app.config["TESTING"] = True
    detail = dashboard.app.test_client().get("/api/listing?id=eleiloes:1").get_json()
    got = detail["outline"]
    assert any(r["label"] == "Yard or garden" for r in got["rows"])
    assert got["plot"] and len(got["buildings"]) == 2
