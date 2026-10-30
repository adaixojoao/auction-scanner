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
import time
import unicodedata
import urllib.parse
from contextlib import contextmanager
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
    "GR": "Greece", "RO": "Romania", "PL": "Poland", "CY": "Cyprus", "BG": "Bulgaria", "SK": "Slovakia",
    "LV": "Latvia", "LT": "Lithuania",
    "AT": "Austria", "LU": "Luxembourg", "IE": "Ireland", "EE": "Estonia",
    "FI": "Finland", "GB": "United Kingdom",
}
# Display order everywhere (report, console, dashboard): Portugal first.
COUNTRY_ORDER = list(COUNTRY_NAMES)

FLAGS = {
    code: "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)
    for code in COUNTRY_NAMES
}


# The owner's goals have a budget each (2026-10-05): a home to live in
# ("max_price"), a home bought to let or resell ("invest_max_price", normally
# higher: a house far below the local price is rarely the cheapest house), and
# land ("land_max_price"). A scrape keeps anything under the highest of them,
# so no goal is thrown away at scrape time; each tab then applies its own.
LAND_MAX_PRICE = 100_000
INVEST_MAX_PRICE = 100_000
# The owner's rules for the work spent on a listing (2026-10-13): above
# CHECK_MAX_PRICE (what you would pay, price_to_pay) a listing is kept only so
# the scanner knows it, and is never scored, checked or shown; from
# LIGHT_CHECKS_FROM the light checks come first, and the heavy ones (climate
# maps, photos) only for a listing that can be shown. No budget goes above
# CHECK_MAX_PRICE. 0 turns a rule off.
CHECK_MAX_PRICE = 100_000
LIGHT_CHECKS_FROM = 50_000


def _setting(config: dict | None, key: str, default: float) -> float:
    try:
        return max(0.0, float((config or {}).get(key, default) or 0))
    except (TypeError, ValueError):
        return float(default)


def check_max_price(config: dict | None) -> float:
    """Above this, a listing is never scored, checked or shown (0: no limit)."""
    return _setting(config, "check_max_price", CHECK_MAX_PRICE)


def light_checks_from(config: dict | None) -> float:
    """From this price, heavy checks wait until the light ones say it can be shown (0: off)."""
    return _setting(config, "light_checks_from", LIGHT_CHECKS_FROM)


def _capped(config: dict | None, budget: float) -> float:
    cap = check_max_price(config)
    return min(budget, cap) if cap else budget


def _budget(config: dict | None, key: str, default: float, floor: float) -> float:
    try:
        budget = max(float(floor), float((config or {}).get(key) or default))
    except (TypeError, ValueError):
        budget = max(float(floor), default)
    return _capped(config, budget)


def land_max_price(config: dict | None, max_price: float) -> float:
    """The price limit for land searches: never below the house limit."""
    return _budget(config, "land_max_price", LAND_MAX_PRICE, max_price)


def invest_max_price(config: dict | None, max_price: float) -> float:
    """The price limit for a home bought as an investment."""
    return _budget(config, "invest_max_price", INVEST_MAX_PRICE, max_price)


def scrape_max_price(config: dict | None, max_price: float) -> float:
    """What a scrape must keep: the highest of the three budgets. A listing
    above every budget can serve no goal, so it is not stored."""
    return max(invest_max_price(config, max_price), land_max_price(config, max_price))


def mode_max_price(config: dict | None, mode: str, max_price: float) -> float:
    """The budget of one goal (scoring.MODES): what that tab, its alerts and
    its report section may show.

    Live and invest has no asking-price cap. A €19,000 ruin can cost far more
    to repair than a sound house at €35,000. The ceiling is the all-in estimate
    against the cash (costs.over_cash), so this returns 0 and the listings page
    does not filter on the asking price."""
    if mode == "both":
        return 0.0
    if mode == "invest":
        return invest_max_price(config, max_price)
    if mode in ("land", "forest"):
        return land_max_price(config, max_price)
    return _capped(config, float(max_price))


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


class SourceTimeLimit(requests.exceptions.RequestException):
    """The source ran past its time limit (sources.run_source): every request
    after that fails at once, so the scraper's own loops end."""


# When the source being scraped must stop (time.monotonic()), or None.
_SOURCE_DEADLINE: list = [None]


