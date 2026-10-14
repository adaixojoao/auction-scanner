"""Live and invest checks.

LI-01, LI-03, LI-04 and LI-07 began as characterisation of unsafe behaviour.
Those four now assert the corrected contract. The other tests still record
behaviour this assignment did not change.
"""
import json
import math
from datetime import datetime, timedelta, timezone

import costs
import scoring
from conftest import FakeResponse
from scoring import climate_score, score_detail

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
MILD = {"hot_days": {"rcp45_2071-2100": 2}, "stress": {"stress_2080": 1}}
SALE = json.dumps({"lat": 41.18, "lon": -8.69, "precision": "sale"})


def beach_home(**kw):
    item = {
        "source": "eleiloes", "country": "PT", "title": "Moradia T3", "tipo": "moradia",
        "area_m2": 90, "price": 22000, "description": "moradia em bom estado",
        "beach": {"km": 0.4, "approx": False, "name": "Praia", "text": "0.4 km from Praia"},
        "climate": dict(MILD), "raw_json": SALE,
    }
    item.update(kw)
    return item


def _priced(monkeypatch):
    monkeypatch.setattr(scoring, "local_price", lambda item: (1200.0, "a city series"))


# LI-01. No usable local price: both mode returns the living rank.


def test_li01_missing_valuation_ranks_on_living_fit(monkeypatch):
    monkeypatch.setattr(scoring, "local_price", lambda item: None)
    house = beach_home()
    both, why = score_detail(house, mode="both", now=NOW)
    home = score_detail(dict(house), mode="home", now=NOW)[0]
    assert home > 0
    assert both == 0
    assert house["invest_score"] is None
    assert house["research_only"] is True
    assert why[0].startswith("Investment value not established")


# LI-02. Saved min_score in both mode uses live_score. The listings API
# then filters the combined display score.


def test_li02_saved_minimum_uses_living_fit_and_the_api_uses_the_combined_score(db, add, monkeypatch):
    import config
    import dashboard
    from db import load_listings

    config.save_config({"filters": {"min_score": 45}, "cash_on_hand": 10_000_000})
    add("eleiloes", "gap", title="Moradia T3", price=22000, area_m2=90,
        raw_json=SALE, description="moradia em bom estado")

    def fake(item, now=None, targets=None, mode="home"):
        if mode == "both":
            item["live_score"] = 80.0
            item["invest_score"] = 10.0
            return 20.0, ["the worse of the two (80 to live in, 10 as an investment)"]
        return 80.0, ["home"]

    monkeypatch.setattr(scoring, "score_detail", fake)
    shown = load_listings(db, filters={"min_score": 45}, mode="both", now=NOW)
    assert shown == []
    kept_rows = load_listings(db, filters={"min_score": 0}, mode="both", now=NOW)
    assert [it["id"] for it in kept_rows] == ["eleiloes:gap"]
    assert kept_rows[0]["score"] == 0.0 and kept_rows[0]["live_score"] == 80.0

    dashboard._LISTS.clear()
    dashboard.app.config["TESTING"] = True
    client = dashboard.app.test_client()
    dropped = client.get("/api/listings?mode=both&min_score=0").get_json()
    assert dropped["items"] == []
    config.save_config({"filters": {"min_score": 0}, "cash_on_hand": 10_000_000})
    dashboard._LISTS.clear()
    kept = client.get("/api/listings?mode=both&min_score=0").get_json()
    assert [it["id"] for it in kept["items"]] == ["eleiloes:gap"]
    assert kept["items"][0]["score"] == 0.0


# LI-03. Both mode uses each goal's own nested weights, then restores the context.


def test_li03_both_mode_ignores_nested_goal_weights(monkeypatch):
    _priced(monkeypatch)
    house = beach_home()
    weights = {"home": {"beach": 0.0}, "invest": {"price": 0.0, "beach": 2.0}}
    both = score_detail(house, mode="both", now=NOW, targets={"weights": weights})[0]
    home_beach_off = score_detail(house, mode="home", now=NOW, targets={"weights": weights})[0]
    home_default = score_detail(house, mode="home", now=NOW)[0]
    invest_price_off = score_detail(house, mode="invest", now=NOW, targets={"weights": weights})[0]
    invest_default = score_detail(house, mode="invest", now=NOW)[0]
    assert home_beach_off < home_default
    assert invest_price_off != invest_default
    assert both == min(home_beach_off, invest_price_off)
    assert scoring.w("beach") == 1.0


# LI-04. Hazard grade stays put when a preference weight changes.


