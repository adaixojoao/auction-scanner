"""Official standing-timber prices → data/forest_prices.csv.

Portugal: ICNF's SIMeF ("Preços da madeira por espécie - áreas públicas"), the
average price per m³ of wood sold standing in public-forest sales, per species and
quarter. The table is an ASP.NET grid read page by page; this keeps the last three
years, weighted by the volume sold.

    python scripts/update_forest_prices.py
"""
from __future__ import annotations

import csv
import html
import os
import re
import sys
import time
from collections import defaultdict

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "forest_prices.csv")
SIMEF = "https://simef.icnf.pt/pages/ESTATISTICAS.aspx"
GRID = "ctl00$MainContent$GV_TBLPRECOS_SP"
YEARS = 3
# SIMeF's species → the model's crop names (forestry.CROPS)
PT_SPECIES = {"Pinheiro-bravo": "maritime pine", "Pinheiro-manso": "stone pine", "Pseudotsuga": "Douglas fir",
              "Choupo": "poplar", "Castanheiro": "chestnut", "Sobreiro": "cork oak",
              "Carvalho-roble": "oak", "Eucalipto": "eucalyptus", "Pinheiro-silvestre": "Scots pine"}


# France: France Bois Forêt's yearly indicator of standing prices in private
# forest (grouped sales by the Experts Forestiers de France). Published as a PDF
# only, so its figures are copied here by hand — check the new edition each April.
FR_FBF = {"Douglas fir": 89, "maritime pine": 56, "poplar": 73, "Scots pine": 36, "oak": 228, "chestnut": 119}
FR_FBF_SOURCE = ("France Bois Forêt indicator 2025, private-forest sawlog sales", "2024",
                 "https://franceboisforet.fr/wp-content/uploads/2025/04/FBF_PRIX_PIED_2025-2404_VF.pdf")


def _state(page: str) -> dict:
    return {k: html.unescape(v) for k, v in re.findall(r'id="(__[A-Z]+)" value="([^"]*)"', page)}


def simef_rows(page: str) -> list[list[str]]:
    """[year, samples, species, quarter, min, max, average, m³] per grid row."""
    m = re.search(r'id="MainContent_GV_TBLPRECOS_SP"(.*?)</table>', page, re.S)
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1) if m else "", re.S):
        cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 8 and cells[0].isdigit():
            out.append(cells[:8])
    return out


def _num(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def simef_prices(rows: list[list[str]], years: int = YEARS) -> list[dict]:
    last = max(int(r[0]) for r in rows)
    total = defaultdict(lambda: [0.0, 0.0])
    for year, _, species, _, _, _, avg, m3 in rows:
        if int(year) > last - years and species in PT_SPECIES and _num(m3) > 0:
            total[species][0] += _num(avg) * _num(m3)
            total[species][1] += _num(m3)
    return [{"country": "PT", "crop": PT_SPECIES[sp], "eur_m3": round(v / q, 1), "m3": round(q),
             "period": f"{last - years + 1}-{last}", "source": "ICNF SIMeF, public-forest sales"}
            for sp, (v, q) in sorted(total.items())]


def fetch_simef(session) -> list[list[str]]:
    page = session.get(SIMEF, timeout=60).text
    rows, n = simef_rows(page), 1
    while f"Page${n + 1}" in page or "Page$Next" in page:
        form = _state(page)
        form.update({"__EVENTTARGET": GRID, "__EVENTARGUMENT": f"Page${n + 1}"})
        page = session.post(SIMEF, data=form, timeout=60).text
        more = simef_rows(page)
        if not more or more == rows[-len(more):]:
            break
        rows += more
        n += 1
        time.sleep(0.5)
    return rows


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (auction-scanner)"
    prices = simef_prices(fetch_simef(session))
    prices += [{"country": "FR", "crop": crop, "eur_m3": eur, "m3": "", "period": FR_FBF_SOURCE[1],
                "source": FR_FBF_SOURCE[0]} for crop, eur in FR_FBF.items()]
    if len(prices) < 4:
        print("SIMeF: too few species — the site changed. Nothing written.")
        return 1
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["country", "crop", "eur_m3", "m3", "period", "source"])
        w.writeheader()
        w.writerows(prices)
    print(f"Written {len(prices)} prices to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
