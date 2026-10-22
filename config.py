"""
Auction Scanner configuration.
Loads from config.json in the project directory, with sensible defaults.

Filters decide what is *shown* (report, dashboard, alerts, cartas); nothing is
deleted from the database, so changing a filter takes effect immediately and
can be undone.
"""

import copy
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager

import locks

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULTS = {
    # One budget per goal (common.py). A scrape keeps anything under the
    # highest of them; each tab, its alerts and its report section apply their own.
    "max_price": 100000,          # a home to live in
    "invest_max_price": 100000,   # a home to let or resell
    "land_max_price": 100000,     # a plot (investment land and forestry)
    # The work spent on a listing (common.check_max_price, light_checks_from):
    # above check_max_price it is kept only so the scanner knows it, never
    # scored, checked or shown, and no budget goes above it; from
    # light_checks_from the heavy checks (climate maps, photos) wait until the
    # light ones say it can be shown. 0 turns a rule off.
    "check_max_price": 100000,
    "light_checks_from": 50000,
    # What you can actually lose on one purchase: price + taxes + fees + the
    # low end of repairs (costs.py). 0 means not set — listings stay visible
    # and an offer is refused until you set it. A shortlisted listing stays
    # visible above the figure. The three budgets above are listing prices.
    "cash_on_hand": 0,
    "max_bid": 100000,
    "max_listings": 100,          # Listings shows at most this many: the best first
    # How long a link check that found the sale open counts as current
    # availability evidence for the evaluator (confirm.py). 0 means not set:
    # an open page is then recorded but availability stays unresolved.
    "availability_valid_hours": 0,

    "filters": {
        "countries": ["PT"],      # Portugal focus; [] for all EU
        "types": [],
        "exclude_keywords": [
            "1/2", "1/3", "1/4", "1/5", "1/6", "1/7", "1/8", "1/9",
            "1/10", "1/11", "1/12", "1/14", "1/16", "1/20",
            "avos", "quota", "quinhão", "quinhao", "quota-parte",
            "fração ideal", "fracao ideal", "parte indivisa", "compropriedade",
            "usufruto", "usufruct",
            "ocupado", "arrendado", "inquilino",
            "sem acesso", "encravado",
        ],
        "min_area_m2": 0,
        # Rural plots are only worth it big and cheap (scoring.py, "What we are looking for")
        "rural_min_m2": 10000,    # 1 ha
        "rural_max_eur_m2": 0.5,  # €5,000 per hectare
        "min_score": 1,           # hides a 0, which is the old 50 and below
        "districts": [],
    },

    # Proxy rotation
    "proxies": {
        "enabled": False,
        "list": [],               # e.g. ["http://user:pass@proxy1:8080", "socks5://proxy2:1080"]
        "rotate_every": 5,        # rotate after N requests
    },

    # Email notifications
    "notifications": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",      # use app password for Gmail
        "from_email": "",
        "to_emails": [],          # e.g. ["you@gmail.com"]
        "min_score": 40,          # old 70, on the spread scale (50–100 → 0–100)
        "send_on": "new",         # "new" = only new listings, "all" = every run
    },

    # Telegram alerts (telegram_alert.py)
    "telegram": {
        "enabled": False,
        "token": "",
        "chat_id": "",
        "min_score": 50,          # new-listing alerts (old 75)
        "deadline_min_score": 20, # "ending soon, no offer sent" alerts (old 60)
        "cut_min_pct": 5,         # "the price just dropped" alerts: how big a cut
        "cut_min_score": 20,      # …and from what score (old 60; your shortlist always counts)
        "source_alerts": True,    # tell me when a site stops working (and when it is back)
    },

    # Information requests the app prepares after a scan (outbox.py) for strong
    # sales whose recipient's e-mail is known. Offered on Telegram with Send /
    # Show letter / Skip; nothing is sent until you tap Send.
    "auto_requests": {
        "enabled": True,
        "min_score": 50,          # old 75, on the spread scale
        "per_day": 5,             # at most this many new requests offered per day
    },

    # A vision model looks at the photos of the best homes to judge their
    # condition (photos.py): an open model on this PC through Ollama (free), or
    # Claude with an Anthropic API key. photos_per_scan 0 = the default for the
    # looker (5 Ollama / 20 Claude); when many homes wait, a higher catch-up
    # budget is used automatically.
    "ai": {
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen2.5vl:3b",
        "anthropic_key": "",
        "photo_check": True,
        "photos_per_scan": 0,
    },

    # Street View inside the listing panel: a Google Maps Embed API key (free,
    # from Google Cloud). Without one, the panel links to Street View instead.
    "maps": {
        "google_key": "",
    },

    # A copy of auctions.db somewhere that survives this PC: a OneDrive or
    # Dropbox folder, or another drive. Empty folder = no copy is made.
    "backup": {
        "folder": "",             # e.g. C:\\Users\\you\\OneDrive\\Auction Scanner
        "keep": 14,               # copies kept there; the oldest are deleted
    },

    # Scheduling (scheduler.py). while_app_open: scan on this timetable while
    # the app window is open, without the Windows background task.
    "schedule": {
        "while_app_open": True,
        "pt_every_hours": 168,        # once a week
        "eu_every_hours": 336,        # every other country, once a fortnight
        "retry_every_hours": 2,       # sources whose last scrape failed
        "fotocasa_every_hours": 168,  # fotocasa, in full, every week
        "backup_every_hours": 24,
        "check_times": ["08:00", "20:00"],
        "weekly_report": "mon 08:00",
    },

    # Report output
    "report": {
        "desktop_copy": True,     # also write Auction-Report.docx/.pdf next to the app
    },

    "climate": {
        # data_dir: where the climate layers are. By default, the auction-climate-data
        # folder next to this app.
        "bid_guardrail": False,   # retired (Gamma): the AI check no longer suggests bids; kept so old settings load
    },

    "location": {
        # Offers for a listing placed only at its town (or not at all):
        # "off", "warn" (a warning), "block" (also asks your reason before sending).
        "gate": "warn",
    },

    "checklist": {
        # route → the item keys that must be done before an offer goes out
        # (checklist.TEMPLATES); a route not listed uses the template's own.
        "blocking": {},
    },

    # Settings → Maximum bid: how the bid calculator (bidcap.py) plans.
    "bid_cap": {
        "max_all_in": 0,                 # 0 = no budget ceiling
        "margin_pct": 15,                # recommended bid stays this % below the estimated value
        "contingency_pct": 10,           # risk reserve as a % of the value
        "rural_reserve_per_ha": 500,     # conservation / clearing reserve for rural land
        "rural_reserve_fixed": 0,
        "require_exact": False,          # no bid cap until location and climate are exact
        "adviser_reserve_eur": 1500,     # lawyer / adviser buffer (default for every country)
        "adviser_reserve_by_country": {  # overrides for countries that need their own lawyer
            "FR": 3000, "DE": 2500, "IT": 2000, "NL": 2000,
        },
    },

    # Settings → Stewardship: Climate & Land Stewardship Plan (stewardship.py).
    "stewardship": {
        "enable_for_mixed": False,       # also show the plan on homes / urban plots
        "profile": {
            "region_label": "Portugal interior",
            "suggest_cork_oak": True,    # still labelled "verify locally" in the plan
            "conservation_priority": "biodiversity and fire resilience",
        },
        # Planning allowances €/ha (low, high) — not quotes.
        "cost_eur_per_ha": {
            "baseline": [200, 600],
            "soil_cover": [300, 1200],
            "regen": [100, 500],
            "planting_trial": [400, 2000],
        },
    },

    # CourtBid via Apify (python scraper.py --source courtbid)
    "apify_token": "",

    # Your details, printed on every letter. Set them on the Settings page (they
    # are saved in config.json, which git ignores). Never put real ones here.
    "proponente": {
        "nome": "",
        "nif": "",
        "morada": "",
        "email": "",
        "telefone": "",           # optional
        "localidade": "",         # printed next to the date on each letter
    },

    # The app updates itself from GitHub (master) when it starts (updater.py)
    "updates": {
        "auto": True,
    },

    # Dashboard
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8050,
        "debug": False,           # Flask debugger runs code from the browser: keep off
    },
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        return _deep_merge(DEFAULTS, user)
    return copy.deepcopy(DEFAULTS)


