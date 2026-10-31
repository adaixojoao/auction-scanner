"""Refresh local incomes by municipality, from each country's statistics office.

    python scripts/update_incomes.py

Needs a network connection (Spain's table is ~400 MB, read as it downloads).
Writes data/incomes.csv; the app only reads that file (incomes.py).

Each country measures income its own way, so a place is only ever compared
with its own country's figure:

- France: INSEE Filosofi, median living standard (disposable income per
  consumption unit), by commune and Paris/Lyon/Marseille arrondissement; the
  communes too small to publish get their département's;
- Spain: INE Atlas de distribución de renta de los hogares, median income
  per consumption unit, by municipality;
- Portugal: INE, median gross declared income per tax household, by
  municipality;
- Italy: Ministero dell'Economia e delle Finanze, mean total declared income
  per taxpayer (IRPEF), by comune.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "data", "incomes.csv")
FR_URL = "https://www.insee.fr/fr/statistiques/fichier/8984752/FILOSOFI_CC_csv.zip"
ES_CSV = "https://www.ine.es/jaxiT3/files/t/es/csv_bdsc/30824.csv"
ES_NATIONAL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/53689?nult=1"
ES_MEDIAN = "Mediana de la renta por unidad de consumo"
PT_URL = "https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&varcd=0012712&lang=PT"
IT_URL = ("https://www1.finanze.gov.it/finanze/analisi_stat/public/v_4_0_0/contenuti/"
          "Redditi_e_principali_variabili_IRPEF_su_base_comunale_CSV_{}.zip")
HEADER = ["country", "code", "name", "level", "year", "income"]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "auction-scanner (+https://github.com/adaixojoao/"
                                                             "auction-scanner)"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read()


def france() -> list[list]:
    import prices
    names = {}
    with open(prices.EU_POPULATION_FILE, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["country"] == "FR":
                names[r["lau_code"]] = r["name"]
    z = zipfile.ZipFile(io.BytesIO(_get(FR_URL)))
    data = next(n for n in z.namelist() if n.endswith("_data.csv"))
    out = []
    with z.open(data) as f:
        for r in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"), delimiter=";"):
            if r["FILOSOFI_MEASURE"] != "MED_SL" or not r["OBS_VALUE"]:
                continue
            level = {"COM": "municipality", "ARM": "municipality", "DEP": "department",
                     "FRANCE": "country"}.get(r["GEO_OBJECT"])
            if level:
                code = "FR" if level == "country" else r["GEO"]
                out.append(["FR", code, names.get(code, "France" if level == "country" else ""), level,
                            r["TIME_PERIOD"], round(float(r["OBS_VALUE"]))])
    return out


def spain() -> list[list]:
    """The whole country's table (every municipality, district and census
    section, ~400 MB), read as it streams; only municipalities are kept."""
    best: dict[str, list] = {}
    req = urllib.request.Request(ES_CSV, headers={"User-Agent": "auction-scanner"})
    with urllib.request.urlopen(req, timeout=1800) as resp:
        lines = io.TextIOWrapper(resp, "utf-8-sig")
        for r in csv.reader(lines, delimiter=";"):
            if len(r) < 6 or r[3] != ES_MEDIAN or r[1] or r[2] or not r[5].strip():
                continue
            code, _, name = r[0].partition(" ")
            if not code.isdigit():
                continue
            try:                                   # ".." or "." where the figure is withheld
                year, value = int(r[4]), float(r[5].replace(".", "").replace(",", "."))
            except ValueError:
                continue
            if code not in best or year > best[code][4]:
                best[code] = ["ES", code, name.strip(), "municipality", year, round(value)]
    national = [s for s in json.loads(_get(ES_NATIONAL)) if ES_MEDIAN in s["Nombre"]][0]["Data"][-1]
    return list(best.values()) + [["ES", "ES", "España", "country", national["Anyo"], round(national["Valor"])]]


def portugal() -> list[list]:
    data = json.loads(_get(PT_URL))[0]
    year = data["UltimoPref"]
    out = []
    for r in data["Dados"][year]:
        code = r["geocod"]
        level = "country" if code == "PT" else "municipality" if len(code) == 7 else None
        if level and r.get("valor"):
            out.append(["PT", code, "Portugal" if level == "country" else r["geodsg"], level, year,
                        round(float(r["valor"]))])
    return out


def italy() -> list[list]:
    import datetime
    for year in range(datetime.date.today().year, 2019, -1):
        try:
            z = zipfile.ZipFile(io.BytesIO(_get(IT_URL.format(year))))
            break
        except Exception:                     # not published yet: the year before
            continue
    else:
        raise RuntimeError("no IRPEF file found")
    text = z.open(z.namelist()[0]).read().decode("latin-1")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    head = rows[0]
    count = head.index("Reddito complessivo - Frequenza")
    amount = head.index("Reddito complessivo - Ammontare in euro")
    out, total, people = [], 0.0, 0
    for r in rows[1:]:
        try:
            n, eur = int(r[count]), float(r[amount])
        except (ValueError, IndexError):
            continue
        if n <= 0:
            continue
        total, people = total + eur, people + n
        out.append(["IT", r[2], r[3].title(), "municipality", r[0], round(eur / n)])
    out.append(["IT", "IT", "Italia", "country", year, round(total / people)])
    return out


def main() -> None:
    rows = []
    for name, fn in (("France", france), ("Spain", spain), ("Portugal", portugal), ("Italy", italy)):
        got = fn()
        print(f"{name}: {len(got)} rows", flush=True)
        rows.extend(got)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"{len(rows)} rows written to {OUT}")


if __name__ == "__main__":
    main()
