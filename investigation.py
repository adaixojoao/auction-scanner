"""investigation.py — what is still unknown about a promising listing, and what to find out next.

Read-only and display-only, over the modules that own each fact; nothing here
scores, prices, verifies, hides or bids:

- Price interpretation: presentation.price (costs.selected_acquisition).
- Sale availability: db._availability_evidence and evaluator's sale_availability
  rule (the stored link check and the owner's availability_valid_hours).
- Whole ownership: evaluator's full_ownership rule on a resolved input, and
  the scorer's own share / usufruct / timeshare / co-ownership word lists.
- Title, possession, lawful access: presentation.checks over checklist rows
  (checklist._effective_row), so a "verified" whose bound inputs changed reads
  as needing a recheck, never as verified. What the notice states is shown as
  source-reported, never as verification.
- Intended use: the goal the listing is assessed for (the Listings tab, which
  the owner chose), with the checklist's land-use record where one exists.
- Project costs: presentation.all_in (costs.selected_planning).

Ownership, title, possession, lawful access and intended use use evidence
states (verified, unresolved, concern, not_applicable). Price, availability
and costs keep their own statuses: there is no combined confidence score, and
`purchase_readiness_established` is always False — no record here can
establish that a property is ready to buy.

The next-action order (ACTION_ORDER) is a research workflow recommendation,
not a legal or financial rule: settle what could rule the property out before
paying for anything, then what is actually for sale and whether it still is,
then the rights, then the use, then the money.
"""
from __future__ import annotations

from datetime import datetime, timezone

EVIDENCE_STATES = ("verified", "unresolved", "concern", "not_applicable")
# Price, availability and costs keep their own `status`; their `state` is only
# whether they leave a question open ("established", "unresolved", "concern").
OWN_STATUS = ("price_interpretation", "sale_availability", "project_costs")

LABELS = {
    "price_interpretation": "Price interpretation",
    "sale_availability": "Sale availability",
    "whole_ownership": "Whole ownership",
    "title": "Title and charges",
    "possession": "Possession",
    "lawful_access": "Lawful access",
    "intended_use": "Intended use",
    "project_costs": "Project costs",
}
DIMENSION_ORDER = tuple(LABELS)

# The goal each Listings tab assesses for (scoring.MODES), as the intended use.
PURPOSES = {
    "home": "a home to live in",
    "invest": "a home to let or resell",
    "land": "a plot as an investment",
    "forest": "a forestry project on a plot",
    "both": "a home to live in that also holds its value (Live and invest)",
}

# Research workflow order (module docstring); lower runs first.
ACTION_ORDER = {
    "resolve_concern": 1,
    "clarify_price": 2, "reconfirm_availability": 2, "confirm_interest_sold": 2,
    "check_title": 3, "establish_possession": 3, "establish_legal_access": 3,
    "establish_permitted_use": 4,
    "obtain_cost_evidence": 5,
}
ACTION_WHY = {
    1: "A recorded concern or contradiction could rule the property out; settle it before paying for anything.",
    2: "What is actually for sale, at what figure, and whether it still is, decides whether more work is worth it.",
    3: "Ownership, possession and lawful access are conditions of any acquisition and remain unresolved.",
    4: "Whether the property may be used for the goal it is assessed for is not yet established.",
    5: "Without these costs the all-in figure is incomplete; an unknown cost is not zero.",
}

_LOCATION_LIMIT = "A verified map position does not establish title, possession, access or what may be built."


def _ref_checklist(x: dict) -> dict:
    return {"type": "checklist", "key": x["key"], "label": x["label"], "status": x["status"],
            "recorded_status": x.get("recorded_status"), "needs_recheck": bool(x.get("needs_recheck")),
            "checked_on": x.get("checked_on") or None, "updated_at": x.get("updated_at") or None}


def _dimension(key: str, state: str, summary: str, *, refs=None, limitations=None,
               source_reported: bool = False, status: str | None = None) -> dict:
    return {"key": key, "label": LABELS[key], "state": state, "status": status or state, "summary": summary,
            "source_reported": source_reported, "evidence_refs": refs or [], "limitations": limitations or []}


