import pytest

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


# ─── Slope, read online without GDAL ────────────────────────────────
class _RangeServer:
    """Serves local files by URL with HTTP Range requests, as the AWS bucket does."""

    def __init__(self, files):
        self.files, self.requests = files, []

    def get(self, url, headers=None, timeout=None):
        from conftest import FakeResponse
        self.requests.append(url)
        if url not in self.files:
            return FakeResponse(status=404)
        data = open(self.files[url], "rb").read()
        start, end = (int(x) for x in headers["Range"].split("=")[1].split("-"))
        r = FakeResponse(status=206, headers={"Content-Range": f"bytes {start}-{min(end, len(data) - 1)}/{len(data)}"})
        r.content = data[start:end + 1]
        return r


CELLS = 1200                       # a test tile: 1° over 1200 cells (~93 m), 64-cell blocks


def _plane_tile(path, lat, lon, rise, start_col=0):
    import numpy as np
    import geotiff
    cols = np.arange(start_col, start_col + CELLS, dtype="float32")
    z = np.tile(cols * rise, (CELLS, 1)).astype("float32")
    geotiff.write(path, z, x0=lon, y0=lat + 1, pixel=1 / CELLS, compress=True, tile=64)


def test_an_online_file_reads_by_ranges(tmp_path):
    import geotiff
    data = bytes(range(256)) * 4000
    (tmp_path / "f.bin").write_bytes(data)
    server = _RangeServer({"u": tmp_path / "f.bin"})
    f = geotiff.HttpFile("u", server)
    f.seek(1000)
    assert f.read(10) == data[1000:1010]
    f.seek(-5, 2)
    assert f.read() == data[-5:] and f.read(3) == b""
    f.seek(3)
    assert f.read(len(data)) == data[3:]                  # a big read: one request
    with pytest.raises(FileNotFoundError):
        geotiff.HttpFile("missing", server)


def test_slope_is_read_across_two_tiles_without_rasterio(tmp_path, monkeypatch):
    """A plane rising 20% to the east, split over two DEM tiles at 4°E/5°E."""
    import math
    import sys
    monkeypatch.setitem(sys.modules, "rasterio", None)   # as when Smart App Control refuses it
    lat = 45
    rise = 0.20 * (1 / CELLS) * 111320.0 * math.cos(math.radians(lat + 0.3))     # metres per cell for 20%
    _plane_tile(tmp_path / "a.tif", lat, 4, rise)
    _plane_tile(tmp_path / "b.tif", lat, 5, rise, start_col=CELLS)
    server = _RangeServer({site_check._dem_url(lat, 4): tmp_path / "a.tif",
                           site_check._dem_url(lat, 5): tmp_path / "b.tif"})
    land = site_check.circle(lat + 0.3, 5.0, 800)              # on the seam
    s = site_check.slope_stats(land, server)
    assert abs(s["mean_pct"] - 20) < 0.5 and abs(s["p90_pct"] - 20) < 0.5
    assert s["shares"]["wheeled machines"] == 1.0
    assert s["min_m"] < (CELLS - 5) * rise < (CELLS + 5) * rise < s["max_m"]
    assert len(set(server.requests)) == 2


def test_no_tile_is_the_sea(tmp_path):
    server = _RangeServer({})
    assert site_check.slope_stats(site_check.circle(45.3, -20.0, 300), server) is None
