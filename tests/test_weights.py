"""The owner's weights (Settings → How much each thing counts)."""
import pytest

import dashboard
from scoring import score_detail


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()

HOME = dict(source="eleiloes", country="PT", title="Moradia T3", tipo="moradia", area_m2=120, price=20000,
            beach={"km": 0.3, "text": "0.3 km from the beach"})


def test_weights_scale_bonuses_not_rules():
    plain, _ = score_detail({**HOME, "beach": None})
    normal, _ = score_detail(HOME)
    off, reasons = score_detail(HOME, targets={"weights": {"beach": 0}})
    double, _ = score_detail(HOME, targets={"weights": {"beach": 2}})
    bonus = normal - plain
    assert bonus > 5 and off == plain and "0.3 km from the beach" not in reasons
    assert abs((double - plain) - 2 * bonus) < 0.01
    hot = {**HOME, "climate": {"hot_days": {"rcp45_2071-2100": 30}}}
    _, reasons = score_detail(hot, targets={"weights": {"heat": 0}})
    assert any(r.startswith("rejected: too hot") for r in reasons)      # a rule stays a rule
    assert score_detail(HOME, targets={"weights": {"beach": "x"}})[0] == normal   # nonsense: weight 1


def test_weights_are_saved_and_checked(client):
    ok = client.post("/api/settings", json={"filters": {"weights": {"beach": 1.5, "price": 0}}})
    assert ok.status_code == 200
    assert client.get("/api/settings").get_json()["filters"]["weights"] == {"beach": 1.5, "price": 0}
    assert client.post("/api/settings", json={"filters": {"weights": {"beach": 3}}}).status_code == 400
    assert client.post("/api/settings", json={"filters": {"weights": {"nope": 1}}}).status_code == 400
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="w_home_beach"' in page and 'id="w_invest_beach"' in page and 'id="w_land_price"' in page
    nested = client.post("/api/settings", json={"filters": {"weights": {"home": {"beach": 0}, "invest": {"beach": 2}}}})
    assert nested.status_code == 200
    saved = client.get("/api/settings").get_json()["filters"]["weights"]
    assert saved["home"]["beach"] == 0 and saved["invest"]["beach"] == 2


def test_each_goal_keeps_its_own_dials():
    """Turning the beach off for a home leaves the holiday-let beach alone."""
    off_home = score_detail(HOME, targets={"weights": {"home": {"beach": 0}}})[0]
    loud_invest = score_detail(HOME, mode="invest", targets={"weights": {"invest": {"beach": 2}}})[0]
    assert off_home == score_detail({**HOME, "beach": None})[0]
    assert loud_invest > score_detail(HOME, mode="invest")[0]
    # A flat dict from an older config.json still applies to the goal being scored.
    assert score_detail(HOME, targets={"weights": {"beach": 0}})[0] == off_home
