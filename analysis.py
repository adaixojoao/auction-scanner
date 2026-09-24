"""
analysis.py — everything that asks Claude about listings.

  analyze_property()  one listing, for the "AI check" button on the Offers page
  analyze_with_llm()  batch verdicts on the top listings (python scraper.py --analyze);
                      without ANTHROPIC_API_KEY it writes the prompt to a file and a
                      rule-based analysis.md instead
"""
from __future__ import annotations

import json
import os
import re

from common import LOG, has_term, utcnow
from db import load_listings
from scoring import FRAC_PATTERNS

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-haiku-4-5"

ANALYSIS_PROMPT = """You are a Portuguese real estate investment analyst. Budget: €{budget:,.0f}.
Analyze these auction/sale listings and rank them by investment potential.

For each listing, assess:
1. Is this a FULL property or a fractional share (quota-parte, 1/2, 1/12, avos)?
2. Location quality (urban vs rural, proximity to cities)
3. Red flags (very old listing, unrealistic price, legal complications like "direito de usufruto")
4. Realistic resale/rental potential

Output a JSON array of verdicts, one per listing:
{{"url":"...","score":1-10,"verdict":"BUY/WATCH/SKIP","reason":"one line"}}

Score 8-10 = strong buy opportunity, 5-7 = worth investigating, 1-4 = skip.
Be brutally honest. Most auction listings are bad deals — say so when they are.

Listings:
{listings_json}"""


def _rule_based(compact: list[dict], now) -> str:
    lines = ["# Investment Analysis (rule-based)", f"**Generated**: {now:%Y-%m-%d %H:%M} UTC  ", ""]
    for entry in compact:
        flags = []
        title = entry["title"]
        if has_term(title, FRAC_PATTERNS, negations=False):
            flags.append("FRACTIONAL SHARE — limited utility")
        if has_term(title, ["usufruto"]):
            flags.append("USUFRUCT ONLY — not full ownership")
        if has_term(title, ["direito"]) and has_term(title, ["herança"]):
            flags.append("INHERITANCE RIGHT — legal complexity")
        if entry.get("bid") and entry["price"] and entry["bid"] > entry["price"] * 2:
            flags.append(f"BID {entry['bid'] / entry['price']:.0%} of VB — overheated")
        if entry["price"] and entry["price"] < 500:
            flags.append("VERY LOW VB — likely tiny plot or worthless fraction")
        if has_term(title, ["rústico"]) and entry["price"] and entry["price"] < 10000:
            flags.append("CHEAP RURAL — probably remote/inaccessible")

        verdict = "SKIP" if flags else "INVESTIGATE"
        score = max(1, 6 - len(flags))
        reason = "; ".join(flags) if flags else "No obvious red flags — worth checking details"
        price = f"€{entry['price']:,.0f}" if entry.get("price") else "price unknown"
        lines.append(f"**[{entry['title'][:50]}]({entry['url']})** — {price}")
        lines.append(f"  Score: {score}/10 | {verdict} | {reason}")
        lines.append("")
    return "\n".join(lines)


def analyze_with_llm(db, max_price: float = 50000, category: str = "imoveis",
                     *, filters: dict | None = None, limit: int = 25, out_dir: str | None = None):
    """Send the top-scored listings to Claude for a verdict. Returns the analysis path."""
    now = utcnow()
    items = [it for it in load_listings(db, filters=filters, now=now)
             if it["category"] == category
             and (it.get("price") or 0) <= max_price
             and (it.get("current_bid") or 0) <= max_price]
    if not items:
        LOG.info("No listings to analyze")
        return None
    items.sort(key=lambda it: -it["score"])

    compact = []
    for it in items[:limit]:
        entry = {
            "title": (it["title"] or "")[:80],
            "price": it["price"],
            "bid": it["current_bid"],
            "loc": ", ".join(filter(None, [it["freguesia"], it["concelho"], it["district"]])),
            "tipo": it["tipo"],
            "area": it["area_m2"],
            "ends": (it["date_end"] or "")[:10],
            "url": it["url"],
            "src": it["source"],
            "auto_score": it["score"],
        }
        if it.get("description"):
            entry["desc"] = it["description"][:200]
        compact.append(entry)

    prompt = ANALYSIS_PROMPT.format(budget=max_price, listings_json=json.dumps(compact, ensure_ascii=False))
    out_dir = out_dir or HERE
    analysis_path = os.path.join(out_dir, "analysis.md")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        prompt_path = os.path.join(out_dir, "analysis_prompt.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        LOG.info(f"No ANTHROPIC_API_KEY set. Prompt saved to {prompt_path}; "
                 "paste it into Claude or set the key.")
        with open(analysis_path, "w", encoding="utf-8") as f:
            f.write(_rule_based(compact, now))
        LOG.info(f"Rule-based analysis written to {analysis_path}")
        return analysis_path

    import anthropic

    LOG.info(f"Sending {len(compact)} listings to Claude (prompt ~{len(prompt)} chars)...")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    analysis_text = resp.content[0].text
    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    LOG.info(f"Claude analysis done. Tokens: {tokens}")

    with open(analysis_path, "w", encoding="utf-8") as f:
        f.write("# Investment Analysis\n")
        f.write(f"**Generated**: {now:%Y-%m-%d %H:%M} UTC  \n")
        f.write(f"**Model**: {MODEL} | **Tokens**: {tokens}  \n")
        f.write(f"**Listings analyzed**: {len(compact)}  \n\n")
        f.write(analysis_text)
        f.write("\n")
    LOG.info(f"Analysis written to {analysis_path}")
    return analysis_path


# ── One property: the Offers page's "AI check" ───────────────────────────────

