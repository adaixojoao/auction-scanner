"""checklist.py — the due-diligence checklist for a sale, by country and how it is sold.

Which checklist a listing gets follows letters.py's source groups (route_of),
so there is one idea of "how this sale works". Each item is yours to set:
not started, requested, verified, not applicable, or a concern. Nothing here
says an item is legally verified: "verified" means you checked it, and the
AI check can only suggest (it never writes a status). The labels say what to
check, hedged, and who to ask; they are not legal advice.

Blocking items (the land registry and charges, by default) must be verified
or not applicable before an offer is sent or logged, or you give a reason,
which is kept (checklist_log). Settings → Checklist changes which items block.
"""
from __future__ import annotations

from dataclasses import dataclass

from common import utcnow_iso

STATUSES = ("not_started", "requested", "verified", "not_applicable", "concern")
STATUS_LABELS = {"not_started": "Not started", "requested": "Requested", "verified": "Verified by you",
                 "not_applicable": "Not applicable", "concern": "Concern"}
DONE = {"verified", "not_applicable"}
MIN_OVERRIDE_REASON = 5
CATEGORIES = [
    ("identity", "The property and where it is"),
    ("title", "Title, charges and debts"),
    ("occupancy", "Who lives there"),
    ("terms", "Sale terms, deposit, deadline and how to submit"),
    ("costs", "Taxes, fees and all-in cost"),
    ("condition", "Condition, visit and documents"),
    ("land", "Rural land"),
    ("climate", "Climate and location"),
]


@dataclass(frozen=True)
class Item:
    key: str            # stored per listing: never rename
    category: str
    label: str
    blocking: bool = False
    rural_only: bool = False


def _i(key, category, label, blocking=False):
    return Item(key, category, label, blocking)


_IDENTITY = [
    _i("identity", "identity", "The listing, the sale notice and the land registry describe the same property "
                               "(address, area, parts)"),
    _i("location", "identity", "Location confirmed (Listings → ⓘ → Verify location)"),
]
_TAIL = [
    _i("all_in", "costs", "All-in cost worked out: price, taxes, fees and work (the estimate on Listings → ⓘ "
                          "is only a start)"),
    Item("land_access", "land", "Access by a public road, or a registered right of way", rural_only=True),
    Item("land_boundaries", "land", "Boundaries and area match the registry and the map", rural_only=True),
    Item("land_use", "land", "Land classification known: what may be built or farmed (ask the municipality)",
         rural_only=True),
    Item("land_water", "land", "Water rights or restrictions known (wells, springs, streams, irrigation)",
         rural_only=True),
    Item("land_hazards", "land", "Wildfire and flood constraints known (ask the municipality what applies)",
         rural_only=True),
    _i("climate", "climate", "Climate panel read (heat, flood, water, fire), with its location level in mind"),
]


def _route(label, items):
    return label, _IDENTITY + items + _TAIL


