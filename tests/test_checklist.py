"""The due-diligence checklist: templates by how a sale works, your statuses,
the Offers gate and its override (checklist.py, dashboard)."""
import json

import pytest

import checklist
import dashboard
from db import connect


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000,
                        "proponente": {"nome": "Test Person", "nif": "123",
                                       "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _citius(add, external_id="c1", **fields):
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada", "agente_email": "ae@solic.pt"}
    add("citius", external_id, title="Moradia", price=30000, area_m2=120, raw_json=json.dumps(raw), **fields)
    return f"citius:{external_id}"


def _keys(ck):
    return [x["key"] for x in ck["items"]]


def test_each_sale_gets_the_checklist_of_how_it_is_sold():
    pt = checklist.build({"id": "citius:1", "source": "citius", "kind": "home"}, {}, {})
    assert pt["route"] == "pt_court" and pt["route_label"].startswith("Portugal: court sale")
    assert {"registry", "charges", "occupancy", "minimum", "tax_record", "location", "climate"} <= set(_keys(pt))
    assert sorted(pt["blocking_left"]) == ["charges", "registry"]
    assert not any(k.startswith("land_") for k in _keys(pt))             # a home: no rural-land items

    es = checklist.build({"id": "spain:1", "source": "spain", "kind": "rural_plot"}, {}, {})
    assert es["route"] == "es_boe" and "account" in _keys(es)
    assert {"land_access", "land_boundaries", "land_use", "land_water", "land_hazards"} <= set(_keys(es))
    assert "nota simple" in next(x["label"] for x in es["items"] if x["key"] == "registry")

    fr = checklist.build({"id": "france:1", "source": "france", "kind": "home"}, {}, {})
    assert fr["route"] == "fr_court" and {"lawyer", "deposit", "fees"} <= set(_keys(fr))
    assert fr["blocking_left"] == ["charges"]                             # title is the lawyer's search
    assert checklist.build({"id": "x:1", "source": "somewhere", "kind": "home"}, {}, {})["route"] == "generic"

    # occupancy and the exact location never block unless you say so
    for route, (_label, items) in checklist.TEMPLATES.items():
        blocking = checklist.default_blocking(route)
        assert "charges" in blocking and "occupancy" not in blocking and "location" not in blocking, route
        assert {it.category for it in items} <= {k for k, _t in checklist.CATEGORIES}, route
        assert len({it.key for it in items}) == len(items), route


def test_the_summary_counts_what_is_done_and_what_still_blocks():
    item = {"id": "citius:1", "source": "citius", "kind": "home"}
    ck = checklist.build(item, {}, {})
    assert ck["summary"] == f"0/{ck['total']} complete; 2 blocking items remain."
    rows = {"registry": {"status": "verified"}, "visit": {"status": "not_applicable"},
            "occupancy": {"status": "concern"}}
    ck = checklist.build(item, rows, {})
    assert ck["done"] == 2 and ck["blocking_left"] == ["charges"] and ck["concerns"] == ["occupancy"]
    assert ck["summary"] == f"2/{ck['total']} complete; 1 blocking item remains; 1 concern."
    # Settings can make the location block, or nothing
    assert checklist.build(item, rows, {"checklist": {"blocking": {"pt_court": ["location"]}}})["blocking_left"] \
        == ["location"]
    assert "nothing blocking" in checklist.build(item, rows, {"checklist": {"blocking": {"pt_court": []}}})["summary"]


def test_you_set_statuses_and_every_change_is_kept(client, db, add):
    lid = _citius(add)
    got = client.get(f"/api/checklist?id={lid}").get_json()
    assert got["route"] == "pt_court" and got["history"] == []
    assert ["verified", "Verified by you"] in got["statuses"]

    saved = client.post("/api/checklist", json={"id": lid, "key": "registry", "status": "requested"}).get_json()
    assert next(x for x in saved["items"] if x["key"] == "registry")["status"] == "requested"
    saved = client.post("/api/checklist", json={
        "id": lid, "key": "registry", "status": "verified", "notes": "no mortgage left",
        "reference": "certidão code 1234-5678", "checked_on": "2026-09-01", "verified_by": "me"}).get_json()
    reg = next(x for x in saved["items"] if x["key"] == "registry")
    assert (reg["status"], reg["notes"], reg["reference"], reg["checked_on"], reg["verified_by"]) == \
        ("verified", "no mortgage left", "certidão code 1234-5678", "2026-09-01", "me")
    assert saved["blocking_left"] == ["charges"]

    hist = client.get(f"/api/checklist?id={lid}").get_json()["history"]
    assert [(h["old_status"], h["new_status"]) for h in hist] == [("requested", "verified"), (None, "requested")]

    assert client.post("/api/checklist", json={"id": lid, "key": "land_water", "status": "verified"}).status_code \
        == 400                                                           # not on a home's checklist
    assert client.post("/api/checklist", json={"id": lid, "key": "registry", "status": "done"}).status_code == 400
    assert client.post("/api/checklist", json={"id": lid, "key": "registry", "status": "verified",
                                               "checked_on": "yesterday"}).status_code == 400
    assert client.post("/api/checklist", json={"id": "nope:1", "key": "registry",
                                               "status": "verified"}).status_code == 404


def test_an_offer_with_blocking_items_needs_your_reason_and_keeps_it(client, db, add):
    lid = _citius(add)
    view = client.get("/api/offers").get_json()["review"][0]
    assert view["checklist"]["blocking_left"] == 2 and "2 blocking items remain" in view["checklist"]["summary"]

    refused = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00"})
    assert refused.status_code == 409 and refused.get_json()["checklist_gate"]
    assert "2 blocking items remain" in refused.get_json()["error"]
    assert client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00",
                                                 "checklist_override": "ok"}).status_code == 409   # too short
    info = client.post("/api/offers/sent", json={"id": lid, "type": "pt_info"})   # a request is never held
    assert info.status_code == 200

    sent = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00",
                                                 "checklist_override": "the agent confirmed no charges"}).get_json()
    row = db.execute("SELECT checklist_summary FROM carta_log WHERE id = ?", (sent["log_id"],)).fetchone()
    assert "2 blocking items remain" in row["checklist_summary"]
    override = [h for h in checklist.history(db, lid) if h["action"] == "override"]
    assert override == [{"item_key": None, "carta_log_id": sent["log_id"], "action": "override",
                         "old_status": None, "new_status": None, "reason": "the agent confirmed no charges",
                         "created_at": override[0]["created_at"]}]
    offer = next(o for o in client.get("/api/offers").get_json()["sent"] if o["offer"]["is_offer"])["offer"]
    assert "2 blocking items remain" in offer["checklist_summary"]


