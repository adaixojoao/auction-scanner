"""Fixture tests for the foreign sources rewritten in Sept 2026. Every fixture is
a trimmed copy of the live answer's shape, with invented addresses."""
import json

from conftest import FakeResponse
from sources import REGISTRY, load_all

load_all()


def _asta_lot(lid, price, tipologia="Abitazione di tipo civile", titolo="Via Roma 1", comune="Besano"):
    return {"id": lid, "friendlyId": f"{lid}-Abitazione-{comune}", "tipologia": tipologia, "titolo": titolo,
            "comune": comune, "provincia": "Varese", "prezzoNum": price, "prezzo": f"€ {price or 0:,.2f}",
            "offertaMinima": "€ 20.160,00", "dataAsta": "20/01/2027 - 11:30",
            "descrizione": "Appartamento di 85 mq al piano primo", "posizione": {"lat": 45.9, "lng": 8.9},
            "urlImmaginePrincipale": f"https://documents.astalegale.net/asta/0/{lid}",
            "proceduraNumeroAnno": "29/2025", "tribunale": "Varese"}


def test_astalegale_search_api_pages_and_masked_lots(db, fake_http):
    pages = {1: [_asta_lot("B1", 26880), _asta_lot("P2", None, tipologia="XXXXXXXXXX", titolo="XXX")],
             2: [_asta_lot("B3", 9300, comune="Cambiago")]}

    def handler(method, url, kw):
        assert url == "https://api.astalegale.net/Search" and kw["json"]["prezzoA"] == 30000
        page = kw["json"]["page"]
        return FakeResponse(json_data={"results": {"currentPage": pages.get(page, []), "pageSize": 2,
                                                   "totalResults": 3, "pageIndex": page}})
    fake_http(handler)
    assert REGISTRY["astalegale"].func(db, max_price=30000) == 2        # the masked PVP copy is left out
    rows = {r["external_id"]: r for r in db.execute("SELECT * FROM listings WHERE source='astalegale'")}
    house = rows["B1"]
    assert house["title"] == "Abitazione di tipo civile · Via Roma 1 · Besano"
    assert house["price"] == 26880 and house["min_price"] == 20160 and house["area_m2"] == 85
    assert house["date_end"] == "2027-01-20T11:30:00" and house["concelho"] == "Besano"
    assert house["url"] == "https://www.astalegale.net/Aste/Detail/B1-Abitazione-Besano"
    assert json.loads(house["raw_json"])["lat"] == 45.9