TEMPLATES: dict[str, tuple[str, list[Item]]] = {
    "pt_court": _route("Portugal: court sale (Citius)", [
        _i("registry", "title", "Checked the land registry (certidão permanente, conservatória)", True),
        _i("charges", "title", "Know whether the mortgage and other charges are cancelled by the sale "
                               "(ask the agente de execução)", True),
        _i("debts", "costs", "IMI and condominium debts known"),
        _i("occupancy", "occupancy", "Occupancy known (vacant, tenants, or the owner still living there)"),
        _i("terms", "terms", "Sale type, deadline and where the offer goes known (the edital; ask the agente "
                             "de execução)"),
        _i("minimum", "terms", "Offer respects the minimum in the notice (in carta fechada normally 85% of "
                               "the valor base)"),
        _i("deposit", "terms", "Deposit or guarantee the notice asks for is ready"),
        _i("tax_record", "condition", "Checked the tax record (caderneta predial)"),
        _i("visit", "condition", "Visited the property, or seen recent photos"),
    ]),
    "pt_eleiloes": _route("Portugal: online auction (e-leiloes.pt)", [
        _i("registry", "title", "Checked the land registry (certidão permanente, conservatória)", True),
        _i("charges", "title", "Know whether the mortgage and other charges are cancelled by the sale "
                               "(ask the agente de execução)", True),
        _i("debts", "costs", "IMI and condominium debts known"),
        _i("occupancy", "occupancy", "Occupancy known (vacant, tenants, or the owner still living there)"),
        _i("account", "terms", "Can bid on e-leiloes.pt (Cartão de Cidadão or Chave Móvel Digital)"),
        _i("terms", "terms", "Closing date and the deposit the notice asks for known"),
        _i("tax_record", "condition", "Checked the tax record (caderneta predial)"),
        _i("visit", "condition", "Visited the property, or seen recent photos"),
    ]),
    "pt_bank": _route("Portugal: bank-owned (negotiation)", [
        _i("registry", "title", "Checked the land registry (certidão permanente, conservatória)", True),
        _i("charges", "title", "Seller confirms in writing that it is sold free of charges", True),
        _i("debts", "costs", "IMI and condominium debts known, and who pays them"),
        _i("occupancy", "occupancy", "Occupancy known (vacant or tenanted)"),
        _i("terms", "terms", "Know how the seller wants offers (form, e-mail, agent) and how long they stay valid"),
        _i("financing", "terms", "Financing ready, or the seller's financing conditions known"),
        _i("tax_record", "condition", "Checked the tax record (caderneta predial) and the licence of use"),
        _i("visit", "condition", "Visited the property, or seen recent photos"),
    ]),
    "es_boe": _route("Spain: court or tax auction (subastas.boe.es)", [
        _i("registry", "title", "Checked the land registry (nota simple)", True),
        _i("charges", "title", "Know which charges (cargas) survive the sale", True),
        _i("debts", "costs", "Community fees and IBI debts known"),
        _i("occupancy", "occupancy", "Occupancy known (situación posesoria)"),
        _i("account", "terms", "Digital certificate or Cl@ve ready for subastas.boe.es"),
        _i("deposit", "terms", "Deposit ready (5% of the auction value in court auctions) / financing ready"),
        _i("visit", "condition", "Visited the property, or seen recent photos"),
    ]),
    "es_bank": _route("Spain: bank or servicer (negotiation)", [
        _i("registry", "title", "Checked the land registry (nota simple)", True),
        _i("charges", "title", "Seller confirms in writing which charges remain", True),
        _i("debts", "costs", "Community fees and IBI debts known, and who pays them"),
        _i("occupancy", "occupancy", "Occupancy known (situación posesoria)"),
        _i("terms", "terms", "Know how the seller wants offers and how long they stay valid"),
        _i("financing", "terms", "Financing ready"),
        _i("visit", "condition", "Visited the property, or seen recent photos"),
    ]),
    "fr_court": _route("France: court sale (tribunal judiciaire)", [
        _i("charges", "title", "Know which charges or rights remain after the sale (ask the lawyer)", True),
        _i("occupancy", "occupancy", "Occupancy known (libre or occupé)"),
        _i("terms", "terms", "Read the cahier des conditions de vente"),
        _i("lawyer", "terms", "A lawyer at that court has agreed to bid for you"),
        _i("deposit", "terms", "Bank cheque for 10% of the mise à prix (min. €3,000) ready"),
        _i("fees", "costs", "Budget includes the sale fees and transfer taxes (ask the lawyer for the figure)"),
        _i("visit", "condition", "Visited the property (visits are set by the lawyer)"),
    ]),
    "de_zvg": _route("Germany: forced auction (Zwangsversteigerung)", [
        _i("charges", "title", "Know which land-register rights survive the sale (bestehen bleibende Rechte)",
           True),
        _i("occupancy", "occupancy", "Occupancy known (vermietet, bewohnt or leer)"),
        _i("deposit", "terms", "Security (10% of the Verkehrswert) transferred in time, or certified cheque ready"),
        _i("id_ready", "terms", "ID ready (and a certified power of attorney if someone bids for you)"),
        _i("fees", "costs", "Budget includes property transfer tax and court fees"),
        _i("report", "condition", "Read the valuation report (Gutachten)"),
    ]),
    "it_court": _route("Italy: court sale (professionista delegato)", [
        _i("charges", "title", "Know which charges the perizia lists and whether the sale cancels them "
                               "(ask the delegato)", True),
        _i("occupancy", "occupancy", "Occupancy and date of vacating known"),
        _i("terms", "terms", "Read the avviso di vendita and the perizia"),
        _i("deposit", "terms", "Deposit (cauzione) ready as the notice requires"),
        _i("minimum", "terms", "Offer at or above the offerta minima"),
        _i("debts", "costs", "Condominium arrears known (the buyer can owe the current and previous year)"),
        _i("visit", "condition", "Visited with the custode"),
    ]),
    "nl_notary": _route("Netherlands: execution auction (executieveiling)", [
        _i("charges", "title", "Know which rights or charges remain (veilingvoorwaarden, land registry)", True),
        _i("occupancy", "occupancy", "Know whether tenants stay"),
        _i("terms", "terms", "Read the veilingvoorwaarden"),
        _i("account", "terms", "Registered and identified for the auction"),
        _i("financing", "terms", "Financing or bank guarantee ready"),
        _i("fees", "costs", "Budget includes transfer tax and notary costs"),
        _i("visit", "condition", "Viewed the property, or accept buying it unseen"),
    ]),
    "generic": _route("Other sales (no specific checklist yet)", [
        _i("registry", "title", "Checked the land registry", True),
        _i("charges", "title", "Know which charges and debts remain after the sale (ask the authority or a "
                               "local lawyer)", True),
        _i("occupancy", "occupancy", "Occupancy known"),
        _i("terms", "terms", "Know how offers must be submitted, and by when"),
        _i("visit", "condition", "Visited the property, or seen recent photos"),
    ]),
}


