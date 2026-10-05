"""What a hectare of forest land could earn: timber, cork, nuts and carbon credits.

A rough screening model, not a forest plan: for each crop it checks whether the
place's climate (today and 2081-2100, from climate.py) suits it, then values the
crop's cash flows over FOREST_YEARS at DISCOUNT_RATE and turns them into one
"equivalent € per hectare per year" that can be set against the price of the land.

Figures are mid-range European values from 2024-2026, net of harvesting cost
(the stumpage a buyer pays standing): cork from APCOR and Portuguese DGT
price series; pine, Douglas and poplar stumpage from French ONF/FIBOIS and
Spanish regional sale results; yields from the national forest inventories
(IFN, IFN-ES); carbon from voluntary afforestation (ARR) removal credits and
the EU carbon-removal certification (Regulation 2024/3012). Grants for
planting (PEPAC/CAP, 50-80% in PT/ES/FR) are left out: they only make it better.
"""
from __future__ import annotations

DISCOUNT_RATE = 0.03
FOREST_YEARS = 60

CARBON_EUR_T = 30          # ARR removal credits, 2025-26 (€15-45/t CO2)
CARBON_BUFFER = 0.20       # held back against fire and dieback
# A plot this small joins a grouped project (an aggregator registers many owners):
CARBON_SETUP_EUR_HA = 200  # share of validation and registry, once
CARBON_FEE = 0.15          # the aggregator's share of every credit sold

# name: the climate it needs, what it earns, and what it costs to plant.
#   heat_max: warmest-month mean maximum (°C) it still grows well in by 2081-2100
#   cold_min: the 1-in-10-year coldest day (°C) it survives
#   wet: needs low water stress; river: needs water on or by the land
#   income: list of (first year, every N years, € per ha) — net of harvesting
#   carbon: t CO2 per ha per year it stores and keeps (only crops not clear-felled)
CROPS = {
    "cork oak": dict(heat_max=38, cold_min=-8, countries={"PT", "ES", "FR", "IT"},
                     plant=3000, income=[(30, 9, 900), (39, 9, 2700)], carbon=2.5,
                     note="first cork at ~30 years, then every 9; protected species, ~€2.5/kg"),
    "stone pine": dict(heat_max=38, cold_min=-12, plant=2000, income=[(15, 1, 250)], carbon=3.0,
                       note="grafted, pine nuts from ~15 years"),
    "maritime pine": dict(heat_max=33, cold_min=-12, plant=1800, fire_prone=True,
                          income=[(20, 1, 120), (20, 10, 1200), (40, 40, 11000)], carbon=0,
                          note="resin, thinnings, clear-fell at ~40 years (~8 m³/ha/yr)"),
    "Douglas fir": dict(heat_max=30, cold_min=-20, wet=True, plant=3500,
                        income=[(25, 10, 2500), (50, 50, 45000)], carbon=0,
                        note="~14 m³/ha/yr, clear-fell at ~50 years"),
    "chestnut": dict(heat_max=32, cold_min=-15, wet=True, plant=5000, income=[(10, 1, 900)], carbon=2.0,
                     note="grafted nut orchard, ~1.2 t/ha a year"),
    "poplar": dict(heat_max=34, cold_min=-20, river=True, plant=2500, income=[(14, 14, 12000)], carbon=0,
                   note="by water only, ~20 m³/ha/yr, felled every ~14 years"),
    "native mixed forest": dict(heat_max=40, cold_min=-25, plant=2500, income=[], carbon=None,
                                note="carbon credits only; nothing felled"),
}


START_YEAR = 2026
DRY_FAIL_YEAR = 2060      # water-hungry trees where water stress turns high or extreme by 2080
# Yearly chance a stand burns: a base everywhere, more where high fire danger
# days are many by 2090 (30+, 60+), more again where fires burnt nearby since 2016.
FIRE_BASE, FIRE_DANGER, FIRE_HISTORY = 0.002, 0.005, 0.005
# How easily each crop is lost to a fire: cork bark protects the tree; resinous pines burn.
FIRE_SENSITIVITY = {"cork oak": 0.3, "stone pine": 0.8, "maritime pine": 2.0, "Douglas fir": 1.0,
                    "chestnut": 0.7, "poplar": 0.6, "native mixed forest": 0.7}


def heat_limit_year(heat: dict, heat_max: float) -> int | None:
    """The year the warmest month's mean maximum passes the crop's limit, along
    today (~1995) → 2061-2080 (~2070) → 2081-2100 (~2090), then on at the same
    pace. None when it stays under the limit to the end of the horizon."""
    points = [(y, heat.get(k)) for y, k in ((1995, "today"), (2070, "ssp245_2061-2080"),
                                            (2090, "ssp245_2081-2100")) if heat.get(k) is not None]
    if not points:
        return None
    if points[0][1] > heat_max:
        return START_YEAR
    end = START_YEAR + FOREST_YEARS
    if len(points) >= 2:
        (y1, t1), (y2, t2) = points[-2], points[-1]
        points.append((end, t2 + (t2 - t1) / (y2 - y1) * (end - y2)))
    for (y1, t1), (y2, t2) in zip(points, points[1:]):
        if t2 > heat_max >= t1:
            return max(START_YEAR, round(y1 + (heat_max - t1) / (t2 - t1) * (y2 - y1)))
    return None


def _annuity(npv: float) -> float:
    r, n = DISCOUNT_RATE, FOREST_YEARS
    return npv * r / (1 - (1 + r) ** -n)


