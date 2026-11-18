"""registry_charges.py — what a Spanish registry certificate says passes to the buyer.

A Spanish court sale publishes, beside the court notice, the land registry's
"certificación de dominio y cargas": every charge registered on the property.
Charges older than the creditor's own (usually a mortgage) stay with the
property and the buyer pays them on top of the bid; that is what turns a
€9,000 claim on a €170,000 flat into a trap. The scanner reads the certificate
of a small-claim sale (sale_floors) as soon as it is published.

Registry offices write these certificates in their own layouts (continuous
"Según resulta de la Anotación Letra E…", lettered "A).- Con una HIPOTECA…",
prose with dashed rules, or field lists), amounts in figures or in words, and
about half are scans with no text at all. So the reading is conservative:

- a charge that names this procedure, or "la entidad actora", is the
  creditor's own and is cancelled by the sale;
- every other mortgage or seizure found is counted as passing to the buyer,
  whether or not it ranks first (the worst case);
- an amount is taken only when it reads cleanly; a garbled one makes the
  total unknown rather than smaller;
- a scan, a garbled text or a layout not understood says so and is left to
  the person to read.

Nothing here is a legal opinion: the result is named as read automatically,
with the document's link, and the certificate can be years old.
"""
from __future__ import annotations

import io
import re

from common import normalize

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PAGES = 40
MIN_TEXT = 400               # less text than this from a PDF: a scan
# More mentions of mortgages and seizures than this: a bundle of many
# properties' certificates (one ran to 410 pages), not one to read automatically.
MAX_CHARGE_WORDS = 40

# ── Which BOE document is which ───────────────────────────────────────

def classify(title: str) -> str:
    """"charges" (the registry certificate), "notice" (the edict), "valuation",
    "order" (a court order) or "other", from a document's title on the BOE."""
    t = normalize(title)
    if re.search(r"\bgu[i]a\b", t):
        return "other"                   # the registrars' guide to reading a certificate
    if re.search(r"\bnota simple\b", t) or (
            re.search(r"certific|certificad|registral", t) and re.search(r"carga|dominio|titularidad|registral", t)):
        return "charges"
    if re.search(r"\bedicto\b|condiciones|convocatoria|\banuncio\b", t):
        return "notice"
    if re.search(r"tasaci|valoraci|perit|avaluo", t):
        return "valuation"
    if re.search(r"\bauto\b|\bdecreto\b|mandamiento|oficio", t):
        return "order"
    return "other"


# ── Amounts, in figures or in words ───────────────────────────────────

_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400,
    "quinientos": 500, "quinientas": 500, "seiscientos": 600, "seiscientas": 600,
    "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
_SCALES = {"mil": 1_000, "millon": 1_000_000, "millones": 1_000_000}
_NUMBER_WORDS = set(_UNITS) | set(_SCALES) | {"y"}


def words_to_number(words: list[str]) -> int | None:
    """["seis", "mil", "novecientos", "treinta", "y", "siete"] → 6937."""
    total, current, seen = 0, 0, False
    for w in words:
        if w == "y":
            continue
        if w in _UNITS:
            current += _UNITS[w]
            seen = True
        elif w == "mil":
            total += (current or 1) * 1_000
            current, seen = 0, True
        elif w in ("millon", "millones"):
            total += (current or 1) * 1_000_000
            current, seen = 0, True
        else:
            return None
    return total + current if seen else None


_FIGURE = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:euros?|€|eur)\b")
_WORD_EUROS = re.compile(r"\beuros?\b")
# An amount right after these is not a debt: a valuation, or a tax already paid.
_NOT_A_DEBT = re.compile(r"(tas[oa]|tasad|efectos de subasta|efectos de ejecucion|valor|valorad|liberad"
                         r"|satisfech|autoliquidac)[^.;]{0,100}$")


def amounts(text: str) -> tuple[list[float], bool]:
    """(the debt amounts written in `text`, whether one could not be read).

    `text` is normalized (lower case, no accents). An amount in words with a
    garbled word inside ("dos mil ochena y dos euros") is reported as unread,
    not read as €2."""
    found, unread = [], False
    taken: list[tuple[int, int]] = []
    for m in _FIGURE.finditer(text):
        if _NOT_A_DEBT.search(text[max(0, m.start() - 80):m.start()]):
            taken.append(m.span())
            continue
        found.append(float(m.group(1).replace(".", "").replace(",", ".")))
        taken.append(m.span())
    for m in _WORD_EUROS.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        before = re.findall(r"[a-z]+|\d+|[^\sa-z\d]", text[max(0, m.start() - 160):m.start()])
        run: list[str] = []
        i = len(before) - 1
        while i >= 0 and before[i] in _NUMBER_WORDS:
            run.insert(0, before[i])
            i -= 1
        if not run or run == ["y"]:
            continue                       # "euros" after figures or prose
        # A garbled word between number words: "mil ochena y dos".
        if i >= 1 and before[i].isalpha() and before[i - 1] in _NUMBER_WORDS - {"y"}:
            unread = True
            continue
        start_ctx = text[max(0, m.start() - 160):m.start()]
        if _NOT_A_DEBT.search(start_ctx[: max(0, len(start_ctx) - len(" ".join(run)) - 1)]):
            continue
        value = words_to_number(run)
        if value is None:
            unread = True
            continue
        cents = re.match(r"\s*con\s+([a-z\s]+?)\s+centimos", text[m.end():m.end() + 80])
        if cents:
            c = words_to_number(cents.group(1).split())
            value += (c or 0) / 100
        found.append(float(value))
    return found, unread