def route_of(item: dict) -> str:
    """Which checklist a listing gets: letters.py's source groups."""
    from letters import (DE_COURT_SOURCES, ES_COURT_SOURCES, ES_SERVICER_SOURCES, FR_COURT_SOURCES,
                         IT_COURT_SOURCES, NL_NOTARY_SOURCES, PT_BANK_SOURCES, PT_COURT_SOURCES)
    src = item.get("source") or ""
    for sources, route in ((PT_COURT_SOURCES, "pt_court"), ({"eleiloes"}, "pt_eleiloes"),
                           (PT_BANK_SOURCES, "pt_bank"), (ES_COURT_SOURCES, "es_boe"),
                           (ES_SERVICER_SOURCES, "es_bank"), (FR_COURT_SOURCES, "fr_court"),
                           (DE_COURT_SOURCES, "de_zvg"), (IT_COURT_SOURCES, "it_court"),
                           (NL_NOTARY_SOURCES, "nl_notary")):
        if src in sources:
            return route
    return "generic"


def default_blocking(route: str) -> list[str]:
    return [it.key for it in TEMPLATES[route][1] if it.blocking]


def blocking_keys(route: str, cfg: dict | None) -> set[str]:
    """Settings → Checklist, else the template's own."""
    chosen = (((cfg or {}).get("checklist") or {}).get("blocking") or {}).get(route)
    keys = {it.key for it in TEMPLATES[route][1]}
    return set(chosen) & keys if isinstance(chosen, list) else set(default_blocking(route))


def items_for(item: dict) -> tuple[str, list[Item]]:
    route = route_of(item)
    kind = item.get("kind")
    if kind is None:
        from scoring import property_kind
        kind = property_kind(item)
    return route, [it for it in TEMPLATES[route][1] if not it.rural_only or kind == "rural_plot"]


def stored(db, listing_id: str) -> dict[str, dict]:
    rows = db.execute("SELECT * FROM checklist_items WHERE listing_id = ?", (listing_id,)).fetchall()
    return {r["item_key"]: dict(r) for r in rows}


def stored_all(db) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in db.execute("SELECT * FROM checklist_items"):
        out.setdefault(r["listing_id"], {})[r["item_key"]] = dict(r)
    return out


