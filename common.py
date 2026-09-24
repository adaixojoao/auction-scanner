"""
common.py — helpers shared by every scraper, the scorer and the views.

Everything a scraper used to copy-paste lives here: the HTTP session (retries +
proxy rotation), the listing dict builder, price/date parsing, stable IDs and
word-boundary-aware keyword matching.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("auction-scanner")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

COUNTRY_NAMES = {
    "PT": "Portugal", "ES": "Spain", "FR": "France", "IT": "Italy",
    "DE": "Germany", "NL": "Netherlands", "BE": "Belgium", "HR": "Croatia",
    "GR": "Greece", "RO": "Romania", "PL": "Poland", "CY": "Cyprus",
}
# Display order everywhere (report, console, dashboard): Portugal first.
COUNTRY_ORDER = list(COUNTRY_NAMES)

FLAGS = {
    code: "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)
    for code in COUNTRY_NAMES
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


# ─── HTTP ────────────────────────────────────────────────────────────

_proxy_config: dict | None = None


def configure_http(proxy_config: dict | None):
    """Set the proxy config every session made afterwards should use."""
    global _proxy_config
    _proxy_config = proxy_config if proxy_config and proxy_config.get("enabled") else None


class ScraperSession(requests.Session):
    """A requests.Session with a default timeout and optional proxy rotation."""

    def __init__(self, timeout: float = 20, rotator=None):
        super().__init__()
        self.default_timeout = timeout
        self._rotator = rotator

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        if self._rotator is not None:
            self._rotator.tick()
            kwargs.setdefault("proxies", self._rotator.current_proxy)
        return super().request(method, url, **kwargs)


def make_session(*, verify: bool = True, timeout: float = 20,
                 headers: dict | None = None) -> ScraperSession:
    """Session used by every scraper.

    Retries connection failures and 429/502/503/504 twice with backoff, but never
    read timeouts: a dead site should cost one timeout, not three.
    """
    rotator = None
    if _proxy_config and _proxy_config.get("list"):
        from proxy import ProxyRotator
        rotator = ProxyRotator(_proxy_config["list"], _proxy_config.get("rotate_every", 5))

    session = ScraperSession(timeout=timeout, rotator=rotator)
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept-Language"] = "pt-PT,pt;q=0.9,en;q=0.8"
    if headers:
        session.headers.update(headers)
    session.verify = verify

    retry = Retry(
        total=2, connect=2, read=0, status=2,
        backoff_factor=1.0,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=None,
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ─── Listings ────────────────────────────────────────────────────────

LISTING_FIELDS = (
    "title", "description", "tipo", "area_m2", "price", "current_bid",
    "min_price", "district", "concelho", "freguesia", "url", "image_url",
    "date_end", "raw_json",
)
_NUMERIC_FIELDS = {"area_m2", "price", "current_bid", "min_price"}


def stable_id(*parts) -> str:
    """Deterministic short ID. Never use hash(): it changes on every run."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def to_number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_price(value)


def make_listing(source: str, external_id, country: str = "PT", *,
                 id_prefix: str | None = None, base_url: str | None = None,
                 **fields) -> dict:
    """Build the row dict upsert_listing() expects, with every field present.

    `id_prefix` exists only so sources whose IDs predate this helper keep them
    (e.g. "justiz:" for justiz_auktion). New sources should leave it unset.
    """
    unknown = set(fields) - set(LISTING_FIELDS)
    if unknown:
        raise TypeError(f"make_listing: unknown field(s) {sorted(unknown)}")
    eid = str(external_id).strip()
    if not eid:
        raise ValueError(f"{source}: empty external_id")

    row = {f: None for f in LISTING_FIELDS}
    row.update(fields)
    row["id"] = f"{id_prefix or source}:{eid}"
    row["source"] = source
    row["country"] = country
    row["external_id"] = eid

    for key in ("title", "tipo", "district", "concelho", "freguesia"):
        row[key] = clean_text(row[key])
    if row["description"] is not None:
        row["description"] = str(row["description"]).strip() or None
    for key in _NUMERIC_FIELDS:
        row[key] = to_number(row[key])
    if row["current_bid"] is not None and row["current_bid"] <= 0:
        row["current_bid"] = None
    row["url"] = safe_url(row["url"], base_url)
    row["image_url"] = safe_url(row["image_url"], base_url)
    if row["date_end"] is not None:
        row["date_end"] = str(row["date_end"]).strip() or None
    if isinstance(row["raw_json"], (dict, list)):
        row["raw_json"] = json.dumps(row["raw_json"], ensure_ascii=False, default=str)
    return row


def safe_url(url, base: str | None = None) -> str | None:
    """Absolute http(s) URL or None. Blocks javascript:/data: links from scraped pages."""
    if not url:
        return None
    url = str(url).strip()
    if base:
        url = urllib.parse.urljoin(base if base.endswith("/") else base + "/", url)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    for ch, enc in (('"', "%22"), ("'", "%27"), ("<", "%3C"), (">", "%3E"),
                    ("`", "%60"), (" ", "%20")):
        url = url.replace(ch, enc)
    return url


# ─── Parsing ─────────────────────────────────────────────────────────

