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
Nothing happens when neither is set up. When hundreds of board homes are
waiting (after a scrape or a new source), the per-scan budget rises so the
queue drains in days rather than months.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import urllib.parse

from common import LOG, utcnow_iso

MODEL = "claude-haiku-4-5"          # the same model as the AI check (analysis.py)
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5vl:3b"       # an open vision model that fits in 6 GB of memory
PHOTOS_PER_SCAN = {"ollama": 5, "anthropic": 20}
# When many board homes still have photos and no answer, drain faster. Ollama
# is slow on CPU (~1–3 min/home), so catch-up stays modest; Claude can do more.
# A second, higher tier kicks in when the queue is huge (hundreds waiting).
PHOTOS_CATCHUP = {"ollama": 18, "anthropic": 60}
PHOTOS_CATCHUP_HARD = {"ollama": 24, "anthropic": 80}
PHOTOS_CATCHUP_WHEN = 40            # pending homes that trigger catch-up
PHOTOS_CATCHUP_HARD_WHEN = 200      # pending homes that raise the budget again
PHOTO_FAILURES_IN_A_ROW = 2         # then the looker is down (Ollama out of memory, …)
MAX_PHOTOS = {"ollama": 2, "anthropic": 6}   # a 6 GB laptop swaps with more images in one Ollama call
MAX_PHOTO_BYTES = 3_000_000
# A listing shown at 85 or more in any goal keeps its photos on this PC. Auction
# sites take the pictures down when the sale ends; the detail panel then still
# has them. Twelve is a full gallery, not the two the vision model looks at.
KEEP_SCORE = 85
KEEP_PER_SCAN = 20
KEEP_GALLERY = 12
KEEP_SIDE = 1600
DESCRIPTION_CHARS = 1200   # enough for the condition; more slows a small local model
# Gallery keys scrapers have used (and a few common alternate spellings).
_GALLERY_KEYS = ("fotos", "photos", "images", "imagens", "immagini", "gallery",
                 "slike", "media", "imagini")

PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "condition": {"type": "string", "enum": ["good", "some", "heavy", "unknown"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
        "shows_house": {"type": "boolean"},
    },
    "required": ["condition", "confidence", "notes", "shows_house"],
    "additionalProperties": False,
}

PROMPT = """These are the photos of a property for sale at auction: {title}.
The seller's description (any language): {description}
Judge the building's condition from the photos AND the description:
- "good": lived in or ready to live in, roof and walls sound, maybe dated;
- "some": needs work (old kitchen or bathroom, damp, worn finishes, windows) but sound;
- "heavy": a ruin, no roof, a collapsed or fallen roof, or unfinished construction.
  A collapsed barn or outbuilding roof counts: say "collapsed barn roof" in notes
  and do not call the property good;
- fibre-cement, uralita or asbestos sheet roofing is at least "some" (it has to
  come off): say "fibre-cement" or "asbestos" in notes;
- "unknown": the photos do not show the building (a map, a document, a logo, only land).
Sellers photograph the best parts: when the description admits damage the photos
do not show ("tejado caído", "telhado em ruína", "needs renovation"), use the worse.
"shows_house": true only if at least one photo shows the dwelling itself (outside or inside);
false when they show only land, a barn, a granary, a shed, ruins of outbuildings or the view.
Give a one-line reason in English in "notes"."""


def api_key(cfg: dict | None = None) -> str | None:
    return ((cfg or {}).get("ai") or {}).get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY") or None


def _url(value) -> str | None:
    """A usable photo URL: https preferred; http upgraded; protocol-relative allowed."""
    if isinstance(value, dict):
        value = value.get("url") or value.get("src") or value.get("href") or value.get("photo_url")
    if not isinstance(value, str):
        return None
    u = value.strip()
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    if u.startswith("https://") and len(u) > 12:
        return u
    return None


