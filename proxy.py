"""
Proxy rotation for requests sessions.
Cycles through a list of proxies to avoid IP bans.
"""

import itertools
import logging
import requests

LOG = logging.getLogger("auction-scanner")


class ProxyRotator:
    def __init__(self, proxy_list: list[str], rotate_every: int = 5):
        self._proxies = proxy_list
        self._cycle = itertools.cycle(proxy_list) if proxy_list else None
        self._rotate_every = max(1, rotate_every)
        self._request_count = 0
        self._current = next(self._cycle) if self._cycle else None

    @property
    def current_proxy(self) -> dict | None:
        if not self._current:
            return None
        return {"http": self._current, "https": self._current}

    def tick(self):
        self._request_count += 1
        if self._cycle and self._request_count >= self._rotate_every:
            self._request_count = 0
            self._current = next(self._cycle)
            LOG.debug(f"Rotated to proxy: {self._current}")

    def apply(self, session: requests.Session):
        if self.current_proxy:
            session.proxies.update(self.current_proxy)


def create_session(proxy_config: dict | None = None) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )

    rotator = None
    if proxy_config and proxy_config.get("enabled") and proxy_config.get("list"):
        rotator = ProxyRotator(
            proxy_config["list"],
            proxy_config.get("rotate_every", 5),
        )
        rotator.apply(session)
        LOG.info(f"Proxy rotation enabled: {len(proxy_config['list'])} proxies, "
                 f"rotating every {proxy_config.get('rotate_every', 5)} requests")

    session._proxy_rotator = rotator
    return session


def rotate_if_needed(session: requests.Session):
    rotator = getattr(session, "_proxy_rotator", None)
    if rotator:
        rotator.tick()
        rotator.apply(session)
