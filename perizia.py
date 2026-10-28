"""Perizia: what the court expert's report says about an Italian judicial sale.

The listing card rarely says that only a share is sold, that the flat is let on a
lease the buyer must keep, or that the works are illegal and cannot be made
legal. The expert's report (perizia, "Relazione CTU") does. For the best Live
and invest homes this downloads it, reads its text and keeps what it finds on
the listing (raw_json "perizia"), for scoring.perizia_effect.

Where the report is found:
  - pvp_giustizia, and astalegale codes "P<id>" (the PVP's own id): the PVP's
    JSON API lists the attachments, the report is the one coded PERIZ;
  - other astalegale codes ("B<id>"): the detail page links a "Perizia" document.

A scanned report has no text: that is kept as such ("scanned"), never guessed.

    python perizia.py https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio=4626456
"""
from __future__ import annotations

import json
import re
import sys

from common import LOG, find_terms

VERSION = 1
PER_SCAN = 40                     # reports read per scan, the best homes first
MAX_BYTES = 25_000_000
SOURCES = ("pvp_giustizia", "astalegale")
PVP_API = "https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/vendite/{}/restricted"
PVP_FILES = "https://resource-pvp.giustizia.it"
_PVP_ID = re.compile(r"idAnnuncio=(\d+)")
_ASTALEGALE_CODE = re.compile(r"/Aste/Detail/([PB])(\d+)", re.I)
# <span ...> Perizia (2,2 Mb) </span> ... <a href="https://documents.astalegale.net/file/...">
_ASTALEGALE_LINK = re.compile(r'<a href="(https://documents\.astalegale\.net/file/[^"?]+)')
_ASTALEGALE_LABEL = re.compile(r"([A-Za-zÀ-ÿ][^()<>]{1,58}?)\s*\([^()]*Mb\)", re.I)

# What a report says, each a list of phrases matched as whole words (common.find_terms).
# The expert's instructions (the "quesiti") are copied in capitals at the top of
# many reports and name every risk; _body() drops lines in capitals first.
SHARE = ["nuda proprietà", "nuda proprieta", "diritto di usufrutto", "intero diritto di usufrutto",
         "quota di 1/2", "quota di 1/3", "quota di 1/4", "quota di 2/3", "quota indivisa",
         "diritto di superficie", "diritto di abitazione"]
NOT_LEGAL = ["non sanabile", "non sanabili", "non è sanabile", "non sono sanabili", "non consentono una sanabilità",
             "non consento una sanabilità", "insanabile", "insanabili", "da demolire", "ordinanza di demolizione",
             "non rispetta le norme igienico", "non rispettano le norme igienico", "inagibile", "non agibile"]
LEASED = ["locazione opponibile", "contratto di locazione opponibile", "occupato da terzi con contratto",
          "occupato da terzi con titolo", "comodato opponibile"]
OCCUPIED = ["occupato dal debitore", "occupato dall'esecutato", "occupato da terzi senza titolo",
            "occupato dal fallito", "abitato dal debitore"]
FREE = ["risulta libero", "immobile libero", "è libero", "libero da persone"]
FIXABLE = ["sanatoria", "regolarizzabile", "regolarizzabili", "difformità"]
DAMP = ["muffa", "muffe", "infiltrazioni", "umidità di risalita", "tracce di umidità"]
STRIPPED = ["abbandonato", "stato di abbandono", "vandalizzato", "forzatamente aperto", "privo degli impianti",
            "privi degli impianti", "fatiscente", "fatiscenti", "pericolante"]
_MARKET = re.compile(r"valore di mercato[^\d€]{0,120}?(?:€|euro)?\s*\.?\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?)", re.I)
_BENE = re.compile(r"\bBENE\s+N\S{0,2}\s*(\d+)", re.I)


def _session():
    from common import make_session
    return make_session()


def report_url(session, item: dict) -> tuple[str | None, str]:
    """(link to the report, why) for a listing, or (None, why there is none)."""
    url = item.get("url") or ""
    pvp = _PVP_ID.search(url)
    code = _ASTALEGALE_CODE.search(url)
    pvp_id = pvp.group(1) if pvp else (code.group(2) if code and code.group(1).upper() == "P" else None)
    if pvp_id:
        resp = session.get(PVP_API.format(pvp_id), timeout=30)
        resp.raise_for_status()
        files = (resp.json().get("body") or {}).get("allegati") or []
        for f in files:
            if f.get("codiceTipoAllegato") == "PERIZ" and f.get("linkAllegato"):
                return PVP_FILES + f["linkAllegato"], "pvp"
        return None, "no report among the PVP attachments"
    if code:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        for m in _ASTALEGALE_LINK.finditer(resp.text):
            before = re.sub(r"<[^>]*>", " ", resp.text[max(0, m.start() - 600):m.start()])
            labels = _ASTALEGALE_LABEL.findall(before)
            if labels and "perizia" in labels[-1].lower():
                return m.group(1), "astalegale"
        return None, "no report linked on the astalegale page"
    return None, "not a PVP or astalegale page"


def pdf_text(data: bytes) -> str:
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _body(text: str) -> str:
    """The report without the copied instructions (lines in capitals)."""
    keep = []
    for line in text.splitlines():
        letters = [c for c in line if c.isalpha()]
        if len(letters) > 25 and sum(c.isupper() for c in letters) > 0.8 * len(letters):
            continue
        keep.append(line)
    return re.sub(r"\s+", " ", "\n".join(keep))