def photo_urls(item: dict, limit: int | None = None) -> list[str]:
    """The listing's photos: the cover, then any gallery the source gave."""
    try:
        raw = json.loads(item.get("raw_json") or "{}")
    except ValueError:
        raw = {}
    urls = [item.get("image_url")]
    if isinstance(raw, dict):
        for key in _GALLERY_KEYS:
            gallery = raw.get(key)
            if isinstance(gallery, list):
                urls += gallery
            elif isinstance(gallery, dict):
                urls += list(gallery.values())
    out = []
    for u in urls:
        cleaned = _url(u)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[: max(MAX_PHOTOS.values()) if limit is None else limit]


def _gallery_hash(item: dict) -> str:
    """SHA1 of the sorted gallery URL list — stable fingerprint for change detection (F10)."""
    urls = sorted(photo_urls(item))
    return hashlib.sha1(json.dumps(urls).encode()).hexdigest()


def needs_photo_check(item: dict) -> bool:
    """True when this home has photos and either no stored answer or a useless answer.

    A stored answer with shows_house=False means the vision model saw only
    documents, maps or generic images — not the building.  That is not useful
    evidence of condition, so the listing is re-queued the next time real photos
    appear (F09).

    A stored answer with shows_house=True is also re-queued when the gallery has
    grown since the check (new photos are available — F10).
    """
    kind = item.get("kind")
    if kind is None:
        from scoring import property_kind
        kind = property_kind(item)
    if kind != "home":
        return False
    rj = item.get("raw_json") or ""
    if '"photo_check"' in rj:
        try:
            pc = json.loads(rj).get("photo_check") or {}
        except (ValueError, TypeError):
            pass
        else:
            if pc.get("shows_house") is not False:
                stored = pc.get("gallery_hash")
                if not stored or stored == _gallery_hash(item):
                    return False  # already have a useful check, gallery unchanged
    return bool(photo_urls(item))


def photos_budget(items: list[dict], looker_name: str, configured: int | None = None) -> int:
    """How many homes this scan should look at.

    Settings `photos_per_scan` of 0 means "use the default for this looker".
    When many board homes still wait, raise to PHOTOS_CATCHUP so condition
    from photos can catch Italy's silent cards in days, not months."""
    if configured:
        return int(configured)
    pending = 0
    for item in items:
        if needs_photo_check(item):
            pending += 1
            if pending > PHOTOS_CATCHUP_HARD_WHEN:
                return PHOTOS_CATCHUP_HARD.get(looker_name, PHOTOS_CATCHUP.get(looker_name, 5))
    if pending > PHOTOS_CATCHUP_WHEN:
        return PHOTOS_CATCHUP.get(looker_name, PHOTOS_PER_SCAN.get(looker_name, 5))
    return PHOTOS_PER_SCAN.get(looker_name, 5)


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

    def look_images(self, images: list[bytes], prompt: str) -> dict:
        """Pictures we already fetched (the street and the satellite). The request
        does not carry a URL, so the Google key is not sent with them."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(data).decode("ascii")}}
                   for data in images]
        content.append({"type": "text", "text": prompt})
        resp = self.client.messages.create(
            model=self.model, max_tokens=400, messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": OUTSIDE_SCHEMA}},
        )
        return json.loads(next((b.text for b in resp.content if b.type == "text"), ""))


OLLAMA_PHOTO_SIDE = 448   # px: enough to see a roof or damp; a full photo is ~10x the work on a CPU


def shrink(data: bytes, side: int = OLLAMA_PHOTO_SIDE) -> bytes:
    """The photo scaled down to `side` px, as JPEG (unchanged without Pillow or when unreadable)."""
    try:
        from io import BytesIO

        from PIL import Image
        img = Image.open(BytesIO(data))
        if max(img.size) <= side:
            return data
        img = img.convert("RGB")
        img.thumbnail((side, side))
        out = BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:  # noqa: BLE001: Pillow missing or not an image: send it as it is
        return data


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
                images.append(base64.b64encode(shrink(resp.content)).decode("ascii"))
        if not images:
            raise ValueError("no photo could be fetched")
        resp = self.session.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "format": PHOTO_SCHEMA, "options": {"temperature": 0},
            "keep_alive": "5m",                # held between houses; freed soon after (6 GB laptop)

            "messages": [{"role": "user", "content": prompt, "images": images}],
        }, timeout=900)                        # a CPU takes its time
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])

    def look_images(self, images: list[bytes], prompt: str) -> dict:
        encoded = [base64.b64encode(data).decode("ascii") for data in images if data]
        if not encoded:
            raise ValueError("no photo could be fetched")
        resp = self.session.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "format": OUTSIDE_SCHEMA, "options": {"temperature": 0},
            "keep_alive": "5m",
            "messages": [{"role": "user", "content": prompt, "images": encoded}],
        }, timeout=900)
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])


def check_photos(looker, item: dict) -> dict | None:
    """Ask the model about one listing's photos. None when it has none."""
    urls = photo_urls(item)[:MAX_PHOTOS.get(looker.name, 2)]
    if not urls:
        return None
    found = looker.look(urls, PROMPT.format(title=(item.get("title") or "")[:150],
                                           description=" ".join((item.get("description") or "none").split())[:DESCRIPTION_CHARS]))
    if found.get("condition") not in PHOTO_SCHEMA["properties"]["condition"]["enum"]:
        raise ValueError(f"unexpected answer {found!r:.80}")
    return {"condition": found["condition"], "confidence": found.get("confidence", "low"),
            "notes": str(found.get("notes") or "")[:200], "photos": len(urls),
            "shows_house": found.get("shows_house") is not False,
            "gallery_hash": _gallery_hash(item),
            "by": getattr(looker, "model", looker.name), "at": utcnow_iso()}


