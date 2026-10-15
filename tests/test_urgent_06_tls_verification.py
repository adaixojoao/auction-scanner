import warnings

from sources import pt


def test_eleloes_session_verifies_tls_without_global_warning_changes(monkeypatch):
    calls = []
    sentinel = object()
    monkeypatch.setattr(pt, "make_session", lambda **kwargs: calls.append(kwargs) or sentinel)
    before = list(warnings.filters)
    assert pt._eleiloes_session() is sentinel
    assert calls == [{"verify": True, "timeout": 30}]
    assert warnings.filters == before
