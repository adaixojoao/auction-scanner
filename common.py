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
    "GR": "Greece", "RO": "Romania", "PL": "Poland", "CY": "Cyprus", "BG": "Bulgaria", "SK": "Slovakia",
    "LV": "Latvia",
    "AT": "Austria", "LU": "Luxembourg", "IE": "Ireland", "EE": "Estonia",
    "FI": "Finland",
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
INVEST_MAX_PRICE = 150_000


def _budget(config: dict | None, key: str, default: float, floor: float) -> float:
    try:
        return max(float(floor), float((config or {}).get(key) or default))
    except (TypeError, ValueError):
        return max(float(floor), default)


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
    its report section may show."""
    if mode == "invest":
        return invest_max_price(config, max_price)
    if mode in ("land", "forest"):
        return land_max_price(config, max_price)
    return float(max_price)


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


def price_to_pay(item: dict) -> float:
    """What you would realistically pay for a listing: the current bid, else the
    minimum accepted, else the base value. A bid below the minimum accepted does
    not buy it (e-leilões opens at 50% but accepts from 85%), so the minimum
    wins then. A floor under 20% of the base value is a bidding step (Spain's
    "puja mínima" of €1,743 on a €174,300 flat), not a price that buys it. One
    definition, used by the scorer, the round matcher, the cost estimate and Offers."""
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
    r"(m²|m2|m\s?2|mq|sq\.?\s?m|ha|hectares?)(?![a-z])", re.I)


def _area_value(number: str, unit: str) -> float | None:
    number = number.replace("\u00a0", " ")
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?", number):
        value = float(number.replace(" ", "").replace(".", "").replace(",", "."))
    else:                          # "106,63", and a typed "106, 63"
        value = float(number.replace(" ", "").replace(",", "."))
    if unit.lower().startswith("h"):
        value *= 10000
    return value if value > 0 else None


def find_areas(text) -> list[float]:
    """Every size in a text, in m², in the order written."""
    values = []
    for m in _AREA_RE.finditer(str(text or "")):
        value = _area_value(m.group(1), m.group(2))
        if value:
            values.append(value)
    if not values:
        # Italian court texts put the unit first: "di MQ. 90", "mq 127,22".
        m = _AREA_UNIT_FIRST_RE.search(str(text or ""))
        if m:
            value = float(m.group(1).replace(".", "").replace(",", "."))
            if value > 0:
                values.append(value)
    return values


def find_area(text) -> float | None:
    """The first size in a text, in m²: "d'environ 35,50 m²" → 35.5,
    "com 1.250 m2" → 1250, "8 ha" → 80000. None if there is none."""
    found = find_areas(text)
    return found[0] if found else None


_AREA_UNIT_FIRST_RE = re.compile(r"\bmq\.?\s*(\d{1,6}(?:,\d{1,2})?)\b", re.I)

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
    """When a listing stops being biddable. A bare date (midnight) means that whole day."""
    dt = parse_dt(date_end)
    if dt is not None and (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0)             and dt.year < 9999:          # Citius writes 9999-01-01 for "no date set"
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
