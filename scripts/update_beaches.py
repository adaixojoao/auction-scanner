"""
Refresh data/beaches.csv: the sea beaches of every country the scanner covers,
from OpenStreetMap (Overpass), for the score's "X km from the beach".

    python scripts/update_beaches.py              # every country
    python scripts/update_beaches.py PT ES        # just these (the others are kept)

A sea beach is a natural=beach within 300 m of the coastline: river and lake
beaches ("praia fluvial") are left out, they count as water next to the property
instead. Beaches do not move; refreshing once a year is plenty. Commit the file
through a pull request.
"""
from __future__ import annotations

import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from geo import BEACH_FILE  # noqa: E402

SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
COUNTRIES = ("PT", "ES", "FR", "IT", "HR", "GR", "CY", "NL", "BE", "DE")
COLUMNS = ("country", "lat", "lon", "name")


# Rough boxes (south, west, north, east) of the countries too big for one
# request: they are asked in TILE-degree squares. Others go in one piece.
BOXES = {"FR": (41.3, -5.2, 51.2, 9.6), "IT": (35.4, 6.6, 47.1, 18.6), "GR": (34.8, 19.3, 41.8, 29.7),
         "HR": (42.3, 13.4, 46.6, 19.5), "DE": (47.2, 5.8, 55.1, 15.1), "ES": (27.6, -18.2, 43.8, 4.4)}
TILE = 2.0


def tiles(country: str) -> list[tuple[float, float, float, float] | None]:
    box = BOXES.get(country)
    if not box:
        return [None]
    s, w, n, e = box
    out, lat = [], s
    while lat < n:
        lon = w
        while lon < e:
            out.append((lat, lon, min(lat + TILE, n), min(lon + TILE, e)))
            lon += TILE
        lat += TILE
    return out


def beach_query(country: str, tile: tuple[float, float, float, float] | None = None) -> str:
    if tile is None:
        return (f'[out:json][timeout:600];area["ISO3166-1"="{country}"][admin_level=2]->.a;'
                'way["natural"="coastline"](area.a)->.c;nwr["natural"="beach"](area.a)(around.c:300);out center tags;')
    s, w, n, e = tile
    wide = f"({s - 0.01:.2f},{w - 0.01:.2f},{n + 0.01:.2f},{e + 0.01:.2f})"   # the coast just over the edge counts too
    return (f'[out:json][timeout:300];area["ISO3166-1"="{country}"][admin_level=2]->.a;'
            f'way["natural"="coastline"]{wide}->.c;'
            f'nwr["natural"="beach"]({s},{w},{n},{e})(area.a)(around.c:300);out center tags;')


def parse_beaches(country: str, payload: dict) -> list[dict]:
    rows, seen = [], set()
    for el in payload.get("elements", []):
        where = el.get("center") or el
        try:
            lat, lon = round(float(where["lat"]), 4), round(float(where["lon"]), 4)
        except (KeyError, TypeError, ValueError):
            continue
        if (lat, lon) in seen:
            continue
        seen.add((lat, lon))
        rows.append({"country": country, "lat": lat, "lon": lon,
                     "name": ((el.get("tags") or {}).get("name") or "").strip()})
    return rows


def _ask(requests, query: str, what: str, tries: int = 4) -> dict | None:
    """Overpass's answer, or None when no server answered in `tries` attempts."""
    for attempt, server in enumerate((SERVERS * tries)[:tries]):
        try:
            r = requests.post(server, data={"data": query}, timeout=660,
                              headers={"User-Agent": "auction-scanner (beach list)"})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001: busy server, try the next
            print(f"  {what}: {server} failed ({type(e).__name__}), retrying", flush=True)
            time.sleep(20 * (attempt + 1))
    return None


def _quarters(tile):
    s, w, n, e = tile
    ms, mw = (s + n) / 2, (w + e) / 2
    return [(s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)]


def _tile_beaches(requests, country: str, tile, what: str, depth: int = 0) -> tuple[list[dict], int]:
    """(beaches, tiles skipped). A tile the servers time out on (a long, busy
    coast) is asked again in four quarters, twice over at most."""
    answer = _ask(requests, beach_query(country, tile), what, tries=2 if tile and depth < 2 else 4)
    if answer is not None:
        return parse_beaches(country, answer), 0
    if tile is None or depth >= 2:
        print(f"  {what}: skipped", flush=True)
        return [], 1
    rows, skipped = [], 0
    for q, part in enumerate(_quarters(tile), 1):
        found, missed = _tile_beaches(requests, country, part, f"{what}.{q}", depth + 1)
        rows += found
        skipped += missed
    return rows, skipped


def fetch(requests, country: str) -> list[dict]:
    rows, seen, skipped = [], set(), 0
    parts = tiles(country)
    for i, tile in enumerate(parts, 1):
        found, missed = _tile_beaches(requests, country, tile, f"{country} {i}/{len(parts)}")
        skipped += missed
        for row in found:
            if (row["lat"], row["lon"]) not in seen:
                seen.add((row["lat"], row["lon"]))
                rows.append(row)
        if len(parts) > 1:
            time.sleep(2)
    if skipped:
        print(f"  {country}: {skipped} tile(s) skipped — run it again later to fill them", flush=True)
    return rows


def main(argv=None) -> int:
    import requests
    wanted = [c.upper() for c in (argv if argv is not None else sys.argv[1:])] or list(COUNTRIES)
    kept = []
    if os.path.exists(BEACH_FILE):
        with open(BEACH_FILE, encoding="utf-8", newline="") as f:
            kept = [r for r in csv.DictReader(f) if r["country"] not in wanted]
    rows = list(kept)
    for country in wanted:
        found = fetch(requests, country)
        print(f"{country}: {len(found)} sea beaches", flush=True)
        rows += found
    rows.sort(key=lambda r: (r["country"], float(r["lat"]), float(r["lon"])))
    with open(BEACH_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} beaches written to {BEACH_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