# The sale's own pin, the cadastre, or a pin you verified. A street or a town
# pin is a different building, so a ruin there would be the wrong house.
OUTSIDE_EXACT = {"sale", "cadastre", "verified", "advertised"}
# How far a street photo may be from the pin. Farther than this is a different
# building. The aerial photo is a square of this half-width, so the pin sits
# in the middle.
OUTSIDE_M = 40
# Open pictures, no key. Street photos: Panoramax (the shared catalogue; dense
# in France, sparse elsewhere). Aerial photos, about 20–25 cm, where a national
# survey publishes one: France IGN, Spain PNOA, Portugal DGT. Germany and Italy
# have no single open national aerial service, so only a street photo is asked for.
PANORAMAX_SEARCH = "https://api.panoramax.xyz/api/search"
_AERIAL = {
    "FR": ("https://data.geopf.fr/wms-r/wms", "ORTHOIMAGERY.ORTHOPHOTOS", "EPSG:3857", "IGN"),
    "PT": ("https://cartografia.dgterritorio.gov.pt/wms/ortos2025", "Ortos2025-RGB", "EPSG:3763", "DGT"),
}
OUTSIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "condition": {"type": "string", "enum": ["heavy", "standing", "unknown"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
        "shows_house": {"type": "boolean"},
    },
    "required": ["condition", "confidence", "notes", "shows_house"],
    "additionalProperties": False,
}
OUTSIDE_PROMPT = """These pictures are the outside of one building at a sale's map pin: {title}.
One, if present, is an official aerial photo of the block around the pin.
One, if present, is the nearest open street photo (it may show the street rather than the door).
They are not the seller's photos. The inside is not shown.
- "heavy": an obvious ruin — no roof, a collapsed or fallen roof, or walls missing so it is a shell.
  Say "collapsed roof" or "missing walls" in notes.
- "standing": the roof is on, or the walls are up. Say "outside standing" in notes.
  This is not a judgment of the inside, the damp or the kitchen.
- "unknown": you cannot see this building, or you cannot tell which roof is the sale
  (trees, several roofs, the wrong house, only land, too blurred).
"shows_house": true only if a picture shows the dwelling itself, not a barn, a shed or the view.
Do not guess. If you are unsure, answer "unknown"."""


class OutsideUnavailable(Exception):
    """The picture server did not answer. Try next scan, and do not store a guess."""


