import os
import sys

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must never reach the real sites (or Telegram, or SMTP)."""
    def refuse(self, method, url, *a, **kw):
        raise AssertionError(f"test tried to reach the network: {method} {url}")
    monkeypatch.setattr(requests.Session, "request", refuse)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlretrieve",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no network in tests")))
    import smtplib

    def no_smtp(*a, **k):
        raise AssertionError("test tried to send e-mail")
    monkeypatch.setattr(smtplib, "SMTP", no_smtp)


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    """Never touch the real config.json, scan lock or reports folder."""
    import config
    import pipeline
    import scheduler
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(pipeline, "LOCK_PATH", str(tmp_path / "scan.lock"))
    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(scheduler, "LOCK_PATH", str(tmp_path / "scheduler.lock"))
    # data/pt_home_prices.csv changes every quarter: tests use the fixed city
    # table unless they supply their own price file (tests/test_prices.py).
    import prices
    monkeypatch.setattr(prices, "PT_FILE", str(tmp_path / "no_pt_home_prices.csv"))


@pytest.fixture
def db(tmp_path, monkeypatch):
    import db as dbmod
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    conn = dbmod.connect(path)
    yield conn
    conn.close()


@pytest.fixture
def add(db):
    """add(source=..., external_id=..., **fields) → upserted listing dict."""
    from common import make_listing
    from db import upsert_listing

    counter = {"n": 0}

    def _add(source="eleiloes", external_id=None, country="PT", **fields):
        counter["n"] += 1
        row = make_listing(source, external_id or f"x{counter['n']}", country, **fields)
        upsert_listing(db, row)
        db.commit()
        return row
    return _add


class FakeResponse:
    def __init__(self, text="", status=200, json_data=None, headers=None):
        self.text = text
        self.status_code = status
        self._json = json_data
        self.content = text.encode("utf-8")
        self.headers = headers or {"content-type": "application/json" if json_data is not None else "text/html"}

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json

    reason = "Not Found"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    """Routes requests to a handler(method, url, kwargs) → FakeResponse."""
    def __init__(self, handler):
        self.handler = handler
        self.headers = {}
        self.calls = []

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.handler(method, url, kw)


@pytest.fixture
def fake_http(monkeypatch):
    """fake_http(handler) patches make_session in every source module."""
    import importlib

    import sources
    sources.load_all()

    def install(handler):
        session = FakeSession(handler)
        for mod in ["sources._cards"] + [f"sources.{m}" for m in sources._MODULES]:
            module = importlib.import_module(mod)
            if hasattr(module, "make_session"):
                monkeypatch.setattr(module, "make_session", lambda *a, **k: session)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        return session
    return install
