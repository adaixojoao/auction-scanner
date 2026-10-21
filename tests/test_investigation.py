"""Acquisition pilot: evidence gaps, next actions and the investigation queue
(investigation.py). Real helpers throughout: nothing scored, verified, hidden
or bid here; a high score, a source's words or a stale record never verify."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import dashboard
import investigation


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def soon(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def home(add, eid="h", **fields):
    base = dict(title="Moradia T3 com quintal", price=60_000, tipo="moradia",
                district="Guarda", concelho="Guarda", date_end=soon(), url="https://www.e-leiloes.pt/leilao/x")
    return add(external_id=eid, **{**base, **fields})


def loaded(db, listing_id, mode="home"):
    from db import load_listings
    return load_listings(db, include_hidden=True, where="id = ?", params=(listing_id,), mode=mode)[0]


def assessed(db, listing_id, mode="home"):
    import checklist
    it = loaded(db, listing_id, mode)
    return investigation.assess(it, checklist.stored_for(db, [listing_id]).get(listing_id), {}, mode)


def dims(a):
    return {d["key"]: d for d in a["dimensions"]}


# ─── Evidence gaps ─────────────────────────────────────────────────────────

def test_a_high_score_with_nothing_recorded_stays_unresolved(db, add):
    home(add)
    a = assessed(db, "eleiloes:h")
    d = dims(a)
    for key in ("whole_ownership", "title", "possession", "intended_use", "project_costs"):
        assert d[key]["state"] == "unresolved", key
    assert "verified" not in {x["state"] for x in a["dimensions"]}
    assert a["purchase_readiness_established"] is False
    assert list(d) == list(investigation.DIMENSION_ORDER)


def test_a_source_reported_vacancy_is_not_verified_possession(db, add):
    home(add, raw_json=json.dumps({"occupation": "vacant"}))
    p = dims(assessed(db, "eleiloes:h"))["possession"]
    assert p["state"] == "unresolved" and p["source_reported"] is True


def test_a_verification_whose_inputs_changed_is_not_verified(db, add):
    import checklist
    home(add)
    for key in ("registry", "charges"):
        checklist.set_status(db, loaded(db, "eleiloes:h"), key, "verified")
    db.commit()
    t = dims(assessed(db, "eleiloes:h"))["title"]
    assert t["state"] == "verified" and any("not independent legal verification" in x for x in t["limitations"])
    home(add, title="Moradia T3 com penhora")       # the notice changed
    t = dims(assessed(db, "eleiloes:h"))["title"]
    assert t["state"] == "unresolved" and any("no longer matches" in x for x in t["limitations"])


def test_contradictions_stay_visible(db, add):
    import checklist
    home(add, raw_json=json.dumps({"occupation": "occupied"}))
    checklist.set_status(db, loaded(db, "eleiloes:h"), "occupancy", "verified")
    db.commit()
    a = assessed(db, "eleiloes:h")
    assert any("occupied" in c for c in a["contradictions"])
    assert a["next_action"]["key"] == "resolve_concern"


def test_a_share_is_a_concern_about_whole_ownership(db, add):
    home(add, title="Moradia T2 - 1/2 indivisa")
    a = assessed(db, "eleiloes:h")
    assert dims(a)["whole_ownership"]["state"] == "concern"
    assert a["next_action"]["key"] == "resolve_concern"


def test_a_verified_location_does_not_establish_access_title_or_possession(db, add):
    import checklist
    add(external_id="p", title="Terreno rústico", price=8_000, tipo="terreno rústico",
        date_end=soon())
    checklist.set_status(db, loaded(db, "eleiloes:p", "land"), "location", "verified")
    db.commit()
    d = dims(assessed(db, "eleiloes:p", "land"))
    for key in ("lawful_access", "title", "possession"):
        assert d[key]["state"] == "unresolved", key
    assert any("access right" in x for x in d["lawful_access"]["limitations"])


def test_the_purpose_is_the_goal_and_never_assumed_permitted(db, add):
    home(add)
    for mode in ("home", "invest", "both"):
        a = assessed(db, "eleiloes:h", mode)
        assert a["purpose"] == investigation.PURPOSES[mode]
        assert dims(a)["intended_use"]["state"] == "unresolved"


def test_unknown_costs_are_never_zero(db, add):
    home(add)          # no floor area: repairs cannot be priced
    c = dims(assessed(db, "eleiloes:h"))["project_costs"]
    assert c["state"] == "unresolved" and c["status"] == "incomplete"
    home(add, eid="np", price=None)
    a = assessed(db, "eleiloes:np")
    assert dims(a)["project_costs"]["status"] == "not_established" and a["all_in"]["low"] is None
    assert dims(a)["price_interpretation"]["state"] == "unresolved"


def test_the_assessment_changes_no_score(db, add):
    home(add)
    before = loaded(db, "eleiloes:h")
    a = assessed(db, "eleiloes:h")
    after = loaded(db, "eleiloes:h")
    assert a["opportunity"]["score"] == before["score"] == after["score"]


# ─── Next actions ──────────────────────────────────────────────────────────

def test_actions_follow_the_fixed_order(db, add):
    home(add)
    a = assessed(db, "eleiloes:h")
    actions = [a["next_action"], *a["more_actions"]]
    priorities = [n["priority"] for n in actions]
    assert priorities == sorted(priorities)
    keys = [n["key"] for n in actions]
    assert keys.index("check_title") < keys.index("establish_permitted_use") < keys.index("obtain_cost_evidence")
    assert all(n["requested_evidence"] for n in actions)


def test_a_recorded_request_verifies_nothing(db, add):
    import checklist
    home(add)
    for key in ("registry", "charges"):
        checklist.set_status(db, loaded(db, "eleiloes:h"), key, "requested")
    db.commit()
    a = assessed(db, "eleiloes:h")
    assert dims(a)["title"]["state"] == "unresolved"
    assert "does not verify" in a["next_action"]["completion_note"]


# ─── The queue ─────────────────────────────────────────────────────────────

def test_the_queue_is_bounded_ordered_and_scoped(client, db, add):
    for n in range(25):
        home(add, eid=f"q{n}", price=40_000 + n * 1_000)
    r = client.get("/api/investigation-queue?mode=home")
    data = r.get_json()
    assert r.status_code == 200 and data["limit"] == 20 and len(data["items"]) == 20
    assert data["eligible_total"] == 25 and "not every opportunity" in data["scope"]
    rows = {it["id"]: it for it in dashboard.all_listings("home")}
    ranked, _ = dashboard._investigation_rows("home")
    assert [e["listing_id"] for e in data["items"]] == [it["id"] for it in ranked[:20]]
    assert all(e["opportunity"]["score"] == rows[e["listing_id"]]["score"] for e in data["items"])
    page2 = client.get("/api/investigation-queue?mode=home&offset=20").get_json()
    assert len(page2["items"]) == 5


def test_dismissed_and_ended_listings_leave_the_queue_but_keep_their_history(client, db, add):
    import checklist
    from db import set_listing_status
    home(add, eid="open")
    home(add, eid="gone")
    home(add, eid="ended", date_end=(datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat())
    checklist.set_status(db, loaded(db, "eleiloes:gone"), "registry", "requested")
    set_listing_status(db, "eleiloes:gone", "dismissed")
    db.commit()
    data = client.get("/api/investigation-queue?mode=home").get_json()
    ids = [e["listing_id"] for e in data["items"]]
    assert ids == ["eleiloes:open"]
    assert [h["listing_id"] for h in data["history"]] == ["eleiloes:gone"]
    assert db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3     # nothing deleted


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "offset=-1"])
def test_bad_queue_parameters_are_refused(client, query):
    assert client.get("/api/investigation-queue?mode=home&" + query).status_code == 400


def test_an_unknown_listing_is_not_found(client):
    assert client.get("/api/investigation?id=nope:1").status_code == 404
    assert client.get("/api/investigation/brief.md?id=nope:1").status_code == 404


def test_the_drawer_carries_the_same_assessment(client, db, add):
    home(add)
    drawer = client.get("/api/listing?id=eleiloes:h&mode=home").get_json()["investigation"]
    alone = client.get("/api/investigation?id=eleiloes:h&mode=home").get_json()
    keep = ("dimensions", "next_action", "unresolved", "concerns", "contradictions", "purpose")
    assert {k: drawer[k] for k in keep} == {k: alone[k] for k in keep}


def test_the_page_renders_and_is_in_the_navigation(client):
    html = client.get("/investigations").get_data(as_text=True)
    assert 'id="inv-queue"' in html and 'href="/investigations"' in html and "investigation.js" in html


# ─── The brief ─────────────────────────────────────────────────────────────

def test_the_brief_is_inert_markdown_without_notes(client, db, add):
    home(add, eid="b/1 x", title="Moradia [click](javascript:alert(1)) <script>x</script> **bold** # head",
         url="https://www.e-leiloes.pt/leilao/x y<z>")
    db.execute("UPDATE listings SET user_note=? WHERE id=?", ("PRIVATE-NOTE-123", "eleiloes:b/1 x"))
    db.commit()
    r = client.get("/api/investigation/brief.md?id=eleiloes:b/1%20x&mode=home")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/markdown"
    assert r.headers["Content-Disposition"] == 'attachment; filename="investigation_eleiloes_b_1_x.md"'
    assert r.headers["Cache-Control"] == "no-store"
    assert "PRIVATE-NOTE-123" not in body
    assert "[click](" not in body and "<script>" not in body and "**bold**" not in body
    assert "\n# head" not in body
    assert "not established" in body.lower() or "unresolved" in body.lower()
    assert "<https://www.e-leiloes.pt/leilao/x%20y%3Cz%3E>" in body and "x y<z>" not in body


def test_markdown_escape_neutralises_markup():
    out = investigation._md("[a](http://x) `c` *e* _u_ <b> \\ !")
    for bad in ("[a](", "`c`", "*e*", "_u_", "<b>"):
        assert bad not in out


# ─── Integration: rows, cards, filters, recording ──────────────────────────

def test_listings_rows_carry_the_same_summary_as_the_assessment(client, db, add):
    home(add)
    item = next(it for it in client.get("/api/listings?mode=home").get_json()["items"] if it["id"] == "eleiloes:h")
    alone = client.get("/api/investigation?id=eleiloes:h&mode=home").get_json()
    assert item["investigation"] == investigation.summary(alone)
    assert item["investigation"]["next"]["key"] == alone["next_action"]["key"]
    assert "Title and charges" in item["investigation"]["open"]


def test_a_non_property_row_has_no_investigation(client, db, add):
    add(external_id="car", title="Automóvel ligeiro", price=3_000, date_end=soon())
    items = client.get("/api/listings?mode=home&min_score=0").get_json()["items"]
    car = [it for it in items if it["id"] == "eleiloes:car"]
    assert not car or car[0]["investigation"] is None


def test_the_queue_reads_the_listings_filters(client, db, add):
    home(add, eid="guarda", district="Guarda", concelho="Guarda")
    home(add, eid="faro", district="Faro", concelho="Faro", title="Moradia T3 em Faro")
    data = client.get("/api/investigation-queue?mode=home&search=faro").get_json()
    assert [e["listing_id"] for e in data["items"]] == ["eleiloes:faro"]
    assert data["filters"] == {"search": "faro"}
    listed = client.get("/api/listings?mode=home&search=faro").get_json()["items"]
    assert [it["id"] for it in listed] == ["eleiloes:faro"]
    assert client.get("/api/investigation-queue?mode=home&kind=castle").status_code == 400


def test_recording_through_the_checklist_recalculates_the_assessment(client, db, add):
    home(add)
    drawer = client.get("/api/listing?id=eleiloes:h&mode=home").get_json()["investigation"]
    assert drawer["record"]["items"]["registry"]["status"] == "not_started"
    for key in ("registry", "charges"):
        r = client.post("/api/checklist", json={"id": "eleiloes:h", "key": key, "status": "verified",
                                                "notes": "certidão read", "checked_on": "2026-10-01"})
        assert r.status_code == 200
    after = client.get("/api/listing?id=eleiloes:h&mode=home").get_json()["investigation"]
    assert dims(after)["title"]["state"] == "verified"
    assert after["next_action"]["key"] != "check_title"
    assert after["record"]["items"]["registry"]["notes"] == "certidão read"


def test_record_notes_stay_out_of_the_brief_and_queue(client, db, add):
    home(add)
    client.post("/api/checklist", json={"id": "eleiloes:h", "key": "registry", "status": "requested",
                                        "notes": "PRIVATE-CHECK-NOTE"})
    brief = client.get("/api/investigation/brief.md?id=eleiloes:h&mode=home").get_data(as_text=True)
    queue = client.get("/api/investigation-queue?mode=home").get_data(as_text=True)
    assert "PRIVATE-CHECK-NOTE" not in brief and "PRIVATE-CHECK-NOTE" not in queue
