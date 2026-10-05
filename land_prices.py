"""What a hectare of land is worth where a listing is, from official tables.

France: forest prices per forest region (SAFER, Le prix des terres 2025, 2025
figures), the only forest-to-forest comparison available; regions are IGN's
groupings of ecological regions, matched here by département (approximate where
a département spans two). Elsewhere no official forest price is published;
agricultural prices (Eurostat apri_lprc) are not used, because forest and scrub
sell well below farmland and the "discount" would be false.
"""
from __future__ import annotations

SAFER_FOREST_2025 = {
    "Nord-Bassin parisien": 6480, "Est": 7880, "Ouest": 5270, "Massif Central": 2850,
    "Sud-Ouest": 3310, "Alpes-Méditerranée-Pyrénées": 3400,
}
SAFER_SOURCE = "SAFER Le prix des terres 2025 (forests, 2025)"
_FR_REGION = {
    "Ouest": "14 22 29 35 44 49 50 53 56 61 72 85 79",
    "Nord-Bassin parisien": "02 08 10 18 27 28 36 37 41 45 51 59 60 62 75 76 77 78 80 89 91 92 93 94 95",
    "Est": "01 21 25 39 52 54 55 57 67 68 70 71 88 90",
    "Sud-Ouest": "16 17 24 31 32 33 40 46 47 64 82 86",
    "Massif Central": "03 12 15 19 23 42 43 48 58 63 69 87",
    "Alpes-Méditerranée-Pyrénées": "04 05 06 07 09 11 13 26 30 34 38 65 66 73 74 81 83 84 2A 2B",
}
FR_DEPT_REGION = {d: region for region, depts in _FR_REGION.items() for d in depts.split()}

def _dept(item: dict) -> str | None:
    d = str(item.get("district") or "").strip().upper()
    if d.isdigit():
        return d.zfill(2)
    return d if d in ("2A", "2B") else None


def forest_value(item: dict) -> dict | None:
    """{"eur_ha", "label"}: the official forest price per hectare where the
    listing is, when there is one (France, by département)."""
    if (item.get("country") or "").upper() != "FR":
        return None
    region = FR_DEPT_REGION.get(_dept(item) or "")
    if not region:
        return None
    return {"eur_ha": SAFER_FOREST_2025[region], "label": f"{SAFER_SOURCE}, {region}"}
