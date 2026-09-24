"""Romania: ANAF tax seizures."""
from __future__ import annotations

import json
import time

from common import LOG, make_listing, make_session, to_number
from db import upsert_listing
from sources import register


@register("anaf", "RO")
def scrape_anaf(db, max_price: float = 50000, **_):
    """anaf.ro — Romanian tax-authority forced sales. Prices are in RON, not EUR."""
    session = make_session()
    url = "https://www.anaf.ro/BunuriSechestrate/rest/bunuri"
    total_scraped = 0

    for page in range(1, 20):
        try:
            resp = session.get(url, params={"tipBun": "I", "pagina": page, "nrBunuriPagina": 50})
            if resp.status_code in (404, 500):
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if page == 1:
                raise
            break

        items = data if isinstance(data, list) else data.get("bunuri", data.get("items", []))
        if not items:
            break
        for item in items:
            eid = str(item.get("id", item.get("idBun", "")) or "")
            if not eid:
                continue
            price = to_number(item.get("pretVanzare")) or to_number(item.get("pretEvaluare")) or 0
            if price > max_price:
                continue
            upsert_listing(db, make_listing(
                "anaf", eid, "RO",
                title=str(item.get("denumire") or item.get("descriere") or f"ANAF sale {eid}")[:200],
                description=item.get("descriere") or item.get("observatii"),
                tipo="imovel",
                area_m2=item.get("suprafata"),
                price=price, min_price=item.get("pretMinim") or price,
                district=item.get("judet") or item.get("localitate"),
                concelho=item.get("localitate"),
                url=item.get("url") or f"https://www.anaf.ro/BunuriSechestrate/detalii.html?id={eid}",
                base_url="https://www.anaf.ro",
                date_end=item.get("dataLicitatie") or item.get("dataLimita"),
                raw_json=json.dumps(item, ensure_ascii=False)[:2000],
            ))
            total_scraped += 1

        db.commit()
        LOG.info(f"  ANAF page {page}: {len(items)} items (total: {total_scraped})")
        if len(items) < 50:
            break
        time.sleep(1)
    return total_scraped
