"""geotiff.py reads the climate layers without GDAL (Smart App Control blocks rasterio)."""
import sys

import numpy as np
import pytest

import geotiff


def _lzw_encode(data: bytes) -> bytes:
    """TIFF LZW, as GDAL and libtiff write it (MSB first, the width grows one code early)."""
    codes, bits = [], 9

    def emit(code):
        codes.append((code, bits))
    table, nxt, w = {bytes([i]): i for i in range(256)}, 258, b""
    emit(256)
    for c in data:
        wc = w + bytes([c])
        if wc in table:
            w = wc
            continue
        emit(table[w])
        table[wc], nxt, w = nxt, nxt + 1, bytes([c])
        if nxt > (1 << bits) - 1:
            bits += 1
        if nxt >= 4093:
            emit(256)
            table, nxt, bits = {bytes([i]): i for i in range(256)}, 258, 9
    if w:
        emit(table[w])
        nxt += 1                                   # the reader adds an entry here too
        if nxt > (1 << bits) - 1 and bits < 12:
            bits += 1
    emit(257)
    acc = n = 0
    out = bytearray()
    for code, width in codes:
        acc, n = (acc << width) | code, n + width
        while n >= 8:
            n -= 8
            out.append((acc >> n) & 0xFF)
    if n:
        out.append((acc << (8 - n)) & 0xFF)
    return bytes(out)


@pytest.mark.parametrize("data", [b"", b"A", b"TOBEORNOTTOBEORTOBEORNOT" * 40,
                                  bytes(np.random.default_rng(1).integers(0, 4, 20000, dtype=np.uint8)),
                                  bytes(np.random.default_rng(2).integers(0, 256, 9000, dtype=np.uint8))],
                         ids=["empty", "one byte", "repeats", "4 symbols", "random"])
def test_lzw_reads_what_libtiff_writes(data):
    assert geotiff.lzw_decode(_lzw_encode(data)) == data


@pytest.fixture
def grid(tmp_path):
    arr = (np.arange(30 * 40, dtype="float32").reshape(30, 40) / 7).astype("float32")
    arr[3, 5] = -9999
    path = tmp_path / "g.tif"
    geotiff.write(path, arr, x0=-8.0, y0=41.0, pixel=0.01, nodata=-9999)
    return path, arr


def test_a_written_map_is_placed_and_read_back(grid):
    path, arr = grid
    r = geotiff.Raster(str(path))
    assert (r.width, r.height, r.epsg, r.nodata, r.is_projected) == (40, 30, 4326, -9999.0, False)
    assert r.index(-7.995, 40.995) == (0, 0) and r.index(-7.605, 40.705) == (29, 39)
    assert r.value(29, 39) == arr[29, 39] and r.value(3, 5) is None and r.value(30, 0) is None
    block = r.read(-2, -3, 5, 6, fill=0)                  # boundless, as the water search reads
    assert (block[:2] == 0).all() and (block[:, :3] == 0).all()
    assert np.array_equal(block[2:, 3:], arr[:3, :3])


def test_the_predictors_and_lzw_are_undone(grid):
    path, arr = grid
    r = geotiff.Raster(str(path))
    rows = arr.shape[0] if r._tiled else r._shape(0)[0]
    strip = arr[:rows]
    # Floating point predictor (3): big-endian bytes split by significance, then differenced.
    planes = strip.astype(">f4").view(np.uint8).reshape(rows, 40, 4).transpose(0, 2, 1).reshape(rows, -1)
    diffed = np.diff(planes, axis=1, prepend=0).astype(np.uint8)
    r._predictor, r._compression = 3, 5
    assert np.array_equal(r._decode(_lzw_encode(diffed.tobytes()), 0), strip)
    # Horizontal predictor (2) on 16-bit integers, as the tree maps are.
    ints = (np.arange(rows * 40) % 3 - 1).astype("<i2").reshape(rows, 40)
    ints[0, 0] = -32768
    r.dtype, r._order, r._predictor = np.dtype("int16"), "<", 2
    diffed = np.diff(ints, axis=1, prepend=0).astype("<i2")
    assert np.array_equal(r._decode(_lzw_encode(diffed.tobytes()), 0), ints)


def test_gdal_float32_nodata_matches_in_the_maps_own_type(tmp_path):
    arr = np.full((4, 4), np.float32(-3.4e38), dtype="float32")
    arr[0, 0] = 1.5
    geotiff.write(tmp_path / "n.tif", arr, x0=0, y0=4, pixel=1, nodata="-3.4e+38", compress=True)
    r = geotiff.Raster(str(tmp_path / "n.tif"))
    assert r.value(0, 0) == 1.5 and r.value(1, 1) is None


def test_a_projected_map_reports_its_crs(tmp_path):
    geotiff.write(tmp_path / "p.tif", np.ones((2, 2), "int16"), x0=2_600_000, y0=1_900_000, pixel=100, epsg=3035)
    r = geotiff.Raster(str(tmp_path / "p.tif"))
    assert r.epsg == 3035 and r.is_projected and r.index(2_600_150, 1_899_950) == (0, 1)


@pytest.mark.parametrize("opts", [dict(compress="lzw", predictor=2, dtype="int16"),
                                  dict(compress="lzw", predictor=3, dtype="float32"),
                                  dict(compress="deflate", predictor=3, dtype="float32", tiled=True,
                                       blockxsize=16, blockysize=16),
                                  dict(compress="deflate", predictor=1, dtype="float64", tiled=True,
                                       blockxsize=16, blockysize=16, sparse_ok=True)])
def test_maps_written_by_gdal_read_the_same(tmp_path, opts):
    """Where rasterio loads (CI, Linux), what it writes reads back cell for cell."""
    rasterio = pytest.importorskip("rasterio", exc_type=ImportError)   # blocked on this PC
    from rasterio.transform import from_origin
    dtype = opts.pop("dtype")
    rng = np.random.default_rng(3)
    arr = (rng.integers(-1, 2, (70, 50)) if dtype == "int16" else rng.normal(20, 5, (70, 50))).astype(dtype)
    arr[10:40, 20:40] = -9999                             # whole empty tiles: left unwritten when sparse
    path = tmp_path / "gdal.tif"
    with rasterio.open(path, "w", driver="GTiff", height=70, width=50, count=1, dtype=dtype, crs="EPSG:3035",
                       transform=from_origin(2_600_000, 1_900_000, 100, 100), nodata=-9999, **opts) as dst:
        dst.write(arr, 1)
    r = geotiff.Raster(str(path))
    assert (r.epsg, r.nodata) == (3035, -9999)
    assert np.array_equal(r.read(0, 0, 70, 50, fill=-9999), arr)
    with rasterio.open(path) as src:
        assert src.index(2_603_333, 1_896_666) == r.index(2_603_333, 1_896_666)


def test_the_app_reads_layers_without_rasterio(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "rasterio", None)   # as when Smart App Control refuses it
    import climate
    climate._raster.cache_clear()
    try:
        geotiff.write(tmp_path / "x.tif", np.full((10, 10), 4.5, "float32"), x0=-9, y0=39, pixel=0.1)
        assert climate._sample(str(tmp_path / "x.tif"), 38.5, -8.5) == 4.5
    finally:
        climate._raster.cache_clear()