@contextmanager
def source_deadline(seconds: float | None):
    """Requests made inside fail with SourceTimeLimit once `seconds` have passed."""
    _SOURCE_DEADLINE[0] = time.monotonic() + seconds if seconds else None
    try:
        yield
    finally:
        _SOURCE_DEADLINE[0] = None


def past_source_deadline() -> bool:
    deadline = _SOURCE_DEADLINE[0]
    return deadline is not None and time.monotonic() >= deadline


class ScraperSession(requests.Session):
    """A requests.Session with a default timeout and optional proxy rotation."""

    def __init__(self, timeout: float = 20, rotator=None):
        super().__init__()
        self.default_timeout = timeout
        self._rotator = rotator

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        deadline = _SOURCE_DEADLINE[0]
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SourceTimeLimit("the source's time limit is over")
            # One slow request must not run far past the limit either.
            if isinstance(kwargs["timeout"], (int, float)):
                kwargs["timeout"] = min(kwargs["timeout"], max(left, 1))
        if self.verify is True and "verify" not in kwargs:
            extra = HOST_INTERMEDIATES.get(urllib.parse.urlsplit(url).hostname or "")
            if extra:
                kwargs["verify"] = ca_bundle_with(*extra)
        if self._rotator is not None:
            self._rotator.tick()
            kwargs.setdefault("proxies", self._rotator.current_proxy)
        return super().request(method, url, **kwargs)


CERTS_DIR = __import__("pathlib").Path(__file__).resolve().parent / "certs"


# Hosts that serve the wrong intermediate certificate, and the one they need.
# Every ScraperSession verifies these with it, so a link check or a detail
# page reaches them as the scraper does. citius.mj.pt: leaf signed by Let's
# Encrypt YR2, served with YR1 (2026-10-24).
HOST_INTERMEDIATES = {
    "www.citius.mj.pt": ("letsencrypt-yr2.pem",),
    "citius.mj.pt": ("letsencrypt-yr2.pem",),
    # e-leiloes.pt: sends only its leaf, signed by Sectigo DV R36.
    "e-leiloes.pt": ("sectigo-dv-r36.pem",),
    "www.e-leiloes.pt": ("sectigo-dv-r36.pem",),
}


@functools.lru_cache(maxsize=None)
def ca_bundle_with(*pem_names: str) -> str:
    """Path of a CA bundle: certifi's roots plus the intermediates in certs/.

    For a host that serves the wrong intermediate: verification stays on, and
    the missing link is supplied instead of turned off.
    """
    import certifi
    import tempfile
    from pathlib import Path
    data = Path(certifi.where()).read_bytes()
    for name in pem_names:
        data += b"\n" + (CERTS_DIR / name).read_bytes()
    out = Path(tempfile.gettempdir()) / f"auction-scanner-ca-{'-'.join(pem_names)}.pem"
    out.write_bytes(data)
    return str(out)


