"""Your verified condition on a listing overrides the ad for scores and costs,
and survives on the listings row (schema v35)."""
import pytest

import config
import costs
import dashboard
import db as dbmod
import scoring
from db import OWNER_CONDITIONS, set_owner_condition


@pytest.fixture
def client(db):
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _home(**kw):
    base = {"source": "whitestar", "country": "PT", "title": "Moradia T2",
            "description": "Estado: Novo. Em bom estado.", "price": 15000, "area_m2": 144,
            "tipo": "moradia", "raw_json": "{}"}
    base.update(kw)
    return base


def test_owner_condition_outranks_a_good_ad():
    ad = _home()
    assert scoring.condition(ad) == "good"
    some = {**ad, "owner_condition": "some"}
    assert scoring.condition(some) == "some"
    assert scoring.owner_condition(some) == "some"
    sc, why = scoring.score_detail(some, mode="home")
    assert any("needs some work (you recorded)" in r for r in why)
    assert scoring.display_score(sc) < scoring.display_score(scoring.score_detail(ad, mode="home")[0])


def test_owner_condition_drives_the_repair_estimate():
    ad = _home()
    good = costs.renovation(ad)
    some = costs.renovation({**ad, "owner_condition": "some"})
    assert good and some
    assert some["low"] > good["high"]
    assert "you recorded that it needs work" in some["note"]


def test_clearing_owner_condition_returns_to_the_ad(db, add):
    add("whitestar", "11718", title="Moradia T2", description="Estado: Novo",
        price=15000, area_m2=144, tipo="moradia")
    lid = "whitestar:11718"
    assert set_owner_condition(db, lid, "some", "kitchen and baths")
    db.commit()
    row = dict(db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone())
    assert row["owner_condition"] == "some" and row["owner_condition_note"] == "kitchen and baths"
    assert row["owner_condition_at"]
    assert set_owner_condition(db, lid, None)
    db.commit()
    row = dict(db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone())
    assert row["owner_condition"] is None and row["owner_condition_note"] is None
    assert scoring.condition(row) == "good"


def test_owner_condition_rejects_unknown_words(db, add):
    add("whitestar", "x", title="Moradia", price=10000, area_m2=80)
    with pytest.raises(ValueError):
        set_owner_condition(db, "whitestar:x", "mint")
    assert set(OWNER_CONDITIONS) == {"good", "some", "heavy", "unknown"}


def test_migration_v35_adds_owner_condition_columns(tmp_path):
    path = str(tmp_path / "v34.db")
    conn = dbmod.connect(path)
    for col in ("owner_condition", "owner_condition_note", "owner_condition_at"):
        conn.execute(f"ALTER TABLE listings DROP COLUMN {col}")
    conn.execute("PRAGMA user_version = 34")
    conn.commit()
    conn.close()
    conn = dbmod.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    assert {"owner_condition", "owner_condition_note", "owner_condition_at"} <= cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
    conn.close()


def test_api_sets_and_clears_owner_condition(client, db, add):
    add("whitestar", "pinhel", title="Moradia T2", description="Estado: Novo",
        price=15000, area_m2=144, tipo="moradia")
    lid = "whitestar:pinhel"
    r = client.post("/api/listing/condition",
                    json={"id": lid, "condition": "some", "note": "needs renovations"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["owner_condition"] == "some" and body["owner_condition_note"] == "needs renovations"
    detail = client.get(f"/api/listing?id={lid}").get_json()
    assert detail["owner_condition"] == "some"
    found = dbmod.load_listings(db, include_hidden=True, where="id = ?", params=(lid,), mode="home")
    assert scoring.condition(found[0]) == "some"
    cleared = client.post("/api/listing/condition", json={"id": lid, "condition": ""})
    assert cleared.get_json()["owner_condition"] is None


def test_a_rescrape_does_not_wipe_owner_condition(db, add):
    from common import make_listing
    from db import upsert_listing
    add("whitestar", "keep", title="Moradia T2", description="Estado: Novo",
        price=15000, area_m2=144, tipo="moradia")
    lid = "whitestar:keep"
    set_owner_condition(db, lid, "heavy", "ruin inside")
    db.commit()
    upsert_listing(db, make_listing("whitestar", "keep", "PT", title="Moradia T2",
                                    description="Estado: Novo", price=14000, area_m2=144,
                                    tipo="moradia"))
    db.commit()
    row = dict(db.execute("SELECT owner_condition, owner_condition_note, price FROM listings WHERE id=?",
                          (lid,)).fetchone())
    assert row["owner_condition"] == "heavy" and row["owner_condition_note"] == "ruin inside"
    assert row["price"] == 14000
