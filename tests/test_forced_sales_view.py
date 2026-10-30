"""A Listings view of forced sales only (court, tax, social security, bank),
and Imovirtual's "ready_to_use" read as what it is (2026-10-30)."""
import json

import pytest

import dashboard
from scoring import condition


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000, "max_listings": 50})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def ids(data):
    return [it["id"] for it in data["items"]]


def test_forced_sales_only_keeps_court_and_bank_sales(client, add):
    add("citius", "court", title="Moradia", price=20000)
    add("cgd", "bank", title="Moradia", price=20000)
    add("imovirtual", "agent", title="Moradia", price=20000)
    every = set(ids(client.get("/api/listings?per_page=100").get_json()))
    forced = set(ids(client.get("/api/listings?per_page=100&forced=1").get_json()))
    assert "imovirtual:agent" in every
    assert forced == {"citius:court", "cgd:bank"}


def test_ready_to_use_is_not_good_condition():
    house = {"title": "Moradia", "description": "Moradia em zona rural", "source": "imovirtual",
             "raw_json": json.dumps({"construction_status": "ready_to_use"})}
    assert condition(house) == "unknown"
    assert condition({**house, "raw_json": json.dumps({"construction_status": "to_renovation"})}) == "some"
