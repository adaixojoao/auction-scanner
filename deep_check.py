"""deep_check.py — what a buyer would find reading the file, for the top leads.

The scanner ranks on what a listing card states. The traps behind a "too cheap"
price are usually in the file: the court report says it is occupied or only a
share, the sale notice says the house is lived in and was not photographed
inside, the debtor's registered address is the property itself, the building is
registered as a café. On 2026-10-31 these were read by hand for the forced
sales; this reads them for any list of leads, and says what it found.

Read-only: it never changes a listing or a score. Each check names its
evidence; a verdict is "reject", "caution" or "clean" (nothing found, which is
not the same as checked by a lawyer).

    python deep_check.py --top 50              # the best leads (db.load_best)
    python deep_check.py pvp:4597468 citius:…  # given listings
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from common import LOG, find_terms, normalize

# Words in a sale notice or card that settle a question the price raises.
# Each is matched as whole words, negation-aware (common.find_terms).
LIVED_IN = [
    "encontra-se habitado", "imóvel habitado", "imovel habitado", "encontra-se ocupado", "imóvel ocupado",
    "habitado pelo executado", "ocupado pelo executado", "ocupado pela executada", "a ocupante",
    "não foi autorizado o registo fotográfico", "nao foi autorizado o registo fotografico",
    "não permitiu o registo fotográfico", "nao permitiu o registo fotografico",
    "occupato dal debitore", "occupato dall'esecutato", "occupato da terzi", "abitato dal debitore",
    "occupé par", "loué à", "bail en cours", "vivienda habitual del deudor", "ocupada por terceros",
]
POOR_STATE = [
    "condições deficitárias", "condicoes deficitarias", "mau estado", "em ruína", "em ruinas",
    "stato di abbandono", "fatiscente", "en mauvais état", "en ruine",
]
UNREGISTERED = [
    "não constam na matriz", "nao constam na matriz", "não constam da matriz", "não consta da descrição predial",
    "anexos que não constam", "non sanabile", "abusivo", "abusiva", "construcción ilegal",
]
NOT_A_HOME = [
    "destina a serviços", "destina-se a serviços", "destinado a comércio", "destinada a comércio",
    "destinado a serviços", "atelier", "abegoaria", "casa de arrecadação", "arrecadação de um só vão",
    "bail commercial", "locale commerciale", "uso ufficio", "local comercial", "nave industrial",
]
HOME_KINDS = ("home",)


def text_findings(item: dict) -> list[tuple[str, str]]:
    """(level, reason) from the listing's own title and description."""
    from common import land_figure
    from confirm import gone_reason
    from scoring import NOT_A_BUILDING, UNFINISHED_HOUSE, is_fractional_share, is_percent_share, property_kind
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    out: list[tuple[str, str]] = []
    gone = gone_reason(item)
    if gone:
        out.append(("reject", f"no longer a sale: {gone}"))
    if is_fractional_share(item.get("title") or "") or is_percent_share(text):
        out.append(("reject", "only a share of the property is sold"))
    # The scorer's own lists, so the two cannot disagree (2026-10-31: a mobile
    # home and a house still being built had reached the top of Investment home).
    for level, words, what in (("caution", LIVED_IN, "lived in or occupied"),
                               ("caution", POOR_STATE, "poor condition"),
                               ("caution", UNREGISTERED, "works not on the registry or not legal"),
                               ("reject", NOT_A_HOME, "not a home"),
                               ("reject", NOT_A_BUILDING, "a mobile home or caravan, not a house"),
                               ("reject", UNFINISHED_HOUSE, "still being built")):
        found = find_terms(text, words, negations=words not in (NOT_A_BUILDING, UNFINISHED_HOUSE))
        if found and (level != "reject" or property_kind(item) in HOME_KINDS):
            out.append((level, f"{what}: the notice says \"{found[0]}\""))
    area = item.get("area_m2")
    if area and land_figure(text, area):
        out.append(("caution", f"the size stored ({area:,.0f} m²) is the land in the notice, not the home"))
    return out


_ADDRESS_WORDS = re.compile(r"\b(?:rua|travessa|avenida|largo|praceta|estrada|caminho|lugar|beco)\b\s+(?:d[aeo]s?\s+)?([\w\s]{3,40}?)(?:,|\s+n\.?[ºo°]|\s+\d)", re.I)


_LINK_WORDS = {"da", "de", "do", "das", "dos", "e"}


