"""Planting grants that may apply to a forestry project: shown beside the
model's figures, never in a score, a bid or a return.

A grant pays part of planting (or, in France, replanting) and only once an
application is approved; it never pays for the land. Each scheme is a row of
data/forest_grants.csv with its legal text and the day it was checked. A row is
"partial" until its per-hectare amounts have been read in the official text:
it is then named as a possibility with what is known, and no euro figure is
worked out. A "confirmed" row with a rate and a cost cap gives a figure in
today's money: premiums are fixed in euros, so each later year is worth
`forestry.INFLATION` less (forestry._npv).
"""
from __future__ import annotations

import csv
import functools
import os

GRANTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "forest_grants.csv")
FIELDS = ("country", "region", "measure", "name", "case", "species", "rate", "bonus_max", "cost_cap_eur_ha",
          "premium_eur_ha_year", "premium_years", "income_loss_years", "min_ha", "max_timber_x_cost",
          "conditions", "legal_text", "source_url", "checked_on", "status")
STATUSES = ("partial", "confirmed")
CASES = ("planting", "replanting")
# The broadleaves Portugal's regional forest plans (PROF) normally favour. Which
# species a PROF favours differs by region: the plan for that region decides.
FAVOURED = {"cork oak", "native mixed forest"}
# A nut or pod orchard is normally a farm investment, not forest planting.
ORCHARDS = {"chestnut", "walnut", "carob"}
NEVER = {"eucalyptus"}
NOTE = "If an application is approved; not in the score or the return."


def _number(value: str) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


@functools.lru_cache(maxsize=4)
def _rows(path: str, mtime: float) -> tuple[dict, ...]:
    with open(path, encoding="utf-8") as f:
        return tuple(csv.DictReader(f))


def rows(path: str | None = None) -> tuple[dict, ...]:
    path = path or GRANTS_FILE
    try:
        return _rows(path, os.path.getmtime(path))
    except OSError:
        return ()


def case_for(crop: str | None, *, established: bool, wooded: bool) -> str | None:
    """What the project does on the land: plant bare land, replant a wood, or keep a stand (no grant)."""
    if not crop or established:
        return None
    return "replanting" if wooded else "planting"


def _species(crop: str) -> str:
    return "favoured_broadleaf" if crop in FAVOURED else "other"


def _value(row: dict, plant_eur_ha: float | None) -> float | None:
    """The grant per hectare a year, in today's money, as forestry.py values a crop. None until confirmed."""
    import forestry
    rate, cap = _number(row["rate"]), _number(row["cost_cap_eur_ha"])
    if row["status"] != "confirmed" or rate is None or cap is None or not plant_eur_ha:
        return None
    flows = {0: rate * min(plant_eur_ha, cap)}
    premium, years = _number(row["premium_eur_ha_year"]), _number(row["premium_years"])
    if premium and years:
        for y in range(1, int(years) + 1):
            flows[y] = premium
    return forestry._annuity(forestry._npv(flows))


def _what(row: dict) -> str:
    parts = []
    rate = _number(row["rate"])
    if rate is not None:
        cap = _number(row["cost_cap_eur_ha"])
        parts.append(f"{rate:.0%} of eligible {row['case']} cost"
                     + (f" up to €{cap:,.0f}/ha" if cap else " (the cap per hectare not yet checked)"))
    elif row["case"] == "replanting":
        parts.append("part of the replanting cost (the rate not yet checked)")
    if row["premium_years"]:
        amount = _number(row["premium_eur_ha_year"])
        parts.append(f"a maintenance premium for {row['premium_years']} years"
                     + (f" of €{amount:,.0f}/ha, fixed in euros, so inflation erodes it" if amount
                        else " (the amount not yet checked)"))
    if row["income_loss_years"]:
        parts.append(f"on farmland, an income-loss premium for {row['income_loss_years']} years")
    return ", ".join(parts)


def for_project(country: str | None, crop: str | None, case: str | None, *, hectares: float = 0,
                region: str | None = None, plant_eur_ha: float | None = None,
                timber_eur: float | None = None, path: str | None = None) -> dict:
    """{"grants": [...], "refused": [...], "text": str}: the schemes that may pay for this project.

    Each grant is {"measure", "name", "what", "eur_ha_year" (None until its amounts are
    confirmed), "legal_text", "source_url", "checked_on", "status", "conditions"}."""
    country = (country or "").upper()
    out = {"grants": [], "refused": [], "text": "", "note": NOTE}
    if not case or not crop:
        out["text"] = "No planting in this project, so no planting grant: the stand is kept."
        return out
    if crop in NEVER:
        out["refused"].append(f"{crop}: no planting grant")
        out["text"] = out["refused"][0]
        return out
    rows_here = [r for r in rows(path) if r["country"] == country and r["case"] == case
                 and (not r["region"] or r["region"] == (region or ""))]
    for r in rows_here:
        if r["species"] not in ("any", _species(crop)):
            continue
        if crop in ORCHARDS:
            out["refused"].append(f"{r['measure']}: a {crop} orchard is normally a farm investment, "
                                  "not forest planting — check which measure applies")
            break
        min_ha = _number(r["min_ha"])
        if min_ha and hectares < min_ha:
            out["refused"].append(f"{r['measure']}: needs at least {min_ha:g} ha")
            continue
        limit = _number(r["max_timber_x_cost"])
        if limit and timber_eur is not None and plant_eur_ha and hectares \
                and timber_eur >= limit * plant_eur_ha * hectares:
            out["refused"].append(f"{r['measure']}: the standing timber is worth {limit:g}× the replanting "
                                  "cost or more, so the stand is not eligible")
            continue
        out["grants"].append({"measure": r["measure"], "name": r["name"], "what": _what(r),
                              "eur_ha_year": _value(r, plant_eur_ha), "legal_text": r["legal_text"],
                              "source_url": r["source_url"], "checked_on": r["checked_on"],
                              "status": r["status"], "conditions": r["conditions"]})
    if out["grants"]:
        out["text"] = "; ".join(f"{g['name']} ({g['measure']}): {g['what']}" for g in out["grants"])
    elif out["refused"]:
        out["text"] = "; ".join(out["refused"])
    elif country == "ES":
        out["text"] = f"Spain's {case} grants are set by each region; none is in the table yet."
    else:
        out["text"] = f"No {case} grant for this country is in the table yet."
    return out
