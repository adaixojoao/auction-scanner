"""Refresh each country's mortgage rates and house price index.

    python scripts/update_markets.py

Needs a network connection. Writes data/mortgage_rates.csv and
data/house_prices.csv; the app only reads those files (markets.py).

- mortgage_rates: the ECB's MFI interest rate statistics (MIR), the average
  annual rate on new loans to households for house purchase in each euro-area
  country, monthly, the last 13 months; and the 12-month Euribor (FM), the
  rate most variable loans follow, as country "EURIBOR";
- house_prices: Eurostat's house price index (prc_hpi_q, all dwellings
  bought), the change over a year and over five years, latest quarter.
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MORTGAGES = os.path.join(HERE, "data", "mortgage_rates.csv")
HOUSE_PRICES = os.path.join(HERE, "data", "house_prices.csv")
ECB = "https://data-api.ecb.europa.eu/service/data/{}?lastNObservations=13&format=csvdata"
MIR = "MIR/M..B.A2C.A.R.A.2250.EUR.N"
EURIBOR = "FM/M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA"
HPI = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hpi_q"
       "?format=JSON&lang=en&purchase=TOTAL&unit=I15_Q&lastTimePeriod=21")


def _get(url: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def mortgages() -> list[dict]:
    rows = []
    for key, area in ((MIR, None), (EURIBOR, "EURIBOR")):
        for r in csv.DictReader(io.StringIO(_get(ECB.format(key), "text/csv").decode("utf-8-sig"))):
            if r.get("OBS_VALUE"):
                rows.append({"country": area or r["REF_AREA"].replace("U2", "EA"), "month": r["TIME_PERIOD"],
                             "rate": round(float(r["OBS_VALUE"]), 2)})
    return sorted(rows, key=lambda x: (x["country"], x["month"]))


def house_prices() -> list[dict]:
    data = json.loads(_get(HPI))
    geo, time = data["dimension"]["geo"]["category"]["index"], data["dimension"]["time"]["category"]["index"]
    quarters = sorted(time, key=time.get)
    values, n = data["value"], len(quarters)
    rows = []
    for country, g in geo.items():
        if len(country) != 2 and country != "EA":
            continue
        series = {q: values.get(str(g * n + time[q])) for q in quarters}
        latest = next((q for q in reversed(quarters) if series[q] is not None), None)
        if not latest:
            continue
        i = quarters.index(latest)
        def change(back: int):
            old = series.get(quarters[i - back]) if i >= back else None
            return round((series[latest] / old - 1) * 100, 1) if old else ""
        rows.append({"country": "GR" if country == "EL" else country, "quarter": latest,
                     "year_pct": change(4), "five_year_pct": change(20)})
    return sorted(rows, key=lambda x: x["country"])


def _write(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    m, h = mortgages(), house_prices()
    _write(MORTGAGES, m)
    _write(HOUSE_PRICES, h)
    print(f"{len(m)} monthly rates written to {MORTGAGES}; {len(h)} countries written to {HOUSE_PRICES}")


if __name__ == "__main__":
    main()
