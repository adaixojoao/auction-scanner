"""coast_retreat.py — whether a French listing's commune is on the coastline-retreat list.

data/fr_coast_retreat.csv (scripts/update_coast_retreat.py) holds the
communes the décret of art. L321-15 Code de l'environnement lists (loi Climat
et Résilience, 2021). Such a commune maps where its coast will be in 30 and
in 100 years (Code de l'urbanisme L121-22-2 ff.): in the 30-year zone new
building is banned, in the 30-to-100-year zone a new building must be
demolished once the sea comes within 30 years of it, the commune has a
pre-emption right (L219-1), and a seller must say whether the home is in
either zone (Code de l'environnement L125-5). Shown, not scored.
"""
from __future__ import annotations

import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fr_coast_retreat.csv")


def note(item: dict, path: str = PATH) -> str | None:
    if (item.get("country") or "").upper() != "FR":
        return None
    import rent_rules
    row, _ = rent_rules._commune(item, rent_rules._table(path))
    if not row:
        return None
    return (f"{row['name']} is on the coastline-retreat list (since {row['since'][:4]}, décret {row['decree']}): "
            "once it maps its retreat zones, new building is banned where the sea is expected within 30 years, "
            "and in the 30-to-100-year zone a new building must be demolished when the sea comes close — ask "
            "the mairie for the map; the seller's risk statement must say if the home is in a zone (Code de "
            "l'urbanisme L121-22-2)")
