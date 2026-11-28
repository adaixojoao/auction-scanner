"""geotiff.py — read the climate GeoTIFFs without GDAL.

rasterio brings its own compiled GDAL, and Windows Smart App Control refuses
to load it ("An Application Control policy has blocked this file", Nov 2026):
every climate, flood, air and tree reading failed. The GeoPackages were moved
to sqlite3 for the same reason (climate._read_gpkg). This reads the layers
with tifffile (pure Python) and numpy, which load.

Only what the app needs: the tags that place the grid (pixel scale, tie point,
EPSG code, GDAL's nodata), band 1, and windows of it, decoded strip by strip
or tile by tile so a 300 MB map is never read whole. Compression none, deflate
and LZW, predictors none, horizontal and floating point: every layer in
auction-climate-data. Anything else raises, and the caller treats it as
unread. Building the layers (scripts/update_climate.py) still uses rasterio.
"""
from __future__ import annotations

import logging
import math
import threading
import zlib
from collections import OrderedDict

import numpy as np

# tifffile logs a warning for GDAL's float32 nodata (-3.4e38 written as a
# float64 string): harmless, it is parsed here.
logging.getLogger("tifffile").setLevel(logging.ERROR)

_SCALE, _TIEPOINT, _GEOKEYS, _NODATA = 33550, 33922, 34735, 42113
_GEOGRAPHIC = {4326, 4258, 4269}
SEGMENTS_KEPT = 16          # decoded strips/tiles kept per map (the water search reads a block)