def build(item: dict, rows: dict[str, dict], cfg: dict | None) -> dict:
    """The checklist of one listing with your statuses, and its summary."""
    route, items = items_for(item)
    blocking = blocking_keys(route, cfg)
    out = []
    for it in items:
        row = rows.get(it.key) or {}
        out.append({"key": it.key, "category": it.category, "label": it.label, "blocking": it.key in blocking,
                    "status": row.get("status") or "not_started", "notes": row.get("notes") or "",
                    "reference": row.get("reference") or "", "checked_on": row.get("checked_on") or "",
                    "verified_by": row.get("verified_by") or "", "updated_at": row.get("updated_at") or ""})
    done = sum(1 for x in out if x["status"] in DONE)
    left = [x["key"] for x in out if x["blocking"] and x["status"] not in DONE]
    concerns = [x["key"] for x in out if x["status"] == "concern"]
    summary = (f"{done}/{len(out)} complete; "
               + (f"{len(left)} blocking item{'s' if len(left) != 1 else ''} remain{'s' if len(left) == 1 else ''}"
                  if left else "nothing blocking")
               + (f"; {len(concerns)} concern{'s' if len(concerns) != 1 else ''}" if concerns else "") + ".")
    return {"route": route, "route_label": TEMPLATES[route][0], "categories": CATEGORIES, "items": out,
            "done": done, "total": len(out), "blocking_left": left, "concerns": concerns, "summary": summary}


def set_status(db, item: dict, key: str, status: str, *, notes: str = "", reference: str = "",
               checked_on: str = "", verified_by: str = "") -> None:
    """Your status for one item, logged with the one it replaces. ValueError
    for an item this listing's checklist does not have, or an unknown status."""
    _route, items = items_for(item)
    if key not in {it.key for it in items}:
        raise ValueError(f"no checklist item {key!r} for this listing")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    old = stored(db, item["id"]).get(key) or {}
    now = utcnow_iso()
    db.execute("""
        INSERT INTO checklist_items (listing_id, item_key, status, notes, reference, checked_on, verified_by,
                                     updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(listing_id, item_key) DO UPDATE SET status=excluded.status, notes=excluded.notes,
            reference=excluded.reference, checked_on=excluded.checked_on, verified_by=excluded.verified_by,
            updated_at=excluded.updated_at""",
               (item["id"], key, status, notes[:2000], reference[:500], checked_on[:10], verified_by[:100], now))
    db.execute("INSERT INTO checklist_log (listing_id, item_key, action, old_status, new_status, created_at) "
               "VALUES (?,?,?,?,?,?)", (item["id"], key, "status", old.get("status"), status, now))
    db.commit()


def record_offer(db, listing_id: str, carta_log_id: int, summary: str, reason: str | None) -> None:
    """With the offer: the checklist as it was, and your reason if it was sent with blocking items left."""
    db.execute("UPDATE carta_log SET checklist_summary = ? WHERE id = ?", (summary, carta_log_id))
    if reason:
        db.execute("INSERT INTO checklist_log (listing_id, carta_log_id, action, reason, created_at) "
                   "VALUES (?,?,?,?,?)", (listing_id, carta_log_id, "override", reason, utcnow_iso()))
    db.commit()


def history(db, listing_id: str) -> list[dict]:
    rows = db.execute("SELECT item_key, carta_log_id, action, old_status, new_status, reason, created_at "
                      "FROM checklist_log WHERE listing_id = ? ORDER BY created_at DESC, id DESC",
                      (listing_id,)).fetchall()
    return [dict(r) for r in rows]


def as_text(item: dict, checklist: dict) -> str:
    """The printable summary (no blank lines: text_pdf would read blank-line
    blocks as a letter's parts)."""
    lines = [f"Due-diligence checklist: {item.get('title') or item['id']}"[:200],
             f"{checklist['route_label']} · {item['id']} · printed {utcnow_iso()[:10]}",
             checklist["summary"],
             "Your own record of what you checked. It is not legal advice and does not replace a local lawyer."]
    by_cat = {}
    for x in checklist["items"]:
        by_cat.setdefault(x["category"], []).append(x)
    for cat, title in CATEGORIES:
        if cat not in by_cat:
            continue
        lines.append(title.upper())
        for x in by_cat[cat]:
            mark = "[x]" if x["status"] in DONE else "[!]" if x["status"] == "concern" else "[ ]"
            extra = " · ".join(v for v in (STATUS_LABELS[x["status"]], x["checked_on"],
                                           f"by {x['verified_by']}" if x["verified_by"] else "",
                                           "blocking" if x["blocking"] else "") if v)
            lines.append(f"{mark} {x['label']} ({extra})")
            for v in (x["notes"], x["reference"]):
                if v:
                    lines.append(f"      {v}")
    return "\n".join(lines)