def needs_outside_check(item: dict) -> bool:
    """A home with no photos, no stated condition, and a pin that is the building."""
    if '"outside_check"' in (item.get("raw_json") or ""):
        return False
    if photo_urls(item):
        return False
    kind = item.get("kind")
    if kind is None:
        from scoring import property_kind
        kind = property_kind(item)
    if kind != "home":
        return False
    from scoring import (GOOD_CONDITION, HEAVY_WORK, SOME_WORK, has_term, portal_condition,
                         _whole_house_good)
    if portal_condition(item):
        return False
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    if has_term(text, HEAVY_WORK + SOME_WORK) or (has_term(text, GOOD_CONDITION) and _whole_house_good(text)):
        return False
    import geo
    pos = geo.position(item)
    return bool(pos and pos.get("precision") in OUTSIDE_EXACT)


def _web_mercator(lat: float, lon: float) -> tuple[float, float]:
    x = math.radians(lon) * 6378137.0
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0
    return x, y


def _pt_tm06(lat: float, lon: float) -> tuple[float, float]:
    """ETRS89 / Portugal TM06 (EPSG:3763), metres. The DGT aerial service asks
    for this grid. The projection's own origin maps to (0, 0)."""
    a = 6378137.0
    f = 1 / 298.257222101
    lat0 = math.radians(39.66825833333333)
    lon0 = math.radians(-8.133108333333334)
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)

    def arc(phi: float) -> float:
        e4, e6 = e2 * e2, e2 * e2 * e2
        return a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                    - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                    + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                    - (35 * e6 / 3072) * math.sin(6 * phi))

    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    aa = (lam - lon0) * math.cos(phi)
    x = n * (aa + (1 - t + c) * aa ** 3 / 6
             + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa ** 5 / 120)
    y = (arc(phi) - arc(lat0) + n * math.tan(phi) * (
        aa ** 2 / 2 + (5 - t + 9 * c + 4 * c * c) * aa ** 4 / 24
        + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * aa ** 6 / 720))
    return x, y


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(-85.0, min(85.0, lat)))
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def aerial_url(country: str, lat: float, lon: float) -> tuple[str, str] | None:
    """(picture URL, who published it) for an open aerial photo of the block
    around the pin, or None where no such survey is wired in.

    Spain's map service answers a prepared tile quickly; a made-to-order picture
    of one field was too slow to use. France and Portugal draw a square centred
    on the pin."""
    if (country or "").upper() == "ES":
        x, y = _tile_xy(lat, lon, 19)
        url = ("https://www.ign.es/wmts/pnoa-ma?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
               "&LAYER=OI.OrthoimageCoverage&STYLE=default&TILEMATRIXSET=GoogleMapsCompatible"
               f"&FORMAT=image/jpeg&TILEMATRIX=19&TILECOL={x}&TILEROW={y}")
        return url, "PNOA"
    found = _AERIAL.get((country or "").upper())
    if not found:
        return None
    base, layer, crs, name = found
    if crs == "EPSG:3763":
        x, y = _pt_tm06(lat, lon)
    else:
        x, y = _web_mercator(lat, lon)
    half = float(OUTSIDE_M)
    query = urllib.parse.urlencode({
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer,
        "STYLES": "", "CRS": crs, "BBOX": f"{x - half:.1f},{y - half:.1f},{x + half:.1f},{y + half:.1f}",
        "WIDTH": 512, "HEIGHT": 512, "FORMAT": "image/jpeg",
    })
    return f"{base}?{query}", name


def street_search_url(lat: float, lon: float) -> str:
    """Panoramax pictures within OUTSIDE_M of the pin. No account."""
    dlat = OUTSIDE_M / 111320
    dlon = dlat / max(0.2, math.cos(math.radians(lat)))
    bbox = f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}"
    return PANORAMAX_SEARCH + "?" + urllib.parse.urlencode({"limit": 10, "bbox": bbox})


def _asset_href(feature: dict) -> str | None:
    assets = feature.get("assets") or {}
    for key in ("sd", "thumb", "hd"):
        href = (assets.get(key) or {}).get("href")
        if isinstance(href, str) and href.startswith("https://"):
            return href
    return None


