import notifications


def test_only_displayed_alerts_are_marked(monkeypatch):
    rows = [{"id": f"lead-{n}", "score": 80, "title": f"Lead {n}", "url": "https://example.test/"}
            for n in range(75)]
    delivered = []
    monkeypatch.setattr(notifications, "load_best", lambda *a, **k: rows)
    monkeypatch.setattr(notifications, "not_yet_alerted", lambda db, channel, ids: set(ids))
    monkeypatch.setattr(notifications, "mixed_order", lambda item: item["id"])
    monkeypatch.setattr(notifications, "_build_email_html", lambda *a, **k: "test body")
    monkeypatch.setattr(notifications, "_send_email", lambda *a, **k: True)
    monkeypatch.setattr(notifications, "mark_alerted", lambda db, channel, ids: delivered.extend(ids))
    notifications.send_alerts(object(), {"enabled": True, "to_emails": ["owner@example.test"],
                                         "min_score": 50, "send_on": "new"})
    expected = sorted(rows, key=lambda item: item["id"])[:50]
    assert delivered == [row["id"] for row in expected]
    assert len(set(row["id"] for row in rows) - set(delivered)) == 25


def test_failed_email_marks_nothing(monkeypatch):
    rows = [{"id": "lead", "score": 80, "title": "Lead", "url": "https://example.test/"}]
    delivered = []
    monkeypatch.setattr(notifications, "load_best", lambda *a, **k: rows)
    monkeypatch.setattr(notifications, "not_yet_alerted", lambda db, channel, ids: set(ids))
    monkeypatch.setattr(notifications, "mixed_order", lambda item: item["id"])
    monkeypatch.setattr(notifications, "_build_email_html", lambda *a, **k: "test body")
    monkeypatch.setattr(notifications, "_send_email", lambda *a, **k: False)
    monkeypatch.setattr(notifications, "mark_alerted", lambda db, channel, ids: delivered.extend(ids))
    notifications.send_alerts(object(), {"enabled": True, "to_emails": ["owner@example.test"],
                                         "min_score": 50})
    assert delivered == []
