import site_check


def test_a_circle_has_the_area_asked_for():
    land = site_check.circle(45.3, 4.0, 250)
    assert abs(site_check.area_ha(land) - 19.63) < 0.2          # π × 250² m²


def test_shares_are_clipped_to_the_land():
    land = site_check.circle(45.3, 4.0, 250)
    minx, miny, maxx, maxy = land.bounds
    half = {"type": "Polygon", "coordinates": [[[minx - 1, miny - 1], [land.centroid.x, miny - 1],
                                                [land.centroid.x, maxy + 1], [minx - 1, maxy + 1], [minx - 1, miny - 1]]]}
    shares = site_check._shares([{"geometry": half, "properties": {"tfv": "Forêt fermée de sapin ou épicéa"}}],
                                land, lambda p: p["tfv"])
    assert abs(shares["Forêt fermée de sapin ou épicéa"] - 0.5) < 0.02


def test_the_report_reads_plainly():
    report = {"lat": 45.355, "lon": 6.27, "hectares": 50.2, "exact": False, "satellite": "https://maps/x",
              "slope": {"min_m": 899, "max_m": 1391, "mean_pct": 60, "p90_pct": 80,
                        "shares": {"wheeled machines": 0.04, "tracked or winch only": 0.46, "cable yarding only": 0.5}},
              "forest": {"Forêt fermée de sapin ou épicéa": 0.69}, "extraction": {"Inaccessible": 0.76},
              "natura2000": [], "znieff": ["ZNIEFF 1: Coteaux"], "tracks": {"track": 11},
              "errors": ["tracks: HTTPError"]}
    lines = site_check.describe(report)
    assert "slope mean 60%, steepest tenth over 80%" in lines[1]
    assert "50% cable yarding only" in lines[2]
    assert any(line.startswith("protection: ZNIEFF 1") for line in lines)
    assert lines[-1] == "not checked: tracks: HTTPError"


def test_montado_is_counted_from_portuguese_land_cover():
    cover = {"Superfícies silvopastoris de sobreiro": 0.83, "Florestas de pinheiro manso": 0.06,
             "Florestas de sobreiro": 0.04, "Florestas de azinheira": 0.02, "Pastagens espontâneas": 0.05}
    assert abs(site_check.montado_share(cover) - 0.89) < 1e-9


def test_eucalyptus_is_counted_from_portuguese_land_cover():
    cover = {"Florestas de eucalipto": 0.72, "Matos": 0.28}
    assert abs(site_check.eucalyptus_share(cover) - 0.72) < 1e-9
    assert site_check.eucalyptus_share({"Florestas de sobreiro": 1.0}) == 0


def test_grid_points_stay_inside_the_land():
    from shapely.geometry import Point
    land = site_check.circle(39.05, -8.10, 400)
    pts = site_check.grid_points(land)
    assert 30 <= len(pts) <= 60 and all(land.contains(Point(p)) for p in pts)


def test_access_is_estimated_from_slope_and_the_nearest_track():
    slope = {"shares": {"wheeled machines": 0.8, "tracked or winch only": 0.2, "cable yarding only": 0.0}}
    assert site_check.access_estimate(slope, 20).startswith("mostly wheeled machines ground, a track reaches")
    assert "843 m away — long haul" in site_check.access_estimate(slope, 843)
    assert site_check.access_estimate(None, 20) is None


def test_the_stored_summary_keeps_what_the_score_needs():
    report = {"hectares": 30.0, "exact": False, "lat": 45.3, "lon": 4.0, "satellite": "https://maps/x",
              "slope": {"min_m": 1, "max_m": 2, "mean_pct": 61, "p90_pct": 80,
                        "shares": {"wheeled machines": 0.1, "tracked or winch only": 0.4, "cable yarding only": 0.5}},
              "extraction": {"Inaccessible": 0.6, "Zone non exploitable (pente trop élevée)": 0.2,
                             "Accessible - Classe de débardage 1 : 0 - 250 m": 0.2},
              "tracks": {"track": 2, "_nearest_m": 640.0}, "natura2000": [], "znieff": ["ZNIEFF 1: X"],
              "pt_cover": None, "eu_forest": None, "forest": {"Forêt fermée de sapin ou épicéa": 0.7}}
    s = site_check.summary(report)
    assert (s["cable_share"], s["inaccessible"], s["track_m"], s["forest_share"]) == (0.5, 0.8, 640.0, 0.7)
    assert abs(site_check._radius_for(300000) - 309.0) < 0.1
    assert site_check._radius_for(1000) == 100.0


def test_check_pending_walks_past_listings_already_checked(db, add, monkeypatch):
    """A full shortlist of already-checked plots used to block every later one."""
    import json
    monkeypatch.setattr(site_check, "check", lambda *a, **k: {
        "hectares": 12, "exact": True, "lat": 40.54, "lon": -7.27, "satellite": "https://maps/x",
        "natura2000": [], "znieff": [],
        "pt_cover": {"Florestas de eucalipto": 0.7, "Pastagens espontâneas": 0.3},
    })
    at = "40.54,-7.27"
    for i in range(4):
        raw = {"climate": {"at": at}}
        if i < 3:
            raw["site_check"] = {"v": 1, "at": at}
        add("eleiloes", f"p{i}", title="Terreno rústico", tipo="terreno", country="PT",
            area_m2=200000, price=20000, raw_json=raw)
    items = sorted((dict(r) for r in db.execute("SELECT * FROM listings")), key=lambda r: r["id"])
    assert site_check.check_pending(db, items, limit=1) == 1
    stored = json.loads(db.execute("SELECT raw_json FROM listings WHERE id='eleiloes:p3'").fetchone()[0])
    assert stored["site_check"]["eucalyptus"] == 0.7
    assert stored["site_check"]["at"] == at
