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

VERSION = 5                       # 2: a share only when the passage is about what is sold; 3: checklists; 4: reports under other codes, summaries for scans; 5: owners still living there
PER_SCAN = 40                     # reports read per scan, the best homes first
MAX_BYTES = 25_000_000
MAX_TRIES = 3                     # candidate reports read for one listing (a scan gives way to the next)
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
            "occupato dal fallito", "abitato dal debitore",
            # Todi and Sala Consilina, 2026-10-31: the owners or the debtor still live there.
            "occupato dagli attuali proprietari", "occupato dai proprietari", "abitato dai proprietari",
            "occupato dagli esecutati"]
# Not "occupata dallo stesso esecutato": Sala Consilina's report says it of
# "altra unità immobiliare", the debtor's other flat next door.
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


# Another attachment that is the report all the same: a "Perizia.pdf" filed as
# ALTRO (Mondolfo, 2026-10-30), or the expert's summary ("quadro sinottico",
# coded STIMA), read when the report itself is a scan (Nepi).
_REPORT_NAME = re.compile(r"perizi|relazione|\bctu\b|stima|sinottico", re.I)
_SUPPLEMENT = re.compile(r"integrativ", re.I)


def pvp_reports(files: list[dict]) -> list[str]:
    """The PVP attachments that can be the expert's report, best first: the
    reports coded PERIZ (a supplement after the main one), then one filed
    under another code with a report's name, then the expert's summary."""
    def rank(f):
        code, link = f.get("codiceTipoAllegato"), f.get("linkAllegato") or ""
        name = link.rsplit("/", 1)[-1]
        if code == "PERIZ":
            return 1 if _SUPPLEMENT.search(name) else 0
        if code == "STIMA":
            return 3
        return 2 if _REPORT_NAME.search(name) else None
    ranked = [(rank(f), i, f["linkAllegato"]) for i, f in enumerate(files) if f.get("linkAllegato")]
    return [PVP_FILES + link for r, _, link in sorted(x for x in ranked if x[0] is not None)]


def report_url(session, item: dict) -> tuple[str | None, str]:
    """(link to the report, why) for a listing, or (None, why there is none)."""
    links, why = report_urls(session, item)
    return (links[0] if links else None), why


def report_urls(session, item: dict) -> tuple[list[str], str]:
    """([links to the report and what can stand in for it, best first], why)."""
    url = item.get("url") or ""
    pvp = _PVP_ID.search(url)
    code = _ASTALEGALE_CODE.search(url)
    pvp_id = pvp.group(1) if pvp else (code.group(2) if code and code.group(1).upper() == "P" else None)
    if pvp_id:
        resp = session.get(PVP_API.format(pvp_id), timeout=30)
        resp.raise_for_status()
        links = pvp_reports((resp.json().get("body") or {}).get("allegati") or [])
        return (links, "pvp") if links else ([], "no report among the PVP attachments")
    if code:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        for m in _ASTALEGALE_LINK.finditer(resp.text):
            before = re.sub(r"<[^>]*>", " ", resp.text[max(0, m.start() - 600):m.start()])
            labels = _ASTALEGALE_LABEL.findall(before)
            if labels and "perizia" in labels[-1].lower():
                return [m.group(1)], "astalegale"
        return [], "no report linked on the astalegale page"
    return [], "not a PVP or astalegale page"


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
                          r"precisando|eventuali|eventuale|esprima|esponga|predisponendo|ai fini della|"
                          r"nel caso di|in questo caso|se fosse|deve contenere|seguenti indicazioni|"
                          r"esistenza di)\b", re.I)
# A share is a finding only when the passage is about what is sold: not the
# history of the title ("pervenuto in nuda proprietà per donazione") nor a lien
# copied from the registers ("Contro: ... per la quota di 1/3").
_SOLD = re.compile(r"\b(pignorat\w*|in vendita|posto in vendita|oggetto della vendita|valore della quota|"
                   r"diritto e quota|quota in vendita|tipologia del diritto|diritto posto|diritto venduto|"
                   r"diritti di|intero diritto)\b", re.I)
_HISTORY = re.compile(r"\b(pervenut\w*|donazione|successione|a favore|contro:|trascritt\w*|iscritt\w*)", re.I)


def _snippet(text: str, terms: list[str], about_sale: bool = False) -> str:
    """The first passage stating one of `terms` as a finding, or "".

    With `about_sale` the passage must also be about what is sold, not the
    title's history."""
    low = text.lower()
    for term in terms:
        start = 0
        while (i := low.find(term.lower(), start)) >= 0:
            start = i + 1
            passage = text[max(0, i - 80): i + 120]
            near = text[max(0, i - 120): i + 40]
            # Judge this occurrence, not an earlier one in the passage: under the
            # heading "DIFFORMITA':", "non presenta difformità" is negated (Nepi).
            here = text[max(0, i - 40): i + len(term) + 1]
            if not find_terms(here, [term]) or _INSTRUCTION.search(near):
                continue
            if about_sale and (not _SOLD.search(passage) or _HISTORY.search(near)):
                continue
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
        found = _snippet(body, terms, about_sale=key == "share")
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
    """The first readable report among the candidates. One that is a scan or
    too large gives way to the next (the expert's summary); when none can be
    read, the first one's state is kept."""
    links, why = report_urls(session, item)
    if not links:
        return {"found": False, "why": why}
    first = None
    for link in links[:MAX_TRIES]:
        resp = session.get(link, timeout=60)
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            got = {"found": True, "url": link, "why": "report too large to read"}
        else:
            got = {"found": True, "url": link, **read(pdf_text(resp.content))}
        if not got.get("scanned") and "why" not in got:
            return got
        first = first or got
    return first


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