def _words(text: str) -> set[str]:
    """A street name's words, without "da", "dos"…: "Quinta dos Cubos" and
    "Quinta Cubos" are one street (Oliveira do Douro, 2026-10-31)."""
    return {w for w in re.findall(r"\w+", normalize(text or "")) if w not in _LINK_WORDS and not w.isdigit()}


def _street(text: str) -> set[str]:
    m = _ADDRESS_WORDS.search(text or "")
    return _words(m.group(1)) if m else set()


def debtor_at_property(detail_text: str, description: str) -> bool:
    """Whether a debtor's registered address (Citius "Intervenientes") is the
    sold property's own street: the usual sign that the owner lives there."""
    street = _street(description)
    if not street:
        return False
    for m in re.finditer(r"Executad[oa]\s*\|?\s*Nome:.*?Morada:\s*\|?\s*([^|]+)", detail_text or "", re.S):
        if street <= _words(m.group(1)):
            return True
    return False


def citius_findings(session, item: dict) -> list[tuple[str, str]]:
    from sources.pt import CITIUS_DETAILS
    raw = json.loads(item.get("raw_json") or "{}")
    if not raw.get("html_id"):
        return []
    resp = session.post(CITIUS_DETAILS, data="{htmlId:%s}" % int(raw["html_id"]),
                        headers={"Content-Type": "application/json; charset=utf-8"})
    resp.raise_for_status()
    html = resp.json().get("d") or ""
    plain = re.sub(r"(\s*\|\s*)+", " | ", re.sub(r"<[^>]+>", " | ", html)).replace("&#160;", " ")
    out = []
    if debtor_at_property(plain, item.get("description") or ""):
        out.append(("caution", "the debtor's registered address is the property itself — probably lived in"))
    if not re.search(r"Agente de Execu|Encarregado", plain, re.I):
        out.append(("info", "no enforcement agent named on Citius — ask the court section"))
    return out


def report_findings(session, item: dict) -> list[tuple[str, str]]:
    """The Italian court report (perizia.py), for PVP and astalegale sales."""
    import perizia
    if item.get("source") not in perizia.SOURCES:
        return []
    got = perizia.check(session, item)
    if not got.get("found"):
        return [("info", f"no court report: {got.get('why')}")]
    if got.get("scanned"):
        return [("info", "the court report is a scan with no text: read it yourself")]
    blocked, _, reasons = perizia.effect(got)
    out = [("reject", blocked)] if blocked else []
    for r in reasons:
        level = "caution" if any(w in r for w in ("occupied", "lease", "damp", "abandoned", "irregular")) else "info"
        out.append((level, r))
    return out


def verdict(findings: list[tuple[str, str]]) -> str:
    levels = {level for level, _ in findings}
    return "reject" if "reject" in levels else "caution" if "caution" in levels else "clean"


def deep_check(item: dict, session=None) -> dict:
    """{"id", "verdict", "findings": [(level, reason)], "errors": [...]} for one listing."""
    findings = text_findings(item)
    errors = []
    for check in (citius_findings if item.get("source") == "citius" else None,
                  report_findings if item.get("source") in ("pvp_giustizia", "astalegale") else None):
        if check is None:
            continue
        try:
            if session is None:
                from common import make_session
                session = make_session(timeout=40)
            findings += check(session, item)
        except Exception as e:  # noqa: BLE001 — one site down is a gap, not a verdict
            errors.append(f"{check.__name__}: {type(e).__name__}")
            LOG.info(f"Deep check of {item.get('id')}: {check.__name__} failed ({type(e).__name__})")
    return {"id": item.get("id"), "verdict": verdict(findings), "findings": findings, "errors": errors}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read the file behind the best leads")
    p.add_argument("ids", nargs="*", help="listing ids (default: the best leads)")
    p.add_argument("--top", type=int, default=50)
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = p.parse_args(argv)
    from config import load_config
    from db import connect, load_best
    db = connect()
    if args.ids:
        q = ",".join("?" * len(args.ids))
        items = [dict(r) for r in db.execute(f"SELECT * FROM listings WHERE id IN ({q})", args.ids)]
    else:
        best = load_best(db, filters=load_config().get("filters"))
        items = sorted(best, key=lambda it: it.get("score") or 0, reverse=True)[:args.top]
    from common import make_session
    session = make_session(timeout=40)
    results = [deep_check(it, session) for it in items]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for r in results:
            print(f"{r['verdict']:8} {r['id']}")
            for level, reason in r["findings"]:
                print(f"    {level:8} {reason}")
            for e in r["errors"]:
                print(f"    error    {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
