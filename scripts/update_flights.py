"""Refresh the airports with direct passenger flights from Amsterdam Schiphol.

    python scripts/update_flights.py

Needs a network connection. Writes data/flights_from_ams.csv; the app only
reads that file (surroundings.travel).

- routes: Eurostat's avia_par_nl, passengers carried between Schiphol (EHAM)
  and each partner airport, monthly; an airport counts when its route carried
  passengers in the latest year published;
- where each airport is: OurAirports (public domain), by ICAO code.
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "flights_from_ams.csv")
ROUTES = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/avia_par_nl"
          "?format=JSON&lang=en&unit=PAS&tra_meas=PAS_CRD&freq=M&lastTimePeriod=24")
AIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=600) as resp:
        return resp.read()


def main() -> None:
    data = json.loads(_get(ROUTES))
    routes = data["dimension"]["airp_pr"]["category"]["index"]
    months = sorted(data["dimension"]["time"]["category"]["index"].items(), key=lambda x: x[1])
    values, n = data["value"], len(months)
    year = max(m[:4] for m, i in months if any(values.get(str(r * n + i)) for r in routes.values()))
    passengers: dict[str, int] = {}
    for route, r in routes.items():
        if not route.startswith("NL_EHAM_"):
            continue
        total = sum(values.get(str(r * n + i)) or 0 for m, i in months if m.startswith(year))
        if total > 0:
            passengers[route.split("_")[-1]] = int(total)
    rows = []
    for a in csv.DictReader(io.StringIO(_get(AIRPORTS).decode("utf-8"))):
        icao = a.get("icao_code") or a.get("ident")
        if icao in passengers:
            rows.append({"icao": icao, "iata": a.get("iata_code") or "", "name": a["name"],
                         "country": a["iso_country"], "lat": a["latitude_deg"], "lon": a["longitude_deg"],
                         "passengers": passengers[icao], "year": year})
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["icao", "iata", "name", "country", "lat", "lon", "passengers", "year"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["icao"]))
    missing = sorted(set(passengers) - {r["icao"] for r in rows})
    print(f"{len(rows)} airports written to {OUT} ({year}); not found in OurAirports: {missing}")


if __name__ == "__main__":
    main()
