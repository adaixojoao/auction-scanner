"""Refresh the nights booked on short-let platforms in each EU region.

    python scripts/update_platform_nights.py

Needs a network connection. Writes data/platform_nights.csv; the app only
reads that file (holiday_lets.py).

The source is Eurostat's tour_ce_oan3: guest nights spent at short-stay
accommodation booked through Airbnb, Booking and Expedia, by NUTS 3 region,
every year (experimental statistics; the platforms send Eurostat totals, never
bookings). A municipality is attached to its region by data/place_nuts3.csv.
"""
from __future__ import annotations

import csv
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/tour_ce_oan3"
       "?format=JSON&lang=en&c_resid=TOTAL&unit=NR")
OUT = os.path.join(HERE, "data", "platform_nights.csv")


def main() -> None:
    with urllib.request.urlopen(URL, timeout=300) as resp:
        data = json.load(resp)
    dims, size = data["id"], data["size"]
    index = {d: data["dimension"][d]["category"]["index"] for d in dims}
    geos = sorted(index["geo"], key=index["geo"].get)
    years = sorted(index["time"], key=index["time"].get)
    stride = {d: 1 for d in dims}
    for i in range(len(dims) - 2, -1, -1):
        stride[dims[i]] = stride[dims[i + 1]] * size[i + 1]
    values = data["value"]
    rows = []
    for geo in geos:
        if len(geo) != 5:                      # NUTS 3 only (country and NUTS 1/2 totals are shorter)
            continue
        series = {}
        for year in years:
            pos = index["geo"][geo] * stride["geo"] + index["time"][year] * stride["time"]
            value = values.get(str(pos))
            if value is not None:
                series[year] = int(value)
        if not series:
            continue
        last = max(series)
        prev = str(int(last) - 1)
        rows.append([geo, last, series[last], prev if prev in series else "", series.get(prev, "")])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nuts3", "year", "nights", "prev_year", "prev_nights"])
        w.writerows(rows)
    print(f"{len(rows)} regions written to {OUT}")


if __name__ == "__main__":
    main()
