"""Refresh the French communes on the coastline-retreat list.

    python scripts/update_coast_retreat.py

Needs a network connection. Writes data/fr_coast_retreat.csv; the app only
reads that file (coast_retreat.py).

The list is the décret of article L321-15 of the Code de l'environnement
(loi Climat et Résilience, 2021) as the Ministère de la Transition écologique
publishes it on data.gouv.fr: the communes whose planning must be adapted to
the retreat of the coastline. Each row keeps the décret that first listed the
commune and the one in force.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "fr_coast_retreat.csv")
DATASET = "https://www.data.gouv.fr/api/1/datasets/liste-des-communes-volontaires-pour-sadapter-au-recul-du-trait-de-cote/"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def main() -> None:
    resources = json.loads(_get(DATASET))["resources"]
    url = max((r for r in resources if r["format"] == "csv"), key=lambda r: r["last_modified"])["url"]
    rows = []
    for r in csv.DictReader(io.StringIO(_get(url).decode("utf-8-sig"))):
        if r.get("is_currently_active") != "True":
            continue
        decrees = sorted(ast.literal_eval(r["trait_de_cote_historique"] or "[]"), key=lambda d: d["start_date"])
        now = next((d for d in decrees if d.get("is_active")), decrees[-1] if decrees else {})
        rows.append({"insee": r["code_commune"], "name": r["nom_commune"].title(),
                     "since": decrees[0]["start_date"] if decrees else "",
                     "decree": (now.get("numero_decret") or "").replace("_", "-"), "url": now.get("url_decret") or ""})
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["insee", "name", "since", "decree", "url"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["insee"]))
    print(f"{len(rows)} communes written to {OUT} ({url})")


if __name__ == "__main__":
    main()
