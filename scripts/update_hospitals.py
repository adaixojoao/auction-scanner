"""Refresh the hospitals of every EU country, from Eurostat's GISCO.

    python scripts/update_hospitals.py

Needs a network connection. Writes data/hospitals.csv; the app only reads
that file (hospitals.py).

The source is GISCO's "Healthcare services in Europe": the hospitals each
country's health ministry lists, with their position. Whether a hospital has
an emergency department is filled in for few countries; it is kept where the
record says so (the emergency field, or an emergency service named among its
specialities, as Portugal's "Urgência"). Germany is not in the dataset.
"""
from __future__ import annotations

import csv
import io
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://gisco-services.ec.europa.eu/pub/healthcare/last/csv/EU.csv"
OUT = os.path.join(HERE, "data", "hospitals.csv")
EMERGENCY = re.compile(r"urg[eê]ncia|urgencias?\b|urgences\b|emergenc|pronto soccorso|notaufnahme|spoedeisende",
                       re.I)


def main() -> None:
    with urllib.request.urlopen(URL, timeout=300) as resp:
        text = resp.read().decode("utf-8-sig")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            lat, lon = round(float(r["lat"]), 5), round(float(r["lon"]), 5)
        except (TypeError, ValueError):
            continue
        said = " ".join(r.get(k) or "" for k in ("list_specs", "comments", "facility_type"))
        # "emergency": the record says it has an emergency department; "hospital": it does not say
        kind = ("emergency" if (r.get("emergency") or "").upper() == "YES" or EMERGENCY.search(said)
                else "hospital")
        name = (r.get("site_name") or r.get("hospital_name") or "").strip()
        rows.append([r["cntr_id"], lat, lon, name, (r.get("city") or "").strip().title(), kind])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["country", "lat", "lon", "name", "city", "kind"])
        w.writerows(rows)
    print(f"{len(rows)} hospitals written to {OUT}")


if __name__ == "__main__":
    main()
