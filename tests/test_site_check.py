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
