from datetime import datetime, timezone

import pytest

from common import (effective_end, find_price, find_terms, make_listing, parse_date_dmy,
                    parse_dt, parse_price, safe_url, stable_id)


@pytest.mark.parametrize("text,expected", [
    ("36.163,00 €", 36163.0),
    ("36 163,00", 36163.0),
    ("36163", 36163.0),
    ("36.163", 36163.0),
    ("1,234", 1234.0),          # comma + 3 digits is a thousands separator
    ("1,234.50", 1234.5),
    ("1234,5", 1234.5),
    ("12.50", 12.5),
    ("Valor base: 30 000,00 €", 30000.0),
    ("sin puja mínima", None),
    ("", None),
    (None, None),
    (4500, 4500.0),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Apartamento T2 Lisboa 85.000 €", 85000.0),   # not 2
    ("Moradia T3 - € 120 000 - 3 quartos", 120000.0),
    ("Preço: EUR 45.500,00", 45500.0),
    ("Lote 7 com 350 m2", None),                    # no currency, no price
    ("", None),
])
def test_find_price_needs_currency(text, expected):
    assert find_price(text) == expected


def test_stable_id_is_deterministic():
    assert stable_id("a", 1) == stable_id("a", 1)
    assert stable_id("a", 1) != stable_id("a", 2)
    assert len(stable_id("x")) == 16


@pytest.mark.parametrize("url,base,expected", [
    ("/lot/1", "https://leilosoc.com", "https://leilosoc.com/lot/1"),
    ("lot/1", "https://leilosoc.com", "https://leilosoc.com/lot/1"),
    ("https://x.pt/a b", None, "https://x.pt/a%20b"),
    ('https://x.pt/"onmouseover=alert(1)', None, "https://x.pt/%22onmouseover=alert(1)"),
    ("javascript:alert(1)", None, None),
    ("javascript:alert(1)", "https://x.pt", None),
    ("data:text/html,hi", None, None),
    ("", None, None),
])
def test_safe_url(url, base, expected):
    assert safe_url(url, base) == expected


def test_make_listing_normalises_and_rejects_typos():
    row = make_listing("x", 5, "ES", title="  Casa \n  grande  ", price="30.000 €",
                       current_bid=0, url="/a", base_url="https://x.es", concelho="")
    assert row["id"] == "x:5" and row["external_id"] == "5" and row["country"] == "ES"
    assert row["title"] == "Casa grande"
    assert row["price"] == 30000.0
    assert row["current_bid"] is None     # 0 means "no bids"
    assert row["concelho"] is None        # "" must not overwrite a known value
    assert row["url"] == "https://x.es/a"
    with pytest.raises(TypeError):
        make_listing("x", 1, titel="typo")
    assert make_listing("cgd", "9", id_prefix="cgd_leilao")["id"] == "cgd_leilao:9"


def test_dates():
    assert parse_date_dmy("Termin: 05.11.2026 10:00") == "2026-11-05T00:00:00"
    assert parse_date_dmy("30/02/2026") is None
    naive = parse_dt("2026-09-30T10:00:00")
    assert naive.tzinfo is not None
    assert parse_dt("2026-09-30T10:00:00Z") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert parse_dt("rubbish") is None
    # a bare date is biddable for the whole day
    assert effective_end("2026-09-30T00:00:00").hour == 23
    assert effective_end("2026-09-30T10:00:00").hour == 10


@pytest.mark.parametrize("text,term,hit", [
    ("imóvel desocupado", "ocupado", False),
    ("imóvel ocupado pelo executado", "ocupado", True),
    ("O imóvel não se encontra ocupado", "ocupado", False),
    ("sem garagem, ocupado", "ocupado", True),
    ("Casal do Mato", "casa", False),
    ("Casa de dois pisos", "casa", True),
    ("Processo 11/2023", "1/2", False),
    ("01/2025", "1/2", False),
    ("1 / 2 (Um Meio) prédio", "1/2", True),
    ("½ indiviso", "1/2", True),
    ("Proc. 1/12.3TBSTR", "1/12", False),
    ("direito a 1/12 do prédio", "1/12", True),
    ("nue-propriété", "nue-propri*", True),
    ("Ruína", "ruin*", True),
])
def test_find_terms_whole_words(text, term, hit):
    assert bool(find_terms(text, [term])) is hit