class Raster:
    """One GeoTIFF: `index(x, y)` → (row, col), `read(row, col, h, w)` → band 1."""

    def __init__(self, path: str):
        import tifffile
        self.path = path
        self._tif = tifffile.TiffFile(path)
        page = self._tif.pages[0]
        self._page = page
        tags = page.tags
        self.width, self.height = int(page.imagewidth), int(page.imagelength)
        self.dtype = np.dtype(page.dtype)
        self._spp = int(page.samplesperpixel)
        self._planar = int(page.planarconfig)            # 1 contiguous, 2 separate
        self._compression = int(page.compression)
        self._predictor = int(page.predictor)
        self._order = self._tif.byteorder                # "<" or ">"
        self._tiled = bool(page.is_tiled)
        if self._tiled:
            self._seg_h, self._seg_w = int(page.tilelength), int(page.tilewidth)
        else:
            self._seg_h, self._seg_w = int(page.rowsperstrip or self.height), self.width
            self._seg_h = min(self._seg_h, self.height)
        self._across = math.ceil(self.width / self._seg_w)
        self._down = math.ceil(self.height / self._seg_h)
        self._offsets, self._counts = page.dataoffsets, page.databytecounts

        if _SCALE not in tags or _TIEPOINT not in tags:
            raise ValueError(f"{path}: no pixel scale / tie point (not a north-up GeoTIFF)")
        sx, sy = tags[_SCALE].value[:2]
        i, j, _, x, y, _ = tags[_TIEPOINT].value[:6]
        keys = _geokeys(tags[_GEOKEYS].value) if _GEOKEYS in tags else {}
        self.epsg = keys.get(3072) or keys.get(2048)
        point = keys.get(1025) == 2                      # RasterPixelIsPoint: the tie point is a cell centre
        self.x0 = x - (i + (0.5 if point else 0)) * sx
        self.y0 = y + (j + (0.5 if point else 0)) * sy
        self.sx, self.sy = float(sx), float(sy)
        self.is_projected = bool(self.epsg) and self.epsg not in _GEOGRAPHIC
        nodata = _nodata(tags[_NODATA].value) if _NODATA in tags else None
        # Compared in the map's own type: "-3.4e+38" is a float32 that is not -3.4e38.
        self.nodata = None if nodata is None else self.dtype.type(nodata).item()
        self._kept: OrderedDict[int, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()

    def index(self, x: float, y: float) -> tuple[int, int]:
        """(row, col) of the cell holding (x, y), in the map's own CRS (may be off the grid)."""
        return math.floor((self.y0 - y) / self.sy), math.floor((x - self.x0) / self.sx)

    def contains(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width

    def read(self, row: int, col: int, h: int = 1, w: int = 1, fill=0) -> np.ndarray:
        """Band 1 over rows row..row+h, cols col..col+w; cells off the grid are `fill`."""
        out = np.full((h, w), fill, dtype=np.result_type(self.dtype, np.asarray(fill).dtype))
        r0, r1 = max(row, 0), min(row + h, self.height)
        c0, c1 = max(col, 0), min(col + w, self.width)
        if r0 >= r1 or c0 >= c1:
            return out
        for sr in range(r0 // self._seg_h, (r1 - 1) // self._seg_h + 1):
            for sc in range(c0 // self._seg_w, (c1 - 1) // self._seg_w + 1):
                seg = self._segment(sr * self._across + sc)
                top, left = sr * self._seg_h, sc * self._seg_w
                a0, a1 = max(r0, top), min(r1, top + seg.shape[0])
                b0, b1 = max(c0, left), min(c1, left + seg.shape[1])
                if a0 < a1 and b0 < b1:
                    out[a0 - row:a1 - row, b0 - col:b1 - col] = seg[a0 - top:a1 - top, b0 - left:b1 - left]
        return out

    def value(self, row: int, col: int):
        """One cell, or None when it is off the grid or nodata."""
        if not self.contains(row, col):
            return None
        v = self.read(row, col)[0, 0]
        if self.nodata is not None and v == self.nodata:
            return None
        if isinstance(v, np.floating) and math.isnan(v):
            return None
        return v

    def close(self):
        self._tif.close()

    # ─── decoding one strip or tile ──────────────────────────────────
    def _segment(self, n: int) -> np.ndarray:
        with self._lock:
            if n in self._kept:
                self._kept.move_to_end(n)
                return self._kept[n]
            if not self._counts[n]:                      # a sparse map leaves empty tiles unwritten
                seg = np.full(self._shape(n), np.nan if self.nodata is None else self.nodata, dtype=self.dtype)                     if self.dtype.kind == "f" or self.nodata is not None else np.zeros(self._shape(n), self.dtype)
            else:
                fh = self._tif.filehandle
                fh.seek(self._offsets[n])
                seg = self._decode(fh.read(self._counts[n]), n)
            self._kept[n] = seg
            if len(self._kept) > SEGMENTS_KEPT:
                self._kept.popitem(last=False)
            return seg

    def _shape(self, n: int) -> tuple[int, int]:
        if self._tiled:
            return self._seg_h, self._seg_w
        return min(self._seg_h, self.height - (n % self._down) * self._seg_h), self._seg_w   # the last strip may be short

    def _decode(self, raw: bytes, n: int) -> np.ndarray:
        if self._compression == 1:
            data = raw
        elif self._compression in (8, 32946):
            data = zlib.decompress(raw)
        elif self._compression == 5:
            data = lzw_decode(raw)
        else:
            raise ValueError(f"{self.path}: compression {self._compression} is not read without GDAL")
        spp = 1 if self._planar == 2 else self._spp
        rows, width = self._shape(n)
        size = self.dtype.itemsize
        need = rows * width * spp * size
        data = data[:need]
        if len(data) < need:
            rows = len(data) // (width * spp * size)
            data = data[:rows * width * spp * size]
        if self._predictor == 3:                         # floating point: bytes split by significance
            b = np.frombuffer(data, np.uint8).reshape(rows, size, width * spp)
            b = np.cumsum(b.reshape(rows, -1), axis=1, dtype=np.uint8).reshape(rows, size, width * spp)
            arr = np.ascontiguousarray(b.transpose(0, 2, 1)).view(self.dtype.newbyteorder(">"))
            arr = arr.reshape(rows, width, spp).astype(self.dtype.newbyteorder("="))
        else:
            arr = np.frombuffer(data, self.dtype.newbyteorder(self._order)).reshape(rows, width, spp)
            arr = arr.astype(self.dtype.newbyteorder("="))
            if self._predictor == 2:                     # horizontal differencing
                arr = np.cumsum(arr, axis=1, dtype=arr.dtype)
            elif self._predictor != 1:
                raise ValueError(f"{self.path}: predictor {self._predictor} is not read without GDAL")
        return arr[:, :, 0]


def _geokeys(values) -> dict[int, int]:
    """{key id: value} of the GeoKey directory's short values (the EPSG codes)."""
    v = list(values)
    out = {}
    for k in range(4, 4 + 4 * v[3], 4):
        key, loc, _count, val = v[k:k + 4]
        if loc == 0:
            out[key] = val
    return out


def _nodata(text):
    try:
        v = float(str(text).strip().strip("\x00"))
    except ValueError:
        return None
    if not math.isnan(v) and abs(v) > 3.4e38:
        return float(np.float32(np.sign(v) * np.finfo(np.float32).max))
    return v


def lzw_decode(data: bytes) -> bytes:
    """TIFF's LZW (MSB first, 9-12 bit codes, the width grows one code early)."""
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    bits, pos, end = 9, 0, len(data) * 8
    padded = data + b"\x00\x00\x00"
    prev = None
    while pos + bits <= end:
        b = pos >> 3
        code = (int.from_bytes(padded[b:b + 3], "big") >> (24 - bits - (pos & 7))) & ((1 << bits) - 1)
        pos += bits
        if code == 256:                                  # clear
            del table[258:]
            bits, prev = 9, None
            continue
        if code == 257:                                  # end of information
            break
        if prev is None:
            entry = table[code]
        else:
            entry = table[code] if code < len(table) else prev + prev[:1]
            table.append(prev + entry[:1])
            if len(table) >= (1 << bits) - 1 and bits < 12:
                bits += 1
        out += entry
        prev = entry
    return bytes(out)


def write(path, arr, *, x0: float, y0: float, pixel: float, epsg: int = 4326, nodata=None,
          compress: bool = False) -> None:
    """A small single-band north-up GeoTIFF (tests and hand-made layers)."""
    import tifffile
    model = 1 if epsg not in _GEOGRAPHIC else 2
    crs_key = 3072 if model == 1 else 2048
    keys = [1, 1, 0, 3, 1024, 0, 1, model, 1025, 0, 1, 1, crs_key, 0, 1, epsg]
    extra = [(_SCALE, "d", 3, (pixel, pixel, 0.0), False),
             (_TIEPOINT, "d", 6, (0.0, 0.0, 0.0, x0, y0, 0.0), False),
             (_GEOKEYS, "H", len(keys), keys, False)]
    if nodata is not None:
        extra.append((_NODATA, "s", 0, str(nodata), False))
    tifffile.imwrite(str(path), np.asarray(arr), extratags=extra, compression="zlib" if compress else None,
                     photometric="minisblack", metadata=None)
