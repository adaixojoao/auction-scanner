"""
Refresh data/pt_home_prices.csv: the median price per m² of homes sold in every
Portuguese municipality, from Statistics Portugal (INE), for the score's
"X% below local prices".

    python scripts/update_prices.py                      # the default indicator
    python scripts/update_prices.py --indicator 0012345  # another INE indicator code

Run it on a PC that can reach www.ine.pt, check the summary it prints, and
commit data/pt_home_prices.csv through a pull request. INE publishes the
figures every quarter; refreshing once or twice a year is plenty.

The indicator wanted is INE's "Valor mediano das vendas por m² de alojamentos
familiares (€) por Localização geográfica (Município)", quarterly, from the
housing price statistics at local level. If the default code below is not
that indicator, find the right code on ine.pt (Estatísticas → Indicadores,
search "valor mediano das vendas por m2") and pass it with --indicator.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from prices import COLUMNS, PT_FILE  # noqa: E402

API = "https://www.ine.pt/ine/json_indicador/pindica.jsp"
DEFAULT_INDICATOR = "0012009"


def parse_ine(payload) -> tuple[list[dict], str, str]:
    """(rows, period, indicator title) from INE's JSON API answer: the latest
    period's values for municipalities (7-digit geographic codes), for all
    kinds of dwelling ("Total") when the indicator splits them."""
    entry = payload[0] if isinstance(payload, list) else payload
    title = entry.get("IndicadorDsg", "")
    data = entry.get("Dados") or {}
    if not data:
        raise ValueError(f"no data in INE's answer: {str(entry)[:300]}")
    period = entry.get("UltimoPref") if entry.get("UltimoPref") in data else list(data)[-1]
    rows = []
    for rec in data[period]:
        code = str(rec.get("geocod", ""))
        if not (code.isdigit() and len(code) == 7):          # municipalities only
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
