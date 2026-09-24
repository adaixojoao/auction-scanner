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

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULTS = {
    "max_price": 100000,
    "max_bid": 100000,

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
        "min_score": 45,
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
        "min_score": 70,          # only notify for high-confidence deals
        "send_on": "new",         # "new" = only new listings, "all" = every run
    },

    # Telegram alerts (telegram_alert.py)
    "telegram": {
        "enabled": False,
        "token": "",
        "chat_id": "",
        "min_score": 75,          # new-listing alerts
        "deadline_min_score": 60, # "ending soon, no offer sent" alerts
    },

    # Scheduling (scheduler.py). while_app_open: scan on this timetable while
    # the app window is open, without the Windows background task.
    "schedule": {
        "while_app_open": True,
        "pt_every_hours": 2,
        "eu_every_hours": 6,
        "check_times": ["08:00", "20:00"],
        "weekly_report": "mon 08:00",
    },

    # Report output
    "report": {
        "desktop_copy": True,     # also write Auction-Report.docx/.pdf to the Desktop
    },

    # CourtBid via Apify (python scraper.py --source courtbid)
    "apify_token": "",

    # Proponente details for carta generation
    "proponente": {
        "nome": "Joao Castro Adaixo",
        "nif": "260243132",
        "morada": "Rua Antonio Sergio, n. 49, 3. Esq.\n6300-665 Guarda",
        "email": "adaixojoao@gmail.com",
        "localidade": "Guarda",   # printed next to the date on each carta
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


def save_config(cfg: dict):
    """Write config.json atomically (a crash mid-write must not lose settings)."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def update_config(changes: dict) -> dict:
    """Merge `changes` into config.json (keys it does not mention are kept) and
    return the full effective config."""
    save_config(_deep_merge(load_user_config(), changes))
    return load_config()


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
