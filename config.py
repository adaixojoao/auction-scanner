"""
Auction Scanner configuration.
Loads from config.json in the project directory, with sensible defaults.
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULTS = {
    "max_price": 50000,
    "max_bid": 50000,

    # Filtering
    "filters": {
        "countries": [],          # empty = all; e.g. ["PT", "ES"]
        "types": [],              # empty = all; e.g. ["apartamento", "moradia"]
        "exclude_keywords": [
            "1/2", "1/3", "1/4", "1/5", "1/6", "1/7", "1/8", "1/9",
            "1/10", "1/11", "1/12", "1/14", "1/16",
            "avos", "quota", "quinhão", "quinhao", "quota-parte",
            "fração ideal", "fracao ideal", "parte indivisa",
            "usufruto",
        ],
        "min_area_m2": 0,
        "min_score": 0,           # minimum investment score to include in alerts
        "districts": [],          # empty = all; e.g. ["Lisboa", "Porto"]
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
        "min_score": 60,          # only notify for listings scoring >= this
        "send_on": "new",         # "new" = only new listings, "all" = every run
    },

    # Scheduling
    "schedule": {
        "enabled": False,
        "interval_hours": 6,
        "sources": "all",
    },

    # Dashboard
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8050,
    },
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        return _deep_merge(DEFAULTS, user)
    return DEFAULTS.copy()


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
