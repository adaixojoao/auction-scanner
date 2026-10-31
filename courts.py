"""courts.py — the e-mail of the Portuguese court section that runs a sale.

data/pt_court_emails.csv (scripts/update_court_emails.py) holds each juízo's
address as the Conselho Superior da Magistratura publishes it. A Citius sale
names its juízo in the process line ("Juízo de Execução de Almada - Juiz 2")
and its comarca in the court line ("Almada - Tribunal Judicial da Comarca de
Lisboa"). A juízo the directory does not list has no address: none is made up.
"""
from __future__ import annotations

import csv
import functools
import json
import os
import re

from common import normalize

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pt_court_emails.csv")
_JUIZO = re.compile(r"(Ju[ií]zo\s.+?)(?:\s+[-–]\s*Juiz\s*\d+|$)", re.I)
_COMARCA = re.compile(r"Comarca\s+(?:d[aeo]s?\s+)?(.+)$", re.I)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize(text)).strip()


@functools.lru_cache(maxsize=4)
def _table(path: str = PATH) -> dict[str, list[tuple[str, str]]]:
    """{comarca: [(juízo key, e-mail)]}."""
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.setdefault(r["comarca"], []).append((_key(r["juizo"]), r["email"]))
    except OSError:
        pass
    return out


def _slug(comarca: str) -> str:
    return _key(comarca).replace(" ", "-")


def court_email(item: dict, path: str = PATH) -> str | None:
    """The juízo's published e-mail for a Citius sale, or None."""
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    raw = raw or {}
    processo, tribunal = str(raw.get("processo") or ""), str(raw.get("tribunal") or "")
    juizo = _JUIZO.search(processo.split(",", 1)[-1])
    comarca = _COMARCA.search(tribunal)
    if not juizo or not comarca:
        return None
    # The directory writes "Juízo de Execução de Soure - J1 e J2", "… da Guarda tem":
    # the sale's juízo is the start of the name, a whole word at a time.
    want = _key(juizo.group(1))
    found = {email for name, email in _table(path).get(_slug(comarca.group(1)), [])
             if name == want or name.startswith(want + " ")}
    return found.pop() if len(found) == 1 else None
