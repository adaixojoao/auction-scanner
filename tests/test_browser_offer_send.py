"""Alpha on the production Offers page: one operation per draft, and an
uncertain send is never offered as a blind retry. In-process Flask on a
temporary database (the harness of tests/test_browser_listings.py); every
e-mail request is answered by the test, nothing is sent."""
import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_browser_listings as harness  # noqa: E402

browser, app = harness.browser, harness.app
BASE = harness.BASE


def _ready(app, add):
    import config
    raw = {"processo": "165/10.3TBMRA, Juízo", "tribunal": "Juízo de Moura",
           "modalidade": "Venda mediante propostas em carta fechada", "agente_email": "ae@solic.example"}
    add(source="citius", external_id="c1", title="Moradia citius", price=30000, area_m2=120,
        raw_json=raw, date_end="2031-01-01T10:00:00+00:00")
    cfg = config.load_config()
    config.save_config({**cfg, "notifications": {"smtp_host": "smtp.example.invalid", "smtp_port": 587,
                                                 "smtp_user": "me@example.invalid", "smtp_password": "pw"}})
    app.client.post("/api/listings/status", json={"id": "citius:c1", "status": "shortlisted"})
    app.page.on("dialog", lambda d: d.accept())
    app.page.goto(f"{BASE}/offers")
    app.page.wait_for_selector('.oitem[data-key]')
    app.page.click('.oitem[data-key]')
    app.page.wait_for_selector("#send-btn")
    app.page.select_option("#ltype", "pt_info")
    app.page.wait_for_selector("#send-btn:not([disabled])")
    app.page.fill("#to", "ae@solic.example")
    app.page.dispatch_event("#to", "input")


def test_an_uncertain_send_is_not_offered_again(app, add):
    _ready(app, add)
    app.override["/api/offers/email"] = lambda q, b: (409, {
        "error": "The connection broke while this e-mail was being sent, so it may have gone. "
                 "Do not send it again: look in the Sent folder of the mailbox first.",
        "uncertain": True, "state": "delivery_unknown", "operation_id": "x"})
    app.page.click("#send-btn")
    app.page.wait_for_selector('#send-status [role="alert"]')
    assert "Sent folder" in app.text("#send-status")
    assert app.page.is_disabled("#send-btn")
    assert "Outcome unknown" in app.text("#send-btn")


def test_a_failed_send_tried_again_reuses_the_same_operation(app, add):
    _ready(app, add)
    app.override["/api/offers/email"] = lambda q, b: (400, {"error": "The e-mail could not be sent: no route",
                                                            "state": "failed", "operation_id": "x"})
    app.page.click("#send-btn")
    app.page.wait_for_selector("#send-btn:not([disabled])")
    app.page.click("#send-btn")
    app.page.wait_for_function("() => true")
    app.page.wait_for_timeout(200)
    posted = [json.loads(b) for _m, _p, _q, b in app.calls("/api/offers/email", "POST")]
    assert len(posted) == 2 and posted[0]["operation_id"] == posted[1]["operation_id"]
    assert len(posted[0]["operation_id"]) >= 8
