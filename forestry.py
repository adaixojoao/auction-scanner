"""What a hectare of forest land could earn: timber, cork, nuts and carbon credits.

A rough screening model, not a forest plan: for each crop it checks whether the
place's climate (today and 2081-2100, from climate.py) suits it, then values the
crop's cash flows over FOREST_YEARS at DISCOUNT_RATE and turns them into one
"equivalent € per hectare per year" that can be set against the price of the land.

PROVISIONAL: the yields, prices, costs and climate limits below are rough
mid-range European figures, not yet taken from a published table. They are
being replaced by sourced ones (EU-Trees4F for where each species can live in
2095; national inventories for yields; SIMeF/ICNF, ONF, LVM and APCOR for
prices). Until then, compare listings with them; do not plan with them.
Grants for planting (PEPAC/CAP) are left out.
"""
from __future__ import annotations

import csv
import functools
import os
import re

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
#   income: list of (first year, every N years, € per ha[, m³ per ha]) — net of harvesting;
#   with m³, the € is replaced by m³ × the official standing price where one is known
#   carbon: t CO2 per ha per year it stores and keeps (only crops not clear-felled)
CROPS = {
    "cork oak": dict(heat_max=38, cold_min=-8, countries={"PT", "ES", "FR", "IT"},
                     plant="broadleaf", income=[(30, 9, 900), (39, 9, 2700, None, 1080)], carbon=2.5,
                     note="first cork at ~30 years, then every 9; protected species, ~€2.5/kg"),
    "stone pine": dict(heat_max=38, cold_min=-12, plant="broadleaf", income=[], harvest=(15, 600, "pine cones"),
                       carbon=3.0, note="grafted, pine cones from ~15 years, ~600 kg/ha"),
    "maritime pine": dict(heat_max=33, cold_min=-12, plant="conifer", fire_prone=True,
                          income=[(20, 1, 120), (20, 10, 1200, 30), (40, 40, 11000, 324)], carbon=0,
                          note="resin, thinnings, clear-fell at ~40 years"),
    "Douglas fir": dict(heat_max=30, cold_min=-20, wet=True, plant="conifer",
                        income=[(25, 10, 2500, 40), (50, 50, 45000, 565)], carbon=0,
                        note="thinnings, clear-fell at ~50 years"),
    "chestnut": dict(heat_max=32, cold_min=-15, wet=True, plant=5000, income=[], harvest=(10, 1200, "chestnuts"),
                     carbon=2.0, note="grafted nut orchard, ~1.2 t/ha a year"),
    "walnut": dict(heat_max=33, cold_min=-20, wet=True, river=True, plant=6000, income=[], harvest=(8, 2000, "walnuts"),
                   carbon=2.0, note="grafted nut orchard, ~2 t/ha a year from ~8 years; only by water (irrigated)"),
    "carob": dict(heat_max=42, cold_min=-4, plant="broadleaf", income=[], harvest=(8, 2000, "carob"), carbon=2.5,
                  countries={"PT", "ES", "IT", "GR", "CY", "HR", "FR"},
                  note="dry-farmed pods, ~2 t/ha a year from ~8 years; tolerates heat, not frost"),
    "poplar": dict(heat_max=34, cold_min=-20, river=True, plant=2500, income=[(14, 14, 12000, 280)], carbon=0,
                   note="by water only, ~20 m³/ha/yr, felled every ~14 years"),
    # Northern and central Europe (Latvia, Germany, Poland…): yields are provisional.
    "Scots pine": dict(heat_max=30, cold_min=-40, plant="conifer", income=[(30, 10, 600, 25), (60, 60, 9000, 171)],
                       carbon=0, note="thinnings, clear-fell at ~60 years on good soil"),
    "Norway spruce": dict(heat_max=28, cold_min=-40, wet=True, plant="conifer",
                          income=[(25, 10, 700, 30), (55, 55, 16000, 531)], carbon=0,
                          note="clear-fell at ~55 years; bark beetle in droughts"),
    "birch": dict(heat_max=29, cold_min=-40, plant=1500, income=[(20, 10, 300, 15), (50, 50, 8000, 220)],
                  carbon=0, note="plywood and pulp, clear-fell at ~50 years"),
    "native mixed forest": dict(heat_max=40, cold_min=-25, plant="broadleaf", income=[], carbon=None,
                                note="carbon credits only; nothing felled"),
}


