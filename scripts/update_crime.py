"""Refresh the recorded crime of each place, from each country's official figures.

    python scripts/update_crime.py

Needs a network connection. Writes data/crime.csv; the app only reads that
file (crime.py). Rates are per 1,000 residents (French burglaries: per 1,000
homes), with one row for the whole country to compare with.

- France: SSMSI, Ministère de l'Intérieur, by commune: burglaries of homes
  and physical violence outside the family. A commune with too few cases to
  publish (fewer than 5 over three years) has no rate;
- Portugal: INE (from DGPJ), by municipality: all crimes, property crimes,
  crimes against physical integrity;
- Spain: Ministerio del Interior, Balance de Criminalidad, the towns over
  20,000 people and every province: all offences, burglaries of homes,
  violent robberies and assaults. Counts over the population of
  data/eu_population.csv;
- Italy: ISTAT, crimes reported by the police, by province: all crimes,
  burglaries of homes, robberies.

Crime recorded is crime reported: a place where few report looks safer than
it is. Commit the file through a pull request; once a year is plenty.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "data", "crime.csv")
POPULATION = os.path.join(HERE, "data", "eu_population.csv")

FR_BASE = ("https://static.data.gouv.fr/resources/bases-statistiques-communale-departementale-et-regionale-de-la-"
           "delinquance-enregistree-par-la-police-et-la-gendarmerie-nationales/")
FR_DATASET = ("https://www.data.gouv.fr/api/1/datasets/bases-statistiques-communale-departementale-et-regionale-"
              "de-la-delinquance-enregistree-par-la-police-et-la-gendarmerie-nationales/")
FR_BURGLARY, FR_VIOLENCE = "Cambriolages de logement", "Violences physiques hors cadre familial"
PT_URL = "https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&varcd=0012260&lang=PT"
ES_URL = ("https://estadisticasdecriminalidad.ses.mir.es/sec/jaxiPx/files/_px/es/csv_bdsc/"
          "DatosBalanceAnt/l0/1509012.csv_bdsc")
IT_URL = "https://esploradati.istat.it/SDMXWS/rest/data/IT1,73_67_DF_DCCV_DELITTIPS_9,1.0/?startPeriod={}&format=csv"
COLUMNS = ["country", "code", "name", "level", "year", "total", "burglary", "violence", "source"]


def _get(url: str, accept: str | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "auction-scanner (+https://github.com/adaixojoao/"
                                                             "auction-scanner)", **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read()


def _num(text) -> float | None:
    """'11.781' (Spanish thousands), '0,0078' (French decimals), '4.4' → float."""
    if text in (None, "", "NA"):
        return None
    text = str(text).strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _r(v: float | None) -> str:
    return "" if v is None else f"{v:.2f}"


# ─── France ─────────────────────────────────────────────────────────────
def france() -> list[list]:
    resources = json.loads(_get(FR_DATASET))["resources"]

    def url(prefix: str, fmt: str) -> str:
        return next(r["url"] for r in resources if r["title"].startswith(prefix) and r.get("format") == fmt)

    rows = []
    dep = list(csv.DictReader(io.StringIO(_get(url("DEP -", "csv")).decode("utf-8-sig")), delimiter=";"))
    year = max(r["annee"] for r in dep)
    for indicator, key in ((FR_BURGLARY, "insee_log"), (FR_VIOLENCE, "insee_pop")):
        picked = [r for r in dep if r["annee"] == year and r["indicateur"] == indicator]
        count = sum(_num(r["nombre"]) or 0 for r in picked)
        base = sum(_num(r[key]) or 0 for r in picked)
        rows.append((indicator, count / base * 1000 if base else None))
    national = dict(rows)
    out = [["FR", "FR", "France", "country", year, "", _r(national[FR_BURGLARY]), _r(national[FR_VIOLENCE]),
            "SSMSI"]]
    communes: dict[str, dict] = {}
    with urllib.request.urlopen(urllib.request.Request(url("COM -", "csv.gz"), headers={"User-Agent": "auction-scanner"}),
                                timeout=900) as resp:
        text = io.TextIOWrapper(gzip.GzipFile(fileobj=resp), encoding="utf-8-sig")
        for r in csv.DictReader(text, delimiter=";"):
            if r["annee"] != year or r["indicateur"] not in (FR_BURGLARY, FR_VIOLENCE):
                continue
            entry = communes.setdefault(r["CODGEO_2026"], {})
            entry[r["indicateur"]] = _num(r["taux_pour_mille"]) if r["est_diffuse"] == "diff" else None
    for code, rates in sorted(communes.items()):
        out.append(["FR", code, "", "commune", year, "", _r(rates.get(FR_BURGLARY)), _r(rates.get(FR_VIOLENCE)),
                    "SSMSI"])
    return out


# ─── Portugal ───────────────────────────────────────────────────────────
def portugal() -> list[list]:
    data = json.loads(_get(PT_URL))[0]
    year = data["UltimoPref"]
    by_place: dict[str, dict] = {}
    for r in data["Dados"][year]:
        by_place.setdefault(r["geocod"], {"name": r["geodsg"]})[r["dim_3"]] = _num(r.get("valor"))
    out = []
    for code, v in sorted(by_place.items()):
        level = "country" if code == "PT" else "municipality" if len(code) == 7 else None
        if level:
            out.append(["PT", code, v["name"] if level != "country" else "Portugal", level, year,
                        _r(v.get("T")), _r(v.get("6")), _r(v.get("1")), "INE/DGPJ"])
    return out


# ─── Spain ──────────────────────────────────────────────────────────────
def _es_population() -> dict[str, int]:
    with open(POPULATION, encoding="utf-8", newline="") as f:
        return {r["lau_code"]: int(r["population"]) for r in csv.DictReader(f)
                if r["country"] == "ES" and r.get("population")}


def spain() -> list[list]:
    raw = _get(ES_URL)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    pop = _es_population()
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    year = max(m.group(1) for r in rows[1:] if len(r) > 2 for m in [re.search(r"enero-diciembre (\d{4})", r[2])] if m)
    counts: dict[str, dict] = {}
    order: list[str] = []
    for r in rows[1:]:
        if len(r) < 4 or r[2] != f"enero-diciembre {year}":
            continue
        if r[0] not in counts:
            order.append(r[0])
        key = re.match(r"^([IVX]+|\d+(?:\.\d+)?)", r[1])     # "III. TOTAL…", "7.1.-Robos…", "6. Robos…"
        if key:
            counts.setdefault(r[0], {})[key.group(1)] = _num(r[3])
    out = []
    province = None
    for geo in order:
        c = counts[geo]
        total, burglary = c.get("III"), c.get("7.1")
        violence = (c.get("6") or 0) + (c.get("3") or 0) if c.get("6") is not None or c.get("3") is not None else None
        m = re.match(r"^(\d{5}) (.+)$", geo)
        if geo.startswith("Provincia de"):
            province = {"name": geo[len("Provincia de "):], "geo": geo}
            continue
        if m:
            code = m.group(1)
            if province and "code" not in province:
                province["code"] = code[:2]
                people = sum(p for k, p in pop.items() if k.startswith(code[:2]))
                pc = counts[province["geo"]]
                pv = ((pc.get("6") or 0) + (pc.get("3") or 0)) if pc.get("6") is not None else None
                out.append(["ES", code[:2], province["name"], "province", year, *_rates(
                    people, pc.get("III"), pc.get("7.1"), pv), "Ministerio del Interior"])
            people = pop.get(code)
            if people:
                out.append(["ES", code, m.group(2), "municipality", year, *_rates(people, total, burglary, violence),
                            "Ministerio del Interior"])
        elif geo == "NACIONAL":
            out.append(["ES", "ES", "Spain", "country", year, *_rates(sum(pop.values()), total, burglary, violence),
                        "Ministerio del Interior"])
    return out


def _rates(people, *counts) -> list[str]:
    return [_r(c / people * 1000 if people and c is not None else None) for c in counts]


# ─── Italy ──────────────────────────────────────────────────────────────
def italy() -> list[list]:
    from datetime import date
    text = _get(IT_URL.format(date.today().year - 3), "application/vnd.sdmx.data+csv;version=1.0.0").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    year = max(r["TIME_PERIOD"] for r in rows)
    by_area: dict[str, dict] = {}
    for r in rows:
        if r["TIME_PERIOD"] == year and r["TYPE_CRIME"] in ("TOT", "BURGTHEF", "ROBBER"):
            by_area.setdefault(r["REF_AREA"], {})[r["TYPE_CRIME"]] = _num(r["OBS_VALUE"])
    out = []
    for code, v in sorted(by_area.items()):
        level = "country" if code == "IT" else "province" if len(code) == 5 else None
        if level:                                        # ISTAT gives rates per 100,000
            out.append(["IT", code, "Italy" if level == "country" else "", level, year,
                        *(_r(v[k] / 100) if v.get(k) is not None else "" for k in ("TOT", "BURGTHEF", "ROBBER")),
                        "ISTAT"])
    return out


def main() -> None:
    wanted = [a.upper() for a in sys.argv[1:]] or ["FR", "PT", "ES", "IT"]
    kept = []
    if os.path.exists(OUT) and sys.argv[1:]:
        with open(OUT, encoding="utf-8", newline="") as f:
            kept = [r for r in csv.reader(f)][1:]
        kept = [r for r in kept if r[0] not in wanted]
    fetch = {"FR": france, "PT": portugal, "ES": spain, "IT": italy}
    rows = kept
    for country in wanted:
        got = fetch[country]()
        print(f"{country}: {len(got)} places")
        rows += got
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    print(f"{len(rows)} places written to {OUT}")


if __name__ == "__main__":
    main()
