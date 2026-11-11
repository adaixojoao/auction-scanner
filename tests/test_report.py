import os
from datetime import datetime, timedelta, timezone

from db import record_scrape
from report import _shown_score, generate_report, md_cell


def test_report_covers_every_country_and_escapes_tables(db, add, tmp_path):
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    add("eleiloes", "1", "PT", title="Moradia | com quintal [T3]", price=20000, url="https://e-leiloes.pt/evento/1",
        date_end=soon, concelho="Guarda")
    add("zvg", "2", "DE", title="Einfamilienhaus Wohnhaus", tipo="imovel", price=40000)
    add("greece", "3", "GR", title="Apartment", tipo="imovel", price=30000)
    add("citius", "4", "PT", title="Prédio urbano em Moura", price=None)
    add("citius", "6", "PT", title="Prédio sem valor publicado", price=0)
    add("eleiloes", "5", "PT", title="Anel em ouro", price=500)
    record_scrape(db, "bpi", count=0, status="error", message="HTTPError: 404")

    path = generate_report(db, max_price=50000, out_dir=str(tmp_path), desktop_copy=False,
                           known_sources=["eleiloes", "bpi"])
    md = open(path, encoding="utf-8").read()

    assert "### Germany (1)" in md and "### Greece (1)" in md   # old report only knew 6 countries
    assert "## Top picks" in md and "## Ending within 7 days — 1" in md
    assert "Moradia \\| com quintal (T3)" in md
    assert "Price unknown" in md and "Prédio urbano em Moura" in md
    top, _, _rest = md.partition("## Ending within 7 days")
    assert "Prédio sem valor publicado" not in top
    assert "Prédio sem valor publicado" in md
    assert "Ouro & Joias (Gold & Jewelry) — 1 listings" in md
    assert "| bpi | error |" in md and "1 source(s) failing" in md
    assert os.path.exists(tmp_path / "report.docx")
    assert os.path.exists(tmp_path / "report.pdf")


def test_the_report_names_the_eligibility_check_and_does_not_call_it_a_bid():
    from collections import Counter

    from report import _why, build_markdown
    item = {"title": "House", "score": 40, "rank": 40, "mode": "invest",
            "mode_label": "Investment home", "country": "PT", "category": "imoveis",
            "price": 20000, "current_bid": None, "reasons": ["a cheap house"],
            "evaluator_state": "finalist", "tipo": "moradia", "area_m2": 80,
            "date_end": "2030-06-01",
            "url": "https://example.test/house", "id": "x:1"}
    assert "not a bid" in _why(item)
    assert _why({"reasons": ["only a reason"]}) == "only a reason"
    md, _parts = build_markdown([item], Counter(), [], 50000, 50000, datetime.now(timezone.utc))
    assert "Finalist on the checks" in md and "not a bid" in md


def test_shown_score_uses_the_unclamped_rank():
    assert _shown_score({"score": 100, "rank": 118.4}) == 118
    assert _shown_score({"score": 73, "rank": 73.2}) == 73
    assert _shown_score({"score": 40, "rank": 30}) == 40


def test_md_cell():
    assert md_cell("a|b\n[c]") == "a\\|b (c)"
    assert md_cell("<script>") == "&lt;script&gt;"
    assert md_cell("x" * 10, 5) == "xxxx…"