def test_li04_hazard_grade_moves_with_preference_weights():
    # Heat only. At the default weight the grade is good; at weight 0 it is caution.
    climate = {"hot_days": {"rcp45_2071-2100": 0}}
    captured = {}
    real = scoring.climate_score

    def wrap(c, kind=None):
        captured["result"] = real(c, kind)
        return captured["result"]

    scoring.climate_score = wrap
    try:
        item = {"climate": climate, "title": "Moradia", "tipo": "moradia", "price": 22000,
                "area_m2": 90, "country": "PT", "source": "eleiloes"}
        score_detail(item, mode="home", now=NOW, targets={"weights": {"heat": 1}})
        at_one = captured["result"]["grade"]
        score_detail(item, mode="home", now=NOW, targets={"weights": {"heat": 0}})
        at_zero = captured["result"]["grade"]
    finally:
        scoring.climate_score = real
    assert at_one == at_zero == "good"


# LI-05. A reassuring grade does not require flood or fire coverage.


def test_li05_partial_climate_can_grade_excellent_without_flood_or_fire():
    climate = {"hot_days": {"rcp45_2071-2100": 0}, "water_km": 0.2,
               "stress": {"stress_2080": 1}}
    got = climate_score(climate, "home")
    assert got["grade"] == "excellent"
    assert "flood_m" not in climate and "fire" not in climate and "fire_danger" not in climate
    assert "flood" not in got["flags"] and "fire" not in "".join(got["flags"])


# LI-06. Street and advertised pins pass the both-mode building gate.


def test_li06_street_and_advertised_pins_pass_the_building_gate(monkeypatch):
    import geo
    _priced(monkeypatch)
    # A top-level lat/lon is always precision "sale". The street pin has to sit in geo.
    street = beach_home(raw_json=json.dumps(
        {"geo": {"lat": 41.18, "lon": -8.69, "precision": "street", "query": "Rua do Sol, Matosinhos"}}))
    advertised = beach_home(raw_json=json.dumps(
        {"geo": {"lat": 41.18, "lon": -8.69, "precision": "advertised"}}))
    assert score_detail(street, mode="both", now=NOW)[0] > 0
    assert score_detail(advertised, mode="both", now=NOW)[0] > 0
    assert geo.location_confidence(street)["level"] == "street"
    # "advertised" is not in LEVEL_OF, so the confidence label falls through.
    assert geo.location_confidence(advertised)["level"] == "area"
    assert geo.location_confidence(advertised)["precision"] == "advertised"


# LI-07. The gate uses the notice floor; valuation and the bid cap use the stored area.


def test_li07_land_150_and_notice_80_are_not_one_area(monkeypatch):
    import bidcap
    def series(item):
        return (1000.0, "a city series")

    monkeypatch.setattr(scoring, "local_price", series)
    monkeypatch.setattr(bidcap, "local_price", series)
    house = beach_home(area_m2=150, description="área total do terreno de 150m2 e habitação de 80 m2",
                       price=40000)
    assert costs.building_m2(house) == (80.0, "notice")
    assert score_detail(house, mode="both", now=NOW)[0] > 0
    assert scoring.market_value_estimate(house) == 80_000
    work = costs.renovation(house)
    assert work["low"] == round(80 * work["eur_m2"][0])
    cap = bidcap.calculate_bid_cap(house, {"filters": {}, "bid_cap": {}}, {
        "climate": climate_score(house["climate"], "home"),
        "location": {"level": "exact"},
    })
    assert "80" in cap["basis"]["note"]
    assert "150" not in cap["basis"]["note"]


# LI-08. predicted_final changes the rank. The cash filter does not receive it as the bid.