def _npv(flows: dict[int, float]) -> float:
    return sum(v / (1 + DISCOUNT_RATE) ** y for y, v in flows.items())


def _carbon_rate(crop: dict, heat_today: float | None) -> float:
    if crop["carbon"] is not None:
        return crop["carbon"]
    return 6.0 if heat_today is not None and heat_today < 27 else 3.5   # temperate vs Mediterranean


EXISTING_WORDS = {
    "cork oak": ("sobreiro", "sobro", "montado", "cortiça", "cortica", "alcornoque", "chêne-liège", "chene-liege",
                 "sughera", "sughereta"),
    "stone pine": ("pinheiro manso", "pinheiros mansos", "pino piñonero", "pinos piñoneros", "pin parasol"),
    "maritime pine": ("pinheiro bravo", "pinhal", "pino marítimo", "pino pinaster", "pin maritime", "pinède"),
    "chestnut": ("castanheiro", "souto", "castaño", "castaños", "châtaignier", "castagno", "castagneto"),
    "Douglas fir": ("douglas",),
    "poplar": ("choupo", "choupal", "chopo", "chopera", "peuplier", "peupleraie", "pioppo", "pioppeto"),
}


def growing(text: str) -> set[str]:
    """The crops the listing says are already on the land."""
    low = (text or "").lower()
    return {crop for crop, words in EXISTING_WORDS.items() if any(w in low for w in words)}


def options(climate: dict | None, country: str | None, hectares: float, water_on_land: bool = False,
            existing: set[str] | None = None) -> list[dict]:
    """Every crop that suits the place, best first:
    {"crop", "eur_ha_year", "carbon_eur_ha_year", "note", "limits"}."""
    c = climate or {}
    heat = c.get("heat") or {}
    cold = (c.get("amoc_cold10") or {}).get("off")      # the cold if the Atlantic current fails
    stress = (c.get("stress") or {}).get("stress_2080")
    dry_stress = stress is not None and (stress >= 3 or stress == -1)
    fire_days = (c.get("fire_danger") or {}).get("high_days_2090") or 0
    by_water = water_on_land or (c.get("water_km") is not None and c["water_km"] <= 0.3)
    fire = c.get("fire") or {}
    burn = FIRE_BASE + (FIRE_DANGER if fire_days >= 30 else 0) + (FIRE_DANGER if fire_days >= 60 else 0)         + (FIRE_HISTORY if fire.get("burnt_here") or fire.get("count") else 0)
    out = []
    for name, crop in CROPS.items():
        limits = []
        if crop.get("countries") and (country or "").upper() not in crop["countries"]:
            continue
        if cold is not None and cold < crop["cold_min"]:
            continue
        if crop.get("river") and not by_water:
            continue
        # The year the place gets too hot (or, for thirsty trees, too dry) for it:
        # nothing is earned after that, and a stand not yet felled is lost.
        ends = heat_limit_year(heat, crop["heat_max"])
        if dry_stress and crop.get("wet"):
            ends = min(ends or DRY_FAIL_YEAR, DRY_FAIL_YEAR)
        last = FOREST_YEARS if ends is None else ends - START_YEAR
        if last <= 0:
            continue
        if ends is not None and last < FOREST_YEARS:
            limits.append(f"too {'dry' if dry_stress and crop.get('wet') else 'hot'} for it from ~{ends}")
        growth = 0.7 if dry_stress else 1.0
        if dry_stress:
            limits.append("dry: lower yield")
        p = burn * FIRE_SENSITIVITY.get(name, 1.0)      # yearly chance the stand burns
        if burn >= FIRE_BASE + FIRE_DANGER:
            limits.append(f"fire: {1 - (1 - p) ** 40:.0%} chance of losing it within 40 years")
        established = name in (existing or ())
        flows = {0: 0 if established else -crop["plant"]}
        income = crop["income"]
        if established:          # already growing: the mature income from now (felling at half a rotation)
            income = [(1 if every < 20 else every // 2, every, eur) for first, every, eur in income
                      if every == 1 or (first, every, eur) == income[-1]]
            limits.append("already on the land")
        for first, every, eur in income:
            for y in range(first, last + 1, every):
                flows[y] = flows.get(y, 0) + eur * growth * (1 - p) ** y
        rate = _carbon_rate(crop, heat.get("today")) * growth
        carbon = 0.0
        if rate:
            yearly = rate * (1 - CARBON_BUFFER) * (1 - CARBON_FEE) * CARBON_EUR_T
            carbon = _annuity(_npv({y: yearly * (1 - p) ** y for y in range(1, last + 1)}) - CARBON_SETUP_EUR_HA)
        crop_value = _annuity(_npv(flows))
        out.append({"crop": name, "eur_ha_year": round(crop_value + max(carbon, 0)),
                    "carbon_eur_ha_year": round(max(carbon, 0)), "note": crop["note"], "limits": limits,
                    "until": ends})
    return sorted(out, key=lambda o: -o["eur_ha_year"])


def describe(option: dict) -> str:
    extra = f" incl. €{option['carbon_eur_ha_year']} carbon" if option["carbon_eur_ha_year"] else ""
    limits = f"; {', '.join(option['limits'])}" if option["limits"] else ""
    if option.get("until"):
        extra += f", only until ~{option['until']}"
    return f"best crop: {option['crop']} ≈ €{option['eur_ha_year']:,}/ha a year{extra} (estimate{limits})"