def nearest_street_href(collection: dict, lat: float, lon: float) -> str | None:
    """The closest Panoramax photo within OUTSIDE_M, or None."""
    import geo
    best, best_km = None, None
    for feature in collection.get("features") or []:
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        try:
            km = geo.distance_km(lat, lon, float(coords[1]), float(coords[0]))
        except (TypeError, ValueError):
            continue
        if km * 1000 > OUTSIDE_M:
            continue
        href = _asset_href(feature)
        if href and (best_km is None or km < best_km):
            best, best_km = href, km
    return best


def _as_jpeg(data: bytes, side: int = OLLAMA_PHOTO_SIDE) -> bytes | None:
    """The picture as a JPEG no wider than `side`, or None when it is not an image."""
    try:
        from io import BytesIO

        from PIL import Image
        img = Image.open(BytesIO(data)).convert("RGB")
        if max(img.size) > side:
            img.thumbnail((side, side))
        out = BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — not an image, or Pillow is missing
        return None


def _read(session, url: str):
    """The response, or raises OutsideUnavailable when the server failed.
    None when this URL has nothing to show (a missing photo, an error page)."""
    resp = session.get(url, timeout=30)
    if resp.status_code >= 500 or resp.status_code in (401, 403):
        raise OutsideUnavailable()
    if resp.status_code != 200:
        return None
    return resp


def _jpeg_from(resp) -> bytes | None:
    if resp is None:
        return None
    kind = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not kind.startswith("image/") or not resp.content or len(resp.content) > MAX_PHOTO_BYTES:
        return None
    return _as_jpeg(resp.content)


def fetch_outside(session, pos: dict, country: str) -> tuple[list[bytes], list[str]]:
    """(pictures, sources) from Panoramax and, where one exists, the national
    aerial survey. A server failure raises. No picture nearby is an empty list."""
    images, sources = [], []
    lat, lon = float(pos["lat"]), float(pos["lon"])
    found = _read(session, street_search_url(lat, lon))
    href = None
    if found is not None:
        try:
            href = nearest_street_href(found.json(), lat, lon)
        except ValueError:
            href = None
    if href:
        jpeg = _jpeg_from(_read(session, href))
        if jpeg:
            images.append(jpeg)
            sources.append("Panoramax")
    aerial = aerial_url(country, lat, lon)
    if aerial:
        jpeg = _jpeg_from(_read(session, aerial[0]))
        if jpeg:
            images.append(jpeg)
            sources.append(aerial[1])
    return images, sources


def check_outside(looker, item: dict, session) -> dict | None:
    """What the open street photo and the aerial photo show: an obvious ruin,
    a standing outside, or nothing usable. None when this listing should not
    be looked at."""
    import geo
    if not needs_outside_check(item):
        return None
    pos = geo.position(item)
    images, sources = fetch_outside(session, pos, item.get("country") or "")
    if not images:
        return {"condition": "unknown", "confidence": "low", "notes": "no open picture at this pin",
                "shows_house": False, "photos": 0, "sources": sources, "by": "outside", "at": utcnow_iso()}
    found = looker.look_images(images, OUTSIDE_PROMPT.format(title=(item.get("title") or "")[:150]))
    if found.get("condition") not in OUTSIDE_SCHEMA["properties"]["condition"]["enum"]:
        raise ValueError(f"unexpected answer {found!r:.80}")
    return {"condition": found["condition"], "confidence": found.get("confidence", "low"),
            "notes": str(found.get("notes") or "")[:200], "photos": len(images),
            "sources": sources, "shows_house": found.get("shows_house") is not False,
            "by": getattr(looker, "model", None) or getattr(looker, "name", "outside"),
            "at": utcnow_iso()}


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


def _exact_sea(item: dict) -> bool:
    """The pin is the building and the sea is within 10 km. Photos of these come first."""
    from scoring import SWIM_MAX_KM
    beach = item.get("beach") or {}
    km = beach.get("km")
    return km is not None and not beach.get("approx") and km <= SWIM_MAX_KM


