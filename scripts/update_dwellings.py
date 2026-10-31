"""Refresh how many homes in each EU region are nobody's main home.

    python scripts/update_dwellings.py

Needs a network connection. Writes data/dwellings_2021.csv; the app only
reads that file (holiday_lets.dwellings_note).

The source is Eurostat's cens_21dwob_r3, the 2021 census of every EU country
by NUTS 3 region: conventional dwellings, and those not occupied as anyone's
usual residence (empty homes and second homes together; the census does not
split them). One row per region, and one per country to compare with.
"""
from __future__ import annotations

import csv
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/cens_21dwob_r3"
       "?format=JSON&lang=en&building=TOTAL&housing=DW&housing=DW_NOC")
OUT = os.path.join(HERE, "data", "dwellings_2021.csv")


def main() -> None:
    with urllib.request.urlopen(URL, timeout=600) as resp:
        data = json.load(resp)
    dims, size = data["id"], data["size"]
    index = {d: data["dimension"][d]["category"]["index"] for d in dims}
    stride = {d: 1 for d in dims}
    for i in range(len(dims) - 2, -1, -1):
        stride[dims[i]] = stride[dims[i + 1]] * size[i + 1]
    fixed = sum(index[d][next(iter(index[d]))] * stride[d] for d in dims if d not in ("housing", "geo"))
    values = data["value"]
    rows = []
    for geo in sorted(index["geo"], key=index["geo"].get):
        if len(geo) not in (2, 5):                         # countries and NUTS 3 regions
            continue
        at = fixed + index["geo"][geo] * stride["geo"]
        total = values.get(str(at + index["housing"]["DW"] * stride["housing"]))
        empty = values.get(str(at + index["housing"]["DW_NOC"] * stride["housing"]))
        if total and empty is not None:
            rows.append([geo, int(total), int(empty)])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["geo", "dwellings", "not_main_home"])
        w.writerows(rows)
    print(f"{len(rows)} regions written to {OUT}")


if __name__ == "__main__":
    main()
