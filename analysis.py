"""
analysis.py — batch LLM verdicts on the top listings (python scraper.py --analyze).

Without ANTHROPIC_API_KEY it writes the prompt to analysis_prompt.txt for manual
use, plus a rule-based analysis.md.
"""
from __future__ import annotations

import json
import os

from common import LOG, has_term, utcnow
from db import load_listings
from scoring import FRAC_PATTERNS

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-haiku-4-5-20251001"

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
                     *, filters: dict | None = None, limit: int = 25):
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
    analysis_path = os.path.join(HERE, "analysis.md")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        prompt_path = os.path.join(HERE, "analysis_prompt.txt")
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
