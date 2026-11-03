"""What reading the real pages of Live and invest's top homes showed (2026-11-02)."""
import json

import geo
import perizia
import pipeline
from common import has_term, make_listing, patch_raw
from db import mark_duplicates, upsert_listing
from scoring import HEAVY_WORK, USUFRUCT_PATTERNS, _occupation, categorize, property_kind
from sources import pt

PAD = " Descrizione dell'appartamento, cucina, bagno e due camere, in muratura." * 10


def _raw(db, lid):
    return json.loads(db.execute("SELECT raw_json FROM listings WHERE id = ?", (lid,)).fetchone()[0])


# ─── What a step found stays ────────────────────────────────────────────
def test_a_rescrape_keeps_what_the_lookups_found(db, add):
    row = add(source="pvp_giustizia", external_id="1", country="IT", id_prefix="pvp", title="Casa", price=1000,
              raw_json={"procedura": "1"})
    patch_raw(db, row["id"], {"surroundings": {"found": {"noise": 1}}, "perizia": {"v": 6}, "outline": {"m2": 5},
                              "site_check": {"at": "1,2"}})
    db.commit()
    add(source="pvp_giustizia", external_id="1", country="IT", id_prefix="pvp", title="Casa", price=1000,
        raw_json={"procedura": "1"})
    raw = _raw(db, row["id"])
    assert raw["surroundings"] == {"found": {"noise": 1}} and raw["perizia"] == {"v": 6}
    assert raw["outline"] == {"m2": 5} and raw["site_check"] == {"at": "1,2"}


def test_an_older_copy_does_not_wipe_a_later_step(db, add, monkeypatch):
    row = add(external_id="a", title="Moradia em Rua X, Moura", concelho="Moura", price=1000)
    old_copy = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    patch_raw(db, row["id"], {"surroundings": {"found": {"noise": 1}}})
    monkeypatch.setattr(geo, "geocode", lambda *a, **k: {"lat": 1.0, "lon": 2.0, "precision": "street", "query": "q"})
    assert geo.geocode_pending(db, None, [old_copy], limit=1) == 1
    raw = _raw(db, row["id"])
    assert raw["surroundings"] == {"found": {"noise": 1}} and raw["geo"]["lat"] == 1.0


# ─── Property filed as property ─────────────────────────────────────────
def test_a_portals_own_word_for_a_home_is_property():
    home = {"title": "Abitazione di tipo civile · Via Cavour, 20 · Bianco", "tipo": "abitazione di tipo civile"}
    assert property_kind(home) == "home" and categorize(home) == "imoveis"
    assert categorize({"title": "Einfamilienhaus in Wien", "tipo": "Einfamilienhaus"}) == "imoveis"
    assert categorize({"title": "Audi A4 2015"}) == "outros"
    assert categorize({"title": "Mobília de casa", "tipo": "mobiliario"}) == "outros"


def test_italian_usufruct_and_words_for_uninhabitable():
    assert has_term("Usufrutto per 1/1 sul seguente bene", USUFRUCT_PATTERNS)
    assert has_term("Necessita de obras, não tem condições de habitabilidade.", HEAVY_WORK)
    assert not has_term("Em ótimas condições de habitabilidade.", HEAVY_WORK)


# ─── The report and the sale page ───────────────────────────────────────
def test_a_question_or_a_form_box_is_not_free():
    question = "9. accerti se l’immobile è libero o occupato; acquisisca il titolo." + PAD
    assert "free" not in perizia.read(question)
    box = "Stato dell’immobile Libero X Locato Altro Comodato d’uso gratuito" + PAD
    assert "free" not in perizia.read(box)
    assert perizia.read("Alla data del sopralluogo l'immobile risulta libero. 4. VINCOLI." + PAD)["free"]


BIANCO = ("<div><b>Stato di occupazione:</b> <span>Occupato da debitore/famiglia</span></div>"
          "<div><b>Superficie:</b> <span>164.95</span> <i>Mq</i></div>")
CASAL_VELINO = "<div>Superficie di circa mq 9</div><div><b>Superficie:</b> <span>32</span></div><div>Vani:</div>"


def test_the_sale_pages_own_fields():
    assert perizia.page_facts(BIANCO) == {"page_occupation": "occupied", "page_occupation_said": "Occupato da debitore/famiglia",
                                          "page_area": 164.95}
    assert perizia.page_facts(CASAL_VELINO) == {"page_area": 32.0}
    assert perizia.page_facts("<b>Stato di occupazione:</b><span>Libero</span><p>")["page_occupation"] == "vacant"
    assert perizia.page_facts("<b>Stato di occupazione:</b><span>In corso di liberazione</span><p>")["page_occupation"] == "occupied"


