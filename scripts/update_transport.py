"""
Refresh data/transport.csv: airports with scheduled flights and the stations
long-distance trains stop at, for every country the scanner covers, from
OpenStreetMap (Overpass). Used for "X km from the airport / the station".

    python scripts/update_transport.py              # every country
    python scripts/update_transport.py PT ES        # just these (the others are kept)

- airport: aeroway=aerodrome with an IATA code (LIS, OPO, FAO…): flying clubs and
  airstrips have none.
- station: the stops of train routes tagged service=long_distance, high_speed or
  intercity (Alfa Pendular / Intercidades, AVE, TGV, Frecciarossa, IC…). A local
  halt on a regional line is not a way out of the region.

Commit the file through a pull request; once a year is plenty.
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from geo import TRANSPORT_FILE  # noqa: E402

SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
COUNTRIES = ("PT", "ES", "FR", "IT", "HR", "GR", "CY", "NL", "BE", "DE")
COLUMNS = ("country", "kind", "lat", "lon", "name")
LONG_DISTANCE = ("Alfa Pendular|Intercidades|AVE|Alvia|Avlo|Ouigo|Iryo|Euromed|Larga Distancia|TGV|inOui|"
                 "Intercit|Frecciarossa|Frecciargento|Frecciabianca|Italo|ICE|Intercity|EuroCity|Eurostar|"
                 "Railjet|Nightjet|IC ")
# Airstrips and flying clubs that happen to have an IATA code but no airline.
SMALL_FIELD = re.compile(r"^(aer[oó]dromo|a[eé]rodrome|flugplatz|segelflug|vliegveld|aviosuperficie|aeroclub)", re.I)


def query(country: str, kind: str) -> str:
    area = f'area["ISO3166-1"="{country}"][admin_level=2]->.a;'
    if kind == "airport":
        return f'[out:json][timeout:300];{area}nwr["aeroway"="aerodrome"]["iata"](area.a);out center tags;'
    # Few countries tag the service; the trains' names and networks say it too.
    return (f'[out:json][timeout:300];{area}relation["route"="train"](area.a)->.all;'
            '(relation.all["service"~"^(long_distance|high_speed|intercity)$"];'
            f'relation.all["name"~"{LONG_DISTANCE}",i];relation.all["network"~"{LONG_DISTANCE}",i];)->.r;'
            'node(r.r:"stop");out tags;node(r.r:"stop_exit_only");out tags;node(r.r:"stop_entry_only");out tags;')


def parse(country: str, kind: str, payload: dict) -> list[dict]:
    rows, seen = [], set()
    for el in payload.get("elements", []):
        where = el.get("center") or el
        try:
            lat, lon = round(float(where["lat"]), 4), round(float(where["lon"]), 4)
        except (KeyError, TypeError, ValueError):
            continue
        name = ((el.get("tags") or {}).get("name") or "").strip()
        if kind == "airport" and SMALL_FIELD.search(name):
            continue
        # A station's stop positions (one per track) are one station.
        key = (name, round(lat, 2), round(lon, 2)) if name else (lat, lon)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"country": country, "kind": kind, "lat": lat, "lon": lon, "name": name})
    return rows


def fetch(requests, country: str, kind: str) -> list[dict]:
    for attempt, server in enumerate(SERVERS * 2):
        try:
            r = requests.post(server, data={"data": query(country, kind)}, timeout=360,
                              headers={"User-Agent": "auction-scanner (transport list)"})
            r.raise_for_status()
            return parse(country, kind, r.json())
        except Exception as e:  # noqa: BLE001: busy server, try the next
            print(f"  {country} {kind}: {server} failed ({type(e).__name__}), retrying", flush=True)
            time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"no Overpass server answered for {country} {kind}")


def main(argv=None) -> int:
    import requests
    wanted = [c.upper() for c in (argv if argv is not None else sys.argv[1:])] or list(COUNTRIES)
    kept = []
    if os.path.exists(TRANSPORT_FILE):
        with open(TRANSPORT_FILE, encoding="utf-8", newline="") as f:
            kept = [r for r in csv.DictReader(f) if r["country"] not in wanted]
    rows = list(kept)
    for country in wanted:
        for kind in ("airport", "station"):
            try:
                found = fetch(requests, country, kind)
            except RuntimeError as e:
                print(f"{country}: {kind}s skipped ({e}) — run it again later", flush=True)
                continue
            print(f"{country}: {len(found)} {kind}s", flush=True)
            rows += found
    rows.sort(key=lambda r: (r["country"], r["kind"], float(r["lat"]), float(r["lon"])))
    with open(TRANSPORT_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} airports and stations written to {TRANSPORT_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
