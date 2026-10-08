"""Offer-outcome analytics (analytics.py): counts, win rates, feedback, CSV,
optional detail fields, and the v11 → v12 migration."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import analytics
import dashboard
from common import make_listing
from db import connect, upsert_listing


@pytest.fixture
def client(db, monkeypatch):
    import config
    config.save_config({"filters": {}, "cash_on_hand": 10_000_000,
                        "proponente": {"nome": "Test Person", "nif": "123",
                                       "morada": "Rua 1\n6300 Guarda", "email": "t@x.pt"},
                        "checklist": {"blocking": {"pt_court": [], "es_boe": [], "fr_court": []}}})
    monkeypatch.setattr("letters._unicode_fonts", lambda: None)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _seed(db):
    """A handful of offers across countries/sources with known outcomes."""
    specs = [
        # source, ext, country, title, price, area, concelho, first_seen_days, bid, outcome, extras
        ("citius", "a1", "PT", "Moradia em bom estado", 40000, 100, "Porto", 10, 20000, "won",
         {"all_in_cost": 28000, "occupancy_found": "vacant"}),
        ("citius", "a2", "PT", "Moradia", 50000, 90, "Porto", 5, 30000, "lost",
         {"lost_reason": "outbid", "diligence_blocker": 0}),
        ("citius", "a3", "PT", "Moradia em ruínas", 25000, 80, "Guarda", 8, 12000, "cancelled",
         {"lost_reason": "climate", "diligence_blocker": 1}),
        ("spain", "s1", "ES", "Piso", 60000, 70, None, 12, 40000, "lost",
         {"lost_reason": "title", "diligence_blocker": 1}),
        ("spain", "s2", "ES", "Casa", 45000, 85, None, 3, 25000, "pending", {}),
        ("france", "f1", "FR", "Maison", 55000, 110, None, 20, 35000, "won",
         {"winning_bid": 35000, "all_in_cost": 42000}),
        ("eleiloes", "e1", "PT", "Apartamento", 30000, 60, "Lisboa", 2, 18000, "expired", {}),
    ]
    for src, ext, country, title, price, area, place, days, bid, outcome, extra in specs:
        row = make_listing(src, ext, country, title=title, price=price, area_m2=area, concelho=place)
        upsert_listing(db, row)
        db.execute("UPDATE listings SET first_seen = ?, last_seen = ? WHERE id = ?",
                   (_ago(days), _ago(0), row["id"]))
        cols = ["listing_id", "country", "sent_date", "bid_amount", "method", "outcome",
                "created_at", "is_offer"]
        vals = [row["id"], country, _ago(max(days // 2, 1))[:10], bid, "email", outcome,
                _ago(max(days // 2, 1)), 1]
        for k, v in extra.items():
            cols.append(k)
            vals.append(v)
        db.execute(f"INSERT INTO carta_log ({', '.join(cols)}) VALUES ({','.join('?' * len(vals))})",
                   vals)
    db.execute("INSERT INTO carta_log (listing_id, country, sent_date, method, outcome, created_at, is_offer) "
               "VALUES (?,?,?,?,?,?,0)", ("citius:a1", "PT", _ago(1)[:10], "email", "answered", _ago(1)))
    db.commit()


def test_summary_counts_win_rates_and_skips_information_requests(db):
    _seed(db)
    s = analytics.summary(db)
    assert s["counts"]["offers"] == 7
    assert s["counts"]["won"] == 2 and s["counts"]["lost"] == 2
    assert s["counts"]["cancelled"] == 1 and s["counts"]["expired"] == 1
    assert s["counts"]["no_response"] == 1
    assert s["win_rate"] == round(100 * 2 / 6, 1)   # decided = 6
    assert s["median_days_to_submit"] is not None
    by_country = {r["key"]: r for r in s["by_country"]}
    assert by_country["PT"]["offers"] == 4 and by_country["ES"]["offers"] == 2
    assert by_country["FR"]["won"] == 1 and by_country["FR"]["win_rate"] == 100.0
    reasons = {r["key"]: r["count"] for r in s["lost_reasons"]}
    assert reasons["outbid"] == 1 and reasons["climate"] == 1 and reasons["title"] == 1
    assert any("Diligence" in f["text"] or "diligence" in f["text"].lower() for f in s["feedback"])


def test_csv_export_is_anonymised(db):
    _seed(db)
    text = analytics.export_csv(db)
    assert "listing_id" not in text.splitlines()[0] and "sent_to" not in text
    assert "Moradia" not in text and "@" not in text
    assert text.splitlines()[0].startswith("country,source,kind")
    assert text.count("\n") >= 8


def test_outcome_detail_fields_and_api(client, db, add):
    add("citius", "x1", title="Moradia", price=40000, area_m2=100, concelho="Porto",
        raw_json=json.dumps({"processo": "1/10", "tribunal": "X", "modalidade": "Carta fechada",
                             "agente_email": "ae@solic.pt"}))
    sent = client.post("/api/offers/sent", json={"id": "citius:x1", "bid": "20.000,00",
                                                 "checklist_override": "checked at the court"}).get_json()
    log_id = sent["log_id"]
    bad = client.patch(f"/api/carta-log/{log_id}", json={"lost_reason": "spaceship"})
    assert bad.status_code == 400
    ok = client.patch(f"/api/carta-log/{log_id}", json={
        "outcome": "lost", "lost_reason": "title", "diligence_blocker": True,
        "occupancy_found": "tenanted", "winning_bid": 25000}).get_json()
    assert ok["offer"]["outcome"] == "lost" and ok["offer"]["lost_reason"] == "title"
    assert ok["offer"]["diligence_blocker"] == 1 and ok["offer"]["winning_bid"] == 25000
    row = db.execute("SELECT * FROM carta_log WHERE id = ?", (log_id,)).fetchone()
    assert row["occupancy_found"] == "tenanted"

    page = client.get("/outcomes")
    assert page.status_code == 200 and "Outcomes" in page.get_data(as_text=True)
    data = client.get("/api/analytics").get_json()
    assert data["counts"]["offers"] >= 1 and data["counts"]["lost"] >= 1
    csv_body = client.get("/api/analytics.csv").get_data(as_text=True)
    assert "text/csv" in client.get("/api/analytics.csv").content_type
    assert "listing_id" not in csv_body.splitlines()[0]
    assert "Moradia" not in csv_body


def test_a_version_11_database_gets_the_outcome_detail_columns(tmp_path):
    path = str(tmp_path / "v11.db")
    conn = connect(path)
    cols = ("winning_bid", "all_in_cost", "lost_reason", "diligence_blocker",
            "occupancy_found", "title_found", "access_found", "condition_after")
    for col in cols:
        conn.execute(f"ALTER TABLE carta_log DROP COLUMN {col}")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()
    conn = connect(path)
    have = {r[1] for r in conn.execute("PRAGMA table_info(carta_log)")}
    assert set(cols) <= have
    conn.close()
