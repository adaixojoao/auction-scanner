"""Refresh when each EU region's homes were built.

    python scripts/update_building_age.py

Needs a network connection. Writes data/building_age_2021.csv; the app only
reads that file (building_age.py).

The source is Eurostat's cens_21dwop_r3, the 2021 census of every EU country
by NUTS 3 region: conventional dwellings by period of construction. One row
per region, and one per country to compare with.
"""
from __future__ import annotations

import csv
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/cens_21dwop_r3"
       "?format=JSON&lang=en&housing=DW")
OUT = os.path.join(HERE, "data", "building_age_2021.csv")
PERIODS = {"before_1946": ("Y_LT1919", "Y1919-1945"), "y1946_1980": ("Y1946-1960", "Y1961-1980"),
           "y1981_2000": ("Y1981-2000",), "since_2001": ("Y2001-2010", "Y2011-2015", "Y_GE2016")}


def main() -> None:
    with urllib.request.urlopen(URL, timeout=600) as resp:
        data = json.load(resp)
    dims, size = data["id"], data["size"]
    index = {d: data["dimension"][d]["category"]["index"] for d in dims}
    stride = {d: 1 for d in dims}
    for i in range(len(dims) - 2, -1, -1):
        stride[dims[i]] = stride[dims[i + 1]] * size[i + 1]
    fixed = sum(index[d][next(iter(index[d]))] * stride[d] for d in dims if d not in ("y_const", "geo"))
    values = data["value"]
    rows = []
    for geo in sorted(index["geo"], key=index["geo"].get):
        if len(geo) not in (2, 5):                         # countries and NUTS 3 regions
            continue
        at = fixed + index["geo"][geo] * stride["geo"]

        def count(period: str) -> int:
            return int(values.get(str(at + index["y_const"][period] * stride["y_const"])) or 0)

        row = {"geo": geo, **{k: sum(count(p) for p in ps) for k, ps in PERIODS.items()}}
        if sum(row[k] for k in PERIODS):                   # the region gave its periods
            rows.append(row)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["geo", *PERIODS])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} regions written to {OUT}")


if __name__ == "__main__":
    main()
