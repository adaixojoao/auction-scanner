"""Settings refuse non-finite bid-cap numbers, and a bid cap whose inputs are
not finite says so instead of failing the offer pages."""
import pytest

import bidcap
import dashboard


@pytest.fixture(autouse=True)
def _dataset_prices(monkeypatch):
    """The test city table is labelled "city estimate", which never bids (F07);
    relabel it as a dataset so these tests exercise the bid arithmetic."""
    real = bidcap.local_price

    def labelled(item):
        eur_m2, source = real(item)
        return eur_m2, ("INE (test)" if source == "city estimate" else source)
    monkeypatch.setattr(bidcap, "local_price", labelled)


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _post(client, body: str):
    return client.post("/api/settings", data=body, content_type="application/json")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-1", "true", '"5"'])
@pytest.mark.parametrize("key", ["max_all_in", "margin_pct", "adviser_reserve_eur"])
def test_settings_refuse_non_finite_bid_cap_numbers(client, key, value):
    r = _post(client, f'{{"bid_cap": {{"{key}": {value}}}}}')
    assert r.status_code == 400 and "bid_cap" in r.get_json()["error"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-5", "false"])
def test_settings_refuse_non_finite_adviser_reserve_by_country(client, value):
    r = _post(client, f'{{"bid_cap": {{"adviser_reserve_by_country": {{"PT": {value}}}}}}}')
    assert r.status_code == 400


def test_settings_still_take_finite_bid_cap_numbers(client):
    r = _post(client, '{"bid_cap": {"max_all_in": 120000, "margin_pct": 12.5, '
                      '"adviser_reserve_by_country": {"PT": 0}}}')
    assert r.status_code == 200
    assert _post(client, '{"bid_cap": "lots"}').status_code == 400


@pytest.mark.parametrize("key", ["contingency_pct", "max_all_in", "adviser_reserve_eur"])
def test_a_non_finite_saved_setting_gives_no_bid_cap_rather_than_an_error(key):
    home = {"id": "citius:h1", "source": "citius", "country": "PT", "kind": "home",
            "title": "Moradia em bom estado", "description": "Habitação em bom estado de conservação",
            "concelho": "Porto", "area_m2": 100, "price": 80000, "category": "imoveis"}
    settings = {"filters": {}, "bid_cap": {"margin_pct": 15, "require_exact": False,
                                           "adviser_reserve_by_country": {}, key: float("nan")}}
    cap = bidcap.calculate_bid_cap(home, settings, bidcap.evidence_for(home))
    if cap["basis"] is None:
        pytest.skip("no valuation for this synthetic home")
    assert cap["recommended_bid"] is None and cap["absolute_max_bid"] is None
    assert any(r.startswith("No bid cap: invalid inputs") for r in cap["reasons"])