def _from_check(key: str, area: dict | None, items: list, *, missing: str, limitations: list) -> dict:
    """An evidence dimension from presentation.checks' state for a checklist area."""
    refs = [_ref_checklist(x) for x in items]
    if area is None:
        return _dimension(key, "unresolved", missing, refs=refs, limitations=limitations)
    state = area["state"]
    if state in ("verified", "not_applicable", "concern"):
        out = _dimension(key, state, area["text"], refs=refs, limitations=limitations)
        if state == "verified":
            out["limitations"] = limitations + ["Verified by you in the checklist: your record of what you "
                                                "checked, not independent legal verification."]
        return out
    if state == "needs_recheck":
        return _dimension(key, "unresolved", area["text"], refs=refs,
                          limitations=limitations + ["An earlier verification no longer matches the listing's "
                                                     "current details, so it is not current verification."])
    return _dimension(key, "unresolved", area["text"], refs=refs, limitations=limitations,
                      source_reported=state == "source_reported")


def _ownership(item: dict, evaluated) -> dict:
    from scoring import (USUFRUCT_PATTERNS, _raw, has_term, is_co_ownership, is_fractional_share,
                         is_percent_share, is_timeshare)
    title = item.get("title") or ""
    full = f"{title} {item.get('description') or ''}"
    raw = _raw(item)
    found = None
    if raw.get("proindiviso") or is_fractional_share(title) or is_percent_share(full):
        found = "a share of the property"
    elif raw.get("nuda_propiedad") or has_term(full, USUFRUCT_PATTERNS):
        found = "a usufruct or bare ownership"
    elif is_timeshare(full):
        found = "a timeshare"
    elif is_co_ownership(full):
        found = "co-ownership with others"
    refs = [{"type": "listing_text", "fields": ["title", "description"]}]
    limits = ["The listing's own words are a source claim; the interest sold is set by the sale notice and "
              "the registry."]
    if found:
        return _dimension("whole_ownership", "concern", f"The listing's words describe {found}, not the whole "
                          "property.", refs=refs, limitations=limits, source_reported=True)
    req = evaluated.get("full_ownership")
    if req is not None and req.outcome == "fail":
        return _dimension("whole_ownership", "concern", req.message,
                          refs=[{"type": "evaluator", "code": req.code}], limitations=limits)
    note = ("No record establishes that the whole property is being sold. The listing names no share, "
            "usufruct or timeshare, which is not proof of full ownership.")
    if req is not None and req.outcome == "pass":
        note = req.message
    return _dimension("whole_ownership", "unresolved", note, refs=refs, limitations=limits)


def _availability(item: dict, evaluated, deadline: dict) -> dict:
    from db import _availability_evidence
    ev = _availability_evidence(item, _valid_hours())
    req = evaluated["sale_availability"]
    refs = []
    if ev.get("sale_availability") is not None:
        refs.append({"type": "link_check", "state": ev.get("sale_availability"),
                     "checked_at": ev.get("sale_availability_checked_at"),
                     "expires_at": ev.get("sale_availability_expires_at")})
    limits = ["An open sale page is not proof the sale is still open; availability is current only within the "
              "validity window set under Settings."]
    if deadline.get("precision") == "date":
        limits.append("The deadline is a date only: the closing time is not published.")
    if req.outcome == "pass":
        status, state = "current", "established"
    elif req.outcome == "fail":
        status, state = "ended", "concern"
    else:
        status = "expired" if req.code == "DATA_AVAILABILITY_EXPIRED" else "unresolved"
        state = "unresolved"
    return _dimension("sale_availability", state, req.message, refs=refs, limitations=limits, status=status)


def _price(basis: dict) -> dict:
    kind = basis.get("kind")
    refs = [{"type": "listing_fields", "fields": ["price", "current_bid", "min_price"]}]
    if kind in ("unusable", "unpublished"):
        return _dimension("price_interpretation", "unresolved", f"{basis.get('label')}: {basis.get('note')}".strip(": "),
                          refs=refs, status=kind)
    limits = [basis.get("note")] if basis.get("note") else []
    return _dimension("price_interpretation", "established", f"{basis.get('label')}: the figure the listing gives",
                      refs=refs, limitations=limits, status=kind or "unknown")


def _costs(estimate: dict, built: dict) -> dict:
    refs = [_ref_checklist(x) for x in built["items"] if x["key"] in ("all_in", "fees", "debts")]
    limits = list(estimate.get("excludes") and [f"No estimate includes: {', '.join(estimate['excludes'])}."] or [])
    if estimate.get("headline"):
        return _dimension("project_costs", "unresolved", estimate["headline"], refs=refs, limitations=limits,
                          status="not_established")
    missing = estimate.get("missing") or []
    if not estimate.get("repairs_priced") or missing:
        text = "Incomplete estimate: " + ("; ".join(missing) if missing else "repairs not priced")
        return _dimension("project_costs", "unresolved", text, refs=refs, limitations=limits, status="incomplete")
    return _dimension("project_costs", "unresolved",
                      "Estimated from the listing; no dated quote or technical review recorded",
                      refs=refs, limitations=limits + ["Every figure is an estimate (costs.py)."], status="estimated")