def queue(db, cfg: dict) -> list[dict]:
    """Homes to look at. Exact beach homes first, then the best of each home goal.

    Unknown condition is what makes the all-in estimate too wide to trust. The
    photos are how that gets settled, so a house by the sea is looked at before
    an inland one with a higher score."""
    from db import load_listings
    best: dict[str, dict] = {}

    def keep(it: dict) -> None:
        if it.get("kind") != "home":
            return
        rank = it.get("rank", it["score"])
        old = best.get(it["id"])
        if old is None or rank > old.get("rank", -1):
            best[it["id"]] = it

    for mode in ("both", "home", "invest"):
        for it in load_listings(db, filters=cfg.get("filters"), mode=mode):
            keep(it)
    # Hidden only because the all-in uses the middle of an unknown repair
    # range. A photo that shows a sound house uses the low end instead; not
    # when the price, taxes and fees are already over the cash (from
    # common.light_checks_from: db.heavy_checks_spared), as no photo brings it under.
    from common import light_checks_from
    from db import _cash_on_hand, heavy_checks_spared
    cash, light_from = _cash_on_hand(), light_checks_from(cfg)
    for it in load_listings(db, filters=cfg.get("filters"), mode="both", include_hidden=True):
        reason = it.get("hidden_reason") or ""
        if reason and not reason.startswith("cash "):
            continue
        if _exact_sea(it) and not heavy_checks_spared(it, cash, light_from):
            keep(it)
    return sorted(best.values(), key=lambda it: (not _exact_sea(it), (it.get("rank") or 0) < KEEP_SCORE,
                                                 -it.get("rank", it["score"])))


def photos_root(db) -> str | None:
    """Folder of saved photos, next to the database. None for a memory database."""
    path = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not path:
        return None
    return os.path.join(os.path.dirname(os.path.abspath(path)), "kept_photos")


def _photo_folder(listing_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(listing_id))
    return (cleaned or "listing")[:80]


