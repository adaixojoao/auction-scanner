"""tops.py — what may sit in the top of a goal.

A home that has not been looked at (photos.py) does not take a place in the
top of My home or Investment home. Plots and other goals are unchanged.
The same rules decide whether a fixed set of homes still survives, so a
change that lets a bad house through, or drops a sound one, fails the tests.
"""
from __future__ import annotations

TOP_N = 20
# The two goals where a house is what is being bought.
SEEN_GOALS = ("home", "invest")


def looked_at(item: dict) -> bool:
    """True when a photo check has been stored AND the photos actually show the house.

    Key presence alone is not enough: a check that concluded shows_house=False
    (only documents, maps, or a logo was provided) is not useful evidence about
    the building's condition.
    """
    import json
    rj = item.get("raw_json") or ""
    if '"photo_check"' not in rj:
        return False
    try:
        pc = json.loads(rj).get("photo_check") or {}
    except (ValueError, TypeError):
        return False
    # A low-confidence (or unrated) "yes" is a guess, not evidence (F09).
    return bool(pc.get("shows_house")) and pc.get("confidence") in ("medium", "high")


def place_unseen_homes_after(items: list[dict], mode: str) -> list[dict]:
    """Homes with no photo check go after everything that can be shown.

    `items` is already in score order. The first rows are what the top is
    taken from, so an unseen home cannot outrank one that was looked at.
    """
    if mode not in SEEN_GOALS:
        return items
    front, back = [], []
    for it in items:
        if it.get("kind") == "home" and not looked_at(it):
            back.append(it)
        else:
            front.append(it)
    return front + back


def top_blockers(item: dict) -> list[str]:
    """Why this home should not be counted as surviving in a top 20."""
    from common import portal_price_unusable
    from confirm import gone_reason
    from scoring import condition, property_kind, score

    why = []
    kind = property_kind(item)
    if kind != "home":
        why.append("not a home")
    import source_validation
    portal = (source_validation.CATALOG.get(item.get("source")) or {}).get("kind") in ("aggregator", "bank")
    if kind == "home" and portal and condition(item) == "unknown":
        why.append("state unknown")
    if portal_price_unusable(item):
        why.append("false price")
    if gone_reason(item):
        why.append("sold")
    if kind == "home" and not looked_at(item):
        why.append("not seen")
    sc, reasons = score(item, mode="home")
    text = " ".join(reasons)
    if sc == 0 and "possession" in text:
        why.append("occupied")
    if any("land registry" in r for r in reasons):
        why.append("no papers")
    return why
