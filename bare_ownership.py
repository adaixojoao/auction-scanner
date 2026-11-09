"""Bare ownership / future possession — classify a listing for the Bare ownership goal.

Other goals still skip usufruct and protected tenancies. This module only says
whether a notice is a candidate for that goal, and what it states about holders,
rent and purge. Figures that need a rate or a life table live in costs.py and
life_tables.py; unknown ages are guessed in guesses.py.
"""
from __future__ import annotations

import json
import re

from common import find_terms, has_term, normalize

# (a) Buying the bare ownership while someone else keeps use/habitation.
BARE_TERMS = [
    "nua propriedade", "nua-propriedade", "direito de nua propriedade",
    "nuda propiedad", "nue-propri*", "nuda proprietà", "nuda proprieta",
    "blote eigendom", "blosses eigentum", "bloßes eigentum",
    "propriete grevee d*usufruit", "propriété grevée d*usufruit",
]

# Usufruct / habitation mentioned as a burden on the sale of the property
# (not the right being sold alone).
USUFRUCT_BURDEN = [
    "usufruto", "usufructo", "usufruct", "usufrutto", "usufruit",
    "direito de habitação", "direito de habitacao", "diritto di abitazione",
    "derecho de habitacion", "derecho de habitación",
    "nießbrauch", "niessbrauch", "vruchtgebruik",
]

# (e) The lot is the usufruct / habitation right itself, or only a share of it.
RIGHT_ONLY = [
    "venda do usufruto", "penhora de*usufruto", "penhora do usufruto",
    "cessao de usufruto", "cessão de usufruto",
    "venta del usufructo", "embargo del usufructo",
    "diritto di usufrutto", "quota*usufrutto", "vendita dell*usufrutto",
    "usufruto de ruina", "usufruto de ruína", "usufruto -", "usufruto no predio",
    "usufruto no prédio", "usufruto em imovel", "usufruto em imóvel",
    "usufruto - terreno", "usufruto-terreno",
]

# (b) Explicit purge / caducidade on forced sale — never inferred.
# Do not match a bare "caducidade" (that word also appears in "nada diz sobre
# caducidade" and in planning "projecto caducado").
PURGE_TERMS = [
    "caduca com", "caduca nos termos", "caduca na venda", "caduca com a venda",
    "ônus caduca", "onus caduca", "usufruto caduca", "art*824*",
    "cancelacion de cargas posteriores", "cancelación de cargas posteriores",
    "cancelacion de cargas", "cancelación de cargas", "art*674* lec",
    "cancellazione delle iscrizioni", "decreto di trasferimento", "art*586* cpc",
    "purge des hypotheques", "purge des hypothèques", "purge hypothecaire",
]

# (c) Old / protected tenancy with rent.
OLD_RENT_TERMS = [
    "renda antiga", "nrau", "contrato de arrendamento anterior",
    "arrendamento anterior a 1990", "arrendado pelo valor",
    "renta antigua", "lau 1964", "contrato de renta antigua",
    "equo canone", "canone equilibrato", "contratto*equo canone",
    "loi de 1948", "loyer 1948",
]

# Joint / successive usufruct — wait for the last survivor.
JOINT_TERMS = [
    "simultaneo e sucessivo", "simultâneo e sucessivo",
    "usufruto conjunto", "usufruto sucessivo",
    "congiunto", "usufrutto congiunto", "successivo",
    "reversion", "réversion", "au profit du survivant",
    "ultimo sobrevivente", "último sobrevivente", "ultimo superstite",
]

VIAGER_OCCUPIED = ["viager occupe", "viager occupé", "viager avec réserve"]
VIAGER_FREE = ["viager libre"]

_AGE_YEARS = re.compile(
    r"\b(\d{2})\s*anni\b|\bedad(?:\s+de)?\s+(\d{2})\b|\bidade(?:\s+de)?\s+(\d{2})\b"
    r"|\b(\d{2})\s*anos?\b|\b(\d{2})\s*years?\s*old\b",
    re.I,
)
_BIRTH = re.compile(
    r"\bnascid[oa]\s+(?:em\s+)?(\d{4})\b|\bnato\s+(?:nel\s+)?(\d{4})\b"
    r"|\bnacido\s+(?:en\s+)?(\d{4})\b|\bné(?:e)?\s+en\s+(\d{4})\b",
    re.I,
)
_SEX_F = re.compile(r"\b(mulher|senhora|esposa|coniuge femminile|moglie|mujer|épouse|madame)\b", re.I)
_SEX_M = re.compile(r"\b(homem|senhor|esposo|marito|coniuge maschile|hombre|époux|monsieur)\b", re.I)
_RENT = re.compile(
    r"(?:arrendado|renda|rent|alquiler|canone|loyer)\D{0,40}?"
    r"(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{1,2})?)\s*€?(?:\s*/\s*|\s+)?(?:mês|mes|mese|mois|month)?",
    re.I,
)
_CONTRACT_YEAR = re.compile(
    r"(?:contrato|contract|arrendamento|arrendamiento).{0,40}\b(19\d{2}|20[0-2]\d)\b",
    re.I,
)