def test_an_offer_with_nothing_blocking_goes_without_a_reason(client, db, add):
    lid = _citius(add)
    for key in ("registry", "charges"):
        client.post("/api/checklist", json={"id": lid, "key": key, "status": "verified"})
    sent = client.post("/api/offers/sent", json={"id": lid, "bid": "26.000,00"})
    assert sent.status_code == 200
    assert not [h for h in checklist.history(db, lid) if h["action"] == "override"]


def test_the_blocking_items_setting(client):
    import config
    assert config.DEFAULTS["checklist"] == {"blocking": {}}
    html = client.get("/settings").get_data(as_text=True)
    assert "ck-blocking" in html and 'data-route="fr_court"' in html
    assert client.post("/api/settings", json={"checklist": {"blocking": {"atlantis": []}}}).status_code == 400
    assert client.post("/api/settings", json={"checklist": {"blocking": {"pt_court": ["moat"]}}}).status_code == 400
    assert client.post("/api/settings", json={"checklist": {"blocking": {"pt_court": ["registry", "location"]}}}
                       ).get_json() == {"ok": True}
    assert config.load_config()["checklist"]["blocking"]["pt_court"] == ["registry", "location"]


def test_the_printable_summary(client, db, add):
    lid = _citius(add)
    client.post("/api/checklist", json={"id": lid, "key": "registry", "status": "verified",
                                        "notes": "clean", "verified_by": "me"})
    ck = checklist.build(dict(id=lid, source="citius", kind="home", title="Moradia"), checklist.stored(db, lid), {})
    text = checklist.as_text({"id": lid, "title": "Moradia"}, ck)
    assert "\n\n" not in text and "not legal advice" in text
    assert "[x] Checked the land registry" in text and "by me" in text and "      clean" in text
    pdf = client.get(f"/api/checklist.pdf?id={lid}")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert "checklist_citius_c1.pdf" in pdf.headers["Content-Disposition"]
    assert client.get("/api/checklist.pdf?id=nope:1").status_code == 404


def test_a_version_9_database_gets_the_checklist_tables(tmp_path):
    path = str(tmp_path / "v9.db")
    conn = connect(path)
    conn.execute("DROP TABLE checklist_items")
    conn.execute("DROP TABLE checklist_log")
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM checklist_log").fetchone()[0] == 0
    assert "checklist_summary" in {r[1] for r in conn.execute("PRAGMA table_info(carta_log)")}
    conn.close()
