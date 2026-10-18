"""UI Batch 2: Listings filters and the selected sort run over every eligible
match before the display cap; counts, pages and inputs are validated."""
import json

import pytest

import dashboard


@pytest.fixture
def client(db):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000, "max_listings": 10})
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def homes(add, n=30, **fields):
    """n synthetic homes; the cheaper, the higher they score (test_dashboard)."""
    for i in range(n):
        add(external_id=f"h{i}", title="Moradia", price=5000 + i * 300, **fields)


def ids(data):
    return [it["id"] for it in data["items"]]


def test_a_filter_finds_a_match_beyond_the_unfiltered_cap(client, add):
    homes(add)
    add(external_id="far", title="Moradia junto ao rio Zêzere", price=60000)   # scores below the top 10
    add("citius", "court", title="Moradia", price=61000)
    plain = client.get("/api/listings?per_page=1000").get_json()
    assert plain["matched_total"] == 32 and plain["display_total"] == 10
    assert "eleiloes:far" not in ids(plain) and "citius:court" not in ids(plain)
    assert ids(client.get("/api/listings?per_page=1000&search=Zêzere").get_json()) == ["eleiloes:far"]
    assert ids(client.get("/api/listings?per_page=1000&source=citius").get_json()) == ["citius:court"]


def test_sorting_chooses_from_every_match_before_the_cap(client, add):
    homes(add)
    top = ids(client.get("/api/listings?per_page=1000").get_json())
    dear = client.get("/api/listings?per_page=1000&sort=price&dir=desc").get_json()
    assert dear["display_total"] == 10
    assert ids(dear)[0] == "eleiloes:h29"          # the dearest match, not the dearest of the best 10
    assert "eleiloes:h29" not in top
    prices = [it["price"] for it in dear["items"]]
    assert prices == sorted(prices, reverse=True)
    # Sorting by score keeps the best N by score.
    assert set(ids(client.get("/api/listings?per_page=1000&sort=score&dir=asc").get_json())) == set(top)


def test_counts_name_matches_display_and_page(client, add):
    homes(add)
    d = client.get("/api/listings?per_page=4&page=3").get_json()
    assert (d["matched_total"], d["display_total"], d["cap"], d["effective_page"]) == (30, 10, 10, 3)
    assert len(d["items"]) == 2
    assert (d["found"], d["total"], d["page"]) == (30, 10, 3)          # the older names stay
    past = client.get("/api/listings?per_page=4&page=99").get_json()
    assert past["effective_page"] == 3 and ids(past) == ids(d)
    empty = client.get("/api/listings?per_page=4&page=5&search=nothing-like-this").get_json()
    assert (empty["matched_total"], empty["display_total"], empty["effective_page"], empty["items"]) == (0, 0, 1, [])


@pytest.mark.parametrize("sort", ["price", "score", "title", "date_end"])
def test_pages_do_not_overlap_even_with_equal_values(client, add, sort):
    for i in range(12):
        add(external_id=f"same{i}", title="Moradia", price=9000)      # ties on every sort column
    whole = ids(client.get(f"/api/listings?per_page=1000&sort={sort}").get_json())
    pages = [ids(client.get(f"/api/listings?per_page=3&page={p}&sort={sort}").get_json()) for p in range(1, 5)]
    flat = [i for page in pages for i in page]
    assert flat == whole and len(set(flat)) == len(flat) == 10


def test_the_goal_budget_holds_with_an_empty_or_large_maximum(client, add):
    client.post("/api/settings", json={"max_price": 30000})
    add(external_id="ok", title="Moradia", price=20000)
    add(external_id="dear", title="Moradia", price=50000)
    for query in ("", "&max_price=", "&max_price=10000000"):
        assert ids(client.get(f"/api/listings?mode=home{query}").get_json()) == ["eleiloes:ok"]
    assert ids(client.get("/api/listings?mode=home&max_price=10000").get_json()) == []    # it narrows