COUNTRY_CONTEXT = {
    "PT": "Portugal. Judicial sale via Citius or e-leilões. Carta fechada = sealed bid; offers below 85% of the valor base are normally not accepted. Risk: IMI debts pass to the buyer.",
    "ES": "Spain. Judicial auction via BOE. Minimum bid 50-75% of appraised value. Risk: occupants with legal protection.",
    "FR": "France. Judicial auction via licitor.com. Buyer pays ~8% notary fees. Risk: occupants with droit au maintien.",
    "DE": "Germany. Zwangsversteigerung. First round has 5/10 and 7/10 limits of Verkehrswert. Risk: Grundschuld not cleared.",
    "IT": "Italy. Judicial sale via pvp.giustizia.it. Starting bid 25% below appraisal. Risk: occupants, condominium debts.",
    "NL": "Netherlands. Executieveiling. 2% transfer tax. Risk: hidden defects, no warranty.",
    "HR": "Croatia. Forced sale via FINA/e-oglasna. Starts 75% of value, 50% in the second round. Risk: unclear title.",
    "GR": "Greece. Electronic auction via eauction.gr. Starting bid 2/3 of appraisal. Risk: ENFIA tax debts transfer.",
    "BE": "Belgium. Notary auction via biddit.be. Legally binding bid. Risk: structural defects, no warranty.",
    "RO": "Romania. ANAF tax seizure; prices in RON. Risk: multiple creditors, unclear priority.",
    "PL": "Poland. Bailiff auction; prices in PLN. First: 3/4 of appraisal, second: 1/2. Risk: occupants, mortgage not cleared.",
    "CY": "Cyprus. Forced sale via DLS. Risk: title deeds not issued, occupants.",
}

_NULLABLE_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_STRINGS = {"type": "array", "items": {"type": "string"}}
PROPERTY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["BUY", "INVESTIGATE", "SKIP"]},
        "confidence": {"type": "integer", "description": "1 (guess) to 10 (certain)"},
        "summary": {"type": "string"},
        "positives": _STRINGS,
        "risks": _STRINGS,
        "red_flags": _STRINGS,
        "market_value_estimate": _NULLABLE_NUMBER,
        "discount_pct": _NULLABLE_NUMBER,
        "renovation_cost": _NULLABLE_NUMBER,
        "recommended_bid": {"type": "string", "description": "Portuguese format, e.g. 4.000,00"},
        "max_bid": {"type": "string", "description": "Portuguese format, e.g. 6.000,00"},
        "bid_reason": {"type": "string"},
        "visit_first": {"type": "boolean"},
        "next_steps": _STRINGS,
    },
    "required": ["verdict", "confidence", "summary", "positives", "risks", "red_flags",
                 "market_value_estimate", "discount_pct", "renovation_cost", "recommended_bid",
                 "max_bid", "bid_reason", "visit_first", "next_steps"],
    "additionalProperties": False,
}


def _property_prompt(data: dict) -> str:
    country = data.get("country") or "PT"
    return f"""You are an expert in European judicial property auctions with 20 years of experience.
The buyer wants to acquire properties well below market value for charitable purposes.
They are based in Portugal but buy across the EU.

COUNTRY CONTEXT: {COUNTRY_CONTEXT.get(country, "European judicial auction.")}

PROPERTY DATA:
- Title: {data.get('title', '')}
- Country: {country}
- Location: {data.get('location', '')}
- Area: {data.get('area_m2', '')} m²
- Base value: €{data.get('price', '')}
- Current bid: €{data.get('current_bid') or 'no bids'}
- Sale type: {data.get('modalidade', '')}
- Category: {data.get('categoria', '')}
- Deadline: {data.get('date_end', '')}
- Court/agent: {data.get('tribunal', '')}
- Case number: {data.get('processo', '')}
- Description: {data.get('description', '')}
- Auto score: {data.get('score', '')}/100 ({', '.join(data.get('reasons', []))})
- Bid the buyer is considering: EUR {data.get('bid', '')}

Check specifically:
1. Red flags in the description (occupants, tax debts, unclear title, usufruct, fractional ownership).
2. Whether the case number is old (pre-2020); old cases accumulate complications.
3. Whether the price per m² makes sense for the location.
4. Renovation cost if it is a dwelling (€100-200/m² light, €300-500/m² heavy).
5. Whether it can be bid on remotely from Portugal.
6. The realistic all-in cost (bid + taxes + fees + renovation).
7. Whether the considered bid is at or above the legal minimum for this sale type.

Be direct and honest, including about what you cannot know from this data. Write in English.
Money estimates are plain numbers in euros (null when unknown); bids use Portuguese format."""


def analyze_property(data: dict) -> dict:
    """Ask Claude about one listing. Returns the PROPERTY_SCHEMA dict, or {"error": ...}."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"error": "ANTHROPIC_API_KEY is not set. Add it to your environment to use the AI check."}
    try:
        import anthropic
    except ImportError:
        return {"error": "The anthropic package is not installed (pip install anthropic)."}

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": _property_prompt(data)}]
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=2000, messages=messages,
            output_config={"format": {"type": "json_schema", "schema": PROPERTY_SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)
    except TypeError:
        # anthropic SDK too old for output_config: ask for JSON in the prompt instead.
        pass
    except anthropic.APIStatusError as e:
        return {"error": f"Claude API error ({e.status_code}): {e.message}"}
    except anthropic.APIConnectionError:
        return {"error": "Could not reach the Claude API (network)."}

    resp = client.messages.create(
        model=MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": _property_prompt(data) + "\n\nRespond ONLY with a JSON object with these keys: "
                   + ", ".join(PROPERTY_SCHEMA["required"])}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group()) if m else {"error": "Claude did not return JSON", "summary": text}
    except ValueError:
        return {"error": "Claude returned malformed JSON", "summary": text}