_NO_PRICE_RE = re.compile(r"\bsin\s+(puja|lotes|tramos|m[ií]nima)", re.I)
_NUMBER_RE = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})+,\d{1,2}"      # 36.163,00  /  36 163,00
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"    # 36,163  /  36,163.00
    r"|\d+,\d{1,2}(?!\d)"                   # 1234,50
    r"|\d{1,3}(?:[.\s]\d{3})+"              # 36.163  /  36 163
    r"|\d+(?:\.\d{1,2})?)"                  # 36163  /  36163.5
)
_CURRENCY_NUMBER_RE = re.compile(
    r"(?:€|EUR\b|euros?\b)\s*:?\s*(?P<a>\d[\d.,\s]*\d|\d)"
    r"|(?P<b>\d[\d.,\s]*\d|\d)\s*(?:€|EUR\b|euros?\b)",
    re.I,
)


def _number_from_match(num: str) -> float | None:
    num = num.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", num):
        num = num.replace(",", "")                 # English thousands
    elif "," in num:
        num = num.replace(".", "").replace(",", ".")  # European decimal comma
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", num):
        num = num.replace(".", "")                 # European thousands
    try:
        return float(num)
    except ValueError:
        return None


def parse_price(text) -> float | None:
    """First amount in `text`, European or English formatted.

    "36.163,00 €" → 36163.0, "36 163" → 36163.0, "1,234" → 1234.0.
    Use this on a field that holds a price. For a whole card of text, use
    find_price(), which ignores numbers without a currency marker (so "T2" is
    not read as €2).
    """
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = str(text).replace("\xa0", " ").replace(" ", " ").strip()
    if not s or _NO_PRICE_RE.search(s):
        return None
    m = _NUMBER_RE.search(s)
    return _number_from_match(m.group(1)) if m else None


def find_price(text) -> float | None:
    """First amount next to €/EUR/euro in a block of text, else None."""
    if not text:
        return None
    s = str(text).replace("\xa0", " ").replace(" ", " ")
    for m in _CURRENCY_NUMBER_RE.finditer(s):
        raw = (m.group("a") or m.group("b") or "").strip()
        inner = _NUMBER_RE.search(raw)
        if not inner:
            continue
        # The greedy group may have swallowed a preceding number ("T2 85.000 €");
        # take the last number run, which is the one touching the currency sign.
        runs = list(_NUMBER_RE.finditer(raw))
        chosen = runs[0] if m.group("a") else runs[-1]
        value = _number_from_match(chosen.group(1))
        if value is not None:
            return value
    return None


_DMY_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")


def parse_date_dmy(text) -> str | None:
    """First dd/mm/yyyy (or dd.mm.yyyy / dd-mm-yyyy) in text, as ISO date-time."""
    if not text:
        return None
    m = _DMY_RE.search(str(text))
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def parse_dt(value) -> datetime | None:
    """Parse a stored date_end/timestamp into an aware datetime (naive → UTC)."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            iso = parse_date_dmy(s)
            if not iso:
                return None
            dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def effective_end(date_end) -> datetime | None:
    """When a listing stops being biddable. A bare date (midnight) means that whole day."""
    dt = parse_dt(date_end)
    if dt is not None and (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0):
        dt = dt + timedelta(days=1) - timedelta(seconds=1)
    return dt


def days_left(date_end, now: datetime | None = None) -> float | None:
    end = effective_end(date_end)
    if end is None:
        return None
    now = now or utcnow()
    return (end - now).total_seconds() / 86400


# ─── Text matching ───────────────────────────────────────────────────

def normalize(text) -> str:
    """Lower-case and strip accents, so "Ruína" and "ruina" compare equal."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text)).replace("⁄", "/")  # ½ → 1/2
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


@functools.lru_cache(maxsize=4096)
def term_regex(term: str) -> re.Pattern:
    """Regex for one keyword, matched on whole words.

    - "ocupado" does not match "desocupado"; "casa" does not match "Casal".
    - "1/2" matches "1 / 2" and "1/2" but not "11/2023" or "01/2025".
    - A trailing "*" makes it a prefix: "nue-propri*" matches "nue-propriété".
    """
    prefix = term.endswith("*")
    body = normalize(term.rstrip("*")).strip()
    pattern = ""
    for ch in body:
        if ch == "/":
            pattern += r"\s*/\s*"
        elif ch.isspace():
            pattern += r"\s+"
        elif ch == "-":
            pattern += r"[-\s]?"
        else:
            pattern += re.escape(ch)
    left = r"(?<![0-9/.,])" if body[:1].isdigit() else r"(?<![0-9a-z])"
    if prefix:
        right = ""
    elif body[-1:].isdigit():
        right = r"(?![0-9/]|[.,]\d)"
    else:
        right = r"(?![0-9a-z])"
    return re.compile(left + pattern + right)


NEGATIONS = {"nao", "sem", "not", "non", "livre", "libre", "free", "nicht",
             "kein", "keine", "ni", "senza", "geen"}


def _negated(norm: str, start: int) -> bool:
    clause = re.split(r"[,.;:()\[\]!?\n]", norm[max(0, start - 40):start])[-1]
    return any(w in NEGATIONS for w in re.findall(r"[a-z]+", clause)[-3:])


def find_terms(text, terms, *, negations: bool = True) -> list[str]:
    """Terms from `terms` present in `text` as whole words.

    With negations=True a match preceded, in the same clause and within three
    words, by a negation — "não se encontra ocupado", "sem inquilino", "livre
    de ocupantes" — does not count.
    """
    norm = normalize(text)
    if not norm:
        return []
    found = []
    for term in terms:
        for m in term_regex(term).finditer(norm):
            if negations and _negated(norm, m.start()):
                continue
            found.append(term)
            break
    return found


def has_term(text, terms, *, negations: bool = True) -> bool:
    return bool(find_terms(text, terms, negations=negations))
