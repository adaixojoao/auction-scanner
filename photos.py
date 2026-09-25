"""photos.py — what state a house is in, from its photos.

Most listings do not say whether the house needs work, and that is where most
of the uncertainty is (an unknown condition counts at 75% of the local price).
When a listing has photos (e-leilões, Imobancos, Leilosoc), Claude looks at
them once and says "good", "some" (needs work), "heavy" (ruin, full rebuild)
or "unknown", with a one-line reason. The answer is kept in raw_json
("photo_check") and scoring.condition() uses it when the text says nothing.

Needs an Anthropic API key (Settings → AI, or ANTHROPIC_API_KEY). Only the best
listings are looked at, PHOTOS_PER_SCAN a scan, each once.
"""
from __future__ import annotations

import json
import os

from common import LOG, utcnow_iso

MODEL = "claude-haiku-4-5"          # the same model as the AI check (analysis.py)
PHOTOS_PER_SCAN = 20
MAX_PHOTOS = 4

PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "condition": {"type": "string", "enum": ["good", "some", "heavy", "unknown"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": ["condition", "confidence", "notes"],
    "additionalProperties": False,
}

PROMPT = """These are the photos of a property for sale at auction: {title}.
Judge only what the photos show about the building's condition:
- "good": lived in or ready to live in, roof and walls sound, maybe dated;
- "some": needs work (old kitchen or bathroom, damp, worn finishes, windows) but sound;
- "heavy": a ruin, no roof, collapsed or gutted, or unfinished construction;
- "unknown": the photos do not show the building (a map, a document, a logo, only land).
Give a one-line reason in English in "notes"."""


def api_key(cfg: dict | None = None) -> str | None:
    return ((cfg or {}).get("ai") or {}).get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY") or None


def photo_urls(item: dict) -> list[str]:
    """The listing's photos: the cover, then any gallery the source gave."""
    try:
        raw = json.loads(item.get("raw_json") or "{}")
    except ValueError:
        raw = {}
    urls = [item.get("image_url")]
    for key in ("fotos", "photos", "images", "imagens"):
        gallery = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(gallery, list):
            urls += [g if isinstance(g, str) else (g or {}).get("url") for g in gallery]
    out = []
    for u in urls:
        if isinstance(u, str) and u.startswith("https://") and u not in out:
            out.append(u)
    return out[:MAX_PHOTOS]


def check_photos(client, item: dict) -> dict | None:
    """Ask Claude about one listing's photos. None when it has none."""
    urls = photo_urls(item)
    if not urls:
        return None
    content = [{"type": "image", "source": {"type": "url", "url": u}} for u in urls]
    content.append({"type": "text", "text": PROMPT.format(title=(item.get("title") or "")[:150])})
    resp = client.messages.create(
        model=MODEL, max_tokens=400, messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": PHOTO_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    found = json.loads(text)
    return {"condition": found["condition"], "confidence": found["confidence"],
            "notes": found["notes"][:200], "photos": len(urls), "at": utcnow_iso()}


def check_pending(db, cfg: dict, items: list[dict], limit: int | None = None, client=None) -> int:
    """Look at the photos of the best homes not looked at yet, once each."""
    ai = cfg.get("ai") or {}
    if ai.get("photo_check") is False:
        return 0
    if client is None:
        key = api_key(cfg)
        if not key:
            return 0
        try:
            import anthropic
        except ImportError:
            LOG.info("Photo check: the anthropic package is not installed")
            return 0
        client = anthropic.Anthropic(api_key=key)
    limit = ai.get("photos_per_scan", PHOTOS_PER_SCAN) if limit is None else limit
    done = 0
    for item in items:
        if done >= limit:
            break
        if item.get("kind") != "home" or '"photo_check"' in (item.get("raw_json") or "") or not photo_urls(item):
            continue
        try:
            found = check_photos(client, item)
        except Exception as e:  # noqa: BLE001: a photo check must never fail the scan
            LOG.info(f"Photo check of {item['id']} failed ({type(e).__name__}); trying next scan")
            continue
        if not found:
            continue
        try:
            raw = json.loads(item.get("raw_json") or "{}")
        except ValueError:
            raw = {}
        raw["photo_check"] = found
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        db.commit()
        done += 1
    if done:
        LOG.info(f"Photo check: {done} listings looked at")
    return done