def kept_path(db, listing_id: str, n: int) -> str | None:
    """The saved file for that photo, or None. The name has to be one this
    listing stored; a made-up number is not a file elsewhere on the disk."""
    root = photos_root(db)
    if not root or n < 0:
        return None
    row = db.execute("SELECT raw_json FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not row:
        return None
    try:
        raw = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        return None
    names = raw.get("kept_photos") if isinstance(raw, dict) else None
    if not isinstance(names, list) or n >= len(names):
        return None
    name = names[n]
    if not isinstance(name, str) or name != f"{n}.jpg":
        return None
    path = os.path.abspath(os.path.join(root, _photo_folder(listing_id), name))
    root_abs = os.path.abspath(root)
    try:
        inside = os.path.commonpath([root_abs, path]) == root_abs
    except ValueError:
        return None
    if not inside or not os.path.isfile(path):
        return None
    return path


def _worth_keeping(item: dict) -> bool:
    score = item.get("rank")
    if score is None:
        score = item.get("score") or 0
    return score >= KEEP_SCORE


def keep_one(db, session, item: dict) -> bool:
    """Save this listing's photos on disk. False when it is under KEEP_SCORE,
    already saved, or nothing could be fetched."""
    if not _worth_keeping(item):
        return False
    root = photos_root(db)
    if not root or not item.get("id"):
        return False
    folder = os.path.join(root, _photo_folder(item["id"]))
    os.makedirs(folder, exist_ok=True)
    try:
        raw = json.loads(item.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    urls = photo_urls(item, limit=KEEP_GALLERY)
    saved = [name for name in (raw.get("kept_photos") or [])
             if isinstance(name, str) and os.path.isfile(os.path.join(folder, name))]
    if saved and len(saved) >= min(len(urls), KEEP_GALLERY) and urls:
        return False
    changed = False
    names = []
    for i, url in enumerate(urls):
        name = f"{i}.jpg"
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            names.append(name)
            continue
        try:
            resp = session.get(url, timeout=30)
        except Exception:  # noqa: BLE001 — one dead picture is not the gallery
            break
        if resp.status_code != 200:
            break
        jpeg = _as_jpeg(resp.content, KEEP_SIDE)
        if not jpeg:
            break
        with open(path, "wb") as handle:
            handle.write(jpeg)
        names.append(name)
        changed = True
    if not names or not changed:
        return False
    raw["kept_photos"] = names
    db.execute("UPDATE listings SET raw_json = ? WHERE id = ?",
               (json.dumps(raw, ensure_ascii=False), item["id"]))
    from db import record_evidence_revision
    record_evidence_revision(db, item["id"], "kept_photos", names)
    # Track gallery URL list so callers can detect when new photos arrive (F10).
    record_evidence_revision(db, item["id"], "gallery_urls", sorted(urls))
    db.commit()
    return True


def keep_high(db, cfg: dict | None = None) -> int:
    """Save the photos of every listing at KEEP_SCORE or more in any goal.
    A few each scan; one already saved is left as it is."""
    from db import load_listings
    from scoring import MODES
    filters = (cfg or {}).get("filters")
    chosen: dict[str, dict] = {}
    for mode in MODES:
        for it in load_listings(db, filters=filters, include_hidden=True, mode=mode):
            if _worth_keeping(it) and it["id"] not in chosen:
                chosen[it["id"]] = it
    if not chosen:
        return 0
    from common import make_session
    session = make_session(timeout=30)
    done = 0
    for item in sorted(chosen.values(), key=lambda it: -(it.get("rank") or 0)):
        if done >= KEEP_PER_SCAN:
            break
        try:
            if keep_one(db, session, item):
                done += 1
        except Exception as e:  # noqa: BLE001 — a photo must never fail the scan
            LOG.info(f"Keeping photos of {item.get('id')} failed ({type(e).__name__})")
    if done:
        LOG.info(f"Photos kept for {done} listings at {KEEP_SCORE} or more")
    return done


def check_pending(db, cfg: dict, items: list[dict], limit: int | None = None, looker=None) -> int:
    """Look at the photos of the best homes not looked at yet, once each.

    A home with no photos but a pin on the building is looked at from an open
    street photo and, in Portugal, Spain and France, from the national aerial
    survey. No key. That look can call an obvious ruin; a standing outside is
    only noted."""
    ai = cfg.get("ai") or {}
    if ai.get("photo_check") is False:
        return 0
    looker = looker or make_looker(cfg)
    if looker is None:
        waiting = sum(1 for it in items if needs_photo_check(it))
        if waiting:
            LOG.info(f"Photo check: {waiting} homes waiting, but no looker is set up "
                     f"(start Ollama with the vision model, or set an Anthropic key)")
        return 0
    if limit is None:
        limit = photos_budget(items, looker.name, ai.get("photos_per_scan") or None)
    session = None
    done = failed = 0
    for item in items:
        if done >= limit:
            break
        has_photos = needs_photo_check(item)
        outside = needs_outside_check(item)
        if not has_photos and not outside:
            continue
        try:
            if has_photos:
                found = check_photos(looker, item)
                field = "photo_check"
            else:
                if session is None:
                    from common import make_session
                    session = make_session(timeout=30)
                found = check_outside(looker, item, session)
                field = "outside_check"
        except Exception as e:  # noqa: BLE001: a photo check must never fail the scan
            LOG.info(f"Photo check of {item['id']} failed ({type(e).__name__}); trying next scan")
            failed += 1
            if failed >= PHOTO_FAILURES_IN_A_ROW:   # the looker is down: don't wait out every home
                LOG.info("Photo check: stopped after repeated failures; trying next scan")
                break
            continue
        failed = 0
        if not found:
            continue
        try:
            raw = json.loads(item.get("raw_json") or "{}")
        except ValueError:
            raw = {}
        raw[field] = found
        db.execute("UPDATE listings SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), item["id"]))
        from db import record_evidence_revision
        record_evidence_revision(db, item["id"], field, found)
        db.commit()
        done += 1
    if done:
        LOG.info(f"Photo check: {done} listings looked at")
    return done
