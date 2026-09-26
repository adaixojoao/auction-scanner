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


def beach_query(country: str) -> str:
    return (f'[out:json][timeout:600];area["ISO3166-1"="{country}"][admin_level=2]->.a;'
            'way["natural"="coastline"](area.a)->.c;nwr["natural"="beach"](area.a)(around.c:300);out center tags;')


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


def fetch(requests, country: str) -> list[dict]:
    for attempt, server in enumerate(SERVERS * 2):
        try:
            r = requests.post(server, data={"data": beach_query(country)}, timeout=660,
                              headers={"User-Agent": "auction-scanner (beach list)"})
            r.raise_for_status()
            return parse_beaches(country, r.json())
        except Exception as e:  # noqa: BLE001: busy server, try the next
            print(f"  {country}: {server} failed ({type(e).__name__}), retrying")
            time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"no Overpass server answered for {country}")


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
        print(f"{country}: {len(found)} sea beaches")
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