def test_sale_method_uses_the_stated_method_and_unknown_means_unstated(client, add):
    def method(label):
        return json.dumps({"modalidade": label} if label else {})
    add(external_id="sealed", title="Moradia", price=9000, raw_json=method("Venda mediante propostas em carta fechada"))
    add(external_id="online", title="Moradia", price=9100, raw_json=method("Leilão Eletrónico"))
    add(external_id="odd", title="Moradia", price=9200, raw_json=method("Venda por outra via"))
    add(external_id="none", title="Moradia", price=9300, raw_json=method(None))   # e-leilões, nothing stated
    get = lambda m: sorted(ids(client.get(f"/api/listings?per_page=100&sale_method={m}").get_json()))
    assert get("carta_fechada") == ["eleiloes:sealed"]
    assert get("leilao_eletronico") == ["eleiloes:online"]
    assert get("unknown") == ["eleiloes:none", "eleiloes:odd"]      # not inferred from the source
    assert get("negociacao") == []
    assert len(get("")) == 4
    meta = client.get("/api/meta").get_json()["sale_methods"]
    assert {"value": "unknown", "label": "Unknown"} in meta
    assert {"value": "carta_fechada", "label": "Sealed bids"} in meta


@pytest.mark.parametrize("query", [
    "sort=price;DROP TABLE listings", "sort=hidden_reason", "dir=sideways", "sale_method=auction",
    "kind=castle", "climate=great", "status=sold", "page=0", "page=abc", "page=1.5",
    "max_price=-1", "max_price=nan", "max_price=inf", "max_price=abc", "min_score=-5", "min_score=abc",
])
def test_invalid_input_is_refused(client, add, query):
    add(external_id="a", title="Moradia", price=9000)
    r = client.get(f"/api/listings?{query}")
    assert r.status_code == 400 and "error" in r.get_json()
    assert client.get("/api/listings").get_json()["matched_total"] == 1      # nothing was altered


def test_hidden_and_shortlisted_views_keep_their_meaning_with_filters(client, add):
    homes(add, n=12)
    client.post("/api/listings/status", json={"id": "eleiloes:h11", "status": "shortlisted"})
    client.post("/api/listings/status", json={"id": "eleiloes:h0", "status": "dismissed", "reason": "other"})
    starred = client.get("/api/listings?per_page=100&status=shortlisted").get_json()
    assert ids(starred) == ["eleiloes:h11"]
    hidden = client.get("/api/listings?per_page=100&show_hidden=1&sort=price").get_json()
    assert ids(hidden) == ["eleiloes:h0"] and hidden["items"][0]["hidden_reason"].startswith("dismissed")
    assert "eleiloes:h0" not in ids(client.get("/api/listings?per_page=100&sort=price").get_json())


def test_the_listings_page_carries_the_filter_controls(client):
    html = client.get("/").get_data(as_text=True)
    assert 'Max listed price' in html and 'id="f-sale-method"' in html
    assert 'id="reset-filters"' in html and 'aria-label="Active filters"' in html
    assert 'onclick="page=1; load()"' not in html                       # no Apply button
    assert "const GOAL_BUDGETS = {" in html and '"both": 0' in html
    assert 'id="result-count"' in html


def test_a_figure_that_is_not_a_purchase_price_is_not_filtered_on(client, add):
    add(external_id="ok", title="Moradia", price=20000)
    add(external_id="dear", title="Moradia", price=50000)
    add("imovirtual", "dep", title="Moradia em leilao", price=50000)          # a deposit, not a price
    data = client.get("/api/listings?per_page=100&max_price=30000").get_json()
    rows = {it["id"]: it for it in data["items"]}
    assert "eleiloes:dear" not in rows and "eleiloes:ok" in rows
    assert rows["imovirtual:dep"]["price_unusable"]                          # kept, like an unknown price


@pytest.mark.parametrize("label,method", [
    ("online", "leilao_eletronico"), ("Internet-only Live", "leilao_eletronico"),
    ("Zaal", "leilao_presencial"), ("online en zaal", "unknown"), (None, "unknown"),
])
def test_dutch_sale_method_labels_are_read(client, add, label, method):
    import config
    config.save_config({"filters": {"countries": ["NL"]}, "cash_on_hand": 10_000_000})
    add("netherlands", "nl1", country="NL", title="Woonhuis", price=9000,
        raw_json=json.dumps({"veilingwijze": label}))
    got = ids(client.get(f"/api/listings?per_page=100&country=NL&sale_method={method}").get_json())
    assert got == ["netherlands:nl1"]


def test_a_spanish_legal_type_is_not_a_sale_method(client, add):
    import config
    config.save_config({"filters": {"countries": ["ES"]}, "cash_on_hand": 10_000_000})
    add("spain", "es1", country="ES", title="Vivienda", price=9000,
        raw_json=json.dumps({"tipo_subasta": "JUDICIAL EN VIA DE APREMIO"}))
    assert ids(client.get("/api/listings?per_page=100&country=ES&sale_method=unknown").get_json()) == ["spain:es1"]