# Words of the expert's instructions, not of the findings: "in caso di opere
# abusive, controlli ...", "se trattasi di quota indivisa indichi ...".
_INSTRUCTION = re.compile(r"\b(in caso di|se trattasi|qualora|controlli|verifichi|indichi|determini|dica se|"
                          r"precisando|eventuali|eventuale|esprima|predisponendo|ai fini della)\b", re.I)


def _snippet(text: str, terms: list[str]) -> str:
    """The first passage stating one of `terms` as a finding, or ""."""
    low = text.lower()
    for term in terms:
        start = 0
        while (i := low.find(term.lower(), start)) >= 0:
            start = i + 1
            passage = text[max(0, i - 80): i + 120]
            near = text[max(0, i - 120): i + 40]
            if find_terms(passage, [term]) and not _INSTRUCTION.search(near):
                return passage.strip()
    return ""


def read(text: str) -> dict:
    """What the report says, from its text."""
    body = _body(text)
    if len(body) < 400:
        return {"scanned": True}
    lots = sorted({int(n) for n in _BENE.findall(body)})
    out = {"scanned": False, "lots": len(lots) or 1}
    for key, terms in (("share", SHARE), ("not_legal", NOT_LEGAL), ("leased", LEASED), ("occupied", OCCUPIED),
                       ("free", FREE), ("fixable", FIXABLE), ("damp", DAMP), ("stripped", STRIPPED)):
        found = _snippet(body, terms)
        if found:
            out[key] = found[:220]
    m = _MARKET.search(body)
    if m:
        out["market_value"] = float(m.group(1).replace(".", "").replace(",", "."))
    return out


LEASED_FACTOR = 0.70              # the buyer keeps the tenant: no home to live in for now
OCCUPIED_FACTOR = 0.85            # the custodian frees it, but months pass


def effect(stored: dict | None) -> tuple[str | None, float, list[str]]:
    """(why Live and invest cannot show it, factor, reasons) from a stored report.

    A report covering several properties cannot say which finding is this one's:
    it only adds a reason to check, never blocks or charges."""
    p = stored or {}
    if not p.get("found") or p.get("scanned"):
        return None, 1.0, []
    if (p.get("lots") or 1) > 1:
        named = [k for k in ("share", "not_legal", "leased", "occupied") if p.get(k)]
        return None, 1.0, ([f"the perizia covers {p['lots']} properties and mentions "
                            f"{', '.join(n.replace('_', ' ') for n in named)} — check which is this one"]
                           if named else [])
    if p.get("share"):
        return f"perizia: only a share or a lesser right is sold — “{p['share'][:140]}”", 1.0, []
    if p.get("not_legal"):
        return f"perizia: the works cannot be made legal or it is not habitable — “{p['not_legal'][:140]}”", 1.0, []
    factor, reasons = 1.0, []
    if p.get("leased"):
        factor *= LEASED_FACTOR
        reasons.append(f"perizia: let on a lease the buyer keeps (ranked 30% lower) — “{p['leased'][:120]}”")
    elif p.get("occupied"):
        factor *= OCCUPIED_FACTOR
        reasons.append(f"perizia: occupied, to be freed (ranked 15% lower) — “{p['occupied'][:120]}”")
    elif p.get("free"):
        reasons.append("perizia: free of occupants")
    for key, what in (("stripped", "abandoned or stripped"), ("damp", "damp or mould"),
                      ("fixable", "irregular works that can be legalised")):
        if p.get(key):
            reasons.append(f"perizia: {what} — “{p[key][:100]}”")
    if p.get("market_value"):
        reasons.append(f"perizia market value €{p['market_value']:,.0f}")
    return None, factor, reasons


def check(session, item: dict) -> dict:
    link, why = report_url(session, item)
    if not link:
        return {"found": False, "why": why}
    resp = session.get(link, timeout=60)
    resp.raise_for_status()
    if len(resp.content) > MAX_BYTES:
        return {"found": True, "url": link, "why": "report too large to read"}
    return {"found": True, "url": link, **read(pdf_text(resp.content))}


def check_pending(db, session, items: list[dict], limit: int = PER_SCAN) -> int:
    """Read the report of up to `limit` listings in `items` (best first) that have none yet."""
    import geo
    done = 0
    for item in items:
        if done >= limit:
            break
        if item.get("source") not in SOURCES:
            continue
        if (geo._raw(item).get("perizia") or {}).get("v") == VERSION:
            continue
        try:
            result = check(session, item)
        except Exception as e:  # noqa: BLE001 — a site down: try at the next scan
            LOG.info(f"Perizia {item['id']}: {type(e).__name__}")
            continue
        # The row on disk: a loaded copy of Live and invest can carry a trimmed raw_json.
        row = db.execute("SELECT raw_json FROM listings WHERE id = ?", (item["id"],)).fetchone()
        if row is None:
            continue
        raw = json.loads(row[0] or "{}")
        raw["perizia"] = {**result, "v": VERSION}
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
                   (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
    if done:
        LOG.info(f"Perizia: {done} reports read")
    return done


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(check(_session(), {"url": sys.argv[1]}), ensure_ascii=False, indent=1))
