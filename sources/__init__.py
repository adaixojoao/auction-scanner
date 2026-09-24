"""
sources — every scraper, one module per country, plus the registry that the
CLI, scheduler and health page are built from.

Adding a source:
  1. Write `def scrape_x(db, max_price=100000, **_)` in the country's module.
     Build rows with common.make_listing() and save them with db.upsert_listing().
     Return how many listings you saved. Let the first request's exception
     propagate — run_source() records it, so the health page can show it.
  2. Decorate it with @register("x", "PT").
That is all: --source choices, --country runs, the scheduler and /health pick
it up from REGISTRY.
"""
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from typing import Callable

from common import COUNTRY_ORDER, LOG, utcnow_iso


@dataclass(frozen=True)
class Source:
    name: str
    country: str          # ISO code, or "EU" for pan-European aggregators
    func: Callable
    default: bool = True  # part of --country / "all" runs
    description: str = ""


REGISTRY: dict[str, Source] = {}

_MODULES = ("pt", "es", "fr", "it", "nl", "hr", "de", "gr", "be", "ro", "pl", "cy", "eu")
_loaded = False


def register(name: str, country: str, *, default: bool = True, description: str = ""):
    def deco(func):
        if name in REGISTRY:
            raise ValueError(f"source {name!r} registered twice")
        REGISTRY[name] = Source(name, country, func, default, description or (func.__doc__ or "").strip().split("\n")[0])
        return func
    return deco


def load_all() -> dict[str, Source]:
    global _loaded
    if not _loaded:
        for mod in _MODULES:
            importlib.import_module(f"sources.{mod}")
        _loaded = True
    return REGISTRY


def sources_for(countries=None, *, include_optional: bool = False) -> list[Source]:
    """Sources for the given country codes (None = all), in display order."""
    load_all()
    wanted = None if countries is None else {c.upper() for c in countries}
    rank = {c: i for i, c in enumerate(COUNTRY_ORDER)}
    picked = [s for s in REGISTRY.values()
              if (include_optional or s.default)
              and (wanted is None or s.country in wanted)]
    order = list(REGISTRY)
    return sorted(picked, key=lambda s: (rank.get(s.country, 99), order.index(s.name)))


def describe_error(e: Exception) -> str:
    """One readable line for the Sources page; the full error goes to the log."""
    import requests
    from urllib.parse import urlsplit

    url = getattr(getattr(e, "request", None), "url", None) or ""
    host = urlsplit(url).netloc or ""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code} {e.response.reason or ''} from {host or 'the site'}".strip()
    if isinstance(e, requests.Timeout):
        return f"{host or 'The site'} did not answer in time"
    if isinstance(e, (requests.ConnectionError, requests.exceptions.ProxyError)):
        return f"Could not connect to {host or 'the site'} (site down, moved, or blocked)"
    if isinstance(e, ValueError) and "JSON" in str(e):
        return "The site did not return the expected data (JSON) — its API may have changed"
    return f"{type(e).__name__}: {e}"[:300]


def run_source(db, source: Source, *, max_price: float, config: dict | None = None) -> dict:
    """Run one scraper, never raising. Records the outcome in scrape_log."""
    from db import record_scrape

    started_iso = utcnow_iso()
    t0 = time.monotonic()
    message = None
    try:
        count = source.func(db, max_price=max_price, config=config or {}) or 0
        db.commit()
        status = "ok" if count > 0 else "empty"
    except Exception as e:  # noqa: BLE001 — one broken site must not stop the run
        db.rollback()
        count, status = 0, "error"
        message = describe_error(e)
        LOG.error(f"Source {source.name} failed: {type(e).__name__}: {e}")
    duration = round(time.monotonic() - t0, 1)
    record_scrape(db, source.name, count=count, status=status, message=message,
                  duration_s=duration, timestamp=started_iso)
    LOG.info(f"{source.name}: {count} listings ({status}, {duration}s)")
    return {"source": source.name, "count": count, "status": status,
            "message": message, "duration_s": duration}
