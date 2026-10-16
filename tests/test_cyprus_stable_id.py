"""Cyprus listing IDs must not change when only the price changes."""
import sources
from conftest import FakeResponse


def _page(*rows):
    body = "".join(f"<tr>{r}</tr>" for r in rows)
    return f"<table><tr><th>h</th></tr>{body}</table>"


def _scan(db, fake_http, html):
    fake_http(lambda method, url, kw: FakeResponse(text=html))
    sources.load_all()
    sources.REGISTRY["cyprus"].func(db, max_price=10**9)
    db.commit()
    return [r[0] for r in db.execute("SELECT id FROM listings WHERE source='cyprus' ORDER BY id")]


LINKED = '<td><a href="/sale/1">Plot in Limassol</a></td><td>Limassol</td><td>€ {price}</td>'
UNLINKED = "<td>House in Paphos</td><td>Paphos</td><td>€ {price}</td>"


def test_identical_row_seen_twice_keeps_one_listing(db, fake_http):
    html = _page(LINKED.format(price="40.000"))
    assert _scan(db, fake_http, html) == _scan(db, fake_http, html)
    assert len(_scan(db, fake_http, html)) == 1


def test_price_change_keeps_identity(db, fake_http):
    first = _scan(db, fake_http, _page(LINKED.format(price="40.000"), UNLINKED.format(price="30.000")))
    second = _scan(db, fake_http, _page(LINKED.format(price="35.000"), UNLINKED.format(price="25.000")))
    assert first == second and len(second) == 2


def test_different_rows_stay_separate(db, fake_http):
    other = '<td><a href="/sale/2">Plot in Limassol</a></td><td>Limassol</td><td>€ 40.000</td>'
    assert len(_scan(db, fake_http, _page(LINKED.format(price="40.000"), other))) == 2
