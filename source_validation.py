"""Source validation harness: catalog metadata, offline fixtures, live-safe
checks and a weekly maintenance priority list.

Does not scrape around bot walls, CAPTCHAs or logins. A blocked source stays
honestly blocked. Fixture tests stay offline.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from common import LOG, utcnow_iso


class _FixtureResponse:
    """Minimal response object for offline fixture runs (mirrors tests' FakeResponse)."""

    def __init__(self, text="", status=200, json_data=None, headers=None):
        import requests
        self.text = text
        self.status_code = status
        self._json = json_data
        self.content = text.encode("utf-8")
        self.headers = headers or {
            "content-type": "application/json" if json_data is not None else "text/html"}
        self.reason = "Not Found"
        self._HTTPError = requests.HTTPError

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise self._HTTPError(f"{self.status_code}", response=self)

# official | aggregator | bank | experimental
KINDS = ("official", "aggregator", "bank", "experimental")
# public_html | public_api | authenticated | blocked
ACCESS = ("public_html", "public_api", "authenticated", "blocked")

# Minimum seconds between live Validate clicks for the same source (this process).
LIVE_COOLDOWN_S = 60

_FIXTURES_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "sources"
_live_last: dict[str, float] = {}

# Catalog overlays register() defaults. Keep this the one place that names
# access mode, kind and known limitations for every source.
CATALOG: dict[str, dict] = {
    # Portugal
    "eleiloes": {"kind": "official", "access": "public_api",
                 "docs_url": "https://e-leiloes.pt", "listing_type": "property",
                 "parser_version": "2026-09"},
    "citius": {"kind": "official", "access": "public_html",
               "docs_url": "https://www.citius.mj.pt", "listing_type": "property",
               "limitation": "No page per sale; case/court filters only",
               "parser_version": "2026-09"},
    "financas": {"kind": "official", "access": "authenticated",
                 "docs_url": "https://www.portaldasfinancas.gov.pt",
                 "listing_type": "property",
                 "limitation": "Needs Portal das Finanças account in Settings"},
    "leilosoc": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "bcp": {"kind": "bank", "access": "public_html", "listing_type": "property"},
    "whitestar": {"kind": "bank", "access": "public_api", "listing_type": "property"},
    "novobanco": {"kind": "bank", "access": "blocked", "listing_type": "property",
                  "limitation": "Portal closed; domain gone"},
    "cgd": {"kind": "bank", "access": "public_api", "listing_type": "property"},
    "santander": {"kind": "bank", "access": "public_html", "listing_type": "property"},
    "bpi": {"kind": "bank", "access": "public_html", "listing_type": "property",
            "docs_url": "https://bpiexpressoimobiliario.net/imoveis-bpi",
            "parser_version": "2026-10"},
    "imobancos": {"kind": "aggregator", "access": "public_html", "listing_type": "property"},
    "centroleiloes": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "bidleiloeira": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "imovirtual": {"kind": "aggregator", "access": "public_html", "listing_type": "property",
                   "limitation": "Marketplace ads, not court auctions"},
    "idealista": {"kind": "aggregator", "access": "blocked", "listing_type": "property",
                  "limitation": "CAPTCHA / bot wall; on request only"},
    # Spain
    "spain": {"kind": "official", "access": "public_html",
              "docs_url": "https://subastas.boe.es", "listing_type": "property"},
    "aeat": {"kind": "official", "access": "blocked", "listing_type": "property",
             "limitation": "Own page gone; covered by BOE (Spain)"},
    "sareb": {"kind": "bank", "access": "blocked", "listing_type": "property",
              "limitation": "Incapsula bot wall"},
    "haya": {"kind": "bank", "access": "public_html", "listing_type": "property"},
    "servihabitat": {"kind": "bank", "access": "public_html", "listing_type": "property"},
    "subastasactivas": {"kind": "aggregator", "access": "public_html", "listing_type": "property"},
    "aliseda": {"kind": "bank", "access": "public_html", "listing_type": "property"},
    "altamira": {"kind": "bank", "access": "public_html", "listing_type": "property"},
    "fotocasa": {"kind": "aggregator", "access": "public_html", "listing_type": "property"},
    # France / Italy / NL / …
    "france": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "encheres_publiques": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "bienici": {"kind": "aggregator", "access": "public_html", "listing_type": "property"},
    "notaires": {"kind": "official", "access": "public_api", "listing_type": "property"},
    "italy": {"kind": "aggregator", "access": "public_html", "listing_type": "property"},
    "pvp_giustizia": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "gobidreal": {"kind": "experimental", "access": "public_html", "listing_type": "property",
                  "limitation": "On request only"},
    "astalegale": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "netherlands": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "veilingnotaris": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "veilingbiljet": {"kind": "experimental", "access": "public_html",
                      "limitation": "Same lots as openbareverkoop.nl"},
    "croatia": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "fina": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "zvg": {"kind": "official", "access": "public_html", "listing_type": "property",
            "docs_url": "https://www.zvg-portal.de", "parser_version": "2026-10"},
    "justiz_auktion": {"kind": "official", "access": "public_html", "listing_type": "property",
                       "limitation": "No property category left; surplus goods only — off by default"},
    "zvg_de": {"kind": "official", "access": "public_html", "listing_type": "property"},
    "biddit": {"kind": "official", "access": "blocked", "listing_type": "property",
               "limitation": "On request; access often restricted"},
    "poland": {"kind": "official", "access": "public_html", "listing_type": "property",
               "docs_url": "https://licytacje.komornik.pl", "parser_version": "2026-10"},
    "anaf": {"kind": "official", "access": "blocked", "listing_type": "property",
             "limitation": "On request; not in default scans"},
    "cyprus": {"kind": "experimental", "access": "blocked", "listing_type": "property"},
    "greece": {"kind": "experimental", "access": "blocked", "listing_type": "property"},
    "courtbid": {"kind": "aggregator", "access": "authenticated", "listing_type": "property",
                 "limitation": "Needs Apify token"},
    "greenacres": {"kind": "aggregator", "access": "public_html", "listing_type": "property"},
}


def meta_for(name: str, source=None) -> dict:
    """Merged catalog + Source fields for the Sources page and health API."""
    cat = dict(CATALOG.get(name) or {})
    kind = cat.get("kind") or getattr(source, "kind", None) or "experimental"
    access = cat.get("access") or getattr(source, "access", None) or "public_html"
    if kind not in KINDS:
        kind = "experimental"
    if access not in ACCESS:
        access = "public_html"
    return {
        "kind": kind,
        "access": access,
        "listing_type": cat.get("listing_type") or getattr(source, "listing_type", None) or "property",
        "docs_url": cat.get("docs_url") or getattr(source, "docs_url", None) or "",
        "limitation": cat.get("limitation") or getattr(source, "limitation", None) or "",
        "parser_version": cat.get("parser_version") or getattr(source, "parser_version", None) or "",
        "has_fixture": fixture_manifest_path(name) is not None,
    }


def enrich_health_row(row: dict, source=None, *, fixture_ok: bool | None = None) -> dict:
    """Add catalog fields and a plain-language detail to one health row."""
    m = meta_for(row["source"], source)
    out = {**row, **m}
    detail = _detail(out, fixture_ok=fixture_ok)
    out["detail"] = detail
    if fixture_ok is False:
        out["fixture_failing"] = True
        # Surface fixture failure without inventing a scrape outcome.
        if out.get("state") == "ok":
            out["state"] = "fixture failing"
    else:
        out["fixture_failing"] = False
    return out


def _detail(h: dict, *, fixture_ok: bool | None = None) -> str:
    state = h.get("state")
    msg = (h.get("last_message") or "").strip()
    access = h.get("access")
    if fixture_ok is False:
        return "Offline fixture test failing — parser likely drifted from the saved page"
    if state == "blocked" or (state == "error" and access == "blocked"):
        return msg or (h.get("limitation") or "Access blocked (bot wall, closed site, or login-only)")
    if state == "error":
        return msg or "HTTP or network error on the last run"
    if state == "broken":
        return ("Parser returned zero listings from a page that used to work — "
                "usually the site layout changed")
    if state == "never worked":
        return "Has never returned a listing — layout unconfirmed or site always empty for us"
    if state == "never run":
        return "Not scanned yet on this PC"
    if state == "ok" and (h.get("last_count") or 0) == 0:
        return "Last run reported ok with zero listings"
    if state == "ok":
        return "Returned listings on the last run"
    return msg or ""


def classify_scrape_status(exc: BaseException | None, count: int) -> str:
    """Map a run outcome to scrape_log.status (ok / empty / error / blocked)."""
    from sources import SourceUnavailable
    if exc is None:
        return "ok" if count > 0 else "empty"
    if isinstance(exc, SourceUnavailable):
        return "blocked"
    return "error"


# ─── Offline fixtures ───────────────────────────────────────────────

def fixtures_dir() -> Path:
    return _FIXTURES_DIR


def fixture_manifest_path(source: str) -> Path | None:
    path = _FIXTURES_DIR / f"{source}.json"
    return path if path.is_file() else None


def load_fixture_manifest(source: str) -> dict:
    path = fixture_manifest_path(source)
    if path is None:
        raise FileNotFoundError(f"no fixture for {source}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("source") != source:
        raise ValueError(f"fixture source mismatch: {data.get('source')!r} vs {source!r}")
    return data


def list_fixtures() -> list[str]:
    if not _FIXTURES_DIR.is_dir():
        return []
    return sorted(p.stem for p in _FIXTURES_DIR.glob("*.json"))


def run_fixture(db, source_name: str, *, fake_http, registry=None) -> dict:
    """Run one source against its saved fixture. Offline only.

    `fake_http` is the conftest helper: fake_http(handler) → session.
    Returns {ok, count, expect_count, missing_fields, fee_prices_found, message}.
    """
    from sources import REGISTRY, load_all
    load_all()
    reg = registry or REGISTRY
    if source_name not in reg:
        raise ValueError(f"unknown source {source_name}")
    manifest = load_fixture_manifest(source_name)
    responses = manifest.get("responses") or []
    files = {r["file"]: (_FIXTURES_DIR / r["file"]).read_text(encoding="utf-8")
             for r in responses if r.get("file")}

    def handler(method, url, kw):
        for r in responses:
            needle = r.get("match") or ""
            if needle and needle not in url:
                continue
            raw = files.get(r["file"], "")
            if r.get("json") or r["file"].endswith(".json"):
                return _FixtureResponse(json_data=json.loads(raw))
            return _FixtureResponse(raw, headers={"content-type": "text/html"})
        return _FixtureResponse("{}", json_data={})

    fake_http(handler)
    src = reg[source_name]
    before = {r[0] for r in db.execute("SELECT id FROM listings WHERE source = ?", (source_name,))}
    count = src.func(db, max_price=manifest.get("max_price", 100000)) or 0
    db.commit()
    after = db.execute(
        "SELECT id, title, price, url, date_end FROM listings WHERE source = ?",
        (source_name,)).fetchall()
    new_rows = [r for r in after if r["id"] not in before]
    expect = int(manifest.get("expect_count", 0))
    missing = []
    for field in manifest.get("expect_fields") or ("title", "price", "url"):
        for r in new_rows:
            if r[field] in (None, "", 0):
                missing.append(f"{r['id']}.{field}")
    # Fees / deposits must not be stored as the property price.
    fee_hits = []
    reject = set(manifest.get("reject_prices") or [])
    for r in new_rows:
        if r["price"] is not None and float(r["price"]) in reject:
            fee_hits.append(f"{r['id']}={r['price']}")
    ok = (count == expect and not missing and not fee_hits
          and len(new_rows) == expect)
    message = None
    if count != expect:
        message = f"expected {expect} listings, got {count}"
    elif missing:
        message = f"missing fields: {', '.join(missing[:5])}"
    elif fee_hits:
        message = f"fee/deposit parsed as price: {', '.join(fee_hits)}"
    return {
        "ok": ok,
        "source": source_name,
        "count": count,
        "expect_count": expect,
        "missing_fields": missing,
        "fee_prices_found": fee_hits,
        "message": message,
        "ids": [r["id"] for r in new_rows],
    }


def check_all_fixtures(db, *, fake_http, registry=None) -> dict[str, bool]:
    """Return {source: ok} for every saved fixture. Swallows per-source errors."""
    out = {}
    for name in list_fixtures():
        try:
            out[name] = bool(run_fixture(db, name, fake_http=fake_http, registry=registry)["ok"])
        except Exception as e:  # noqa: BLE001
            LOG.warning(f"fixture {name}: {e}")
            out[name] = False
    return out


# ─── Live-safe validate (one rate-limited request) ───────────────────

def live_probe_url(name: str, source=None) -> str | None:
    """A single URL to GET for Validate — docs_url or None (skip live)."""
    m = meta_for(name, source)
    if m["access"] == "blocked":
        return None
    return m["docs_url"] or None


def validate_source(db, source_name: str, *, live: bool = False, registry=None,
                    session_factory=None, fake_http=None) -> dict:
    """Validate one source: offline fixture first, optional one live GET.

    Never bypasses a bot wall. Live is off unless the user asks (`live=True`)
    and the catalog has a docs_url and access is not blocked.
    """
    from sources import REGISTRY, SourceUnavailable, load_all, run_source
    load_all()
    reg = registry or REGISTRY
    if source_name not in reg:
        raise ValueError(f"unknown source {source_name}")
    src = reg[source_name]
    m = meta_for(source_name, src)
    result = {
        "source": source_name,
        "when": utcnow_iso(),
        "meta": m,
        "fixture": None,
        "live": None,
        "scrape": None,
    }

    if fixture_manifest_path(source_name) and fake_http is not None:
        result["fixture"] = run_fixture(db, source_name, fake_http=fake_http, registry=reg)
    elif fixture_manifest_path(source_name):
        result["fixture"] = {"ok": None, "message": "fixture present; run pytest to check"}
    else:
        result["fixture"] = {"ok": None, "message": "no offline fixture yet"}

    if m["access"] == "blocked":
        result["live"] = {"ok": False, "skipped": True,
                          "message": m["limitation"] or "Catalog marks this source blocked"}
        return result

    if not live:
        result["live"] = {"ok": None, "skipped": True, "message": "Live check not requested"}
        return result

    now = time.monotonic()
    last = _live_last.get(source_name, 0)
    if now - last < LIVE_COOLDOWN_S:
        wait = int(LIVE_COOLDOWN_S - (now - last))
        result["live"] = {"ok": False, "skipped": True,
                          "message": f"Rate limited — wait {wait}s before another live check"}
        return result

    url = live_probe_url(source_name, src)
    if not url:
        # Fall back to a single low-volume scrape via run_source (still one site).
        _live_last[source_name] = now
        scrape = run_source(db, src, max_price=50_000, config={})
        result["scrape"] = scrape
        result["live"] = {
            "ok": scrape["status"] in ("ok", "empty"),
            "status": scrape["status"],
            "message": scrape.get("message") or f"{scrape['status']} ({scrape['count']} listings)",
        }
        return result

    _live_last[source_name] = now
    if session_factory is None:
        from common import make_session as session_factory
    try:
        session = session_factory(timeout=20)
        resp = session.get(url)
        text = getattr(resp, "text", "") or ""
        blocked = ("_Incapsula_Resource" in text or "captcha" in text.lower()
                   or "cf-challenge" in text.lower())
        if blocked:
            result["live"] = {"ok": False, "status_code": resp.status_code,
                              "message": "Bot check / CAPTCHA on the probe URL — not bypassed"}
        elif resp.status_code >= 400:
            result["live"] = {"ok": False, "status_code": resp.status_code,
                              "message": f"HTTP {resp.status_code} from {url}"}
        else:
            result["live"] = {"ok": True, "status_code": resp.status_code,
                              "message": f"Reached {url} (HTTP {resp.status_code})"}
    except SourceUnavailable as e:
        result["live"] = {"ok": False, "message": str(e)}
    except Exception as e:  # noqa: BLE001
        result["live"] = {"ok": False, "message": f"{type(e).__name__}: {e}"[:300]}
    return result


# ─── Weekly maintenance priorities ───────────────────────────────────

def maintenance_report(db, registry=None, *, fixture_results: dict[str, bool] | None = None
                       ) -> list[dict]:
    """Sources to look at first: high stored/useful yield but degraded health.

    Pure ranking for the Sources page — does not change scrapers.
    """
    from db import source_health
    from sources import REGISTRY, load_all
    load_all()
    reg = registry or REGISTRY
    health = source_health(db, reg)
    # Offers that came from each source (useful-lead proxy).
    offer_counts = dict(db.execute(
        "SELECT l.source, COUNT(*) FROM carta_log c "
        "JOIN listings l ON l.id = c.listing_id "
        "WHERE COALESCE(c.is_offer, 1) = 1 GROUP BY l.source").fetchall())
    rows = []
    for h in health:
        src = reg.get(h["source"])
        m = meta_for(h["source"], src)
        state = h["state"]
        if fixture_results and fixture_results.get(h["source"]) is False:
            state = "fixture failing"
        degraded = state in ("error", "broken", "blocked", "fixture failing", "never worked")
        listings = h.get("listings") or 0
        offers = offer_counts.get(h["source"], 0)
        # Priority: useful history × how bad the state is.
        severity = {"fixture failing": 5, "blocked": 4, "error": 4, "broken": 3,
                    "never worked": 2, "never run": 1, "ok": 0}.get(state, 1)
        score = severity * 10 + min(listings, 50) // 5 + min(offers, 20)
        if m["access"] == "blocked" and state == "blocked":
            score = max(0, score - 15)  # expected blocked → lower urgency
        if not degraded and state == "ok":
            continue
        rows.append({
            "source": h["source"],
            "country": src.country if src else None,
            "state": state,
            "listings": listings,
            "offers": offers,
            "failing_runs": h.get("failing_runs") or 0,
            "last_message": h.get("last_message"),
            "limitation": m["limitation"],
            "access": m["access"],
            "kind": m["kind"],
            "has_fixture": m["has_fixture"],
            "priority": score,
            "why": _priority_why(state, listings, offers, m),
        })
    rows.sort(key=lambda r: (-r["priority"], r["source"]))
    return rows


def _priority_why(state: str, listings: int, offers: int, meta: dict) -> str:
    bits = []
    if state == "fixture failing":
        bits.append("offline fixture failing")
    elif state == "broken":
        bits.append("used to return listings, now empty")
    elif state == "error":
        bits.append("last run errored")
    elif state == "blocked":
        bits.append(meta.get("limitation") or "access blocked")
    elif state == "never worked":
        bits.append("never returned a listing")
    if offers:
        bits.append(f"{offers} offer(s) historically")
    elif listings >= 10:
        bits.append(f"{listings} listings stored")
    return "; ".join(bits) or state