def _intended_use(item: dict, mode: str, built: dict) -> dict:
    purpose = PURPOSES.get(mode)
    if not purpose:
        return _dimension("intended_use", "unresolved", "No intended use is set for this assessment.")
    land = [x for x in built["items"] if x["key"] == "land_use"]
    refs = [_ref_checklist(x) for x in land]
    limits = [f"Assessed for {purpose} (the Listings goal). Whether that use is permitted depends on local rules "
              "the scanner does not hold."]
    if land:
        x = land[0]
        if x["status"] == "concern":
            return _dimension("intended_use", "concern", "Concern recorded about the land classification",
                              refs=refs, limitations=limits)
        if x["status"] in ("verified", "not_applicable"):
            return _dimension("intended_use", x["status"], "Land classification recorded by you",
                              refs=refs, limitations=limits)
        if x.get("needs_recheck"):
            return _dimension("intended_use", "unresolved", "Land classification needs rechecking: listing "
                              "inputs changed", refs=refs, limitations=limits)
    return _dimension("intended_use", "unresolved", f"Not established that the property may be used as "
                      f"{purpose}.", refs=refs, limitations=limits)


def _contradictions(item: dict, dims: dict, built: dict) -> list[str]:
    """Visible conflicts between records; nothing is reconciled here."""
    from scoring import _occupation
    out = []
    occupancy_done = [x for x in built["items"] if x["category"] == "occupancy" and x["status"] == "verified"]
    reported = _occupation(item)
    if occupancy_done and reported == "occupied":
        out.append("Possession is checked by you, but the sale reports the property as occupied: review which "
                   "is current.")
    if dims["whole_ownership"]["state"] == "concern" and dims["title"]["state"] == "verified":
        out.append("Title is checked by you, but the listing's words describe less than the whole property: "
                   "review the interest sold.")
    if dims["sale_availability"]["status"] == "ended" and any(
            x["key"] == "terms" and x["status"] == "verified" for x in built["items"]):
        out.append("Sale terms are checked by you, but the sale page reports the sale as ended or reserved.")
    return out


def _references(item: dict) -> list[dict]:
    """Reference numbers the listing actually carries (never made up)."""
    from scoring import _raw
    raw = _raw(item)
    out = [{"label": "Listing", "value": item["id"]}]
    for key, label in (("processo", "Case"), ("tribunal", "Court"), ("referencia_catastral", "Cadastral reference"),
                       ("artigo", "Tax article"), ("descricao", "Registry description")):
        value = raw.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            out.append({"label": label, "value": str(value).strip()[:120]})
    return out


def _links(item: dict) -> list[dict]:
    """The sale page and the official-record links listing_info already gives (http/https only)."""
    import listing_info
    from common import safe_url
    out = []
    def clean(u):   # an autolink <…> ends at the first space or angle bracket
        u = safe_url(u)
        return u.replace(" ", "%20").replace("<", "%3C").replace(">", "%3E") if u else None
    url = clean(item.get("url")) if item.get("source") != "citius" else None
    if url:
        out.append({"label": "Sale page", "url": url})
    try:
        official = listing_info.official_records(item) or {}
    except Exception:  # noqa: BLE001 — a malformed stored record never breaks the assessment
        official = {}
    for link in official.get("links") or []:
        u = clean(link.get("url"))
        if u:
            out.append({"label": str(link.get("label") or "Official record")[:80], "url": u})
    return out


def _information_requests(item: dict) -> list[str]:
    """Information-request letters letters.py supports for this sale (drafted on Offers, never sent here)."""
    try:
        from letters import letter_types_for
        return [t.label for t in letter_types_for(item) if not t.is_offer]
    except Exception:  # noqa: BLE001
        return []