def load_user_config() -> dict:
    """Only what the user set in config.json, without the defaults."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# Every write is one read–merge–write under one lock: a thread lock for this
# process and the OS file lock (locks.py) next to config.json for the others
# (the app, the timetable, a scan). The new file is written under a unique
# name in the same folder and swapped in, so a reader sees the old or the new
# file, never half of one, and a failed write leaves the last good file.
_THREAD_LOCK = threading.Lock()
LOCK_WAIT_S = 10


@contextmanager
def _config_transaction():
    with _THREAD_LOCK:
        held = locks.acquire(CONFIG_PATH + ".lock", attempts=int(LOCK_WAIT_S / 0.025), wait=0.025)
        try:
            yield
        finally:
            locks.release(held)


def _write_config_unlocked(cfg: dict) -> None:
    folder = os.path.dirname(os.path.abspath(CONFIG_PATH))
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, CONFIG_PATH)
                break
            except PermissionError:          # Windows: a reader has config.json open this instant
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save_config(cfg: dict):
    """Replace config.json with `cfg`, atomically and under the config lock.
    A replacement, not a merge: two replacements at once, the last one wins."""
    if not isinstance(cfg, dict):
        raise ValueError("configuration must be a JSON object")
    snapshot = copy.deepcopy(cfg)          # the caller's dict may change while we wait for the lock
    with _config_transaction():
        _write_config_unlocked(snapshot)


def update_config(changes: dict) -> dict:
    """Merge `changes` into config.json (keys it does not mention are kept) and
    return the full effective config. The read, merge and write happen under
    one lock, so two writers at once both keep their changes. The returned
    config is assembled before the lock is let go: it is this write's result,
    not that of a writer who came after."""
    if not isinstance(changes, dict):
        raise ValueError("configuration changes must be a JSON object")
    snapshot = copy.deepcopy(changes)
    with _config_transaction():
        current = load_user_config()
        if not isinstance(current, dict):
            raise ValueError("stored configuration must be a JSON object")
        merged = _deep_merge(current, snapshot)
        _write_config_unlocked(merged)
        return _deep_merge(DEFAULTS, merged)


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
