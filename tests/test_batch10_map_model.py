"""Batch 10: the Listings map's display model (static/listings-map-model.js,
in Node when it is installed) and the stored pin each listing on a page
carries (geo.map_summary, through /api/listings)."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_map_model_preserves_unknown_and_invalid_locations():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; map model test not executed")
    script = r'''
const assert = require("node:assert/strict");
require("./static/listings-map-model.js");
const model = AuctionMapModel.pageModel([
  {id:"a",title:"Located",map:{lat:38.7,lon:-9.1,precision:"municipality",label:"Approximate town pin"}},
  {id:"b",title:"Missing"},
  {id:"c",title:"Invalid",map:{lat:91,lon:0}},
  {id:"d",title:"Numeric string",map:{lat:"38.7",lon:-9.1}},
  {id:"a",title:"Duplicate must not add count",map:{lat:0,lon:0}}
]);
assert.equal(model.total,4);
assert.equal(model.located.length,1);
assert.equal(model.unlocated.length,3);
assert.equal(model.located[0].precision,"municipality");
assert.equal(model.located[0].label,"Approximate town pin");
assert.equal(AuctionMapModel.coordinates({lat:0,lon:0})[0],0);
assert.equal(AuctionMapModel.coordinates({lat:NaN,lon:0}),null);
'''
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def _item(**raw):
    import json
    return {"id": "x:1", "country": "PT", "concelho": "Lisboa", "raw_json": json.dumps(raw)}


def test_map_summary_uses_stored_positions_only():
    import geo
    exact = geo.map_summary(_item(lat=38.71, lon=-9.14))
    assert exact["precision"] == "exact" and (exact["lat"], exact["lon"]) == (38.71, -9.14)
    assert "not a surveyed boundary" in exact["label"]
    assert exact["source_label"] == "coordinates given by the sale"
    town = geo.map_summary(_item(geo={"lat": 38.72, "lon": -9.13, "precision": "municipality", "query": "Lisboa"}))
    assert town["precision"] == "municipality"
    assert town["label"].startswith("Municipality estimate") and "approximate" in town["label"]
    # Known only by its municipality: no pin at the town centre, and nothing is looked up.
    assert geo.map_summary(_item()) is None
    assert geo.map_summary(_item(lat=95, lon=0)) is None
    assert geo.map_summary({"id": "x:2", "raw_json": "not json"}) is None


def test_the_page_carries_each_listings_map_summary(db, add):
    import config
    import dashboard
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    client = dashboard.app.test_client()
    add(external_id="pin", title="Moradia pin", price=20000, raw_json={"lat": 38.71, "lon": -9.14},
        date_end="2031-01-01T10:00:00+00:00")
    add(external_id="nopin", title="Moradia sem pin", price=21000, concelho="Lisboa",
        date_end="2031-01-01T10:00:00+00:00")
    items = {it["id"]: it for it in client.get("/api/listings?mode=home&min_score=0").get_json()["items"]}
    assert items["eleiloes:pin"]["map"]["precision"] == "exact"
    assert items["eleiloes:nopin"]["map"] is None          # still in the results, unlocated
    assert "raw_json" not in items["eleiloes:pin"]
