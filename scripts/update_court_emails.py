"""Refresh the Portuguese court e-mail addresses a sale's letter can go to.

    python scripts/update_court_emails.py

Needs a network connection. Writes data/pt_court_emails.csv; the app only
reads that file (courts.py).

Every address is the one the Conselho Superior da Magistratura publishes on
each court town's page (comarcas.csm.org.pt/<comarca>/acomarca/juizos/<town>/),
next to the name of the juízo it serves: "Juízo de Execução de Almada Morada: …
email: almada.judicial@tribunais.org.pt". Nothing is worked out from a pattern:
a juízo the directory does not list has no address here.
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from common import normalize  # noqa: E402

BASE = "https://comarcas.csm.org.pt"
COMARCAS = ["acores", "aveiro", "beja", "braga", "braganca", "castelo-branco", "coimbra", "evora", "faro",
            "guarda", "leiria", "lisboa", "lisboa-norte", "lisboa-oeste", "madeira", "portalegre", "porto",
            "porto-este", "santarem", "setubal", "viana-do-castelo", "vila-real", "viseu"]
OUT = os.path.join(HERE, "data", "pt_court_emails.csv")
# A page lists each juízo as a block: its name ("Juízo de Execução de Águeda",
# or just "Juízo Central Cível" under the town's heading), perhaps its
# "Competência Territorial", its Morada, then its e-mail.
_BLOCK = re.compile(r"(Ju[ií]zo\s.+?)(?=\s+Ju[ií]zo\s|\s+Unidade\s+Central|\s+CONTACTOS\b|$)")
_NAME_END = re.compile(r"\s+(?:-\s*Juiz\s*\d+\s+)?(?:Compet[êe]ncia|Morada|Contactos|Telefone)\b", re.I)
_EMAIL = re.compile(r"e-?mail:\s*([\w.\-]+@tribunais\.org\.pt)", re.I)
_TOWN = re.compile(r"Ju[ií]zos\s*/\s*(.+?)\s+(?:Ju[ií]zo|Unidade|N[úu]cleo)\s", re.I)


def juizos(text: str) -> list[tuple[str, str]]:
    """[(juízo, e-mail)] from a town page's text, the town added to a bare name."""
    text = re.sub(r"\bJ\s+u[ií]zo", "Juízo", text)
    head = _TOWN.search(text)
    town = head.group(1).strip() if head else ""
    out = []
    for m in _BLOCK.finditer(text):
        block = m.group(1)
        email, name = _EMAIL.search(block), _NAME_END.split(block, 1)[0].strip()
        if not email or len(name) > 90 or not re.search(r"\w{3,}", name[6:]):
            continue
        # "Juízo Central Cível" under Loures's heading; "… de Almada" already says it.
        first = normalize(town).split(" ")[0] if town else ""
        if first and first not in normalize(name):
            name = f"{name} de {town}"
        out.append((name, email.group(1).lower()))
    return out


def _get(session, url: str) -> str | None:
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(2 + 3 * attempt)
            continue
        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 404:
            return None
        time.sleep(2 + 3 * attempt)
    print(f"  could not read {url}", file=sys.stderr)
    return None


def main() -> None:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (auction-scanner court directory)"
    rows: dict[tuple[str, str], dict] = {}
    for comarca in COMARCAS:
        index = _get(session, f"{BASE}/{comarca}/acomarca/juizos/")
        if not index:
            print(f"  {comarca}: no index", file=sys.stderr)
            continue
        towns = sorted(set(re.findall(rf"/{comarca}/acomarca/juizos/([a-z0-9\-]+)/", index)))
        for town in towns:
            url = f"{BASE}/{comarca}/acomarca/juizos/{town}/"
            html = _get(session, url)
            time.sleep(0.5)
            if not html:
                continue
            text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
            for name, email in juizos(text):
                rows.setdefault((comarca, name.lower()), {"comarca": comarca, "juizo": name,
                                                          "email": email, "source": url})
        print(f"  {comarca}: {sum(1 for k in rows if k[0] == comarca)} juízos", file=sys.stderr)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["comarca", "juizo", "email", "source"])
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: (r["comarca"], r["juizo"])))
    print(f"{len(rows)} juízos written to {OUT}")


if __name__ == "__main__":
    main()
