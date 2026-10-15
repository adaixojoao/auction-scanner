import copy

import dashboard


def test_settings_get_does_not_return_secret_values(monkeypatch):
    cfg = {"notifications": {"smtp_password": "fake-smtp-secret"},
           "telegram": {"token": "fake-bot-secret"}, "ai": {"anthropic_key": "fake-ai-secret"}}
    monkeypatch.setattr(dashboard, "_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    response = dashboard.app.test_client().get("/api/settings")
    assert response.status_code == 200
    body = response.get_json()
    assert body["notifications"]["smtp_password"] is None
    assert body["telegram"]["token"] is None
    assert body["ai"]["anthropic_key"] is None
    assert body["notifications"]["has_smtp_password"] is True
    assert body["telegram"]["has_token"] is True
    assert body["ai"]["has_anthropic_key"] is True
    assert "fake-smtp-secret" not in response.get_data(as_text=True)
    assert "fake-bot-secret" not in response.get_data(as_text=True)
    assert "fake-ai-secret" not in response.get_data(as_text=True)


def test_empty_secret_form_fields_do_not_erase_existing_values(monkeypatch):
    changes = []
    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    monkeypatch.setattr("config.update_config", lambda value: changes.append(value) or {})
    response = dashboard.app.test_client().post("/api/settings", json={
        "notifications": {"smtp_password": "", "smtp_user": "owner@example.test"},
        "telegram": {"token": ""}, "ai": {"anthropic_key": ""}})
    assert response.status_code == 200
    assert changes == [{"notifications": {"smtp_user": "owner@example.test"}}]


def test_new_secret_can_be_saved_without_readback(monkeypatch):
    changes = []
    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    monkeypatch.setattr("config.update_config", lambda value: changes.append(value) or {})
    response = dashboard.app.test_client().post("/api/settings", json={"telegram": {"token": "new-fake-token"}})
    assert response.status_code == 200
    assert changes == [{"telegram": {"token": "new-fake-token"}}]
    assert "new-fake-token" not in response.get_data(as_text=True)


def test_explicit_secret_clear_is_supported(monkeypatch):
    changes = []
    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    monkeypatch.setattr("config.update_config", lambda value: changes.append(value) or {})
    response = dashboard.app.test_client().post("/api/settings", json={"clear_secrets": ["telegram.token"]})
    assert response.status_code == 200
    assert changes == [{"telegram": {"token": ""}}]


def test_unknown_clear_field_is_rejected(monkeypatch):
    changes = []
    monkeypatch.setitem(dashboard.app.config, "TESTING", True)
    monkeypatch.setattr("config.update_config", lambda value: changes.append(value) or {})
    response = dashboard.app.test_client().post("/api/settings", json={"clear_secrets": ["dashboard.host"]})
    assert response.status_code == 400
    assert changes == []