def test_the_page_saying_occupied_outranks_a_report_saying_free():
    got = perizia._with_facts({"found": True, "scanned": False, "lots": 1, "free": "risulta libero"},
                              perizia.page_facts(BIANCO))
    assert "free" not in got and "Occupato" in got["occupied"]
    assert _occupation({"raw_json": json.dumps({"perizia": got, "disponibilita": ["NON_SPECIFICATA"]})}) == "occupied"
    assert perizia.effect(got)[1] == perizia.OCCUPIED_FACTOR


def test_a_pvp_sale_finds_its_astalegale_page():
    site = {"urlInserizione": "https://www.astalegale.net/Aste/Dettaglio/B2434668"}
    other = {"urlInserizione": "https://www.astegiudiziarie.it/vendita-l2340630"}
    assert perizia.astalegale_copy({"siti": [other, site]}) == "https://www.astalegale.net/Aste/Detail/B2434668"
    assert perizia.astalegale_copy({"siti": [other, {"urlInserizione": "https://evil.example/Aste/Detail/B1"}]}) is None


def test_the_pages_floor_fills_a_gap_and_never_replaces(db, add, monkeypatch):
    gap = add(source="astalegale", external_id="B1", country="IT", title="Abitazione", price=1000,
              url="https://www.astalegale.net/Aste/Detail/B1")
    told = add(source="astalegale", external_id="B2", country="IT", title="Abitazione", price=1000, area_m2=50,
               url="https://www.astalegale.net/Aste/Detail/B2")
    monkeypatch.setattr(perizia, "check", lambda s, it: {"found": False, "why": "none", "page_area": 32.0})
    assert perizia.check_pending(db, None, [gap, told]) == 2
    area = dict(db.execute("SELECT id, area_m2 FROM listings").fetchall())
    assert area[gap["id"]] == 32.0 and area[told["id"]] == 50


# ─── One lot on two portals ─────────────────────────────────────────────
def test_one_lot_on_the_pvp_and_astalegale_is_one_sale(db, add):
    pvp = add(source="pvp_giustizia", external_id="4631042", country="IT", id_prefix="pvp", title="Immobile", price=17799.78,
              min_price=13349.84, concelho="Bianco",
              raw_json={"tribunale": "Tribunale di LOCRI", "procedura": "55", "numeroLotto": "LOTTO UNICO"})
    copy = add(source="astalegale", external_id="B2431115", country="IT", title="Abitazione", price=17799.78,
               min_price=13349.84, concelho="Bianco",
               raw_json={"tribunale": "Locri", "proceduraNumeroAnno": "55/2015", "codiceLotto": "Unico"})
    same_id = add(source="astalegale", external_id="P4631042", country="IT", title="Abitazione", price=17799.78,
                  concelho="Bianco", raw_json={})
    other_lot = add(source="astalegale", external_id="B9", country="IT", title="Abitazione", price=30000,
                    min_price=22500, concelho="Bianco",
                    raw_json={"tribunale": "Locri", "proceduraNumeroAnno": "55/2015", "codiceLotto": "2"})
    mark_duplicates(db)
    dup = dict(db.execute("SELECT id, duplicate_of FROM listings").fetchall())
    assert dup[copy["id"]] == pvp["id"] and dup[same_id["id"]] == pvp["id"]
    assert dup[pvp["id"]] is None and dup[other_lot["id"]] is None


# ─── A pin the address contradicts ──────────────────────────────────────
REGGIO = {"lat": 38.1064508, "lon": 15.6411356}
TOWNS = {geo.town_key("IT", "Bianco"): {"name": "Bianco", "lat": 38.09, "lon": 16.15}}


def _bianco(add, **raw):
    return add(source="astalegale", external_id="B2437804", country="IT", title="Abitazione · Via Cavour, 20 · Bianco",
               concelho="Bianco", price=1000, raw_json={**REGGIO, **raw})


def test_a_sale_pin_the_address_contradicts_is_set_aside(db, add, monkeypatch):
    row = _bianco(add)
    item = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    assert geo.position(item)["precision"] == "sale"
    hit = {"lat": 38.0891929, "lon": 16.1477569, "precision": "street", "query": "Via Cavour 20, Bianco"}
    monkeypatch.setattr(geo, "geocode", lambda *a, **k: hit)
    assert geo.check_sale_pins(db, None, [item], TOWNS) == 1
    assert geo.position(item) == hit
    # Asked once: the next scan leaves it alone, and the scraper sending the pin again changes nothing.
    monkeypatch.setattr(geo, "geocode", lambda *a, **k: 1 / 0)
    assert geo.check_sale_pins(db, None, [item], TOWNS) == 0
    add(source="astalegale", external_id="B2437804", country="IT", title="Abitazione · Via Cavour, 20 · Bianco",
        concelho="Bianco", price=1000, raw_json=dict(REGGIO))
    again = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    assert geo.position(again) == hit
    # A corrected pin from the source is believed.
    fixed = {**json.loads(again["raw_json"]), "lat": 38.09, "lon": 16.14}
    assert geo.source_position(fixed, again)["precision"] == "sale"