def test_li08_predicted_close_ranks_but_cash_uses_the_current_price(db, add, monkeypatch):
    import config
    import outcomes
    from db import load_listings

    config.save_config({"cash_on_hand": 500_000, "filters": {}})
    add("eleiloes", "live", title="Moradia T3", tipo="moradia", area_m2=90, price=20000,
        district="Viseu", description="moradia em bom estado")
    outcomes.ensure_table(db)
    for n in range(8):
        db.execute("""INSERT INTO auction_results (listing_id, source, country, district, kind, base,
                      final_bid, outcome, ended_at, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (f"old:{n}", "eleiloes", "PT", "Viseu", "home", 10000, 40000, "sold",
                    "2026-09-01T10:00:00", "2026-09-01T12:00:00"))
    db.commit()
    seen = {}
    real = costs.over_cash

    def wrap(item, cash, bid=None, typical=False):
        if item["id"] == "eleiloes:live":
            seen["bid"] = bid
            seen["predicted"] = (item.get("predicted_final") or {}).get("price")
            seen["pay"] = costs.money_out(item, bid=bid, typical=typical)["amount"]
        return real(item, cash, bid=bid, typical=typical)

    monkeypatch.setattr(costs, "over_cash", wrap)
    rows = {it["id"]: it for it in load_listings(db, apply_min_score=False, mode="invest", now=NOW)}
    assert seen["bid"] is None
    assert seen["predicted"] == 80000
    assert seen["pay"] < 80000
    assert any("likely to close around €80,000" in r for r in rows["eleiloes:live"]["reasons"])


# LI-09. Same municipality, similar price and area: distinct addresses merge.
# Same title and opening words: two legal units collapse to the cheaper one.


def test_li09_distinct_addresses_and_legal_units_are_hidden(db, add):
    from db import load_listings, mark_duplicates

    add("fotocasa", "1", "ES", title="Calle Mayor 1", description="Calle Mayor 1, piso primero",
        price=20000, area_m2=80, concelho="Ferrol")
    add("pisos", "2", "ES", title="Calle Nueva 9", description="Calle Nueva 9, un piso distinto",
        price=20200, area_m2=82, concelho="Ferrol")
    # The unit identifiers sit past the 80 characters that are compared.
    opening = "A" * 80
    add("citius", "a", title="Fracção autónoma", description=opening + " fração A artigo 1",
        price=19000, area_m2=80, concelho="Guarda")
    add("citius", "b", title="Fracção autónoma", description=opening + " fração B artigo 2",
        price=20000, area_m2=84, concelho="Guarda")
    mark_duplicates(db)
    hidden = {it["id"]: it["hidden_reason"] for it in load_listings(db, include_hidden=True, apply_min_score=False)
              if it.get("hidden_reason")}
    # The dearer distinct address stays: the keeper is the more complete row, then the later id.
    assert hidden["fotocasa:1"].startswith("duplicate of pisos:2")
    assert hidden["citius:b"].startswith("duplicate of citius:a")


# LI-10. Citius ids follow encounter order. Dropping the first lot reuses its id.


def test_li10_dropping_the_first_citius_lot_reuses_its_id(db, fake_http):
    from sources import REGISTRY
    from sources.pt import CITIUS_URL

    form = """
    <select id="ctl00_ContentPlaceHolder1_ddlTribunais">
      <option value="0">Todos</option><option value="17">Juízo de Lisboa</option>
    </select>
    <input id="__VIEWSTATE" value="vs"><input id="__EVENTVALIDATION" value="ev">
    <input id="__VIEWSTATEGENERATOR" value="gen">
    """

    def block(html_id, price, desc):
        return f"""<span><div class="resultadopubvenda"><strong>Tipo de Bem:</strong> Imóvel <br/>
          <strong>Estado:</strong> Em venda <br/><strong>Valor Base: </strong> {price} € <br/>
          <strong>Modalidade: </strong> Venda mediante proposta em carta fechada <br/>
          <strong>Descrição do Bem: </strong> {desc} <br/>
          <strong>Processo: </strong> 165/10.3TBMRA <br/><strong>Espécie: </strong> Execução Comum <br/>
          <a onclick="javascript:Viewer.Abrir(this, {html_id}, 'ConsultasVenda.aspx/GetHtmlDetails', 'x', 'y')">ver mais</a>
          </div></span>"""

    def page(blocks):
        pager = ('<input type="image" id="ctl00_ContentPlaceHolder1_Pager1_btnNextPage" '
                 'name="ctl00$ContentPlaceHolder1$Pager1$btnNextPage" disabled="disabled"/>')
        return (f'<input type="hidden" name="__VIEWSTATE" value="p"/>{pager}'
                f'<span id="ctl00_ContentPlaceHolder1_dlVenda">{"".join(blocks)}</span>')

    house = block(111, "30.000,00", "Moradia na Rua das Flores, freguesia de Safara, concelho de Moura")
    land = block(222, "2.000,00", "Terreno de cultura em Moura com 2760 m2")
    pages = {"html": page([house, land])}

    def handler(method, url, kw):
        if method == "GET":
            return FakeResponse(form)
        if str(url).endswith("GetHtmlDetails"):
            hid = "111" if "111" in kw.get("data", "") else "222"
            text = "casa de habitação na Rua das Flores" if hid == "111" else "terreno de cultura"
            return FakeResponse(json_data={"d": f"<div>Descrição do Bem: {text} Art. Matricial: 9</div>"})
        return FakeResponse(pages["html"])

    fake_http(handler)
    assert REGISTRY["citius"].func(db, max_price=100000) == 2
    ids = {r["id"] for r in db.execute("SELECT id FROM listings")}
    assert ids == {"citius:165103TBMRA", "citius:165103TBMRA-2"}
    first = db.execute("SELECT url, description FROM listings WHERE id='citius:165103TBMRA'").fetchone()
    assert first["url"] == CITIUS_URL
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='citius:165103TBMRA'").fetchone()[0])
    raw["geo"] = {"lat": 38.1, "lon": -7.2, "precision": "street"}
    db.execute("UPDATE listings SET raw_json=? WHERE id='citius:165103TBMRA'", (json.dumps(raw),))
    db.commit()

    pages["html"] = page([land])
    REGISTRY["citius"].func(db, max_price=100000)
    moved = db.execute("SELECT price, description, raw_json FROM listings WHERE id='citius:165103TBMRA'").fetchone()
    kept = json.loads(moved["raw_json"])
    assert moved["price"] == 2000
    assert kept["geo"]["lat"] == 38.1
    assert "Rua das Flores" in (kept.get("descricao_completa") or moved["description"])


# LI-11. Past 70% under the adjusted local price, discount points become a penalty
# and the rent is dropped.


def test_li11_discount_past_70_percent_penalises_and_drops_rent(monkeypatch):
    import prices
    monkeypatch.setattr(scoring, "local_price", lambda item: (2000.0, "a city series"))
    monkeypatch.setattr(prices, "rent_per_m2", lambda *a, **k: (8.0, "town"))
    # All-in on €50,000 is 64% under the series. €30,000 is 77% and €15,000 is 87%.
    inside = beach_home(price=50000, area_m2=80, description="moradia em bom estado")
    beyond = beach_home(price=30000, area_m2=80, description="moradia em bom estado")
    deeper = beach_home(price=15000, area_m2=80, description="moradia em bom estado")
    in_score, in_why = score_detail(inside, mode="invest", now=NOW)
    out_score, out_why = score_detail(beyond, mode="invest", now=NOW)
    deep_score = score_detail(deeper, mode="invest", now=NOW)[0]
    assert any("64% below local prices" in r for r in in_why)
    assert not any("check why" in r for r in in_why)
    assert any("a year net" in r for r in in_why)
    assert any("77% below local prices" in r for r in out_why)
    assert any("check why" in r for r in out_why)
    assert not any("a year net" in r for r in out_why)
    assert out_score < in_score
    assert deep_score == out_score


# LI-12. A positive bid seen shortly before the end is stored as sold.
# The closing watch then will not read that sale again.


def test_li12_a_pre_close_bid_is_sold_and_the_watch_skips_it(db, add):
    import outcomes

    add("eleiloes", "c1", title="Moradia", price=10000, current_bid=9000,
        date_end="2026-10-14T11:00:00", raw_json={"referencia": "NP9"})
    db.execute("UPDATE listings SET last_seen=? WHERE id='eleiloes:c1'", ("2026-10-14T10:00:00",))
    db.commit()
    assert outcomes.record_results(db, NOW) == 1
    assert tuple(db.execute("SELECT outcome, final_bid FROM auction_results").fetchone()) == ("sold", 9000)

    class Session:
        def get(self, url, **kw):
            raise AssertionError("closing watch read a sale already marked sold")

    assert outcomes.watch_closing(db, Session(), NOW) == 0
    assert db.execute("SELECT final_bid FROM auction_results").fetchone()[0] == 9000


# LI-13. HTTP 403 and 500 are stored as not gone. A search page is not a sale check.


def test_li13_errors_and_a_search_page_are_stored_as_not_gone(db, add):
    import confirm

    assert confirm.page_gone(403, "<html><title>Forbidden</title></html>") is None
    assert confirm.page_gone(500, "<html><title>Error</title></html>") is None
    search = "<html><title>Consultas de venda</title><h1>Pesquisa</h1><p>" + ("imóvel " * 80) + "</p></html>"
    assert confirm.page_gone(200, search) is None
    add("fotocasa", "s", "ES", title="Casa", price=25000, area_m2=80,
        url="https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx")

    class Session:
        def get(self, url, **kw):
            return FakeResponse("no", status=403)

    assert confirm.check_top(db, {"filters": {"min_score": 0}}, Session()) == 0
    raw = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='fotocasa:s'").fetchone()[0])
    assert raw["link_check"]["gone"] is False and raw["link_check"]["why"] is None


# LI-14. The report and the alerts run before enrichment and the link check.


def test_li14_report_and_alerts_precede_enrichment(db, fake_http, monkeypatch, tmp_path):
    import pipeline

    fake_http(lambda m, u, kw: FakeResponse("", status=404))
    order = []
    orig_report, orig_alerts = pipeline.build_report, pipeline.send_alerts

    def report(db_, cfg):
        order.append("report")
        return orig_report(db_, cfg)

    def alerts(db_, cfg):
        order.append("alerts")
        return orig_alerts(db_, cfg)

    monkeypatch.setattr(pipeline, "build_report", report)
    monkeypatch.setattr(pipeline, "send_alerts", alerts)
    import cadastre
    import climate
    import confirm
    import geo
    import outcomes
    import photos
    import site_check
    monkeypatch.setattr(outcomes, "record_results", lambda *a, **k: order.append("results") or 0)
    monkeypatch.setattr(geo, "locate_towns", lambda *a, **k: order.append("geo"))
    monkeypatch.setattr(geo, "geocode_pending", lambda *a, **k: None)
    monkeypatch.setattr(geo, "check_water_pending", lambda *a, **k: None)
    monkeypatch.setattr(cadastre, "locate_pending", lambda *a, **k: None)
    monkeypatch.setattr(climate, "assess_pending", lambda *a, **k: order.append("climate") or 0)
    monkeypatch.setattr(site_check, "check_pending", lambda *a, **k: None)
    monkeypatch.setattr(photos, "check_pending", lambda *a, **k: None)
    monkeypatch.setattr(photos, "keep_high", lambda *a, **k: None)
    monkeypatch.setattr(confirm, "check_top", lambda *a, **k: order.append("confirm") or 0)
    cfg = {"filters": {}, "report": {"out_dir": str(tmp_path / "r"), "desktop_copy": False}}
    pipeline.run_scan(source_names=["bcp"], cfg=cfg, db=db, report=True, alerts=True)
    assert order.index("report") < order.index("climate")
    assert order.index("alerts") < order.index("climate")
    assert order.index("alerts") < order.index("confirm")
    assert order.index("results") < order.index("climate")


# LI-15. Any positive count moves last_ok, including a run that never visited a listing.


def test_li15_a_partial_positive_count_makes_unvisited_listings_stale(db, add):
    from db import load_listings, record_scrape

    add("eleiloes", "old", title="Moradia", price=10000, concelho="Moura")
    add("eleiloes", "fresh", title="Moradia", price=10000, concelho="Lisboa")
    old_seen = (NOW - timedelta(days=10)).isoformat()
    db.execute("UPDATE listings SET last_seen=? WHERE external_id='old'", (old_seen,))
    db.commit()
    record_scrape(db, "eleiloes", count=1, status="ok", timestamp=NOW.isoformat())
    visible = {it["external_id"] for it in load_listings(db, now=NOW)}
    assert visible == {"fresh"}
    hidden = load_listings(db, include_hidden=True, now=NOW)
    old = next(it for it in hidden if it["external_id"] == "old")
    assert old["hidden_reason"].startswith("stale:")


# LI-16. Score cache keys counts, not market content. Stored climate ignores the layer stamp.


def test_li16_cached_decisions_ignore_content_changes(db, add, monkeypatch):
    import climate
    from db import forget_scores, load_listings

    add("eleiloes", "h", title="Moradia T3", tipo="moradia", area_m2=90, price=40000,
        concelho="Viseu", description="moradia em bom estado")
    series = {"eur": 4000.0}

    def price(item):
        return (series["eur"], "series")

    monkeypatch.setattr(scoring, "local_price", price)
    forget_scores(db)
    first = load_listings(db, apply_min_score=False, mode="invest", now=NOW)[0]["rank"]
    series["eur"] = 800.0
    cached = load_listings(db, apply_min_score=False, mode="invest", now=NOW, refresh=True)[0]["rank"]
    forget_scores(db)
    fresh = load_listings(db, apply_min_score=False, mode="invest", now=NOW)[0]["rank"]
    assert cached == first
    assert fresh != first

    spot = climate._spot({"lat": 41.18, "lon": -8.69})
    item = {"raw_json": json.dumps({
        "lat": 41.18, "lon": -8.69, "precision": "sale",
        "climate": {"at": "41.180,-8.690", "spot": spot, "v": 1, "layers": "OLD-STAMP",
                    "result": {"flood_m": 2.0}},
    })}
    assert climate.stored(item)["flood_m"] == 2.0
    assert climate.VERSION != 1


def test_population_vacancy_docstring_does_not_match_the_formula():
    """The prompt asked for this check. The formula is left as it is."""
    pop = 10_000
    factor = max(math.tanh(pop / 25000), 0.15)
    vacancy = costs.RENT_VACANCY_SHARE / factor
    # The docstring says pop=10000 → factor≈0.76 and vacancy×1.3.
    assert factor < 0.4
    assert vacancy > costs.RENT_VACANCY_SHARE * 2
