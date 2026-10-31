"""property_tax.py — the yearly property tax, from what the listing itself states.

- France: the taxe foncière in euros, when the advert gives it;
- Italy: IMU on a second home from the rendita catastale: rendita × 1.05 ×
  160 (dwellings) × the comune's rate, which lies between the 0.86% base and
  the 1.06% ceiling (legge 160/2019, art. 1 c. 745-755); the comune sets it;
- Portugal: IMI from the valor patrimonial tributário: VPT × the municipal
  rate, 0.30% to 0.45% for urban property (CIMI art. 112).

Nothing is guessed where the listing says nothing. Shown, not scored.
"""
from __future__ import annotations

import re

_FR_TAX = re.compile(r"taxe\s+fonci[eè]re(?:\s+(?:de\s+)?20\d{2})?[^€\d\n]{0,40}?(\d{1,2}[  .]?\d{3}|\d{2,4})(?:[.,]\d{2})?\s*(?:€|euros?|eur\b)",
                     re.I)
_IT_RENDITA = re.compile(r"rendita(?:\s+catastale)?[^€\d\n]{0,25}?(?:€|euro|eur)?\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:\.\d{2})?)",
                         re.I)
_PT_VPT = re.compile(r"valor\s+(?:patrimonial(?:\s+tribut[áa](?:vel|rio))?|tribut[áa]vel)(?:\s+atual)?(?:\s*\(CIMI\))?"
                     r"\s*(?:de|:)?\s*€?\s*(\d{1,3}(?:[ .]\d{3})*(?:,\d{2})?)", re.I)
IT_RATES = (0.0086, 0.0106)
PT_RATES = (0.0030, 0.0045)


def _text(item: dict) -> str:
    import geo
    raw = geo._raw(item)
    return " ".join(str(x or "") for x in (item.get("description"), raw.get("descricao_completa"),
                                           raw.get("description"), raw.get("descrizione")))


def _number(text: str) -> float | None:
    text = text.replace(" ", " ").strip()
    if "," in text:
        text = text.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        text = text.replace(" ", "").replace(".", "") if re.search(r"[ .]\d{3}$", text) else text
    try:
        return float(text)
    except ValueError:
        return None


def note(item: dict) -> str | None:
    country = (item.get("country") or "PT").upper()
    text = _text(item)
    if country == "FR":
        m = _FR_TAX.search(text)
        # "taxe foncière et 3000 € de charges": the figure belongs to the next cost
        joined = m and re.search(r"\bet\b|\bcharges?\b|\bou\b", m.group(0)[:m.start(1) - m.start()], re.I)
        amount = _number(m.group(1)) if m and not joined else None
        if amount and 30 <= amount <= 20000:
            return f"taxe foncière €{amount:,.0f} a year (the listing)"
    if country == "IT":
        rendite = {_number(m.group(1)) for m in _IT_RENDITA.finditer(text)} - {None}
        rendita = sum(r for r in rendite if 10 <= r <= 20000)
        if rendita:
            base = rendita * 1.05 * 160
            low, high = (base * r for r in IT_RATES)
            return (f"IMU on a second home about €{low:,.0f}–€{high:,.0f} a year (rendita catastale €{rendita:,.2f}; "
                    "the comune sets the rate between 0.86% and 1.06%; none on a main home)")
    if country == "PT":
        m = _PT_VPT.search(text)
        vpt = _number(m.group(1)) if m else None
        if vpt and vpt >= 500:
            low, high = (vpt * r for r in PT_RATES)
            return (f"IMI about €{low:,.0f}–€{high:,.0f} a year (taxable value €{vpt:,.0f}; the câmara sets the "
                    "rate between 0.30% and 0.45%)")
    return None