_ACTIONS = {
    "resolve_concern": ("Resolve the recorded concern", "Is there something that rules this property out?",
                        ["The document or record behind the concern, and its date"]),
    "clarify_price": ("Find out what the price is for", "What figure is the sale offering the property at?",
                      ["The sale notice's price, its basis (base value, minimum, bid) and conditions"]),
    "reconfirm_availability": ("Reconfirm the sale is still open", "Is this sale still open for offers or bids?",
                               ["A dated check of the sale's own page or the authority running it"]),
    "confirm_interest_sold": ("Confirm what interest is being sold", "Is the whole property being sold?",
                              ["The sale notice's description of the rights sold",
                               "The registry record for this property"]),
    "check_title": ("Check the title and charges", "Who owns it, and which charges stay with it?",
                    ["A current registry certificate for this property",
                     "Which charges the sale cancels, from the authority running it"]),
    "establish_possession": ("Find out who occupies it", "Will the property be handed over vacant?",
                             ["Possession information and the sale conditions on delivery"]),
    "establish_legal_access": ("Identify the legal access route", "Does the property have a lawful way in?",
                               ["Applicable deed or registered access-right documentation"]),
    "establish_permitted_use": ("Check the permitted use", "May the property be used for the goal it is assessed for?",
                                ["The municipality's classification or licence of use for this property"]),
    "obtain_cost_evidence": ("Get a dated cost estimate", "What will the works and fees actually cost?",
                             ["A technical review or a dated estimate for the works"]),
}
_GAP_ACTION = {"price_interpretation": "clarify_price", "sale_availability": "reconfirm_availability",
               "whole_ownership": "confirm_interest_sold", "title": "check_title",
               "possession": "establish_possession", "lawful_access": "establish_legal_access",
               "intended_use": "establish_permitted_use", "project_costs": "obtain_cost_evidence"}


def next_actions(dims: dict, contradictions: list, item: dict) -> list[dict]:
    """Every useful action, in ACTION_ORDER (then DIMENSION_ORDER): the first is the primary one.
    An action done is not a question verified: the checklist stays the record."""
    wanted = []
    for key in DIMENSION_ORDER:
        d = dims[key]
        if d["state"] == "concern":
            wanted.append(("resolve_concern", key, d["summary"]))
        elif d["state"] == "unresolved":
            wanted.append((_GAP_ACTION[key], key, d["summary"]))
    if contradictions and not any(a == "resolve_concern" for a, _, _ in wanted):
        wanted.append(("resolve_concern", "contradiction", contradictions[0]))
    wanted.sort(key=lambda w: (ACTION_ORDER[w[0]], DIMENSION_ORDER.index(w[1]) if w[1] in DIMENSION_ORDER else -1))
    links, refs, letters = _links(item), _references(item), _information_requests(item)
    out = []
    for action, dimension, why_now in wanted:
        title, question, evidence = _ACTIONS[action]
        if action == "resolve_concern":
            title = f"Resolve the concern: {LABELS.get(dimension, 'conflicting records').lower()}"
        out.append({"key": action, "dimension": dimension, "title": title, "question": question,
                    "reason": f"{ACTION_WHY[ACTION_ORDER[action]]} {why_now}".strip(),
                    "priority": ACTION_ORDER[action], "requested_evidence": evidence,
                    "references": refs, "links": links,
                    "information_requests": letters if action in ("check_title", "establish_possession",
                                                                  "confirm_interest_sold", "reconfirm_availability",
                                                                  "clarify_price") else [],
                    "completion_note": "Recording that you asked, or that the step happened, does not verify "
                                       "the answer: record what you found in the checklist."})
    return out


def _valid_hours():
    from db import _availability_valid_hours
    return _availability_valid_hours()


