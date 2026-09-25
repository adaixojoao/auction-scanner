"""
Refresh data/pt_home_prices.csv: the median price per m² of homes sold in every
Portuguese municipality, from Statistics Portugal (INE), for the score's
"X% below local prices".

    python scripts/update_prices.py                      # the default indicator
    python scripts/update_prices.py --indicator 0012345  # another INE indicator code

Run it on a PC that can reach www.ine.pt, check the summary it prints, and
commit data/pt_home_prices.csv through a pull request. INE publishes the
figures every quarter; refreshing once or twice a year is plenty.

The indicator wanted is INE's median sale value per m² of family dwellings by
municipality, quarterly, from the housing price statistics at local level. The
default, 0012234, is "Valor mediano das vendas de alojamentos familiares nos
últimos 12 meses (Metodologia 2022 - €/m²) por Localização geográfica (NUTS -
2024) e Categoria": published every quarter, each value the median of the last
12 months. It is the series that covers all 308 municipalities; INE's plain
quarterly median exists only for regions and cities over 100,000 people. If
INE renumbers it, find the code on ine.pt (Estatísticas → Indicadores, search
"valor mediano das vendas") and pass it with --indicator.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from prices import COLUMNS, PARISH_COLUMNS, PT_FILE, PT_PARISH_FILE  # noqa: E402

API = "https://www.ine.pt/ine/json_indicador/pindica.jsp"
DEFAULT_INDICATOR = "0012234"


def parse_ine(payload) -> tuple[list[dict], str, str]:
    """(rows, period, indicator title) from INE's JSON API answer: the latest
    period's values for municipalities, for all kinds of dwelling ("Total")
    when the indicator splits them.

    Municipalities have 7-character codes. Since the 2024 regions (NUTS 2024)
    many contain letters ("11D1818" Sernancelhe, "1C20204" Barrancos); only
    reading all-digit codes kept 144 of the 308."""
    entry = payload[0] if isinstance(payload, list) else payload
    title = entry.get("IndicadorDsg", "")
    data = entry.get("Dados") or {}
    if not data:
        raise ValueError(f"no data in INE's answer: {str(entry)[:300]}")
    period = entry.get("UltimoPref") if entry.get("UltimoPref") in data else list(data)[-1]
    rows = []
    for rec in data[period]:
        code = str(rec.get("geocod", ""))
        if not (len(code) == 7 and code.isalnum()):          # municipalities only
            continue
        extra = [v for k, v in rec.items() if k.startswith("dim_") and k.endswith("_t")]
        if extra and not all(str(v).strip().lower() in ("total", "t") for v in extra):
            continue
        try:
            value = float(str(rec.get("valor", "")).replace(",", "."))
        except ValueError:
            continue
        rows.append({"municipality": rec.get("geodsg", "").strip(), "eur_m2": round(value),
                     "period": period, "source": "INE"})
    return rows, period, title


def parse_ine_parishes(payload) -> list[dict]:
    """The parishes INE gives a figure for (9-character codes: the Porto and
    Lisbon areas, Setúbal, the Algarve and cities over 100,000 people), each
    under its municipality (the first 7 characters of its code)."""
    entry = payload[0] if isinstance(payload, list) else payload
    data = entry.get("Dados") or {}
    if not data:
        return []
    period = entry.get("UltimoPref") if entry.get("UltimoPref") in data else list(data)[-1]
    towns = {str(r.get("geocod")): r.get("geodsg", "").strip() for r in data[period]
             if len(str(r.get("geocod", ""))) == 7}
    rows = []
    for rec in data[period]:
        code = str(rec.get("geocod", ""))
        extra = [v for k, v in rec.items() if k.startswith("dim_") and k.endswith("_t")]
        if len(code) != 9 or code[:7] not in towns or (
                extra and not all(str(v).strip().lower() in ("total", "t") for v in extra)):
            continue
        try:
            value = float(str(rec.get("valor", "")).replace(",", "."))
        except ValueError:
            continue
        rows.append({"municipality": towns[code[:7]], "parish": rec.get("geodsg", "").strip(),
                     "eur_m2": round(value), "period": period, "source": "INE"})
    return sorted(rows, key=lambda r: (r["municipality"], r["parish"]))


def main(argv=None) -> int:
    import requests

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--indicator", default=DEFAULT_INDICATOR)
    ap.add_argument("--out", default=PT_FILE)
    args = ap.parse_args(argv)

    r = requests.get(API, params={"op": "2", "varcd": args.indicator, "lang": "PT"}, timeout=60)
    r.raise_for_status()
    rows, period, title = parse_ine(r.json())
    print(f"Indicator {args.indicator}: {title}")
    print(f"Period: {period} — {len(rows)} municipalities")
    if len(rows) < 250:
        print("Fewer than 250 municipalities: probably not the right indicator. Nothing written.")
        return 1
    rows.sort(key=lambda row: row["municipality"])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    cheapest = sorted(rows, key=lambda row: row["eur_m2"])[:3]
    dearest = sorted(rows, key=lambda row: -row["eur_m2"])[:3]
    print("Cheapest:", ", ".join(f"{x['municipality']} €{x['eur_m2']}/m²" for x in cheapest))
    print("Dearest: ", ", ".join(f"{x['municipality']} €{x['eur_m2']}/m²" for x in dearest))
    print(f"Written to {args.out}")
    parishes = parse_ine_parishes(r.json())
    if parishes and args.out == PT_FILE:
        with open(PT_PARISH_FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PARISH_COLUMNS)
            writer.writeheader()
            writer.writerows(parishes)
        print(f"{len(parishes)} parishes written to {PT_PARISH_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
