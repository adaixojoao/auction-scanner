"""rent_rules.py — the letting rules of the French commune a listing is in.

data/fr_rent_rules.csv (scripts/update_rent_rules.py) holds the communes on
the tight-market list of décret 2013-392 and those where rents are capped.
On the list a vacant home pays the taxe sur les logements vacants (CGI art.
232) and the commune may add up to 60% to a second home's taxe d'habitation
(CGI art. 1407 ter); in a "zone tendue" proper, a new tenant's rent cannot
rise beyond the rent index over the last one's (loi 1989 art. 18). Where
rents are capped (loi ELAN art. 140) a new lease cannot ask more than the
prefect's reference rent plus 20%. Shown, not scored.
"""
from __future__ import annotations

import csv
import functools
import os
import re

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fr_rent_rules.csv")


@functools.lru_cache(maxsize=2)
def _table(path: str = PATH) -> dict[str, dict]:
    """{insee: row}, and {"name:" + key: row} for names only one commune has."""
    from prices import place_key
    out: dict[str, dict] = {}
    names: dict[str, list[dict]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out[row["insee"]] = row
                names.setdefault(place_key(row["name"]), []).append(row)
    except (OSError, KeyError):
        return {}
    for key, rows in names.items():
        if len(rows) == 1:
            out["name:" + key] = rows[0]
    return out


def _commune(item: dict, table: dict) -> tuple[dict | None, bool]:
    """The commune's row, and whether it was found by its code (else by its name)."""
    import surroundings
    found = surroundings.current(item) or {}
    insee = ((found.get("found") or {}).get("fr_risks") or {}).get("insee")
    if insee:
        return table.get(insee), True
    import geo
    from prices import place_key
    name = geo.municipality(item)
    return (table.get("name:" + place_key(name)) if name else None), False


def note(item: dict, path: str = PATH) -> str | None:
    if (item.get("country") or "").upper() != "FR":
        return None
    row, _ = _commune(item, _table(path))
    if not row:
        return None
    bits = []
    if row.get("zone") == "tendue":
        bits.append("zone tendue: a new tenant's rent cannot rise beyond the rent index, a vacant home pays the "
                    "vacancy tax and the commune may add up to 60% to a second home's taxe d'habitation")
    elif row.get("zone") == "touristique":
        bits.append("tourist and tight-market commune: a vacant home pays the vacancy tax and the commune may "
                    "add up to 60% to a second home's taxe d'habitation")
    if row.get("rent_cap"):
        where = ("in part of the commune" if row["rent_cap"] == "part"
                 else re.sub(r"\s*\(\d+ communes\)", "", row.get("territory") or "") or row["name"])
        until = f"; the current order runs to {row['cap_until']}" if row.get("cap_until") else ""
        bits.append(f"rents are capped ({where}): a new lease cannot exceed the prefect's reference rent "
                    f"plus 20%{until}")
    if not bits:
        return None
    return f"{row['name']}: " + "; ".join(bits) + " (décret 2013-392, loi ELAN)"