def assess(item: dict, check_rows: dict | None, cfg: dict | None, mode: str, presented: dict | None = None,
           now: datetime | None = None) -> dict:
    """The investigation summary of one scored listing (a load_listings row, full, with raw_json)."""
    import checklist
    import evaluator
    import presentation
    from db import _shadow_evidence
    now = now or datetime.now(timezone.utc)
    row = dict(item)
    presented = presented or presentation.summary(row, check_rows, cfg, now)
    built = checklist.build(row, check_rows or {}, cfg)
    areas = {x["area"]: x for x in presented["checks"]["items"]}
    evidence = {**_shadow_evidence(row, _valid_hours()), "_evaluation_now": now.isoformat()}
    policy = {**evaluator.DEFAULT_POLICY}
    evaluated = {"full_ownership": evaluator._req_full_ownership(evidence, policy),
                 "sale_availability": evaluator._req_sale_availability(evidence, policy)}
    in_area = lambda area: [x for x in built["items"] if x["category"] == area or x["key"] == area]  # noqa: E731
    dims = {
        "price_interpretation": _price(presented["price"]),
        "sale_availability": _availability(row, evaluated, presented["deadline"]),
        "whole_ownership": _ownership(row, evaluated),
        "title": _from_check("title", areas.get("title"), in_area("title"),
                             missing="No title or charges check exists for this sale.",
                             limitations=[_LOCATION_LIMIT]),
        "possession": _from_check("possession", areas.get("occupancy"), in_area("occupancy"),
                                  missing="No possession check exists for this sale.",
                                  limitations=["What the sale reports about occupancy is a source claim, not "
                                               "verified possession."]),
        "lawful_access": _from_check("lawful_access", areas.get("land_access"), in_area("land_access"),
                                     missing="No legal-access verification recorded.",
                                     limitations=["A road shown on a map does not establish an access right.",
                                                  _LOCATION_LIMIT]),
        "intended_use": _intended_use(row, mode, built),
        "project_costs": _costs(presented["all_in"], built),
    }
    contradictions = _contradictions(row, dims, built)
    actions = next_actions(dims, contradictions, row)
    reasons = [r for r in (row.get("reasons") or []) if isinstance(r, str)][:8]
    return {"listing_id": row["id"], "title": row.get("title") or "Untitled listing", "mode": mode,
            "purpose": PURPOSES.get(mode), "assessed_at": now.isoformat(timespec="seconds"),
            "purchase_readiness_established": False,
            "opportunity": {"score": row.get("score"), "reasons": reasons},
            "dimensions": [dims[k] for k in DIMENSION_ORDER],
            "unresolved": sum(1 for d in dims.values() if d["state"] == "unresolved"),
            "concerns": sum(1 for d in dims.values() if d["state"] == "concern"),
            "contradictions": contradictions,
            "next_action": actions[0] if actions else None, "more_actions": actions[1:],
            "deadline": presented["deadline"], "price": presented["price"], "all_in": presented["all_in"],
            "references": _references(row), "links": _links(row),
            "stage_history": {"available": False,
                              "note": "Research stages and decision history are not recorded yet: they need a "
                                      "database change that has not been approved."}}


def summary(a: dict) -> dict:
    """The few words a Listings row or card shows of an assessment: counts, the
    open questions by name and the next action. No combined score."""
    n = a["next_action"]
    return {"unresolved": a["unresolved"], "concerns": a["concerns"], "contradictions": len(a["contradictions"]),
            "open": [d["label"] for d in a["dimensions"] if d["state"] in ("unresolved", "concern")],
            "next": {"key": n["key"], "title": n["title"]} if n else None}


# ─── The investigation brief (Markdown) ───

def _md(text, limit: int | None = 400) -> str:
    """Scraped or typed text made inert in Markdown: no links, HTML, emphasis or headings."""
    from report import md_cell
    s = md_cell(" ".join(str(text or "").split()).replace("\\", "\\\\"), limit)
    for ch in "*_`#~!":
        s = s.replace(ch, "\\" + ch)
    return s


def _score(value) -> str:
    return "not scored" if not isinstance(value, (int, float)) else f"{value:.0f}"


def _money(value) -> str:
    from presentation import _amount
    amount = _amount(value)
    return "not known" if amount is None else f"€{amount:,.0f}".replace(",", " ")


_STATE_WORD = {"verified": "Verified by you", "unresolved": "Unresolved", "concern": "Concern",
               "not_applicable": "Not applicable", "established": "Stated by the sale"}


