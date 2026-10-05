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
