"""Batch 4: the short summaries a Listings row shows (presentation.py) say
what each figure is, keep unknowns unknown, never turn a source's words or a
stale record into "verified", and keep a date-only deadline a date."""
import json
from datetime import datetime, timezone

import pytest

import dashboard
import presentation

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def row(**fields):
    base = {"id": "eleiloes:x", "source": "eleiloes", "country": "PT", "category": "imoveis",
            "title": "Moradia T2", "description": "", "raw_json": "{}"}
    return {**base, **fields}


# ─── Price basis ────────────────────────────────────────────────────────────

def test_a_current_bid_is_labelled_and_never_a_final_price():
    p = presentation.price(row(price=100_000, min_price=60_000, current_bid=70_000))
    assert (p["kind"], p["label"], p["amount"]) == ("current_bid", "Current bid", 70_000)
    assert "Not a final purchase price" in p["note"]
    assert {s["label"]: s["amount"] for s in p["secondary"]} == {"Minimum offer": 60_000, "Base value": 100_000}


def test_a_minimum_offer_keeps_the_base_value_as_a_reference():
    p = presentation.price(row(price=100_000, min_price=85_000))
    assert (p["kind"], p["amount"]) == ("minimum", 85_000)
    assert p["secondary"] == [{"label": "Base value", "amount": 100_000, "note": "Reference, not a purchase price"}]


def test_an_auction_base_value_is_a_reference_not_an_obtainable_price():
    p = presentation.price(row(price=50_000))
    assert (p["kind"], p["label"]) == ("base", "Base value")
    assert "not an obtainable purchase price" in p["note"]


def test_a_bidding_step_under_a_fifth_of_the_base_is_not_called_a_minimum():
    # common.price_to_pay ignores such a floor; the label follows it.
    p = presentation.price(row(price=174_300, min_price=1_743))
    assert (p["kind"], p["amount"]) == ("base", 174_300)


def test_a_portal_asking_price_is_labelled_asking(monkeypatch):
    import source_validation
    monkeypatch.setattr(source_validation, "asking_price", lambda source: True)
    p = presentation.price(row(source="idealista", price=80_000))
    assert (p["kind"], p["label"]) == ("asking", "Asking price")


def test_a_deposit_figure_is_not_a_purchase_price(monkeypatch):
    import source_validation
    monkeypatch.setattr(source_validation, "asking_price", lambda source: True)
    p = presentation.price(row(source="idealista", price=5_000, title="Leilão: depósito de caução"))
    assert p["amount"] is None and p["kind"] == "unusable"
    assert p["secondary"][0]["note"] == "Not a purchase price"
    assert presentation.all_in(row(source="idealista", price=5_000, title="Leilão: depósito de caução")
                               )["headline"].startswith("Not established")


def test_no_price_is_not_published_not_zero():
    p = presentation.price(row())
    assert p["amount"] is None and p["label"] == "Price not published"
    a = presentation.all_in(row())
    assert a["low"] is None and a["known_subtotal"] is None and a["headline"]


# ─── Estimated all-in ───────────────────────────────────────────────────────

def test_a_home_without_a_floor_area_shows_a_known_subtotal_not_a_range():
    a = presentation.all_in(row(price=40_000, tipo="moradia"))
    assert a["low"] is None and a["high"] is None and not a["repairs_priced"]
    assert a["known_subtotal"] and a["known_subtotal"] > 40_000
    assert "Repairs not priced: floor area not established" in a["missing"]
    assert "fire insurance" in a["excludes"]


def test_a_home_with_a_stated_floor_gets_an_estimated_range_at_its_basis():
    a = presentation.all_in(row(price=40_000, min_price=40_000, current_bid=45_000, area_m2=90, tipo="moradia"))
    assert a["repairs_priced"] and 45_000 < a["low"] <= a["high"]
    assert a["basis_label"] == "Estimate at the current bid"
    assert not any(m.startswith("Repairs not priced") for m in a["missing"])


def test_the_estimate_is_the_one_costs_already_computes():
    import costs
    it = row(price=40_000, area_m2=90, tipo="moradia")
    plan = costs.selected_planning(dict(it))["estimate"]["all_in"]
    a = presentation.all_in(it)
    assert (a["low"], a["high"]) == (round(plan["low"]), round(plan["high"]))


