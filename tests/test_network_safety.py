"""Regression tests for the offline network guard."""
import urllib.request

import pytest
import requests

SECRET_URL = "https://dummy-user:dummy-password@example.invalid/?token=dummy-token"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_requests_are_blocked_without_echoing_secrets(method):
    with requests.Session() as session:
        with pytest.raises(AssertionError, match="test tried to reach the network") as exc:
            session.request(method, SECRET_URL)
    for secret in ("dummy-user", "dummy-password", "dummy-token", "example.invalid"):
        assert secret not in str(exc.value)


@pytest.mark.parametrize("request_object", [False, True])
def test_urlopen_is_blocked(request_object):
    argument = urllib.request.Request(SECRET_URL) if request_object else SECRET_URL
    with pytest.raises(AssertionError, match="test tried to reach the network") as exc:
        urllib.request.urlopen(argument, timeout=0.01)
    assert "dummy-password" not in str(exc.value)
    assert "dummy-token" not in str(exc.value)


def test_urlretrieve_preserves_oserror_and_writes_nothing(tmp_path):
    target = tmp_path / "must-not-exist.bin"
    with pytest.raises(OSError, match="no network in tests"):
        urllib.request.urlretrieve(SECRET_URL, str(target))
    assert not target.exists()
