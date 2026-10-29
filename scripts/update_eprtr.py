"""
Refresh data/eprtr.csv: the industrial plants that report their pollution to
the EU (E-PRTR, European Environment Agency), with what each releases into the
air. Used by geo.pollution next to the OpenStreetMap list (update_polluters.py):

- heavy: a plant whose latest report (2018 or later) releases a lot of the
  pollutants that harm the people living near it (sulphur and nitrogen oxides,
  fine dust, benzene, heavy metals, dioxins), and every refinery, coke oven,
  steel or metal works, cement kiln and waste incinerator;
- light: every other reporting plant: intensive pig and poultry farms, large
  landfills and sewage works, factories.

In a country with E-PRTR reports, OpenStreetMap's "heavy" sites only count as
light: the EU register says which plants really pollute.

    python scripts/update_eprtr.py            # downloads ~105 MB, a few minutes

Commit the file through a pull request; the EEA publishes once a year.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from geo import EPRTR_FILE  # noqa: E402

SOURCE = ("https://sdi.eea.europa.eu/webdav/datastore/public/eea_t_ied-eprtr_p_2007-2022_v11_r00/"
          "User%20friendly%20.csv%20file/F1_4_Detailed%20releases%20at%20facility%20level%20with%20E-PRTR%20Sector%20"
          "and%20Annex%20I%20Activity%20detail%20into%20Air.csv")
FIRST_YEAR = 2018           # a plant with no report since is taken as closed
COLUMNS = ("country", "kind", "lat", "lon", "name")

# kg a year to the air above which the plant is heavy industry for its
# neighbours. Cautious on purpose (the buyer has asthma): every reported
# release of fine dust, and sulphur and nitrogen oxides, metals, dioxins,
# benzene and PAHs at the E-PRTR reporting threshold itself. A plant reports
# only above that threshold, so reporting at all is the signal.
HEAVY_KG = {
    "Sulphur oxides (SOX)": 150_000, "Nitrogen oxides (NOX)": 100_000,
    "Particulate matter (PM10)": 1, "Benzene": 1_000,
    "Mercury and compounds (as Hg)": 10, "Lead and compounds (as Pb)": 200,
    "Cadmium and compounds (as Cd)": 10, "Arsenic and compounds (as As)": 20,
    "Nickel and compounds (as Ni)": 50, "Chromium and compounds (as Cr)": 100,
    "PCDD + PCDF (dioxins + furans) (as Teq)": 0.0001,
    "Polycyclic aromatic hydrocarbons (PAHs)": 50,
}
# Annex I activities that are heavy whatever they report: refineries,
# gasification, coke ovens, ore roasting, steel, non-ferrous metals,
# cement and lime kilns, hazardous and municipal waste incinerators.
HEAVY_ACTIVITIES = ("1(a)", "1(b)", "1(d)", "2(a)", "2(b)", "2(d)", "2(e)", "3(c)", "5(a)", "5(b)")

COUNTRY_CODES = {
    "Austria": "AT", "Belgium": "BE", "Bulgaria": "BG", "Croatia": "HR", "Cyprus": "CY", "Czechia": "CZ",
    "Czech Republic": "CZ", "Denmark": "DK", "Estonia": "EE", "Finland": "FI", "France": "FR", "Germany": "DE",
    "Greece": "GR", "Hungary": "HU", "Iceland": "IS", "Ireland": "IE", "Italy": "IT", "Latvia": "LV",
    "Liechtenstein": "LI", "Lithuania": "LT", "Luxembourg": "LU", "Malta": "MT", "Netherlands": "NL",
    "Norway": "NO", "Poland": "PL", "Portugal": "PT", "Romania": "RO", "Serbia": "RS", "Slovakia": "SK",
    "Slovenia": "SI", "Spain": "ES", "Sweden": "SE", "Switzerland": "CH", "United Kingdom": "GB",
}


def _short(pollutant: str) -> str:
    return pollutant.split(" (")[0] if "(" in pollutant else pollutant


def facilities(rows) -> list[dict]:
    """One row per plant from the release rows: its latest reported year only."""
    latest: dict[str, int] = {}
    plants: dict[str, dict] = {}
    releases: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        try:
            year, kg = int(r["reportingYear"]), float(r["emissions"] or 0)
            lat, lon = float(r["Latitude"]), float(r["Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        fid = r["FacilityInspireID"]
        releases[(fid, year)][r["pollutant"]] += kg
        if year >= latest.get(fid, 0):
            latest[fid] = year
            plants[fid] = {"country": COUNTRY_CODES.get(r["countryName"]), "lat": round(lat, 4), "lon": round(lon, 4),
                           "name": (r["facilityName"] or "").strip(), "activity": r["EPRTRAnnexIMainActivityCode"] or "",
                           "label": (r["EPRTRAnnexIMainActivityLabel"] or "").split(",")[0][:60]}
    out = []
    for fid, plant in plants.items():
        year = latest[fid]
        if year < FIRST_YEAR or not plant["country"]:
            continue
        air = releases[(fid, year)]
        over = sorted(((kg / HEAVY_KG[p], p, kg) for p, kg in air.items() if p in HEAVY_KG and kg >= HEAVY_KG[p]),
                      reverse=True)
        heavy = bool(over) or plant["activity"].startswith(HEAVY_ACTIVITIES)
        if over:
            worst = f"{_short(over[0][1])} {over[0][2] / 1000:,.0f} t/yr" if over[0][2] >= 1000 else f"{_short(over[0][1])} {over[0][2]:g} kg/yr"
        else:
            worst = plant["label"]
        out.append({"country": plant["country"], "kind": "heavy" if heavy else "light", "lat": plant["lat"],
                    "lon": plant["lon"], "name": f"{plant['name']} ({worst}, E-PRTR {year})"})
    return out


def write(rows: list[dict], path: str = EPRTR_FILE) -> None:
    rows = sorted(rows, key=lambda r: (r["country"], r["kind"], float(r["lat"]), float(r["lon"])))
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    import requests
    with tempfile.TemporaryFile() as tmp:
        with requests.get(SOURCE, stream=True, timeout=600) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                tmp.write(chunk)
        tmp.seek(0)
        rows = facilities(csv.DictReader(io.TextIOWrapper(tmp, encoding="utf-8-sig", newline="")))
    write(rows)
    heavy = sum(r["kind"] == "heavy" for r in rows)
    print(f"{len(rows)} E-PRTR plants ({heavy} heavy) written to {EPRTR_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