START_YEAR = 2026
THRIVE_UNTIL = 2100      # the owner plants only what still thrives here in 2100
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
    end = THRIVE_UNTIL
    if len(points) >= 2:
        (y1, t1), (y2, t2) = points[-2], points[-1]
        points.append((end, t2 + (t2 - t1) / (y2 - y1) * (end - y2)))
    for (y1, t1), (y2, t2) in zip(points, points[1:]):
        if t2 > heat_max >= t1:
            return max(START_YEAR, round(y1 + (heat_max - t1) / (t2 - t1) * (y2 - y1)))
    return None


PRICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "forest_prices.csv")


@functools.lru_cache(maxsize=4)
def _prices(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as f:
        return {(r["country"], r["crop"]): {"eur_m3": float(r["eur_m3"]),
                                            "label": f"€{float(r['eur_m3']):.0f}/m³ {r['source']} {r['period']}"}
                for r in csv.DictReader(f)}


def timber_price(country: str | None, crop: str) -> dict | None:
    """The official standing price per m³ of a crop's wood in a country, if known."""
    try:
        table = _prices(PRICES_FILE, os.path.getmtime(PRICES_FILE))
    except OSError:
        return None
    return table.get(((country or "").upper(), crop))


# Producer prices, € per kg: the midpoint of the official reference range of the
# northern Portuguese government (CCDR-N, Tabela II - Produtos, 1 Mar 2024 - 28 Feb 2025).
_CCDRN = "CCDR-N producer prices 2024-25"
PRODUCE = {"chestnuts": ((1.81 + 3.01) / 2, f"chestnuts €2.41/kg, {_CCDRN}"),
           "pine cones": ((0.76 + 1.22) / 2, f"pine cones €0.99/kg, {_CCDRN}"),
           "walnuts": ((2.40 + 3.58) / 2, f"walnuts €2.99/kg, {_CCDRN}"),
           "carob": ((0.31 + 0.64) / 2, f"carob €0.48/kg, {_CCDRN}")}
HARVEST_COST_SHARE = 0.5   # PROVISIONAL: picking, pruning and orchard upkeep, until a cost survey is found

# Cork (amadia): producers' average sale price and extraction cost, UNAC survey
# "Boletim do Mercado da Cortiça 2019-2023" (Rede Rural Nacional). 2024-25 fell
# 10-25% by press reports, not yet in an official series.
CORK_EUR_ARROBA, CORK_EXTRACTION_EUR_ARROBA, ARROBA_KG = 42.50, 6.86, 15
CORK_SOURCE = "cork €42.50/@ less €6.86 extraction, UNAC/Rede Rural 2023"
CORK_COUNTRIES = {"PT", "ES"}


def cork_net_eur_kg() -> float:
    return (CORK_EUR_ARROBA - CORK_EXTRACTION_EUR_ARROBA) / ARROBA_KG


# Clearing woodland for another use needs an authorisation (FR: défrichement,
# Code forestier L341-3; PT/ES: regional forest services).
CLEARING_PERMIT = {"FR", "PT", "ES"}


# Growth, m³ a hectare a year: IGN Mémento 2025 (national forest inventory, 2015-2023),
# each species' yearly production divided by the area where it is the main species.
# The clear-fell volumes above are growth × rotation less the thinnings. French
# averages, used elsewhere too until the Spanish and Portuguese inventories are read.
GROWTH_IGN = {"maritime pine": 9.6, "Douglas fir": 13.7, "Norway spruce": 11.3, "Scots pine": 4.1}
GROWTH_SOURCE = "growth {:.1f} m³/ha/yr, IGN Mémento 2025"


# Planting cost a hectare: the work from CAOF's 2024 reference matrix (Portugal,
# Comissão de Acompanhamento das Operações Florestais) — scrub clearing, ripping,
# planting, weeding and earthing up, fertilising — the midpoint between easy
# ground (flat, light scrub) and hard (over 25% slope, rocky, dense scrub). The
# plants and tree guards themselves are not in the matrix: PROVISIONAL.
CAOF_WORK = {"conifer": (1464, 2270),             # 1,100 container plants a hectare: easy, hard
             "broadleaf": (1231, 1880)}           # 500 plants a hectare, guards fitted
CAOF_EASY_SLOPE, CAOF_HARD_SLOPE = 10, 25         # CAOF's conditions: under 10% easy, over 25% hard
PLANT_MATERIAL = {"conifer": 1100 * 0.40, "broadleaf": 500 * (1.0 + 1.5)}   # PROVISIONAL
CAOF_SOURCE = "planting work CAOF 2024"
CAOF_SMALL_PLOT = 0.03    # CAOF: +3% for each hectare under 10


def planting_cost(kind: str, hectares: float, slope_pct: float | None = None) -> float:
    """CAOF work at the measured slope (the midpoint when unknown), plus the plants."""
    easy, hard = CAOF_WORK[kind]
    if slope_pct is None:
        work = (easy + hard) / 2
    else:
        t = min(1.0, max(0.0, (slope_pct - CAOF_EASY_SLOPE) / (CAOF_HARD_SLOPE - CAOF_EASY_SLOPE)))
        work = easy + t * (hard - easy)
    small = max(0.0, 10 - hectares) * CAOF_SMALL_PLOT if hectares else 0.0
    return work * (1 + small) + PLANT_MATERIAL[kind]


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
            existing: set[str] | None = None, trees: dict | None = None, wooded: bool = False,
            slope_pct: float | None = None) -> list[dict]:
    """Every crop that still thrives here in 2100, best first:
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
        if wooded and crop.get("harvest") and (country or "").upper() in CLEARING_PERMIT:
            continue          # an orchard would mean clearing the wood first: needs a permit, often refused
        fit = species_fit(trees, name) if trees else None
        if fit is not None:
            # EU-Trees4F (JRC): suitable here around 2065 and 2095 under moderate
            # emissions, or it is not an option. Its verdict replaces the heat rule.
            if not (fit["rcp45_fut2065"] and fit["rcp45_fut2095"]):
                continue
            limits.append("suits this place until 2100 (EU-Trees4F)"
                          + ("" if fit["rcp85_fut2095"] else "; not under high emissions"))
        # The year the place gets too hot (or, for thirsty trees, too dry) for it:
        # nothing is earned after that, and a stand not yet felled is lost.
        ends = None if fit is not None else heat_limit_year(heat, crop["heat_max"])
        if dry_stress and crop.get("wet"):
            ends = min(ends or DRY_FAIL_YEAR, DRY_FAIL_YEAR)
        if ends is not None:
            continue                       # it would not thrive here until 2100
        last = FOREST_YEARS
        growth = 0.7 if dry_stress else 1.0
        if dry_stress:
            limits.append("dry: lower yield")
        p = burn * FIRE_SENSITIVITY.get(name, 1.0)      # yearly chance the stand burns
        if burn >= FIRE_BASE + FIRE_DANGER:
            limits.append(f"fire: {1 - (1 - p) ** 40:.0%} chance of losing it within 40 years")
        sources: set[str] = set()
        established = name in (existing or ())
        plant = crop["plant"]
        if isinstance(plant, str):
            plant = planting_cost(plant, hectares, slope_pct)
            sources.add(CAOF_SOURCE)
        flows = {0: 0 if established else -plant}
        income = crop["income"]
        if established:          # already growing: the mature income from now (felling at half a rotation)
            income = [(1 if t[1] < 20 else t[1] // 2,) + tuple(t[1:]) for t in income
                      if t[1] == 1 or t == income[-1]]
            limits.append("already on the land")
        if name in GROWTH_IGN:
            sources.add(GROWTH_SOURCE.format(GROWTH_IGN[name]))
        price = timber_price(country, name) or timber_price(country, "mixed")
        if price and any(len(t) > 3 and t[3] for t in income):
            sources.add(price["label"])
        if crop.get("harvest"):
            first, kg, product = crop["harvest"]
            eur_kg, label = PRODUCE[product]
            sources.add(label)
            for y in range(first, last + 1):
                flows[y] = flows.get(y, 0) + kg * eur_kg * (1 - HARVEST_COST_SHARE) * growth * (1 - p) ** y
        for first, every, eur, *qty in income:
            if qty and qty[0] and price:
                eur = qty[0] * price["eur_m3"]
            elif len(qty) > 1 and (country or "").upper() in CORK_COUNTRIES:
                eur = qty[1] * cork_net_eur_kg()          # mature cork, kg a hectare a harvest
                sources.add(CORK_SOURCE)
            for y in range(first, last + 1, every):
                flows[y] = flows.get(y, 0) + eur * growth * (1 - p) ** y
        rate = _carbon_rate(crop, heat.get("today")) * growth
        carbon = 0.0
        if rate:
            yearly = rate * (1 - CARBON_BUFFER) * (1 - CARBON_FEE) * CARBON_EUR_T
            carbon = _annuity(_npv({y: yearly * (1 - p) ** y for y in range(1, last + 1)}) - CARBON_SETUP_EUR_HA)
        crop_value = _annuity(_npv(flows))
        out.append({"crop": name, "eur_ha_year": round(crop_value + max(carbon, 0)),
                    "carbon_eur_ha_year": round(max(carbon, 0)), "note": crop["note"], "limits": limits, "sources": sorted(sources),
                    "until": ends})
    return sorted(out, key=lambda o: -o["eur_ha_year"])


def describe(option: dict) -> str:
    extra = f" incl. €{option['carbon_eur_ha_year']} carbon" if option["carbon_eur_ha_year"] else ""
    limits = f"; {', '.join(option['limits'])}" if option["limits"] else ""
    if option.get("until"):
        extra += f", only until ~{option['until']}"
    if option.get("sources"):
        limits += "; prices: " + ", ".join(option["sources"])
    else:
        limits += "; provisional prices"
    return f"best crop: {option['crop']} ≈ €{option['eur_ha_year']:,}/ha a year{extra} (estimate{limits})"


# "Kopējais mežaudzes krājas apjoms ir 1632 m³", "volume sur pied 1 200 m3",
# "1.500 m3 de madera", "2400 kubikmetri": timber already standing on the land.
_VOLUME = re.compile(
    r"(?:kr[āa]j\w*|volume (?:sur pied|de bois)|cubicaje|volume de madeira|madera en pie|"
    r"timber volume|standing (?:volume|timber))[^.\d]{0,40}?(\d[\d .,]*)\s*(?:m3|m³|kubikmetr|metros c[uú]bicos)"
    r"|(\d[\d .,]*)\s*(?:m3|m³|kubikmetr\w*|metros c[uú]bicos)\s+(?:de madera|de madeira|de bois|sur pied|krāj|koksnes)",
    re.I)


def standing_volume(text: str) -> float | None:
    """The m³ of timber the ad says stand on the land, if it says."""
    m = _VOLUME.search(text or "")
    if not m:
        return None
    digits = re.sub(r"[ .](?=\d{3}\b)", "", (m.group(1) or m.group(2)).strip()).replace(",", ".")
    try:
        value = float(digits.rstrip("."))
    except ValueError:
        return None
    return value if 10 <= value <= 200000 else None


def standing_timber(text: str, country: str | None, hectares: float) -> dict | None:
    """{"m3", "eur", "label"}: what the stated standing timber is worth at the
    official average price. None when the ad states no volume, the country has
    no official price, or the volume is beyond any forest (over 1,000 m³ a ha)."""
    m3 = standing_volume(text)
    price = timber_price(country, "mixed")
    s = stand(text)
    estimated = False
    if not m3 and s["mature"] and not s["young"] and (country or "").upper() == "LV" and hectares:
        m3, estimated = hectares * LV_MATURE_M3_HA, True     # "mature stand", no volume given
    if not m3 or not price or (hectares and m3 / hectares > 1000):
        return None
    eur = m3 * price["eur_m3"]
    if s["young"] and not s["mature"]:
        eur /= (1 + DISCOUNT_RATE) ** YOUNG_YEARS             # sold only when it has grown
    return {"m3": m3, "eur": eur, "label": price["label"], "estimated": estimated,
            "young": s["young"] and not s["mature"], "rights_only": s["rights_only"]}


# ─── EU-Trees4F: where each species can live, now and in 2035/2065/2095 ────
# Mauri et al. 2022, Scientific Data (JRC): ensemble of species distribution
# models over 11 regional climate models, binary "suitable" maps at 10 km
# (EPSG:3035), RCP4.5 and RCP8.5. Unpacked into <climate data>/layers/eutrees4f.
EUTREES = {"cork oak": ["Quercus_suber"], "stone pine": ["Pinus_pinea"], "maritime pine": ["Pinus_pinaster"],
           "chestnut": ["Castanea_sativa"], "walnut": ["Juglans_regia"], "carob": ["Ceratonia_siliqua"], "poplar": ["Populus_nigra", "Populus_alba"],
           "Scots pine": ["Pinus_sylvestris"], "Norway spruce": ["Picea_abies"], "birch": ["Betula_pendula"],
           "native mixed forest": ["Quercus_robur", "Quercus_petraea", "Quercus_pyrenaica", "Quercus_ilex",
                                   "Quercus_faginea", "Quercus_pubescens", "Fagus_sylvatica", "Arbutus_unedo",
                                   "Prunus_avium"]}
NATIVE_MIN = 2            # a mixed forest needs at least two native species that still suit the place
PERIODS = ("cur2005", "rcp45_fut2035", "rcp45_fut2065", "rcp45_fut2095", "rcp85_fut2065", "rcp85_fut2095")


def _tree_layer(species: str, period: str) -> str:
    import climate
    return os.path.join(climate.data_dir(), "layers", "eutrees4f", f"{species}_ens-sdms_{period}_bin_pot.tif")


@functools.lru_cache(maxsize=4096)
def trees_at(lat: float, lon: float) -> dict | None:
    """{species: {period: True/False}} at a place; None without the layers."""
    from rasterio.warp import transform
    import climate
    out = {}
    xs, ys = transform("EPSG:4326", "EPSG:3035", [lon], [lat])
    for species in sorted({s for names in EUTREES.values() for s in names}):
        row = {}
        for period in PERIODS:
            path = _tree_layer(species, period)
            src = climate._raster(path)
            if src is None:
                return None
            r, c = src.index(xs[0], ys[0])
            if not (0 <= r < src.height and 0 <= c < src.width):
                return {}
            import rasterio.windows
            v = int(src.read(1, window=rasterio.windows.Window(c, r, 1, 1))[0, 0])
            row[period] = v == 1
        out[species] = row
    return out


def species_fit(trees: dict, crop: str) -> dict | None:
    """{period: suitable} for a crop (any of its species; a mixed forest needs
    NATIVE_MIN of them). None when EU-Trees4F does not cover the crop (Douglas fir)."""
    names = [s for s in EUTREES.get(crop, []) if s in trees]
    if not names:
        return None
    need = NATIVE_MIN if crop == "native mixed forest" else 1
    return {p: sum(trees[s][p] for s in names) >= need for p in PERIODS}


def trees_for_item(item: dict) -> dict | None:
    """EU-Trees4F at the position the climate was read for."""
    import geo
    at = ((geo._raw(item).get("climate") or {}).get("at") or "")
    try:
        lat, lon = (float(x) for x in at.split(","))
    except ValueError:
        return None
    try:
        return trees_at(round(lat, 2), round(lon, 2))
    except Exception:  # noqa: BLE001 — no rasterio or layers: the heat rule decides
        return None


# ─── What an ad says about the stand (Latvian and English) ──────────────────
STAND_WORDS = {
    "young": ("jaunaudz", "izcirtum", "atjaunošan", "young stand", "clear-cut", "replanted"),
    "mature": ("pieaugus", "briestaudz", "galvenā cirt", "galvenajai cirtei", "ciršanas vecum",
               "mature stand", "ready for felling"),
    "rights_only": ("pārdod cirsmu", "pārdodu cirsmu", "cirsmas pārdošana", "felling rights only",
                    "tikai cirsmu"),
}
SPECIES_WORDS = {"pine": ("priede", "priež"), "spruce": ("egle", "egļu"), "birch": ("bērz",),
                 "aspen": ("apse", "apšu"), "alder": ("alksn",), "oak": ("ozol",)}
YOUNG_YEARS = 20          # a young stand's timber is sold about this far ahead
LV_MATURE_M3_HA = 200     # PROVISIONAL growing stock of a mature Latvian stand, until the inventory is read


def stand(text: str) -> dict:
    """{"young", "mature", "rights_only": bool, "species": [...]} from the ad text."""
    low = (text or "").lower()
    out = {k: any(w in low for w in words) for k, words in STAND_WORDS.items()}
    out["species"] = [sp for sp, words in SPECIES_WORDS.items() if any(w in low for w in words)]
    return out
