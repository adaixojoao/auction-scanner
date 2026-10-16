"""Cover Batch 1A's SMTP/socket guards without contacting any service."""
import smtplib
import socket

import pytest

from conftest import FakeResponse, FakeSession


@pytest.mark.parametrize("api", ["SMTP", "SMTP_SSL"])
def test_email_constructors_are_blocked(api):
    with pytest.raises(AssertionError, match="test tried to send e-mail") as exc:
        getattr(smtplib, api)("example.invalid", timeout=0.01)
    assert "example.invalid" not in str(exc.value)


@pytest.mark.parametrize("api", ["connect", "connect_ex"])
@pytest.mark.parametrize("destination", [("192.0.2.1", 443), ("127.0.0.1", 9)])
def test_socket_connections_including_loopback_are_blocked(api, destination):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(AssertionError, match="test tried to reach the network"):
            getattr(sock, api)(destination)


def test_create_connection_fails_before_dns(monkeypatch):
    lookups = []

    def unexpected_dns(*args, **kwargs):
        lookups.append(args)
        raise AssertionError("DNS must not be called")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)
    with pytest.raises(AssertionError, match="test tried to reach the network"):
        socket.create_connection(("example.invalid", 443), timeout=0.01)
    assert lookups == []


def test_fake_session_remains_usable():
    response = FakeResponse(json_data={"listings": []})
    session = FakeSession(lambda method, url, kwargs: response)
    assert session.get("https://example.invalid/fixture", timeout=5).json() == {"listings": []}
    assert session.calls == [("GET", "https://example.invalid/fixture", {"timeout": 5})]
