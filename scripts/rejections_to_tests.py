"""Turn dismissals that carry a reason into tests.

    python scripts/rejections_to_tests.py

Reads the database (listing_status.reason) and writes tests/test_from_rejections.py.
"other" is counted and not asserted: it has no rule to pin down.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "tests", "test_from_rejections.py")


def case_for(reason: str, item: dict) -> str | None:
    """One assertion for this dismissal, or None when there is nothing to pin."""
    if reason == "other" or not reason:
        return None
    payload = json.dumps({
        "source": item.get("source"), "country": item.get("country"),
        "title": item.get("title") or "", "description": item.get("description") or "",
        "tipo": item.get("tipo"), "price": item.get("price"), "area_m2": item.get("area_m2"),
        "raw_json": item.get("raw_json"),
    }, ensure_ascii=False)
    if reason == "occupied":
        return (f"sc, why = score({payload})\n"
                "assert sc == 0 and any('possession' in r for r in why)")
    if reason == "heavy work":
        return f"assert condition({payload}) == 'heavy'"
    if reason == "not a home":
        return f"assert property_kind({payload}) != 'home'"
    if reason == "sold":
        return f"assert gone_reason({payload})"
    if reason == "false price":
        return f"assert portal_price_unusable({payload})"
    if reason == "no papers":
        return (f"sc, why = score({payload})\n"
                "assert any('land registry' in r for r in why)")
    return None


def render(cases: list[tuple[str, str, str | None]]) -> str:
    lines = [
        '"""Generated from dismissals. Do not edit by hand: run scripts/rejections_to_tests.py."""',
        "from common import portal_price_unusable",
        "from confirm import gone_reason",
        "from scoring import condition, property_kind, score",
        "",
    ]
    n = 0
    for lid, reason, body in cases:
        if not body:
            continue
        n += 1
        slug = "".join(ch if ch.isalnum() else "_" for ch in f"{reason}_{lid}")[:80]
        lines.append(f"def test_{n}_{slug}():")
        for part in body.splitlines():
            lines.append(f"    {part}")
        lines.append("")
    if n == 0:
        lines.append("def test_no_reasoned_dismissals_yet():")
        lines.append("    assert True")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    from db import connect
    db = connect()
    try:
        rows = db.execute(
            "SELECT s.listing_id, s.reason, l.source, l.country, l.title, l.description, "
            "l.tipo, l.price, l.area_m2, l.raw_json "
            "FROM listing_status s JOIN listings l ON l.id = s.listing_id "
            "WHERE s.status = 'dismissed' AND s.reason IS NOT NULL "
            "ORDER BY s.listing_id").fetchall()
    finally:
        db.close()
    cases = [(r["listing_id"], r["reason"], case_for(r["reason"], dict(r))) for r in rows]
    text = render(cases)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    written = sum(1 for _, _, body in cases if body)
    print(f"{written} tests from {len(cases)} dismissals → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
