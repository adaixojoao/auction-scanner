"""
Refresh data/polluters.csv: the industry and waste sites that make an area
unpleasant to live in, for every country the scanner covers, from
OpenStreetMap (Overpass). Used by geo.pollution: heavy industry next to a
house hides it, a lighter source nearby costs points.

    python scripts/update_polluters.py              # every country
    python scripts/update_polluters.py PT ES        # just these (the others are kept)

- heavy: coal, lignite, oil and waste-burning power plants; refineries and oil
  terminals, steelworks, smelters, cement, chemical, petrochemical and
  fertiliser plants; incinerators.
- light: gas power plants, landfills, quarries, sewage works.

Commit the file through a pull request; once a year is plenty.
"""
from __future__ import annotations

import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from geo import POLLUTION_FILE  # noqa: E402
from update_transport import SERVERS  # noqa: E402

from common import COUNTRY_NAMES  # noqa: E402

COUNTRIES = tuple(COUNTRY_NAMES)   # every country the scanner covers
COLUMNS = ("country", "kind", "lat", "lon", "name")
HEAVY_FUEL = "coal|lignite|oil|diesel|waste"
# "oil" is how the biggest refineries are tagged (Shell Pernis: industrial=oil),
# and oil terminals with them.
HEAVY_INDUSTRY = "refinery|oil_refinery|oil|steelmaker|steel|steelworks|steel_mill|blast_furnace|smelter|smelting|" \
                 "cement|chemical|petrochemical|incineration|incinerator|aluminium|metallurgy|coke|coking|" \
                 "fertiliser|fertilizer"
QUERIES = {
    "heavy": ('nwr["power"="plant"]["plant:source"~"{fuel}"](area.a);'
              'nwr["industrial"~"^({ind})$"](area.a);'
              'nwr["man_made"="works"]["product"~"{ind}",i](area.a);'
              'nwr["amenity"="waste_incinerator"](area.a);'),
    "light": ('nwr["power"="plant"]["plant:source"="gas"](area.a);'
              'nwr["landuse"="landfill"](area.a);'
              'way["landuse"="quarry"]["name"](area.a);'
              'nwr["man_made"="wastewater_plant"](area.a);'),
}


def query(country: str, kind: str) -> str:
    body = QUERIES[kind].format(fuel=HEAVY_FUEL, ind=HEAVY_INDUSTRY)
    return f'[out:json][timeout:400];area["ISO3166-1"="{country}"][admin_level=2]->.a;({body});out center tags;'


def parse(country: str, kind: str, payload: dict) -> list[dict]:
    rows, seen = [], set()
    for el in payload.get("elements", []):
        where = el.get("center") or el
        try:
            lat, lon = round(float(where["lat"]), 4), round(float(where["lon"]), 4)
        except (KeyError, TypeError, ValueError):
            continue
        tags = el.get("tags") or {}
        # The parts of one plant (each chimney, each tank) are one site.
        key = (round(lat, 2), round(lon, 2))
        if key in seen:
            continue
        seen.add(key)
        what = (tags.get("industrial") or tags.get("plant:source") or tags.get("landuse")
                or tags.get("man_made") or tags.get("amenity") or "")
        name = (tags.get("name") or "").strip()
        rows.append({"country": country, "kind": kind, "lat": lat, "lon": lon,
                     "name": f"{name} ({what})" if name and what else name or what})
    return rows


def fetch(requests, country: str, kind: str) -> list[dict]:
    for attempt, server in enumerate(SERVERS * 2):
        try:
            r = requests.post(server, data={"data": query(country, kind)}, timeout=460,
                              headers={"User-Agent": "auction-scanner (polluter list)"})
            r.raise_for_status()
            payload = r.json()
            # A server that ran out of time still answers 200, with no elements
            # and a "remark": that is a failure, not a country with nothing in it.
            if "error" in (payload.get("remark") or "").lower() or "timed out" in (payload.get("remark") or ""):
                raise RuntimeError(payload["remark"][:120])
            return parse(country, kind, payload)
        except Exception as e:  # noqa: BLE001: busy server, try the next
            print(f"  {country} {kind}: {server} failed ({type(e).__name__}), retrying", flush=True)
            time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"no Overpass server answered for {country} {kind}")


def write(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (r["country"], r["kind"], float(r["lat"]), float(r["lon"])))
    with open(POLLUTION_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    import requests
    wanted = [c.upper() for c in (argv if argv is not None else sys.argv[1:])] or list(COUNTRIES)
    before = []
    if os.path.exists(POLLUTION_FILE):
        with open(POLLUTION_FILE, encoding="utf-8", newline="") as f:
            before = list(csv.DictReader(f))
    rows = [r for r in before if r["country"] not in wanted]
    for country in wanted:
        found = []
        for kind in ("heavy", "light"):
            try:
                found += (got := fetch(requests, country, kind))
            except RuntimeError as e:
                print(f"{country}: {kind} skipped ({e}) — run it again later", flush=True)
                found = None
                break
            print(f"{country}: {len(got)} {kind}", flush=True)
        if found is None:
            # Half a country would read as "clean": keep what the last run found instead.
            rows += [r for r in before if r["country"] == country]
            continue
        rows += found
        write(rows)                       # saved country by country: a stopped run keeps what it got
    write(rows)
    print(f"{len(rows)} polluters written to {POLLUTION_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