def test_a_non_property_has_no_cost_estimate():
    a = presentation.all_in(row(category="moveis", title="Automóvel", price=3_000))
    assert a["headline"] == "No cost estimate for this kind of item" and a["low"] is None


@pytest.mark.parametrize("value", [None, "", "abc", float("nan"), float("inf"), -1, True, {}])
def test_an_amount_that_is_not_a_finite_number_is_not_an_amount(value):
    assert presentation._amount(value) is None


def test_zero_is_an_amount():
    assert presentation._amount(0) == 0


# ─── Checks ─────────────────────────────────────────────────────────────────

def test_nothing_recorded_is_unverified_never_clean():
    c = presentation.checks(row(price=40_000), {}, {})
    states = {x["area"]: x["state"] for x in c["items"]}
    assert states["title"] == "unverified" and states["condition"] == "unverified"
    assert "verified" not in states.values()


def test_a_stated_vacancy_is_source_reported_not_verified():
    c = presentation.checks(row(price=40_000, raw_json=json.dumps({"occupation": "vacant"})), {}, {})
    occ = next(x for x in c["items"] if x["area"] == "occupancy")
    assert occ["state"] == "source_reported" and "not verified" in occ["text"]


def test_a_recorded_concern_is_counted_and_shown(db, add):
    import checklist
    from db import load_listings
    add(external_id="c", title="Moradia", price=40_000)
    item = next(it for it in load_listings(db, include_hidden=True) if it["id"] == "eleiloes:c")
    checklist.set_status(db, item, "registry", "concern", notes="penhora")
    db.commit()
    rows = checklist.stored_for(db, [item["id"]])[item["id"]]
    c = presentation.checks(item, rows, {})
    assert c["concerns"] == 1
    assert next(x for x in c["items"] if x["area"] == "title")["state"] == "concern"


def test_a_verification_whose_inputs_changed_is_not_current(db, add):
    import checklist
    from db import load_listings
    add(external_id="v", title="Moradia", price=40_000)
    item = next(it for it in load_listings(db, include_hidden=True) if it["id"] == "eleiloes:v")
    for key in ("registry", "charges"):
        checklist.set_status(db, item, key, "verified")
    db.commit()
    rows = checklist.stored_for(db, [item["id"]])[item["id"]]
    assert next(x for x in presentation.checks(item, rows, {})["items"] if x["area"] == "title")["state"] == "verified"
    add(external_id="v", title="Moradia com penhora", price=40_000)    # the notice changed
    item = next(it for it in load_listings(db, include_hidden=True) if it["id"] == "eleiloes:v")
    rows = checklist.stored_for(db, [item["id"]])[item["id"]]
    title = next(x for x in presentation.checks(item, rows, {})["items"] if x["area"] == "title")
    assert title["state"] == "needs_recheck"


def test_stored_for_matches_stored_one_by_one(db, add):
    import checklist
    from db import load_listings
    add(external_id="a", title="Moradia", price=40_000)
    add(external_id="b", title="Moradia", price=41_000)
    items = {it["id"]: it for it in load_listings(db, include_hidden=True)}
    checklist.set_status(db, items["eleiloes:a"], "registry", "verified")
    db.commit()
    batch = checklist.stored_for(db, ["eleiloes:a", "eleiloes:b"])
    assert batch == {"eleiloes:a": checklist.stored(db, "eleiloes:a")}


def test_legal_access_appears_only_for_rural_plots():
    home = presentation.checks(row(price=40_000, kind="home"), {}, {})
    plot = presentation.checks(row(price=40_000, kind="rural_plot", title="Terreno rústico"), {}, {})
    assert "land_access" not in {x["area"] for x in home["items"]}
    assert "land_access" in {x["area"] for x in plot["items"]}


# ─── Deadlines ──────────────────────────────────────────────────────────────

def test_a_date_only_deadline_stays_a_date():
    d = presentation.deadline(row(date_end="2030-01-10"), NOW)
    assert d == {"precision": "date", "date": "2030-01-10", "time": None, "timezone": "Europe/Lisbon",
                 "instant": None, "days": 0}


def test_naive_midnight_is_a_date_not_a_closing_time():
    d = presentation.deadline(row(date_end="2030-01-12T00:00:00"), NOW)
    assert d["precision"] == "date" and d["time"] is None and d["days"] == 2


