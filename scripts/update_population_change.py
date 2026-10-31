"""Refresh how many people each municipality gained or lost since 2011.

    python scripts/update_population_change.py

Needs a network connection. Writes data/population_change.csv; the app only
reads that file (population_change.py).

Eurostat GISCO's local administrative units carry each year's population:
the 2011 census count and the latest year's (some countries' newest file
has no counts yet: each country takes its latest year that has). A unit kept
under the same code is compared directly; one merged or split since 2011 has
no code in both years and is left out rather than guessed. Each country's own change, over
the same units, is kept to compare with.
"""
from __future__ import annotations

import csv
import io
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "population_change.csv")
URL = "https://gisco-services.ec.europa.eu/distribution/v2/lau/csv/LAU_RG_01M_{}_4326.csv"
FIRST, LAST = 2011, (2024, 2023, 2022, 2021)


def _year(year: int) -> dict[str, tuple[str, str, int]]:
    with urllib.request.urlopen(URL.format(year), timeout=300) as resp:
        text = resp.read().decode("utf-8-sig")
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            pop = int(float(r.get(f"POP_{year}") or 0))
        except ValueError:
            continue
        if pop > 0:
            out[r["GISCO_ID"]] = (r["CNTR_CODE"], r["LAU_NAME"], pop)
    return out


def main() -> None:
    first = _year(FIRST)
    # Each country takes its newest year with counts, so all its units share one year
    latest: dict[str, tuple[int, dict]] = {}
    for year in LAST:
        units = _year(year)
        for country in {c for c, _, _ in units.values()} - set(latest):
            latest[country] = (year, {g: u for g, u in units.items() if u[0] == country})
    rows, totals = [], {}
    for country, (year, units) in sorted(latest.items()):
        for gid, (_, name, now) in units.items():
            then = first.get(gid)
            if not then or then[0] != country:
                continue
            t = totals.setdefault(country, [0, 0, year])
            t[0], t[1] = t[0] + then[2], t[1] + now
            rows.append([country, gid.split("_", 1)[1], name, year, then[2], now,
                         round((now / then[2] - 1) * 100, 1)])
    for country, (then, now, year) in sorted(totals.items()):
        rows.append([country, country, "", year, then, now, round((now / then - 1) * 100, 1)])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["country", "code", "name", "year", f"pop_{FIRST}", "pop_latest", "change_pct"])
        w.writerows(rows)
    print(f"{len(rows) - len(totals)} units in {len(totals)} countries written to {OUT}")


if __name__ == "__main__":
    main()
