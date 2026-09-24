import os

import pytest

import config
import scraper
from conftest import FakeResponse


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = config.load_config()
    c.update({"filters": {}, "report": {"out_dir": str(tmp_path), "desktop_copy": False},
              "notifications": {"enabled": False}, "telegram": {"enabled": False}})
    monkeypatch.setattr(config, "load_config", lambda: c)
    return c


def test_full_run_against_fake_sites(db, cfg, fake_http, tmp_path, capsys):
    page = {"list": [{"id": 1, "titulo": "Moradia em Guarda", "subtipoId": 21, "valorBase": 25000,
                      "referencia": "LO1", "dataFim": "2099-01-01T10:00:00"}],
            "pagination": {"total": 1}}

    def handler(method, url, kw):
        if "e-leiloes.pt/api/Eventos/?" in url:
            return FakeResponse(json_data=page)
        if "e-leiloes.pt/api/Eventos/" in url:
            return FakeResponse(json_data={"verbas": []})
        return FakeResponse("<html></html>", status=404)
    fake_http(handler)

    assert scraper.main(["--country", "PT"]) == 0
    out = capsys.readouterr().out
    assert "Moradia em Guarda" in out
    assert "Sources needing attention" in out          # the 404s are reported, not hidden
    assert os.path.exists(tmp_path / "report.md")

    statuses = dict(db.execute("SELECT source, status FROM scrape_log").fetchall())
    assert statuses["eleiloes"] == "ok" and statuses["bcp"] == "error"


def test_report_only_and_health(db, cfg, capsys):
    assert scraper.main(["--report-only"]) == 0
    assert scraper.main(["--health"]) == 0
    assert "never run" in capsys.readouterr().out


def test_country_argument():
    p = scraper.build_parser()
    assert p.parse_args(["--country", "pt,es"]).country == ["PT", "ES"]
    assert p.parse_args(["--country", "all"]).country is None
    with pytest.raises(SystemExit):
        p.parse_args(["--country", "XX"])


def test_list_sources(capsys):
    assert scraper.main(["--list-sources"]) == 0
    assert "citius" in capsys.readouterr().out
