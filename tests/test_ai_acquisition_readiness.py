import copy
from pathlib import Path

import pytest

import dashboard


@pytest.mark.parametrize("verdict,expected", [
    ("BUY", "REVIEW"), ("REVIEW", "REVIEW"), ("SKIP", "SKIP"),
])
def test_ai_verdict_is_advisory_and_has_no_bid(verdict, expected):
    result = dashboard._advisory_acquisition_result({
        "verdict": verdict, "recommended_bid": "20000", "max_bid": "30000",
        "summary": "Synthetic advice",
    })
    assert result["verdict"] == expected
    assert result["acquisition_ready"] is False
    assert result["advisory_only"] is True
    assert result["bid_suggestions_available"] is False
    assert result["recommended_bid"] is None
    assert result["max_bid"] is None
    assert "readiness is not established" in result["readiness_notice"]


def test_model_readiness_flags_cannot_unlock_purchase():
    result = dashboard._advisory_acquisition_result({
        "verdict": "BUY", "acquisition_ready": True,
        "advisory_only": False, "bid_suggestions_available": True,
    })
    assert result["verdict"] == "REVIEW"
    assert result["acquisition_ready"] is False
    assert result["bid_suggestions_available"] is False


def test_climate_original_bid_cannot_rescue_withheld_suggestions():
    original = {
        "verdict": "BUY", "recommended_bid": "10000", "max_bid": "15000",
        "climate_guardrail": {
            "applied": True, "was": {"recommended_bid": "20000", "max_bid": "30000"},
            "text": "fixture adjustment", "grade": "caution",
        },
    }
    before = copy.deepcopy(original)
    result = dashboard._advisory_acquisition_result(original)
    assert "was" not in result["climate_guardrail"]
    assert result["climate_guardrail"]["applied"] is False
    assert original == before


def test_error_response_is_preserved():
    original = {"error": "fixture analysis failure"}
    assert dashboard._advisory_acquisition_result(original) == original


def test_api_applies_server_safeguard_to_model_buy(monkeypatch):
    import analysis

    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    monkeypatch.setattr(dashboard, "_config", lambda: {"filters": {}, "climate": {}})
    monkeypatch.setattr(analysis, "analyze_property", lambda data: {
        "verdict": "BUY", "recommended_bid": "20000", "max_bid": "30000",
        "acquisition_ready": True,
    })
    # No listing ID: this also exercises advice that has no resolved DB listing.
    response = dashboard.app.test_client().post("/api/analyze-property", json={
        "title": "Synthetic fixture", "acquisition_ready": True,
    })
    assert response.status_code == 200
    result = response.get_json()
    assert result["verdict"] == "REVIEW"
    assert result["acquisition_ready"] is False
    assert result["recommended_bid"] is None
    assert result["max_bid"] is None


def test_api_error_keeps_existing_failure_status(monkeypatch):
    import analysis

    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    monkeypatch.setattr(dashboard, "_config", lambda: {"filters": {}, "climate": {}})
    monkeypatch.setattr(analysis, "analyze_property", lambda data: {"error": "fixture failure"})
    response = dashboard.app.test_client().post(
        "/api/analyze-property", json={"title": "Synthetic fixture"}
    )
    assert response.status_code == 502
    assert response.get_json() == {"error": "fixture failure"}


def test_offers_template_exposes_advisory_notice_and_no_apply_ai_button():
    template = (Path(dashboard.HERE) / "templates" / "offers.html").read_text(encoding="utf-8")
    assert "d.readiness_notice" in template
    assert "Use suggested bid" not in template
    assert "Withheld — readiness not established" in template