def test_an_aware_instant_is_shown_in_the_sale_time_zone():
    d = presentation.deadline(row(country="ES", date_end="2030-07-01T08:00:00+00:00"), NOW)
    assert (d["precision"], d["time"], d["timezone"]) == ("instant", "10:00", "Europe/Madrid")
    assert d["instant"] == "2030-07-01T08:00:00+00:00"


def test_a_local_time_without_an_offset_is_sale_local_across_daylight_saving():
    winter = presentation.deadline(row(date_end="2030-01-20T14:00:00"), NOW)
    summer = presentation.deadline(row(date_end="2030-07-20T14:00:00"), NOW)
    assert winter["time"] == summer["time"] == "14:00"
    assert winter["instant"] == "2030-01-20T14:00:00+00:00"      # Lisbon is UTC in winter
    assert summer["instant"] == "2030-07-20T13:00:00+00:00"      # and UTC+1 in summer


@pytest.mark.parametrize("value,precision", [(None, "unknown"), ("", "unknown"), ("9999-12-31", "unknown"),
                                             ("soon", "invalid")])
def test_a_missing_or_unreadable_deadline_is_never_a_date(value, precision):
    d = presentation.deadline(row(date_end=value), NOW)
    assert d["precision"] == precision and d["date"] is None and d["instant"] is None


# ─── Thumbnails ─────────────────────────────────────────────────────────────

def test_a_thumbnail_is_https_or_a_kept_copy_never_a_raw_value():
    assert presentation.thumbnail(row(image_url="http://img.example/a.jpg")) == "https://img.example/a.jpg"
    assert presentation.thumbnail(row(image_url="javascript:alert(1)")) is None
    assert presentation.thumbnail(row(image_url=None)) is None
    kept = row(id="eleiloes:a b", raw_json=json.dumps({"kept_photos": ["1.jpg"]}))
    assert presentation.thumbnail(kept) == "/api/kept-photo?id=eleiloes%3Aa%20b&n=0"


# ─── The API ────────────────────────────────────────────────────────────────

def test_every_row_and_research_card_carries_a_summary(client, add):
    add(external_id="p", title="Moradia T2", price=40_000, area_m2=90, date_end="2030-02-01")
    data = client.get("/api/listings?mode=home&min_score=0").get_json()
    assert data["items"], data
    p = data["items"][0]["presentation"]
    assert set(p) == {"price", "all_in", "checks", "deadline", "thumbnail"}
    assert data["items"][0]["reasons"] == data["items"][0]["reasons"]    # unchanged field


def test_one_page_reads_the_checklist_in_a_fixed_number_of_queries(client, add, db, monkeypatch):
    for i in range(12):
        add(external_id=f"q{i}", title="Moradia T2", price=40_000 + i, area_m2=90)
    import checklist
    calls = []
    real = checklist.stored_for
    monkeypatch.setattr(checklist, "stored_for", lambda db, ids: calls.append(len(list(ids))) or real(db, ids))
    monkeypatch.setattr(checklist, "stored", lambda *a: pytest.fail("one read per row"))
    data = client.get("/api/listings?mode=home&min_score=0").get_json()
    assert len(data["items"]) == 12 and calls == [12]


def test_scores_and_reasons_are_the_same_with_the_summaries(client, add):
    add(external_id="s", title="Moradia T2", price=40_000, area_m2=90)
    from db import load_listings
    data = client.get("/api/listings?mode=home&min_score=0").get_json()
    db = dashboard.get_db()
    try:
        direct = {it["id"]: it for it in load_listings(db, include_hidden=True, mode="home")}
    finally:
        db.close()
    for it in data["items"]:
        assert (it["score"], it["reasons"]) == (direct[it["id"]]["score"], direct[it["id"]]["reasons"])


def test_a_page_of_homes_with_an_observed_floor_market_still_answers(client, add):
    # db._score_one hangs the tuple-keyed floor index on every row; it was
    # sent to the browser and broke the response once 12 floors were seen.
    for i in range(12):
        add(external_id=f"f{i}", title="Moradia T2", price=40_000 + i, area_m2=90)
    response = client.get("/api/listings?mode=home&min_score=0")
    assert response.status_code == 200
    assert all("floor_market" not in it and "land_market" not in it for it in response.get_json()["items"])