def as_markdown(a: dict, item: dict) -> str:
    """The brief for a professional review: provenance, gaps and limits. It
    leaves out personal notes, contact history and buyer details."""
    lines = [f"# Investigation brief: {_md(a['title'], 200)}", "",
             f"Generated {a['assessed_at']} (UTC). Research notes, not a valuation, legal advice or a statement "
             "that this property can be bought. **Purchase readiness established: no.**", ""]
    lines += ["## Property and sale", ""]
    for ref in a["references"]:
        lines.append(f"- {_md(ref['label'])}: {_md(ref['value'])}")
    place = ", ".join(x for x in (item.get("concelho"), item.get("district"), item.get("country")) if x)
    if place:
        lines.append(f"- Place: {_md(place)}")
    lines.append(f"- Source: {_md(item.get('source') or 'unknown')}")
    if a.get("purpose"):
        lines.append(f"- Assessed for: {_md(a['purpose'])} (the Listings goal)")
    for link in a["links"]:
        lines.append(f"- {_md(link['label'])}: <{link['url']}>")
    d = a.get("deadline") or {}
    if d.get("date"):
        when = d["date"] + (f" {d['time']}" if d.get("time") else " (date only: closing time not published)")
        lines.append(f"- Deadline: {_md(when)} {_md(d.get('timezone') or '')}".rstrip())
    lines += ["", "## Why it was selected", "",
              f"Opportunity score {_score(a['opportunity']['score'])} for this goal: interest, not verification."]
    lines += [f"- {_md(r)}" for r in a["opportunity"]["reasons"]] or ["- No reasons recorded."]
    p = a.get("price") or {}
    lines += ["", "## Price and costs", "",
              f"- {_md(p.get('label') or 'Price')}: {_money(p.get('amount'))}"
              + (f" — {_md(p.get('note'))}" if p.get("note") else "")]
    for sec in p.get("secondary") or []:
        lines.append(f"- {_md(sec.get('label'))}: {_money(sec.get('amount'))}"
                     + (f" ({_md(sec.get('note'))})" if sec.get("note") else ""))
    c = a.get("all_in") or {}
    if c.get("low") is not None:
        lines.append(f"- Estimated all-in: {_money(c['low'])} to {_money(c['high'])} ({_md(c.get('basis_label'))})")
    elif c.get("headline"):
        lines.append(f"- Estimated all-in: {_md(c['headline'])}")
    elif c.get("known_subtotal") is not None:
        lines.append(f"- Price, taxes and fees only: {_money(c['known_subtotal'])}; works not priced")
    for m in c.get("missing") or []:
        lines.append(f"- Missing: {_md(m)}")
    if c.get("excludes"):
        lines.append(f"- No estimate includes: {_md(', '.join(c['excludes']))}")
    lines += ["", "## Evidence, dimension by dimension", ""]
    for dim in a["dimensions"]:
        word = _STATE_WORD.get(dim["state"], dim["state"])
        lines.append(f"### {_md(dim['label'])}: {word}")
        lines.append("")
        lines.append(_md(dim["summary"], 600) + (" (source-reported)" if dim["source_reported"] else ""))
        for ref in dim["evidence_refs"]:
            if ref["type"] == "checklist":
                dated = ", ".join(x for x in (f"checked {ref['checked_on']}" if ref.get("checked_on") else "",
                                               f"recorded {ref['updated_at'][:10]}" if ref.get("updated_at") else "") if x)
                from checklist import STATUS_LABELS
                lines.append(f"- Checklist: {_md(ref['label'], 200)} — {_md(STATUS_LABELS.get(ref['status'], ref['status']))}"
                             + (" (needs rechecking)" if ref.get("needs_recheck") else "")
                             + (f", {dated}" if dated else ""))
            elif ref["type"] == "link_check":
                lines.append(f"- Sale page check: {_md(ref.get('state'))}, at {_md(ref.get('checked_at') or 'unknown time')}")
        for limit in dim["limitations"]:
            lines.append(f"- Limitation: {_md(limit, 400)}")
        lines.append("")
    lines += ["## Contradictions and open questions", ""]
    lines += [f"- {_md(x, 400)}" for x in a["contradictions"]] or ["- No contradiction between records found."]
    lines += [f"- {_md(d['label'])}: {_md(d['summary'], 400)}" for d in a["dimensions"] if d["state"] == "unresolved"]
    lines += ["", "## Next action", ""]
    act = a.get("next_action")
    if act:
        lines += [f"**{_md(act['title'])}** — {_md(act['question'])}", "", _md(act["reason"], 600), "",
                  "Evidence that would answer it:"]
        lines += [f"- {_md(e)}" for e in act["requested_evidence"]]
        if act.get("information_requests"):
            lines.append("- Information requests that can be drafted on Offers (nothing is sent until you send "
                         "it there): " + _md(", ".join(act["information_requests"])))
        lines += ["", _md(act["completion_note"], 400)]
        for more in a.get("more_actions") or []:
            lines.append(f"- Then: {_md(more['title'])}")
    else:
        lines.append("No open action from the stored records.")
    lines += ["", "## Research stage", "", _md(a["stage_history"]["note"], 400), "",
              "## Limitations", "",
              "- Built only from what the scanner has stored and what you recorded in the checklist; nothing was "
              "fetched, looked up or bought to write it.",
              "- Absent evidence is a gap, not a defect, and never counts as passing a requirement.",
              "- Figures are estimates (taxes, fees and works); an unknown amount is not zero.",
              "- Personal notes, contact history and buyer details are not included.", ""]
    return "\n".join(lines)
