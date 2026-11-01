"""What official maps say about where a listing stands, 2026-10-31: noise,
earthquakes, amenities, zoning, French risks and sold prices. Offline: the
answers are stored ones, the way a scan leaves them."""
import json

import surroundings


def _item(precision="sale", country="FR", found=None, title="Maison à Vierzon", **kw):
    raw = {"geo": {"lat": 47.2258, "lon": 2.0706, "precision": precision}}
    if found is not None:
        raw["surroundings"] = {"v": surroundings.VERSION, "pin": [47.2258, 2.0706], "found": found, "failed": {}}
    return {"id": "x", "country": country, "title": title, "raw_json": json.dumps(raw), **kw}


def test_point_checks_need_the_building_and_the_rest_take_any_pin():
    assert surroundings.missing(_item("sale")) == ["noise", "quake", "amenities", "power", "fr_zoning", "fr_risks",
                                                   "fr_sold", "fr_energy", "fr_heritage", "fr_soil", "nature"]
    assert surroundings.missing(_item("village")) == ["quake", "amenities", "fr_risks", "fr_sold"]
    assert surroundings.missing(_item("municipality")) == ["quake", "fr_risks", "fr_sold"]
    assert surroundings.missing(_item("sale", country="IT", title="Casa a Napoli")) == ["noise", "quake", "amenities",
                                                                                           "power", "it_hazard", "nature"]
    land = _item("sale", country="PT", title="Terreno rústico em Pinhel")
    assert surroundings.missing(land) == ["quake", "power", "pt_zoning", "pt_heritage", "pt_flood",
                                      "nature", "pt_coast"]


def test_an_answer_is_kept_for_its_pin_only():
    item = _item(found={"quake": {"pga": 0.1}})
    assert "quake" not in surroundings.missing(item)
    raw = json.loads(item["raw_json"])
    raw["geo"]["lat"] = 47.3                      # a new pin: another place
    item["raw_json"] = json.dumps(raw)
    assert "quake" in surroundings.missing(item)


def test_a_source_that_keeps_failing_is_left():
    item = _item()
    raw = json.loads(item["raw_json"])
    raw["surroundings"] = {"v": surroundings.VERSION, "pin": [47.2258, 2.0706], "found": {},
                           "failed": {"noise": surroundings.TRIES}}
    item["raw_json"] = json.dumps(raw)
    assert "noise" not in surroundings.missing(item)


def test_noise_is_held_to_the_who_limit_of_its_source():
    notes = surroundings.notes(_item(found={"noise": {"road": None, "rail": 2, "air": None, "ind": None}}))
    assert notes == ["noise at the house (Lden): railway 60–65 dB — over the WHO's health limit (railway 54 dB) "
                     "(EEA noise maps)"]
    quiet = surroundings.notes(_item(found={"noise": {"road": None, "rail": None, "air": None, "ind": None}}))
    assert quiet[0].startswith("no noise of 55 dB or more mapped here")


def test_earthquake_hazard_is_named_from_moderate_up():
    assert surroundings.notes(_item(found={"quake": {"pga": 0.01}})) == []
    line = surroundings.notes(_item(found={"quake": {"pga": 0.22}}))[0]
    assert line.startswith("earthquake hazard high: 0.22 g once in 475 years")


def test_nearest_amenities_from_overpass_elements():
    elements = [{"type": "node", "lat": 47.2268, "lon": 2.0706, "tags": {"shop": "supermarket"}},
                {"type": "way", "center": {"lat": 47.2358, "lon": 2.0706}, "tags": {"shop": "convenience"}},
                {"type": "node", "lat": 47.2259, "lon": 2.0706, "tags": {"highway": "bus_stop"}},
                {"type": "node", "lat": 47.2259, "lon": 2.0706, "tags": {"shop": "bakery"}}]
    near = surroundings.nearest_amenities(elements, 47.2258, 2.0706)
    assert near["supermarket"] == 111 and near["bus or tram stop"] == 11 and near["pharmacy"] is None
    line = surroundings.notes(_item(found={"amenities": near}))[0]
    assert line.startswith("nearest on foot or by car: supermarket 111 m, bus or tram stop 11 m; "
                           "no pharmacy, doctor, school, train station within 2 km")


