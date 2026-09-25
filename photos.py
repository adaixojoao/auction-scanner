"""photos.py — what state a house is in, from its photos.

Most listings do not say whether the house needs work, and that is where most
of the uncertainty is (an unknown condition counts at 75% of the local price).
When a listing has photos (e-leilões, Imobancos, Leilosoc, Servihabitat…), a
vision model looks at them once and says "good", "some" (needs work), "heavy"
(ruin, full rebuild) or "unknown", with a one-line reason. The answer is kept
in raw_json ("photo_check") and scoring.condition() uses it when the text says
nothing.

Two ways to look (Settings → Photo check):
- "ollama" (the default): an open model running on this PC through Ollama
  (ollama.com), free and private. On a laptop without a graphics card a photo
  takes about a minute, so only a few homes are looked at each scan.
- "anthropic": Claude, with an Anthropic API key. Faster, paid.
Nothing happens when neither is set up.
"""
from __future__ import annotations

import base64
import json
import os

from common import LOG, utcnow_iso

MODEL = "claude-haiku-4-5"          # the same model as the AI check (analysis.py)
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5vl:3b"       # an open vision model that fits in 6 GB of memory
PHOTOS_PER_SCAN = {"ollama": 5, "anthropic": 20}
MAX_PHOTOS = {"ollama": 2, "anthropic": 4}
MAX_PHOTO_BYTES = 3_000_000

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
    return out[:max(MAX_PHOTOS.values())]


class ClaudeLooker:
    """Claude looks at the photos by their URLs."""
    name = "anthropic"

    def __init__(self, client, model: str = MODEL):
        self.client, self.model = client, model

    def look(self, urls: list[str], prompt: str) -> dict:
        content = [{"type": "image", "source": {"type": "url", "url": u}} for u in urls]
        content.append({"type": "text", "text": prompt})
        resp = self.client.messages.create(
            model=self.model, max_tokens=400, messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": PHOTO_SCHEMA}},
        )
        return json.loads(next((b.text for b in resp.content if b.type == "text"), ""))


class OllamaLooker:
    """An open model on this PC (Ollama) looks at the photos: they are fetched
    here and sent as images; the answer is held to the same JSON schema."""
    name = "ollama"

    def __init__(self, session, url: str = OLLAMA_URL, model: str = OLLAMA_MODEL):
        self.session, self.url, self.model = session, url.rstrip("/"), model

    def ready(self) -> bool:
        try:
            tags = self.session.get(f"{self.url}/api/tags", timeout=5).json()
        except Exception:  # noqa: BLE001: not installed or not running
            return False
        names = {m.get("name") for m in tags.get("models") or []}
        return self.model in names or f"{self.model}:latest" in names

    def look(self, urls: list[str], prompt: str) -> dict:
        images = []
        for u in urls:
            resp = self.session.get(u, timeout=30)
            resp.raise_for_status()
            if len(resp.content) <= MAX_PHOTO_BYTES:
                images.append(base64.b64encode(resp.content).decode("ascii"))
        if not images:
            raise ValueError("no photo could be fetched")
        resp = self.session.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "format": PHOTO_SCHEMA, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt, "images": images}],
        }, timeout=900)                        # a CPU takes its time
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])


def check_photos(looker, item: dict) -> dict | None:
    """Ask the model about one listing's photos. None when it has none."""
    urls = photo_urls(item)[:MAX_PHOTOS.get(looker.name, 2)]
    if not urls:
        return None
    found = looker.look(urls, PROMPT.format(title=(item.get("title") or "")[:150]))
    if found.get("condition") not in PHOTO_SCHEMA["properties"]["condition"]["enum"]:
        raise ValueError(f"unexpected answer {found!r:.80}")
    return {"condition": found["condition"], "confidence": found.get("confidence", "low"),
            "notes": str(found.get("notes") or "")[:200], "photos": len(urls),
            "by": getattr(looker, "model", looker.name), "at": utcnow_iso()}


def make_looker(cfg: dict):
    """The looker Settings asks for, or None when it is not set up."""
    ai = cfg.get("ai") or {}
    provider = ai.get("provider") or "ollama"
    if provider == "anthropic":
        key = api_key(cfg)
        if not key:
            return None
        try:
            import anthropic
        except ImportError:
            LOG.info("Photo check: the anthropic package is not installed")
            return None
        return ClaudeLooker(anthropic.Anthropic(api_key=key))
    from common import make_session
    looker = OllamaLooker(make_session(timeout=60), ai.get("ollama_url") or OLLAMA_URL,
                          ai.get("ollama_model") or OLLAMA_MODEL)
    if not looker.ready():
        LOG.info(f"Photo check: Ollama with {looker.model} is not running on this PC; skipped")
        return None
    return looker


def check_pending(db, cfg: dict, items: list[dict], limit: int | None = None, looker=None) -> int:
    """Look at the photos of the best homes not looked at yet, once each."""
    ai = cfg.get("ai") or {}
    if ai.get("photo_check") is False:
        return 0
    looker = looker or make_looker(cfg)
    if looker is None:
        return 0
    if limit is None:
        limit = ai.get("photos_per_scan") or PHOTOS_PER_SCAN.get(looker.name, 5)
    done = 0
    for item in items:
        if done >= limit:
            break
        if item.get("kind") != "home" or '"photo_check"' in (item.get("raw_json") or "") or not photo_urls(item):
            continue
        try:
            found = check_photos(looker, item)
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
