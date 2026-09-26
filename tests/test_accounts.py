"""Signing in to sites with the owner's own account (accounts.py)."""
import json

import pytest

import accounts
from conftest import FakeResponse

LOGIN_PAGE = """<html><script>var model = { partID: `SIVI`, path: `/vendasat/lista/vendas`,
  _csrf: { parameterName: `_csrf`, token: `tok-123` }, authVersion: stringOrNull('2') };</script></html>"""
SSO_FORM = """<html><body onload="document.forms[0].submit()"><form method="post"
  action="https://vendas.portaldasfinancas.gov.pt/vendasat/saml"><input type="hidden" name="SAMLResponse"
  value="abc&#61;"/></form></body></html>"""


@pytest.fixture(autouse=True)
def fake_dpapi(monkeypatch):
    """CI runs on Linux: stand in for Windows DPAPI with a reversible marker."""
    monkeypatch.setattr(accounts, "protect", lambda text: "enc:" + text[::-1])
    monkeypatch.setattr(accounts, "unprotect", lambda blob: blob[4:][::-1])


class FlowSession:
    """Plays the acesso.gov.pt sign-in: login page, login POST, SSO form, the site."""

    def __init__(self, after_login):
        self.after_login, self.calls = after_login, []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        resp = FakeResponse(LOGIN_PAGE)
        resp.url = "https://www.acesso.gov.pt/v2/loginForm?partID=SIVI"
        return resp

    def post(self, url, data=None, **kw):
        self.calls.append(("POST", url, data))
        if url.endswith("/v2/login"):
            resp = FakeResponse(self.after_login)
            resp.url = "https://www.acesso.gov.pt/v2/login"
            return resp
        resp = FakeResponse("<h1>Vendas</h1>")
        resp.url = "https://vendas.portaldasfinancas.gov.pt/vendasat/lista/vendas"
        return resp


def test_signing_in_to_acesso_gov_follows_the_single_sign_on_back():
    s = FlowSession(SSO_FORM)
    accounts.LOGINS["financas"]["login"](s, "123456789", "a-password")
    login = next(d for m, u, d in s.calls if m == "POST" and u.endswith("/v2/login"))
    assert login == {"username": "123456789", "password": "a-password", "selectedAuthMethod": "N",
                     "authVersion": "2", "_csrf": "tok-123", "partID": "SIVI", "path": "/vendasat/lista/vendas"}
    sso = next(d for m, u, d in s.calls if m == "POST" and u.endswith("/saml"))
    assert sso == {"SAMLResponse": "abc="}


def test_an_sms_code_or_a_wrong_password_stops_the_sign_in():
    with pytest.raises(accounts.LoginNeedsCode):
        accounts.LOGINS["financas"]["login"](FlowSession('<input name="codigoSms2Fa">'), "1", "p")
    with pytest.raises(accounts.LoginFailed, match="Password errada"):
        accounts.LOGINS["financas"]["login"](FlowSession('{"field":"password","errorMsg":"Password errada"}'), "1", "p")
    with pytest.raises(accounts.LoginFailed, match="CAPTCHA"):
        accounts.LOGINS["financas"]["login"](FlowSession('<div class="g-recaptcha"></div>'), "1", "p")


def test_a_failure_pauses_the_site_and_tells_the_owner_once(db, monkeypatch):
    import telegram_alert
    sent = []
    monkeypatch.setattr(telegram_alert, "send_telegram", lambda token, chat, msg, **k: sent.append(msg) or True)
    cfg = {"accounts": {"financas": {"username": "123456789", "secret": accounts.protect("p")}},
           "telegram": {"enabled": True, "token": "t", "chat_id": "c"}}
    tries = []

    def failing(session, user, pwd):
        tries.append(user)
        raise accounts.LoginNeedsCode("sms")
    monkeypatch.setitem(accounts.LOGINS["financas"], "login", failing)
    assert accounts.account_session(db, cfg, "financas", session=object()) is None
    assert accounts.account_session(db, cfg, "financas", session=object()) is None   # paused: not tried again
    assert tries == ["123456789"] and len(sent) == 1 and "SMS" in sent[0]
    assert accounts.account_session(db, {}, "financas") is None                       # no account: nothing


def test_the_settings_page_never_gets_the_password(db, monkeypatch):
    import config
    import dashboard
    config.save_config({})
    dashboard.app.config["TESTING"] = True
    client = dashboard.app.test_client()
    assert client.post("/api/accounts", json={"site": "financas", "username": "123456789",
                                              "password": "my-secret"}).get_json() == {"ok": True}
    stored = config.load_config()["accounts"]["financas"]
    assert stored["secret"] == "enc:" + "my-secret"[::-1]                       # encrypted, never plain
    view = client.get("/api/accounts").get_json()["financas"]
    assert view["username"] == "123456789" and view["has_password"] is True
    assert "my-secret" not in json.dumps(view) and "secret" not in view
    # A blank password keeps the stored one; "forget" clears both.
    client.post("/api/accounts", json={"site": "financas", "username": "123456789", "password": ""})
    assert config.load_config()["accounts"]["financas"]["secret"] == stored["secret"]
    client.post("/api/accounts", json={"site": "financas", "forget": True})
    assert client.get("/api/accounts").get_json()["financas"]["has_password"] is False
    assert client.post("/api/accounts", json={"site": "nowhere", "username": "x"}).status_code == 400


FORWARD_FORM = """<html><form name="formCredential" id="forwardParticipantForm" method="post"
  action="https://vendas.portaldasfinancas.gov.pt/vendasat/lista/vendas"><input type="hidden" name="ssoID" value="s1"/>
  <input value="SIVI" type="hidden" name="partID"/></form><div id="root-data"></div></html>"""


def test_acesso_gov_forwards_with_a_form_that_has_no_visible_submit():
    """The real answer: a form of hidden inputs to the site, submitted by the page's own script."""
    s = FlowSession(FORWARD_FORM)
    accounts.LOGINS["financas"]["login"](s, "123456789", "a-password")
    forward = [(u, d) for m, u, d in s.calls if m == "POST" and "vendas.portaldasfinancas" in u]
    assert forward == [("https://vendas.portaldasfinancas.gov.pt/vendasat/lista/vendas",
                        {"ssoID": "s1", "partID": "SIVI"})]
