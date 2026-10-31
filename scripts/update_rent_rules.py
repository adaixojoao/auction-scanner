"""Refresh the French communes where letting rules are tighter.

    python scripts/update_rent_rules.py

Needs a network connection. Writes data/fr_rent_rules.csv; the app only reads
that file (rent_rules.py).

- zone: the tight-market list of décret 2013-392 (taxe sur les logements
  vacants, capped rent rises between tenants, the surcharge on second homes),
  as the Ministère de la Transition écologique publishes it (version of
  décret 2025-1267, 22 December 2025): "tendue" or "touristique" (tourist
  and tight);
- rent_cap: the communes where rents are capped (encadrement du niveau des
  loyers, loi ELAN art. 140), "all" or "part" of the commune, and the date the
  prefect's current order runs to. The list is a compilation on data.gouv.fr
  in which every row cites its décret and arrêté préfectoral.
"""
from __future__ import annotations

import csv
import io
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "fr_rent_rules.csv")
ZONES = ("https://static.data.gouv.fr/resources/liste-des-communes-selon-le-zonage-tlv-1/20251230-094759/"
         "zonage-tlv-decret-22-dec-2025.csv")
CAPS = ("https://static.data.gouv.fr/resources/communes-a-encadrement-des-loyers-encadrement-du-niveau-des-loyers-"
        "loi-elan-article-140/20261029-231309/communes-encadrement-des-loyers.csv")
ZONE_NAMES = {"1": "tendue", "2": "touristique"}


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def main() -> None:
    rows: dict[str, dict] = {}
    reader = csv.reader(io.StringIO(_get(ZONES)), delimiter=";")
    header = next(reader)
    latest = len(header) - 1                    # the newest zoning is the last column
    for r in reader:
        if len(r) <= latest:
            continue
        zone = ZONE_NAMES.get(r[latest].strip()[:1])
        if zone:
            rows[r[0]] = {"insee": r[0], "name": r[2], "zone": zone, "rent_cap": "", "cap_until": "", "territory": ""}
    for r in csv.DictReader(io.StringIO(_get(CAPS))):
        row = rows.setdefault(r["code_insee"], {"insee": r["code_insee"], "name": r["commune"], "zone": "",
                                                "rent_cap": "", "cap_until": "", "territory": ""})
        row["rent_cap"] = "part" if r.get("zone") == "partie" else "all"
        row["cap_until"] = r.get("en_vigueur_au") or ""
        row["territory"] = r.get("territoire") or ""
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["insee", "name", "zone", "rent_cap", "cap_until", "territory"])
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda x: x["insee"]))
    caps = sum(1 for x in rows.values() if x["rent_cap"])
    print(f"{len(rows)} communes ({caps} with capped rents) written to {OUT}")


if __name__ == "__main__":
    main()
