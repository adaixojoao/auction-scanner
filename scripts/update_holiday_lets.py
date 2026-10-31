"""Refresh how many licensed holiday lets each Portuguese parish has.

    python scripts/update_holiday_lets.py

Needs a network connection. Writes data/pt_holiday_lets.csv; the app only
reads that file (holiday_lets.py).

The source is Turismo de Portugal's register of alojamento local (RNAL), asked
one municipality at a time (webservices.turismodeportugal.pt …/list_RNAL?Concelho=…).
Each record names the person who runs the let, with their tax number and
e-mail: none of that is kept. Only counts are written — lets, beds and lets
registered in the last two years — per parish.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

URL = "https://webservices.turismodeportugal.pt/RNT_External/rest/RNT/list_RNAL"
MUNICIPALITIES = os.path.join(HERE, "data", "pt_home_prices.csv")
OUT = os.path.join(HERE, "data", "pt_holiday_lets.csv")
RECENT_YEARS = 2


def _municipalities() -> list[str]:
    with open(MUNICIPALITIES, encoding="utf-8") as f:
        return sorted({r["municipality"] for r in csv.DictReader(f) if r.get("municipality")})


def _fetch(session, town: str) -> list[dict] | None:
    for attempt in range(3):
        try:
            resp = session.get(URL, params={"Concelho": town}, timeout=180)
            if resp.status_code == 200:
                return resp.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 + 5 * attempt)
    return None


def main() -> None:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (auction-scanner holiday-let counts)"
    since = (dt.date.today() - dt.timedelta(days=365 * RECENT_YEARS)).isoformat()
    counts: dict[tuple[str, str, str], list[int]] = {}
    missed = []
    for town in _municipalities():
        records = _fetch(session, town)
        if records is None:
            missed.append(town)
            continue
        for rec in records:
            r = rec.get("RNAL_Registo") or {}
            key = (r.get("Concelho") or town, r.get("Freguesia") or "", str(r.get("DTMNFR") or ""))
            row = counts.setdefault(key, [0, 0, 0])
            row[0] += 1
            try:
                row[1] += int(r.get("NrCamas") or 0)
            except (TypeError, ValueError):
                pass
            row[2] += (r.get("DataRegisto") or "") >= since
        time.sleep(0.5)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["municipality", "parish", "dtmnfr", "lets", "beds", "lets_recent", "period", "source"])
        stamp = dt.date.today().isoformat()
        for (town, parish, code), (lets, beds, recent) in sorted(counts.items()):
            w.writerow([town, parish, code, lets, beds, recent, stamp, "Turismo de Portugal, RNAL"])
    print(f"{len(counts)} parishes written to {OUT}" + (f"; not read: {', '.join(missed)}" if missed else ""))


if __name__ == "__main__":
    main()