# ── Reading a certificate ─────────────────────────────────────────────

_SECTION_START = re.compile(r"aparece gravad|se halla afecta|gravada con|cargas propias|\bcargas\s*:|\btercero\s*:")
# Not a bare "diligencia": a "Diligencia de Ordenación" extending a seizure sits
# between two charges (Sevilla, 2026-11-18) and ended the list before the second.
_SECTION_END = re.compile(r"\bdiligencia\s*(?::|de comunicacion)|lo que antecede|igualmente certifico"
                          r"|documentos (?:relativos[^.]{0,40})?pendientes|y para que conste"
                          r"|informacion basica sobre proteccion")
# Read only inside the charges section: the court's request bound into the same
# PDF says "…o, en su caso, que se halla libre de cargas" whatever the answer.
_FREE = re.compile(r"(?<!en su caso, que se halla )libre de (?:toda )?cargas?|no aparece gravad|no consta gravad"
                   r"|no constan? cargas|no existen cargas")
# Anything like a mortgage or a seizure, OCR slips included ("hipoteea", "hipotecaa").
_CHARGE_LIKE = re.compile(r"hipot(?!ecari)[a-z]*|embarg[a-z]*|anotacion")
_FINCA = re.compile(r"\bfinca (?:registral )?(?:numero |n[o.]?\s?|nº\s?)?(\d[\d.]{2,})")
# A charge registration starts with the word, after a break or an article.
_ENTRY = re.compile(r"(?:^|\n\s*|[.:;)\-]\s*|\b(?:con\s+)?(?:una?|otra)\s+|\bpropias\s+)"
                    r"(?:anotacion (?:preventiva )?de )?(hipoteca|embargo)\b")
# "- hipoteca: hipoteca a favor de…": one charge named twice.
_SAME_ENTRY_CHARS = 40
_OWN_WORDS = re.compile(r"\bactora\b|\bejecutante\b|objeto de (?:la presente|este|esta)\b|mismo procedimiento"
                        r"|que motiva|que se ejecuta|expedicion de la presente certificacion")
_PROCEDURE = re.compile(r"(?:procedimiento|autos|ejecucion|expediente)[^.]{0,140}?"
                        r"(?:numero|n\.?\s?[o]\b|n\s?º)?\s*(\d{1,6})\s*/\s*(\d{4}|\d{2})\b")


def _year(y: str) -> str:
    return y if len(y) == 4 else f"20{y}"


_CERTIFICO = re.compile(r"c\s?e\s?r\s?t\s?i\s?f\s?i\s?c\s?o\b")


def procedures(text: str) -> set[str]:
    """The case number ("1942/2016") the certificate was issued for: the first
    one in its "CERTIFICO" paragraph. Not any case near the top — the other
    creditors' cases are named there too, and taking one made an earlier
    seizure look like the creditor's own."""
    m = _CERTIFICO.search(text)
    if not m:
        return set()
    found = _PROCEDURE.search(text[m.end(): m.end() + 1500])
    return {f"{int(found.group(1))}/{_year(found.group(2))}"} if found else set()


def _mentions(segment: str, cases: set[str]) -> bool:
    flat = re.sub(r"(\d)\.(\d{3})\b", r"\1\2", segment)          # "922/2.014" → "922/2014"
    for case in cases:
        n, y = case.split("/")
        if re.search(rf"\b0*{n}\s*/\s*(?:{y}|{y[2:]})\b", flat):
            return True
    return False


def readable(text: str) -> bool:
    """Enough clean text to read: not a scan, not OCR noise."""
    if len(text) < MIN_TEXT:
        return False
    odd = len(re.findall(r"[^\w\s.,;:()/%€ºª'\"«»\-–—]", text))
    words = re.findall(r"[a-z]{3,}", normalize(text[:20000]))
    common = sum(1 for w in words if w in ("finca", "registro", "propiedad", "cargas", "inscripcion",
                                           "favor", "euros", "fecha", "folio", "tomo", "libro"))
    return odd / max(1, len(text)) < 0.02 and common >= 5


