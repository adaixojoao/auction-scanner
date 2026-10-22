"""The dashboard has no login: it must only listen on this computer.

Standard library only — app.main() checks this before packages may be reinstalled.
"""
from __future__ import annotations

import ipaddress


def require_local_host(host) -> str:
    """The host to bind, if it is loopback; otherwise refuse to start."""
    if host == "localhost":
        return host
    try:
        valid = isinstance(host, str) and ipaddress.ip_address(host).is_loopback
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"The dashboard has no login, so it only listens on this computer "
                         f"(127.0.0.1 or localhost), not {host!r}. Change dashboard.host in config.json.")
    return host
