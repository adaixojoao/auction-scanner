"""Offer cutoff vs hearing date, and French farm/SPR planning on home scores."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import presentation
from common import action_deadline, make_listing, offer_end_raw
from scoring import FR_FARM_ZONE_POINTS, FR_SPR_POINTS, score_detail
from sources.it import pvp_listing


NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


def test_pvp_stores_offer_end_separate_from_hearing():
    lot = {
        "id": 99, "prezzoBaseAsta": 10000, "offertaMinima": 7500,
        "dataOraVendita": "2026-11-10T17:30",
        "terminePresentazioneOfferte": "2026-11-09T12:00",
        "descLotto": "Villetta di mq 98", "disponibilita": ["LIBER"],
        "indirizzo": {"citta": "Pulsano", "provincia": "Taranto",
                      "coordinate": {"latitudine": 40.3, "longitudine": 17.3}},
    }
    row = pvp_listing(lot)
    assert row["date_end"] == "2026-11-10T17:30:00"
    raw = json.loads(row["raw_json"])
    assert raw["offer_end"] == "2026-11-09T12:00:00"
    assert offer_end_raw(row) == "2026-11-09T12:00:00"
    assert action_deadline(row) == "2026-11-09T12:00:00"


def test_presentation_shows_offer_and_hearing_when_both_exist():
    item = {
        "country": "IT",
        "date_end": "2030-01-20T17:30:00",
        "raw_json": json.dumps({"offer_end": "2030-01-15T12:00:00"}),
    }
    d = presentation.deadline(item, NOW)
    assert d["role"] == "offer"
    assert d["date"] == "2030-01-15" and d["time"] == "12:00"
    assert d["sale"]["date"] == "2030-01-20" and d["sale"]["time"] == "17:30"
    assert d["offer"]["date"] == "2030-01-15"


def test_expired_when_offers_closed_but_hearing_is_ahead(db, add):
    from db import load_listings
    add(source="pvp_giustizia", external_id="closed", country="IT", title="Casa",
        price=20000, date_end="2099-12-01T17:30:00",
        raw_json=json.dumps({"offer_end": "2020-01-01T12:00:00"}))
    rows = load_listings(db, include_hidden=True, apply_min_score=False, mode="home")
    hit = next(r for r in rows if r["id"] == "pvp:closed" or r["external_id"] == "closed"
               or r["id"].endswith("closed"))
    assert hit.get("hidden_reason") == "expired"


def test_farm_zone_and_spr_cut_live_home_score(monkeypatch):
    pin = [46.234951, 1.587686]
    base = make_listing(
        "france", "1", "FR", title="Maison 80 m2", description="Maison d'habitation",
        price=30000, area_m2=80, date_end="2030-06-01T10:00:00",
        raw_json=json.dumps({
            "lat": pin[0], "lon": pin[1], "precision": "sale",
            "surroundings": {
                "v": 1, "pin": pin,
                "found": {
                    "fr_zoning": {"zone": "A", "label": "A", "name": "agricole"},
                    "fr_heritage": {"spr": ["SPR DE HONFLEUR"]},
                },
            },
        }),
    )
    score, reasons = score_detail(base, now=NOW, mode="home")
    assert any(r.startswith("planning: farm zone") for r in reasons)
    assert any("patrimonial remarquable" in r for r in reasons)
    plain = make_listing(
        "france", "2", "FR", title="Maison 80 m2", description="Maison d'habitation",
        price=30000, area_m2=80, date_end="2030-06-01T10:00:00",
        raw_json=json.dumps({"lat": pin[0], "lon": pin[1], "precision": "sale"}),
    )
    plain_score, _ = score_detail(plain, now=NOW, mode="home")
    assert score <= plain_score + FR_FARM_ZONE_POINTS + FR_SPR_POINTS + 1