def test_a_namesake_town_or_a_town_only_answer_keeps_the_pin(db, add, monkeypatch):
    row = _bianco(add)
    item = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    near_pin = {"lat": 38.1, "lon": 15.65, "precision": "street", "query": "Via Cavour 20, Bianco"}
    monkeypatch.setattr(geo, "geocode", lambda *a, **k: near_pin)
    assert geo.check_sale_pins(db, None, [item], TOWNS) == 0
    assert geo.position(item)["precision"] == "sale"
    patch_raw(db, row["id"], {"sale_pin_checked": None})
    item = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    town_only = {"lat": 38.09, "lon": 16.15, "precision": "municipality", "query": "Bianco"}
    monkeypatch.setattr(geo, "geocode", lambda *a, **k: town_only)
    assert geo.check_sale_pins(db, None, [item], TOWNS) == 0
    assert geo.position(item)["precision"] == "sale"


def test_a_pin_near_its_town_is_not_questioned(db, add, monkeypatch):
    row = add(source="astalegale", external_id="B5", country="IT", title="Abitazione", concelho="Bianco", price=1000,
              raw_json={"lat": 38.1, "lon": 16.16})
    item = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    monkeypatch.setattr(geo, "geocode", lambda *a, **k: 1 / 0)
    assert geo.check_sale_pins(db, None, [item], TOWNS) == 0


# ─── The top of Live and invest gets the lookups and the pages ──────────
def test_the_best_of_live_and_invest_go_first(monkeypatch):
    top = [{"id": f"b{i}", "rank": 60 - i} for i in range(20)]
    high = [{"id": "h1", "rank": 90}, {"id": "b0", "rank": 60}]
    import db as dbmod
    import surroundings
    monkeypatch.setattr(dbmod, "load_listings", lambda *a, **k: list(reversed(top)))
    monkeypatch.setattr(surroundings, "missing", lambda it: [] if it["id"] == "b1" else ["noise"])
    got = [it["id"] for it in pipeline.both_first(None, {}, high)]
    assert got[0] == "b0" and "b1" not in got
    assert len(got) == pipeline.BOTH_LOOKUPS_FIRST + 1 and got[-1] == "h1"


RUIN_PAGE = ('<script id="__NEXT_DATA__" type="application/json">'
             + json.dumps({"props": {"pageProps": {"ad": {"description": "Moradia em Estado de Ru&iacute;na. <br>Isolada."}}}})
             + "</script>")


class _Pages:
    def __init__(self):
        self.asked = []

    def get(self, url):
        self.asked.append(url)
        return type("R", (), {"status_code": 200, "text": RUIN_PAGE})()


def test_the_best_homes_unread_page_is_read_once(db, monkeypatch):
    monkeypatch.setattr(pt.time, "sleep", lambda s: None)
    row = make_listing("imovirtual", "1", "PT", title="Moradia T0 para venda", tipo="moradia", price=23500,
                       description="Moradia T0 para venda · Vila do Porto", url="https://www.imovirtual.com/pt/anuncio/x")
    upsert_listing(db, row)
    land = make_listing("pisos", "2", "ES", title="Casa", price=1000, url="https://example.com/2")
    upsert_listing(db, land)
    db.commit()
    pages = _Pages()
    assert pt.read_imovirtual_details(db, pages, [land, row]) == 1
    stored = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    assert has_term(stored["description"], HEAVY_WORK) and pages.asked == [row["url"]]
    assert pt.read_imovirtual_details(db, pages, [row]) == 0


def test_a_climate_read_during_a_load_leaves_newer_findings(db, add):
    from db import _save_climates
    row = add(source="pvp_giustizia", external_id="9", country="IT", id_prefix="pvp", title="Casa", price=1000,
              raw_json={"procedura": "9"})
    patch_raw(db, row["id"], {"surroundings": {"found": {"noise": 1}}})    # found after the copy was loaded
    _save_climates(db, [(row["id"], {"result": {"hot_days": 2}})])
    raw = _raw(db, row["id"])
    assert raw["surroundings"] == {"found": {"noise": 1}} and raw["climate"]["result"]["hot_days"] == 2


def test_a_pin_no_namesake_can_explain_is_set_aside(db, add, monkeypatch):
    row = add(source="imobancos", external_id="1", country="PT", id_prefix="imobancos", title="Moradia", price=1000,
              concelho="Penafiel", raw_json={"lat": -30.0, "lon": 60.0})
    item = dict(db.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone())
    monkeypatch.setattr(geo, "geocode", lambda session, it, towns: None)
    towns = {geo.town_key("PT", geo.municipality(item)): {"lat": 41.2, "lon": -8.28}}
    assert geo.check_sale_pins(db, None, [item], towns) == 1
    raw = _raw(db, row["id"])
    assert raw["bad_sale_pin"] == [-30.0, 60.0] and "geo" not in raw
    assert geo.source_position(raw, item) is None