def make_session(*, verify: bool | str = True, timeout: float = 20,
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


# ─── Processes ───────────────────────────────────────────────────────

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


_UF_PARISH = re.compile(r"^\s*U\.?\s*F\.?\s+(.+?)(?:\s*\(([^)]+)\))?\s*$", re.I)


def uf_parish(name: str | None) -> str | None:
    """Portuguese união de freguesias → a place name Nominatim can find.

    Whitestar stores labels like "U.F. BEJA (SALVADOR E SANTA MARIA DA FEIRA)".
    The "U.F." prefix is not a place; prefer the parenthetical parish list, else
    the short name after the prefix. Leave ordinary parish names alone."""
    text = clean_text(name)
    if not text:
        return None
    m = _UF_PARISH.match(text)
    if not m:
        return text
    cleaned = clean_text(m.group(2) or m.group(1))
    return cleaned.title() if cleaned else None


def to_number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_price(value)


# On an asking-price portal these words mean the figure in the price field is a
# deposit or a minimum bid (a court sale advertised on fotocasa), not what buys the house.
PORTAL_NOT_A_PRICE = ("subasta", "leilao", "vente aux encheres", "asta")


def portal_price_unusable(item: dict) -> str | None:
    """Why the portal's price must not be treated as the purchase price, or None."""
    import source_validation
    if not source_validation.asking_price(item.get("source")):
        return None
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    hits = find_terms(text, PORTAL_NOT_A_PRICE, negations=False)
    if not hits:
        return None
    return ("the listed price is a deposit or a minimum bid, not the purchase price "
            f"(the ad says {hits[0]})")


def price_to_pay(item: dict) -> float:
    """What you would realistically pay for a listing: the current bid, else the
    minimum accepted, else the base value. A bid below the minimum accepted does
    not buy it (e-leilões opens at 50% but accepts from 85%), so the minimum
    wins then. A floor under 20% of the base value is a bidding step (Spain's
    "puja mínima" of €1,743 on a €174,300 flat), not a price that buys it. One
    definition, used by the scorer, the round matcher, the cost estimate and Offers.

    A portal advert that says it is an auction does not have a purchase price:
    the field is the deposit or the opening bid."""
    note = portal_price_unusable(item)
    if note:
        item["price_unusable"] = note
        return 0.0
    bid = item.get("current_bid") or 0
    floor = item.get("min_price") or 0
    base = item.get("price") or 0
    if floor and base and floor < 0.2 * base:
        floor = 0
    pay = max(bid, floor) if bid else (floor or base or 0)
    return pay + _charges(item) if pay else pay


def _charges(item: dict) -> float:
    """Debts that stay with the property after the sale ("cargas" in a TGSS
    auction): the buyer pays them on top of the bid."""
    raw = item.get("raw_json")
    if not raw or "charges_eur" not in raw:
        return 0.0
    try:
        return float(json.loads(raw).get("charges_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


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


_AREA_RE = re.compile(
    r"(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d+)?|\d+ ?, ?\d{1,2}(?=\s*m)|\d+(?:[.,]\d+)?)\s*"
    r"(m²|m2|m\s?2|mq|sq\.?\s?m|ha|hectares?|metros?\s+cuadrados|metros?\s+quadrados"
    r"|metri\s+quadri|mètres?\s+carrés|metres?\s+carres)(?![a-z])", re.I)
# "superficie total de 45,33 metros" — the unit is cut off, but the word says it is the area.
_SURFACE_METROS = re.compile(
    r"(?:superficie\w*|area).{0,50}?"
    r"(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s+metros?\b",
    re.I)
# Court notices spell the size: "cincuenta y cinco metros con sesenta y seis decímetros".
# Longer words first so "doscientos" is not read as "dos".
_SPOKEN = {
    "cero": 0, "un": 1, "uno": 1, "uma": 1, "una": 1,
    "dos": 2, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "sete": 7, "ocho": 8, "oito": 8, "nueve": 9, "nove": 9,
    "diez": 10, "dez": 10, "once": 11, "onze": 11, "doce": 12, "trece": 13, "treze": 13,
    "catorce": 14, "catorze": 14, "quince": 15, "quinze": 15,
    "dieciseis": 16, "dezasseis": 16, "diecisiete": 17, "dezassete": 17,
    "dieciocho": 18, "dezoito": 18, "diecinueve": 19, "dezanove": 19,
    "veinte": 20, "vinte": 20, "veintiun": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "trinta": 30, "cuarenta": 40, "quarenta": 40,
    "cincuenta": 50, "cinquenta": 50, "sesenta": 60, "sessenta": 60, "setenta": 70,
    "ochenta": 80, "oitenta": 80, "noventa": 90,
    "cien": 100, "cem": 100, "cento": 100, "ciento": 100,
    "doscientos": 200, "doscientas": 200, "duzentos": 200,
    "trescientos": 300, "trescientas": 300, "trezentos": 300,
    "cuatrocientos": 400, "cuatrocientas": 400, "quatrocentos": 400,
    "quinientos": 500, "quinientas": 500, "quinhentos": 500,
    "seiscientos": 600, "seiscientas": 600, "seiscentos": 600,
    "setecientos": 700, "setecientas": 700, "setecentos": 700,
    "ochocientos": 800, "ochocientas": 800, "oitocentos": 800,
    "novecientos": 900, "novecientas": 900, "novecentos": 900,
    "mil": 1000,
}
_SPOKEN_TOKEN = "|".join(sorted(_SPOKEN, key=len, reverse=True))
_SPOKEN_SEQ = rf"(?:{_SPOKEN_TOKEN})(?:\s+(?:y|e)\s+(?:{_SPOKEN_TOKEN})|\s+(?:{_SPOKEN_TOKEN})){{0,8}}"
_WRITTEN_SQUARE = re.compile(
    rf"\b({_SPOKEN_SEQ})\s+metros?\s+(?:cuadrados|quadrados)"
    rf"(?:\s+(?:con\s+|y\s+|e\s+)?({_SPOKEN_SEQ})\s+decimetros?)?",
    re.I)
_WRITTEN_SURFACE = re.compile(
    rf"(?:superficie\w*|area).{{0,60}}?({_SPOKEN_SEQ})\s+metros?"
    rf"(?:\s+(?:con\s+|y\s+|e\s+)?({_SPOKEN_SEQ})\s+decimetros?)?",
    re.I)
_WRITTEN_DM = re.compile(
    rf"\b({_SPOKEN_SEQ})\s+metros?\s+(?:con\s+|y\s+|e\s+)?({_SPOKEN_SEQ})\s+decimetros?",
    re.I)


def _area_value(number: str, unit: str) -> float | None:
    number = number.replace("\u00a0", " ")
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?", number):
        value = float(number.replace(" ", "").replace(".", "").replace(",", "."))
    else:                          # "106,63", and a typed "106, 63"
        value = float(number.replace(" ", "").replace(",", "."))
    if unit.lower().startswith("h"):
        value *= 10000
    return value if value > 0 else None


def _spoken_int(phrase: str | None) -> int | None:
    """"ciento ochenta y tres" → 183. None when the phrase is not a number."""
    if not phrase:
        return None
    total = current = 0
    got = False
    for tok in phrase.split():
        if tok in ("y", "e"):
            if not got:
                return None
            continue
        val = _SPOKEN.get(tok)
        if val is None:
            return None
        got = True
        if tok == "mil":
            current = (current or 1) * 1000
            total += current
            current = 0
        else:
            current += val
    return total + current if got else None


def _norm_origins(text: str) -> tuple[str, list[int]]:
    """Normalized text, and the original index of each of its characters."""
    chars: list[str] = []
    origins: list[int] = []
    for i, ch in enumerate(text):
        for c in normalize(ch):
            chars.append(c)
            origins.append(i)
    return "".join(chars), origins


def _surface_digit_areas(text: str) -> list[tuple[int, float]]:
    """"superficie total de 45,33 metros" → (index, 45.33). Searched without accents."""
    if "metro" not in text.lower():
        return []
    norm, origins = _norm_origins(text)
    found = []
    for m in _SURFACE_METROS.finditer(norm):
        value = _area_value(m.group(1), "m2")
        if value:
            found.append((origins[m.start(1)], value))
    return found


def _written_areas(text: str) -> list[tuple[int, float]]:
    """Spelled sizes, as (index in `text`, m²). A frontage ("frente de dos metros") is not one."""
    if "metro" not in text.lower():
        return []
    norm, origins = _norm_origins(text)
    found: list[tuple[int, float]] = []
    for rx in (_WRITTEN_SQUARE, _WRITTEN_DM, _WRITTEN_SURFACE):
        for m in rx.finditer(norm):
            whole = _spoken_int(m.group(1))
            if whole is None or not 0 < whole <= 500_000:
                continue
            extra = _spoken_int(m.group(2))
            value = whole + (extra or 0) / 100
            start = origins[m.start(1)]
            if any(abs(prev - start) < 3 for prev, _ in found):
                continue
            found.append((start, value))
    return found


def _iter_areas(text) -> list[tuple[int, float]]:
    """Every size as (index, m²), in the order written."""
    text = str(text or "")
    found: list[tuple[int, float]] = []
    for m in _AREA_RE.finditer(text):
        value = _area_value(m.group(1), m.group(2))
        if value:
            found.append((m.start(), value))
    for start, value in _surface_digit_areas(text):
        if not any(abs(prev - start) < 5 for prev, _ in found):
            found.append((start, value))
    for start, value in _written_areas(text):
        if not any(abs(prev - start) < 5 for prev, _ in found):
            found.append((start, value))
    if not found:
        # Italian court texts put the unit first: "di MQ. 90", "metri quadri 9".
        m = _AREA_UNIT_FIRST_RE.search(text)
        if m:
            value = float(m.group(1).replace(".", "").replace(",", "."))
            if value > 0:
                found.append((m.start(1), value))
    found.sort()
    return found


def find_areas(text) -> list[float]:
    """Every size in a text, in m², in the order written."""
    return [value for _, value in _iter_areas(text)]


def find_area(text) -> float | None:
    """The first size in a text, in m²: "d'environ 35,50 m²" → 35.5,
    "com 1.250 m2" → 1250, "8 ha" → 80000, "cincuenta metros cuadrados" → 50.
    None if there is none."""
    found = find_areas(text)
    return found[0] if found else None


def possession(text) -> str | None:
    """A sale's own occupancy line → "occupied" / "vacant" / None.

    "No consta" stays unknown, even when the same note later says occupied.
    "Sin ocupantes" is vacant; "ocupantes sin derecho" is still someone living there."""
    t = normalize(text)
    if not t or "no consta" in t:
        return None
    if "libre" in t or "desocupad" in t or "sin ocupantes" in t:
        return "vacant"
    if "ocupad" in t or "ocupante" in t or "okupa" in t or "arrendad" in t or "inquilin" in t:
        return "occupied"
    return None


def auction_occupation(flags) -> str | None:
    """PVP availability codes → "occupied" / "vacant" / None.

    LIBER is vacant. OCCUP, OCCST (no title) and INCOR (being vacated) are
    occupied. A mix is occupied. NON_SPECIFICATA says nothing."""
    codes = {str(x).upper() for x in flags or [] if x}
    codes.discard("NON_SPECIFICATA")
    if not codes:
        return None
    if codes <= {"LIBER"}:
        return "vacant"
    if codes & {"OCCUP", "OCCST", "INCOR"}:
        return "occupied"
    return None


# A size the text gives the land, not the house: "un terrain de 400 m²", "parcela de 767 m2".
_LAND_BEFORE_AREA = re.compile(
    r"\b(?:terrain|terreno|terrenos|parcela|parcelle|jardin|jard[ií]n|garden|plot|lote|solar|finca|"
    r"quintal|logradouro|land|grundst\w*|giardino|cour|courette|patio)\b[^.;,\d]{0,32}$", re.I)
# {0,32}: "terreno pertinenziale di circa 324 mq" (Sanremo, 2026-10-31) is 24
# characters from the land word to the figure, and the house had no size of its own.
# The footprint on the ground, not the floor: "área de implantação do edifício de 47 m²".
_FOOTPRINT_BEFORE_AREA = re.compile(
    r"\b(?:implanta\w*|emprise|footprint)\b[^.;,\d]{0,24}$", re.I)


def _area_introduced_by(text: str, area: float, pattern: re.Pattern) -> bool:
    """True when `area` occurs in `text` and `pattern` introduces every time."""
    try:
        area = float(area)
    except (TypeError, ValueError):
        return False
    if area <= 0:
        return False
    hits = marked = 0
    window = 48
    for start, value in _iter_areas(text):
        if abs(value - area) > 0.05:
            continue
        hits += 1
        if pattern.search(text[max(0, start - window):start]):
            marked += 1
    return hits > 0 and marked == hits


# A counted block: "trois appartements T3", "2 logements de type T2 et un T4".
# One flat ("un appartement T3", "Moradia T3") is not a block. "dos" is left
# out: in Portuguese it is "of the", not the number two.
_FLAT_NUM = (
    r"1[0-2]|[1-9]|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|"
    r"um|uma|dois|duas|tres|cuatro|cinco|due|tre|quattro"
)
_FLAT_NOUN = r"appartements?|apartamentos?|logements?|fogos?|viviendas?|alloggi|alloggio|apartments?|flats?"
_FLAT_WORD = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "due": 2, "tre": 3, "quattro": 4,
}
_TYPED_FLATS = re.compile(
    rf"\b({_FLAT_NUM})\s+(?:({_FLAT_NOUN})\s+)?(?:de\s+type\s+|type\s+)?([tf])\s*([1-6])\b")
_UNTYPED_FLATS = re.compile(rf"\b({_FLAT_NUM})\s+({_FLAT_NOUN})\b")
# "maison individuelle … de type F2": one dwelling, when no count already covers it.
_TYPE_ONLY = re.compile(r"\b(?:de\s+)?type\s+([tf])\s*([1-6])\b")


def _flat_number(word: str) -> int:
    return _FLAT_WORD[word] if word in _FLAT_WORD else int(word)


def counted_flats(text) -> tuple[dict[str, int], int]:
    """({"T3": 3}, 0) when the text says three T3 apartments, and how many
    more it counts with no type.

    F3 is the same dwelling as T3. A bare "T3", or one apartment, is still
    returned; a caller that guesses a floor requires two or more. The same
    phrase is not read twice."""
    low = normalize(text)
    typed: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    for m in _TYPED_FLATS.finditer(low):
        noun_here = m.group(2)
        if not noun_here:
            # "et un T3": only when this sentence already named a flat, and the
            # count is a word. A digit here is a lot number ("appartement n°3 T3").
            if m.group(1).isdigit():
                continue
            earlier = low[low.rfind(".", 0, m.start()) + 1:m.start()]
            if not re.search(rf"\b(?:{_FLAT_NOUN})\b", earlier):
                continue
        n = _flat_number(m.group(1))
        if n < 1:
            continue
        key = "T" + m.group(4)
        typed[key] = typed.get(key, 0) + n
        spans.append((m.start(), m.end()))
    for m in _TYPE_ONLY.finditer(low):
        if any(a <= m.start() and m.end() <= b for a, b in spans):
            continue
        key = "T" + m.group(2)
        typed[key] = typed.get(key, 0) + 1
        spans.append((m.start(), m.end()))
    untyped = 0
    for m in _UNTYPED_FLATS.finditer(low):
        if any(a <= m.start() and m.end() <= b for a, b in spans):
            continue
        untyped += _flat_number(m.group(1))
    return typed, untyped


def find_floor_area(text) -> float | None:
    """The first size in a text that is not the land's or the footprint: the
    floor area a rent or a value can be worked on. A plot sold as "Maison"
    with only "terrain 1034 m²", or only "área de implantação", has none,
    which is better than a rent on the land or on the ground it covers."""
    text = str(text or "")
    for start, value in _iter_areas(text):
        before = text[max(0, start - 48):start]
        if _LAND_BEFORE_AREA.search(before) or _FOOTPRINT_BEFORE_AREA.search(before):
            continue
        return value
    return None


def land_figure(text, area: float) -> bool:
    """True when `area` is written in `text` and a land word introduces every time.

    The same window as find_floor_area. A size that is also given as floor
    ("casa de 90 m², terreno de 90 m²") is not only land."""
    return _area_introduced_by(str(text or ""), area, _LAND_BEFORE_AREA)


def footprint_figure(text, area: float) -> bool:
    """True when `area` is written in `text` and every time it is the footprint.

    "Área de implantação" is how much ground the building covers, not the floor."""
    return _area_introduced_by(str(text or ""), area, _FOOTPRINT_BEFORE_AREA)


_AREA_UNIT_FIRST_RE = re.compile(
    r"\b(?:mq\.?|metri\s+quadri)\s*(\d{1,6}(?:[.,]\d{1,2})?)\b", re.I)

# "Parcelas de 0,8 a 2,8 ha": the plots for sale, not the development they sit in.
_PLOT_SPAN = re.compile(
    r"\bparcelas?\b[^.]{0,60}?(\d+(?:[.,]\d+)?)\s*(?:a|al|y|–|-)\s*(\d+(?:[.,]\d+)?)\s*"
    r"(ha|hect[aá]reas?|hectares?)",
    re.I)
# The feed's "total area" is sometimes the whole sector. Trust the ad when it
# states a plot at most half as big, and the feed figure is at least a hectare.
STATED_AREA_MIN_FEED = 10_000
STATED_AREA_MAX_SHARE = 0.5


def stated_plot_m2(text) -> float | None:
    """The plot the ad itself describes, in m². A range of plots uses the
    largest of them. Room sizes (under 1 000 m²) are left out."""
    spans = []
    for m in _PLOT_SPAN.finditer(str(text or "")):
        hi = float(m.group(2).replace(",", "."))
        if m.group(3).lower().startswith("h"):
            hi *= 10000
        if hi > 0:
            spans.append(hi)
    if spans:
        return max(spans)
    landish = [a for a in find_areas(text) if a >= 1000]
    return max(landish) if landish else None


def prefer_stated_area(feed, text) -> tuple[float | None, float | None]:
    """(area to use, feed area) when the ad states a much smaller plot.

    The second value is None when the feed figure stands. A house's floor area
    is not a plot: callers skip dwellings."""
    try:
        feed_n = float(feed) if feed else 0.0
    except (TypeError, ValueError):
        feed_n = 0.0
    stated = stated_plot_m2(text)
    if feed_n >= STATED_AREA_MIN_FEED and stated and stated <= feed_n * STATED_AREA_MAX_SHARE:
        return stated, feed_n
    return (feed_n or None), None


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
    """Interpret a deadline without extending an explicit midnight instant.

    Date-only and legacy naive-midnight values retain the whole-day policy.
    An explicitly aware timestamp, including midnight, is an exact instant.
    Year 9999 is the source's unknown-date sentinel, not a bidding deadline.
    Naive values retain parse_dt's legacy UTC interpretation.
    """
    dt = parse_dt(date_end)
    if dt is None or dt.year == 9999:
        return None

    explicit_timezone = False
    if isinstance(date_end, datetime):
        explicit_timezone = date_end.utcoffset() is not None
    elif isinstance(date_end, str):
        text = date_end.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            original = datetime.fromisoformat(text)
        except ValueError:
            original = None
        explicit_timezone = original is not None and original.utcoffset() is not None

    if not explicit_timezone and (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0):
        return dt + timedelta(days=1) - timedelta(seconds=1)
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
    return _normalize(str(text))


_COMBINING = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")


@functools.lru_cache(maxsize=65536)
def _normalize(text: str) -> str:
    # The scorer asks for the same listing text dozens of times per listing
    # (one call per term list): cached, the list of 2,700 listings loads ~2x faster.
    if text.isascii():
        return text.lower()
    decomposed = unicodedata.normalize("NFKD", text).replace("⁄", "/")  # ½ → 1/2
    return _COMBINING.sub("", decomposed).lower()


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


@functools.lru_cache(maxsize=8192)
def _needle(term: str) -> str:
    """The longest plain piece of a term: it must appear as such in any text the
    term's regex matches (separators vary, the letters do not)."""
    body = normalize(term.rstrip("*")).strip()
    return max(re.split(r"[\s/\-]+", body), key=len, default="")


NEGATIONS = {"nao", "sem", "not", "non", "livre", "libre", "free", "nicht",
             "kein", "keine", "ni", "senza", "geen",
             "aucun", "aucune", "aucuns", "rien", "sans", "pas"}   # "aucun travaux à prévoir"


def _negated(norm: str, start: int) -> bool:
    clause = re.split(r"[,.;:()\[\]!?\n]", norm[max(0, start - 40):start])[-1]
    return any(w in NEGATIONS for w in re.findall(r"[a-z]+", clause)[-3:])


def find_terms(text, terms, *, negations: bool = True) -> list[str]:
    """Terms from `terms` present in `text` as whole words.

    With negations=True a match preceded, in the same clause and within three
    words, by a negation — "não se encontra ocupado", "sem inquilino", "livre
    de ocupantes" — does not count.
    """
    if not text:
        return []
    return list(_find_terms(str(text), tuple(terms), negations))


@functools.lru_cache(maxsize=400_000)
def _find_terms(text: str, terms: tuple, negations: bool) -> tuple:
    # Cached: the list is scored again on every page view, over the same texts.
    # With 18,000 listings and their long agent descriptions, searching term
    # by term every time took most of a 40 s load.
    norm = normalize(text)
    if not norm:
        return ()
    found = []
    for term in terms:
        if _needle(term) not in norm:           # cheap: most terms are not in most texts
            continue
        for m in term_regex(term).finditer(norm):
            if negations and _negated(norm, m.start()):
                continue
            found.append(term)
            break
    return tuple(found)


def has_term(text, terms, *, negations: bool = True) -> bool:
    return bool(find_terms(text, terms, negations=negations))