def read_certificate(text: str, cases: set[str] | None = None) -> dict:
    """{"status": "read" | "unreadable", "free", "entries": [{"kind", "own",
    "amount", "unread", "excerpt"}], "cases"} from a certificate's text.

    `cases` is this sale's case number from the BOE account number, the
    reliable source (checked against six certificates, 2026-11-18); without
    it, the case the certificate's own CERTIFICO paragraph names."""
    if not readable(text):
        return {"status": "unreadable"}
    # Line starts are kept: a charge often begins on a new line with no full stop
    # before it ("…dos mil veintiuno\nanotacion de embargo judicial", 2026-11-18).
    lined = normalize(re.sub(r"[ \t\r\f\v]*\n\s*", "\n", re.sub(r"[ \t\r\f\v]+", " ", text)))
    flat = lined.replace("\n", " ")       # same offsets; phrases wrap across lines
    cases = set(cases or ()) or procedures(flat)
    start = _SECTION_START.search(flat)
    if not start:
        return {"status": "unreadable", "cases": sorted(cases)}
    end = _SECTION_END.search(flat, start.end())
    bounds = (start.start(), end.start() if end else len(flat))
    section = flat[bounds[0]: bounds[1]]
    fincas = {f.replace(".", "") for f in _FINCA.findall(flat)}
    if len(fincas) > 3 or len(_CHARGE_LIKE.findall(flat)) > MAX_CHARGE_WORDS:
        return {"status": "unclear", "why": "it covers several properties", "cases": sorted(cases)}
    anchors = []
    for m in _ENTRY.finditer(lined[bounds[0]: bounds[1]]):
        if anchors and m.start(1) - anchors[-1][0] < _SAME_ENTRY_CHARS:
            continue
        anchors.append((m.start(1), m.group(1)))
    entries = []
    for i, (at, word) in enumerate(anchors):
        seg = section[at: anchors[i + 1][0] if i + 1 < len(anchors) else len(section)]
        kind = "mortgage" if word == "hipoteca" else "seizure"
        own = bool(_OWN_WORDS.search(seg)) or _mentions(seg, cases)
        principal = re.search(r"responde esta finca de ([^.;]{0,120}?euros?)", seg)
        found, unread = amounts(principal.group(1) if principal else seg)
        entries.append({"kind": kind, "own": own, "amount": round(sum(found), 2) if found and not unread else None,
                        "unread": unread, "principal_only": bool(principal), "excerpt": seg[:200]})
    # One mortgage alone, in a mortgage enforcement: that mortgage is the creditor's.
    if (len(entries) == 1 and entries[0]["kind"] == "mortgage" and not entries[0]["own"]
            and re.search(r"ejecucion hipotecaria|art(?:iculo|\.)?\s*688", flat)):
        entries[0]["own"] = True
    if not entries:
        # "Free" only when the section says so and names nothing like a charge.
        if _FREE.search(section) and not _CHARGE_LIKE.search(section[12:]):
            return {"status": "read", "free": True, "entries": [], "cases": sorted(cases)}
        return {"status": "unclear", "why": "its list of charges could not be read", "cases": sorted(cases)}
    return {"status": "read", "free": False, "entries": entries, "cases": sorted(cases)}


def pdf_text(content: bytes) -> str | None:
    """The text of a PDF, or None without a PDF reader (pypdf)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:MAX_PAGES])
    except Exception:
        return ""


def case_from_account(expediente: str | None) -> str | None:
    """The case number inside a BOE "Cuenta expediente" ("1660 0000 06 0413 11"
    → "413/2011"): court, 0000, procedure type, number, year."""
    parts = re.findall(r"\d+", expediente or "")
    if len(parts) == 5 and len(parts[3]) == 4 and len(parts[4]) == 2:
        return f"{int(parts[3])}/20{parts[4]}"
    return None


def summary(cert: dict | None) -> tuple[str, float, str] | None:
    """("none" | "stated" | "unknown", amount, words) from a stored reading,
    or None when there is nothing read to go by."""
    if not cert:
        return None
    if cert.get("status") == "not_published":
        return "unknown", 0.0, ("no registry certificate is published on the BOE — ask the court, or get a "
                                "nota simple from the registry")
    if cert.get("status") == "scanned":
        return "unknown", 0.0, "the registry certificate is a scan the scanner cannot read — open it"
    if cert.get("status") != "read":
        why = f" ({cert['why']})" if cert.get("why") else ""
        return "unknown", 0.0, f"the registry certificate could not be read automatically{why} — open it"
    entries = cert.get("entries") or []
    others = [e for e in entries if not e.get("own")]
    if cert.get("free") and not entries:
        return "none", 0.0, "the registry certificate says the property is free of charges (read automatically)"
    if entries and not others:
        return "none", 0.0, ("the registry certificate lists only the creditor's own charge, which the sale "
                             "cancels (read automatically — check it)")
    if not entries:
        return None
    parts = []
    for e in others:
        what = "a mortgage" if e["kind"] == "mortgage" else "a seizure"
        if e.get("amount"):
            parts.append(f"{what} (€{e['amount']:,.0f}{' principal' if e.get('principal_only') else ''})")
        else:
            parts.append(f"{what} (amount not readable)")
    words = (f"the registry certificate lists {', '.join(parts)} besides the creditor's own — counted as "
             "staying with the property (read automatically — check which rank first)")
    if all(e.get("amount") for e in others):
        return "stated", float(sum(e["amount"] for e in others)), words
    return "unknown", 0.0, words