def listing_text(item: dict) -> str:
    """Title, description and the stored detail/perizia words the classifier may read."""
    parts = [item.get("title") or "", item.get("description") or ""]
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    if isinstance(raw, dict):
        for key in ("onus", "encargos", "occupation", "posesion", "disponibilita",
                    "page_text", "omschrijving", "edict", "edicto"):
            val = raw.get(key)
            if isinstance(val, str):
                parts.append(val)
        per = raw.get("perizia")
        if isinstance(per, dict):
            for key in ("text", "summary", "page_occupation", "occupation"):
                val = per.get(key)
                if isinstance(val, str):
                    parts.append(val)
        elif isinstance(per, str):
            parts.append(per)
    return "\n".join(parts)


def _parse_rent(text: str) -> float | None:
    m = _RENT.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return None
    return val if 20 <= val <= 5000 else None


def extract_holders(text: str, *, now_year: int = 2026) -> list[dict]:
    """Ages (and sex when the text says) of usufructuaries / tenants named in the notice."""
    holders = []
    for m in _AGE_YEARS.finditer(text or ""):
        age = next(int(g) for g in m.groups() if g)
        if 40 <= age <= 110:
            holders.append({"age": age, "sex": None, "basis": m.group(0)})
    for m in _BIRTH.finditer(text or ""):
        year = next(int(g) for g in m.groups() if g)
        age = now_year - year
        if 40 <= age <= 110:
            holders.append({"age": age, "sex": None, "basis": m.group(0)})
    # Sex from nearby words — best-effort, optional.
    blob = normalize(text or "")
    sex = "F" if _SEX_F.search(blob) and not _SEX_M.search(blob) else (
        "M" if _SEX_M.search(blob) and not _SEX_F.search(blob) else None)
    if sex and holders and holders[0].get("sex") is None:
        holders[0]["sex"] = sex
    # Deduplicate identical ages.
    seen, out = set(), []
    for h in holders:
        key = h["age"]
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def classify(item: dict, *, now_year: int = 2026) -> dict:
    """What this listing is for the Bare ownership goal.

    Returns kind in:
      bare | purge | old_rent | viager_occupe | skip
    plus holders, rent, flags. Never invents a purge.
    """
    text = listing_text(item)
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}

    hits_bare = find_terms(text, BARE_TERMS) or (
        ["nuda_propiedad"] if raw.get("nuda_propiedad") else [])
    hits_burden = find_terms(text, USUFRUCT_BURDEN)
    hits_right = find_terms(text, RIGHT_ONLY)
    hits_purge = find_terms(text, PURGE_TERMS)
    hits_rent = find_terms(text, OLD_RENT_TERMS)
    joint = bool(find_terms(text, JOINT_TERMS))
    rent = _parse_rent(text)
    contract = None
    cm = _CONTRACT_YEAR.search(text or "")
    if cm:
        contract = int(cm.group(1))
    holders = extract_holders(text, now_year=now_year)

    # (e) Selling only the usufruct / habitation / a share of it.
    title = item.get("title") or ""
    if hits_right or (
        has_term(title, ["usufruto", "usufructo", "usufrutto", "usufruct"])
        and not hits_bare
        and not has_term(title, BARE_TERMS)
    ):
        return {
            "kind": "skip",
            "why": "the lot is the usufruct or habitation right itself, not the bare ownership of the home",
            "holders": holders, "rent_eur_month": rent, "contract_year": contract,
            "joint_survivor": joint, "purge": False, "terms": hits_right or hits_burden,
        }

    # Fractional bare ownership of a garage / tiny share — still skip as not the home.
    from scoring import is_fractional_share, is_percent_share, is_co_ownership
    if is_fractional_share(title) or is_percent_share(text) or is_co_ownership(text):
        if hits_bare or hits_burden:
            return {
                "kind": "skip",
                "why": "only a share of the bare ownership / usufruct, not the whole home",
                "holders": holders, "rent_eur_month": rent, "contract_year": contract,
                "joint_survivor": joint, "purge": False, "terms": hits_bare or hits_burden,
            }

    if has_term(text, VIAGER_FREE):
        return {
            "kind": "skip", "why": "viager libre — no wait for possession",
            "holders": holders, "rent_eur_month": rent, "contract_year": contract,
            "joint_survivor": joint, "purge": False, "terms": ["viager libre"],
        }
    if has_term(text, VIAGER_OCCUPIED) or (
        has_term(text, ["viager"]) and not has_term(text, VIAGER_FREE)
    ):
        return {
            "kind": "viager_occupe",
            "why": "viager occupé — bare ownership plus a life annuity (phase 2)",
            "holders": holders, "rent_eur_month": rent, "contract_year": contract,
            "joint_survivor": joint, "purge": False, "terms": ["viager"],
        }

    # (b) Explicit purge beats a continuing usufruct for possession timing.
    if hits_purge and (hits_bare or hits_burden or has_term(text, ["ocupado", "arrendado", "affittato"])):
        return {
            "kind": "purge",
            "why": f"notice states the burden is purged / caduca on sale ({hits_purge[0]})",
            "holders": holders, "rent_eur_month": rent, "contract_year": contract,
            "joint_survivor": joint, "purge": True, "terms": hits_purge,
        }

    # (a) Bare ownership with third-party use.
    if hits_bare or raw.get("nuda_propiedad"):
        return {
            "kind": "bare",
            "why": f"bare ownership with use kept by another ({(hits_bare or ['nuda_propiedad'])[0]})",
            "holders": holders, "rent_eur_month": rent, "contract_year": contract,
            "joint_survivor": joint, "purge": False, "terms": hits_bare or ["nuda_propiedad"],
        }

    # Usufruct burden on an otherwise full sale of the home.
    if hits_burden and not hits_right:
        from scoring import property_kind
        if property_kind(item) == "home":
            return {
                "kind": "bare",
                "why": f"home sold subject to usufruct / habitation ({hits_burden[0]})",
                "holders": holders, "rent_eur_month": rent, "contract_year": contract,
                "joint_survivor": joint, "purge": False, "terms": hits_burden,
            }

    # (c) Protected / old rent with a stated rent, or pre-1951 building + rent.
    if hits_rent or (rent and has_term(text, ["arrendado", "arrendada", "affittato", "alquiler"])):
        pre1951 = has_term(text, ["anteriormente a 1951", "antes de 1951", "anterior a 1951",
                                  "matriz anteriormente a 1951"])
        if hits_rent or pre1951 or (rent is not None and rent < 250):
            return {
                "kind": "old_rent",
                "why": "tenanted home with a low or protected rent — possession later",
                "holders": holders, "rent_eur_month": rent, "contract_year": contract,
                "joint_survivor": joint, "purge": False,
                "terms": hits_rent or (["pre-1951"] if pre1951 else ["arrendado"]),
            }

    return {
        "kind": "skip",
        "why": "not bare ownership, purged possession or a protected tenancy",
        "holders": holders, "rent_eur_month": rent, "contract_year": contract,
        "joint_survivor": joint, "purge": False, "terms": [],
    }


def present_value(*, free_value: float, wait_years: float, discount: float,
                  rent_eur_year: float = 0.0) -> float:
    """Free value discounted for the wait, plus rent received until then (estimate)."""
    if free_value <= 0 or wait_years < 0:
        return 0.0
    r = max(0.0, float(discount))
    if r == 0:
        return free_value + rent_eur_year * wait_years
    factor = (1.0 + r) ** wait_years
    pv_free = free_value / factor
    if rent_eur_year <= 0:
        return pv_free
    # Annuity of rent for wait_years.
    pv_rent = rent_eur_year * (1.0 - 1.0 / factor) / r
    return pv_free + pv_rent


def irr_estimate(*, pay: float, free_value: float, wait_years: float,
                 rent_eur_year: float = 0.0) -> float | None:
    """Rough annual IRR: pay now, collect rent, get free_value at wait_years."""
    if pay <= 0 or free_value <= 0 or wait_years <= 0:
        return None
    # Binary search r such that PV(inflows)=pay.
    lo, hi = -0.5, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        pv = present_value(free_value=free_value, wait_years=wait_years,
                           discount=mid, rent_eur_year=rent_eur_year)
        if pv > pay:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)