def test_french_zoning_and_the_town_without_a_plan():
    farm = surroundings.notes(_item(found={"fr_zoning": {"zone": "A", "label": "Ap"}}))
    assert farm == ["local plan zone Ap (farm zone: new homes not allowed) (PLU, Géoportail de l'urbanisme)"]
    rnu = surroundings.notes(_item(found={"fr_zoning": {"zone": None}}))
    assert rnu[0].startswith("no local plan online: national planning rules (RNU) apply")


def test_portuguese_reserves():
    assert surroundings.pt_reserve_hit('<FeatureInfoResponse><FIELDS SERVIDAO="RAN"/></FeatureInfoResponse>')
    assert not surroundings.pt_reserve_hit("<FeatureInfoResponse></FeatureInfoResponse>")
    ran = surroundings.notes(_item(country="PT", found={"pt_zoning": {"RAN": True, "REN": False}}))
    assert ran[0].startswith("in the national farmland reserve (RAN): building and enlarging are restricted")


def test_french_risks_name_only_what_matters():
    line = surroundings.notes(_item(found={"fr_risks": {"commune": "Vierzon", "radon": 1, "seismic_zone": 2,
                                                        "clay": 3, "cavities": 0}}))
    assert line == ["risks at Vierzon: clay shrink-swell high (cracks walls in droughts) (Géorisques)"]
    assert surroundings.notes(_item(found={"fr_risks": {"commune": "X", "radon": 1, "seismic_zone": 1}})) == []


def _dvf(i, kind="Maison", price=100000, area=100, lat=47.2258, lon=2.0706, sale=None):
    return {"id_mutation": sale or f"m{i}", "nature_mutation": "Vente", "valeur_fonciere": str(price),
            "type_local": kind, "surface_reelle_bati": str(area), "date_mutation": f"2025-0{1 + i % 9}-01",
            "latitude": str(lat), "longitude": str(lon)}


def test_dvf_keeps_single_dwelling_sales_and_prefers_the_neighbourhood():
    rows = [_dvf(i, price=80000 + i * 5000) for i in range(10)]
    rows += [_dvf(20, kind="Appartement"), _dvf(21, sale="pair"), _dvf(22, sale="pair")]   # not single houses
    rows += [_dvf(30 + i, price=300000, lat=47.30) for i in range(5)]                      # 8 km away
    sales = surroundings.dvf_sales(rows, "Maison")
    assert len(sales) == 15
    found = surroundings.sold_summary(sales, 47.2258, 2.0706)
    assert found["where"] == "within 1 km" and found["n"] == 10 and found["median"] == 1025


def test_sold_prices_are_compared_with_this_listing():
    s = {"kind": "houses", "where": "within 1 km", "commune": "Vierzon", "median": 873, "low": 650,
         "high": 1222, "n": 106, "since": "2024-02-01"}
    line = surroundings.notes(_item(found={"fr_sold": s}, area_m2=90, price=60000))[0]
    assert line == ("houses sold within 1 km: median €873/m² (half between €650 and €1,222; 106 sales since "
                    "2024-02); this one €667/m², -24% (DVF, actual sale prices)")


def test_the_scorers_show_the_notes_without_scoring_them():
    import scoring
    found = {"fr_zoning": {"zone": "N", "label": "N"}}
    with_notes = _item(found=found, area_m2=90, price=60000, source="x", description="")
    without = _item(area_m2=90, price=60000, source="x", description="")
    for mode in ("home", "invest"):
        rank, reasons = scoring.score_detail(with_notes, mode=mode)
        assert any(r.startswith("local plan zone N (natural zone") for r in reasons)
        assert rank == scoring.score_detail(without, mode=mode)[0]
