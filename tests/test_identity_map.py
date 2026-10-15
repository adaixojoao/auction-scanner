"""
tests/test_identity_map.py — Milestone B: stable lot identity for Citius and Leilosoc.

Covers:
- lot_key_citius: priority (registo > art_matricial > description hash)
- lot_key_leilosoc: batchId-keyed
- assign_citius_lot_id: first assign, subsequent lookup, reorder, removal, pagination
- assign_leilosoc_lot_id: first assign, subsequent lookup, multi-lot auction
- backfill_citius / backfill_leilosoc: migration on legacy data
- Conflict quarantine: diverging legal identifiers recorded, not silently accepted
- Leilosoc budget-change: ID survives price going above/below max_price
- Full scraper integration: reorder and removal via fake HTTP (uses db fixture)
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from conftest import FakeResponse as FR
from identity_map import (
    assign_citius_lot_id,
    assign_leilosoc_lot_id,
    backfill_citius,
    backfill_leilosoc,
    lot_key_citius,
    lot_key_leilosoc,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def memdb():
    """In-memory SQLite with the lot_map table (v21 schema only)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE lot_map (
            source           TEXT NOT NULL,
            processo         TEXT NOT NULL,
            lot_key          TEXT NOT NULL,
            listing_id       TEXT NOT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            conflict_details TEXT,
            PRIMARY KEY (source, processo, lot_key)
        );
        CREATE INDEX idx_lot_map_listing ON lot_map(source, listing_id);
        CREATE TABLE listings (
            id TEXT PRIMARY KEY,
            source TEXT,
            external_id TEXT,
            raw_json TEXT
        );
    """)
    return conn


# ─── lot_key_citius ───────────────────────────────────────────────────────────

class TestLotKeyCitius:
    def test_registo_takes_priority(self):
        k = lot_key_citius({"registo": "AP123", "art_matricial": "99", "desc": "house"})
        assert k.startswith("reg:")
        assert "AP123" in k.upper()

    def test_registo_normalised(self):
        assert lot_key_citius({"registo": "ap 1 2 3"}) == lot_key_citius({"registo": "AP123"})

    def test_art_matricial_when_no_registo(self):
        k = lot_key_citius({"art_matricial": "M77", "desc": "terreno"})
        assert k.startswith("mat:")
        assert "M77" in k.upper()

    def test_desc_hash_when_neither(self):
        k = lot_key_citius({"desc": "Moradia na Rua das Flores, Safara, Moura"})
        assert k.startswith("desc:")
        assert len(k) > 8

    def test_same_desc_same_key(self):
        desc = "Apartamento T2 no centro de Faro"
        assert lot_key_citius({"desc": desc}) == lot_key_citius({"desc": desc})

    def test_different_desc_different_key(self):
        assert lot_key_citius({"desc": "House A"}) != lot_key_citius({"desc": "House B"})

    def test_empty_lot_data_gives_desc_key(self):
        k = lot_key_citius({})
        assert k.startswith("desc:")


# ─── lot_key_leilosoc ────────────────────────────────────────────────────────

class TestLotKeyLeilosoc:
    def test_batch_key(self):
        assert lot_key_leilosoc(42) == "batch:42"
        assert lot_key_leilosoc("42") == "batch:42"

    def test_different_batch_different_key(self):
        assert lot_key_leilosoc(1) != lot_key_leilosoc(2)


# ─── assign_citius_lot_id ────────────────────────────────────────────────────

class TestAssignCitiusLotId:
    def test_first_lot_gets_base_eid(self, memdb):
        res = assign_citius_lot_id(memdb, "ABC123", "ABC123",
                                    {"registo": "R001", "desc": "house"})
        assert res.eid == "ABC123"
        assert res.kind.value in ("new", "provisional")

    def test_second_lot_gets_suffixed_eid(self, memdb):
        assign_citius_lot_id(memdb, "ABC123", "ABC123",
                              {"registo": "R001", "desc": "house"})
        res2 = assign_citius_lot_id(memdb, "ABC123", "ABC123",
                                     {"registo": "R002", "desc": "land"})
        assert res2.eid == "ABC123-2"

    def test_same_lot_key_returns_same_eid(self, memdb):
        res1 = assign_citius_lot_id(memdb, "ABC123", "ABC123",
                                     {"registo": "R001", "desc": "house"})
        res2 = assign_citius_lot_id(memdb, "ABC123", "ABC123",
                                     {"registo": "R001", "desc": "house"})
        assert res1.eid == res2.eid == "ABC123"

    def test_reorder_does_not_change_id(self, memdb):
        """When lot B was second on scan 1, it must stay second even if A disappears."""
        assign_citius_lot_id(memdb, "ABC123", "ABC123",
                              {"desc": "Moradia na Rua das Flores Safara Moura"})
        res_land = assign_citius_lot_id(memdb, "ABC123", "ABC123",
                                         {"desc": "Terreno de cultura em Moura"})
        assert res_land.eid == "ABC123-2"

        # Simulate: only land appears in the next scan.
        res_land_rescan = assign_citius_lot_id(memdb, "ABC123", "ABC123",
                                                {"desc": "Terreno de cultura em Moura"})
        assert res_land_rescan.eid == "ABC123-2", "land must keep its ID even when house is absent"

    def test_third_lot_suffix(self, memdb):
        for i, key in enumerate(("R1", "R2", "R3")):
            res = assign_citius_lot_id(memdb, "P1", "P1", {"registo": key})
        assert res.eid == "P1-3"

    def test_different_processo_independent(self, memdb):
        res_a = assign_citius_lot_id(memdb, "P1", "P1", {"registo": "R1"})
        res_b = assign_citius_lot_id(memdb, "P2", "P2", {"registo": "R1"})
        assert res_a.eid == "P1" and res_b.eid == "P2"

    def test_lot_map_row_committed(self, memdb):
        assign_citius_lot_id(memdb, "XYZ", "XYZ", {"registo": "R9"})
        row = memdb.execute(
            "SELECT listing_id FROM lot_map WHERE source='citius' AND processo='XYZ'"
        ).fetchone()
        assert row is not None
        assert row["listing_id"] == "citius:XYZ"

    def test_desc_hash_lot_key_is_provisional(self, memdb):
        """A lot matched only by description hash returns PROVISIONAL."""
        from identity_map import ResolutionKind
        res = assign_citius_lot_id(memdb, "P9", "P9", {"desc": "Some house somewhere"})
        assert res.kind is ResolutionKind.PROVISIONAL

    def test_registo_lot_key_is_new(self, memdb):
        from identity_map import ResolutionKind
        res = assign_citius_lot_id(memdb, "P10", "P10", {"registo": "REGNEW"})
        assert res.kind is ResolutionKind.NEW


# ─── assign_leilosoc_lot_id ──────────────────────────────────────────────────

class TestAssignLeilosocLotId:
    def test_first_lot_gets_auction_id(self, memdb):
        res = assign_leilosoc_lot_id(memdb, "5678", 100)
        assert res.eid == "5678"

    def test_second_lot_gets_suffix(self, memdb):
        assign_leilosoc_lot_id(memdb, "5678", 100)
        res2 = assign_leilosoc_lot_id(memdb, "5678", 200)
        assert res2.eid == "5678-2"

    def test_same_batch_same_eid(self, memdb):
        res1 = assign_leilosoc_lot_id(memdb, "5678", 100)
        res2 = assign_leilosoc_lot_id(memdb, "5678", 100)
        assert res1.eid == res2.eid == "5678"

    def test_different_auction_independent(self, memdb):
        r1 = assign_leilosoc_lot_id(memdb, "A1", 10)
        r2 = assign_leilosoc_lot_id(memdb, "A2", 10)
        assert r1.eid == "A1" and r2.eid == "A2"

    def test_reorder_does_not_reassign(self, memdb):
        """When lot batchId=200 was assigned second, removal of first must not give it first slot."""
        assign_leilosoc_lot_id(memdb, "7000", 100)
        res_200 = assign_leilosoc_lot_id(memdb, "7000", 200)
        assert res_200.eid == "7000-2"
        # Re-scan with only lot 200.
        res_200_again = assign_leilosoc_lot_id(memdb, "7000", 200)
        assert res_200_again.eid == "7000-2"


# ─── Conflict quarantine ──────────────────────────────────────────────────────

class TestConflictQuarantine:
    def test_no_conflict_when_listing_absent(self, memdb):
        res = assign_citius_lot_id(memdb, "ABC", "ABC", {"registo": "NEW"})
        row = memdb.execute(
            "SELECT conflict_details FROM lot_map WHERE source='citius' AND processo='ABC'"
        ).fetchone()
        assert row["conflict_details"] is None
        assert res.kind.value != "quarantined"

    def test_second_lot_with_different_registo_is_not_a_conflict(self, memdb):
        """A second distinct lot in the same processo with a different registry
        number is a new legitimate lot, not a conflict."""
        from identity_map import ResolutionKind
        # Insert existing listing for the first lot.
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES (?,?,?,?)",
            ("citius:ABC", "citius", "ABC",
             json.dumps({"registo": "R_FIRST", "art_matricial": ""})))

        # Second lot in same processo with different registo → should be NEW, not QUARANTINED.
        res = assign_citius_lot_id(memdb, "ABC", "ABC", {"registo": "R_SECOND"})
        assert res.kind is ResolutionKind.NEW, (
            "A second distinct lot (different registo) is a new lot, not a conflict")
        assert res.eid == "ABC-2"

    def test_same_lot_key_collision_quarantines(self, memdb):
        """When the same lot_key appears twice, the second observation is quarantined."""
        from identity_map import ResolutionKind
        # First assignment for lot_key reg:R1.
        res1 = assign_citius_lot_id(memdb, "PROC", "PROC", {"registo": "R1"})
        assert res1.kind is ResolutionKind.NEW

        # Insert a second lot_map row to simulate a collision (two rows with the same
        # lot_key — can only happen via direct DB manipulation; _resolve protects against it).
        # Instead, directly test that _check_same_key_conflict fires.
        from identity_map import _check_same_key_conflict, lot_key_citius
        lk = lot_key_citius({"registo": "R1"})
        conflict = _check_same_key_conflict(memdb, "citius", "PROC", lk, {"registo": "R1"})
        assert conflict is not None and "same_lot_key_collision" in conflict

    def test_identifier_divergence_quarantines_existing_mapping(self, memdb):
        """When a rescan returns a different registo for an existing lot, the mapping
        is quarantined and the upsert must be skipped."""
        from identity_map import ResolutionKind
        # Set up first assignment.
        res1 = assign_citius_lot_id(memdb, "DIVERG", "DIVERG", {"registo": "R_ORIG"})
        assert res1.kind is ResolutionKind.NEW
        # Insert the listing so divergence check can compare.
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES (?,?,?,?)",
            (f"citius:{res1.eid}", "citius", res1.eid,
             json.dumps({"registo": "R_ORIG"})))

        # Rescan: same lot_key but now has a different registo stored in the listing.
        res2 = assign_citius_lot_id(memdb, "DIVERG", "DIVERG", {"registo": "R_CHANGED"})
        # Different lot_key → new eid (R_CHANGED ≠ R_ORIG) — no divergence path here.
        assert res2.eid is not None

        # Divergence happens when the lot_key MATCHES but stored listing has different value.
        # We need to test _check_identifier_divergence directly.
        from identity_map import _check_identifier_divergence
        d = _check_identifier_divergence(memdb, "citius", res1.eid, {"registo": "R_DIFFERENT"})
        assert d is not None and "registo_divergence" in d

    def test_quarantined_observation_returns_none_eid(self, memdb):
        """A quarantined ResolutionResult has eid=None."""
        from identity_map import ResolutionKind
        # Create a mapping then manually mark it quarantined.
        assign_citius_lot_id(memdb, "QQ", "QQ", {"registo": "RR"})
        memdb.execute(
            "UPDATE lot_map SET conflict_details=? WHERE source='citius' AND processo='QQ'",
            (json.dumps({"test": "quarantine"}),))
        res2 = assign_citius_lot_id(memdb, "QQ", "QQ", {"registo": "RR"})
        assert res2.kind is ResolutionKind.QUARANTINED
        assert res2.eid is None


# ─── Backfill migration ───────────────────────────────────────────────────────

class TestBackfillCitius:
    def test_backfill_inserts_rows(self, memdb):
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('citius:P1', 'citius', 'P1', ?)",
            (json.dumps({"processo": "P/1", "registo": "R1"}),))
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('citius:P1-2', 'citius', 'P1-2', ?)",
            (json.dumps({"processo": "P/1", "registo": "R2"}),))
        n = backfill_citius(memdb)
        assert n == 2
        rows = memdb.execute(
            "SELECT listing_id FROM lot_map WHERE source='citius' ORDER BY rowid"
        ).fetchall()
        ids = {r["listing_id"] for r in rows}
        assert "citius:P1" in ids and "citius:P1-2" in ids

    def test_backfill_idempotent(self, memdb):
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('citius:X1', 'citius', 'X1', ?)",
            (json.dumps({"processo": "X/1"}),))
        n1 = backfill_citius(memdb)
        n2 = backfill_citius(memdb)
        assert n1 == 1 and n2 == 0


class TestBackfillLeilosoc:
    def test_backfill_with_batch_id_in_raw(self, memdb):
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('leilosoc:9999', 'leilosoc', '9999', ?)",
            (json.dumps({"batch_id": 111}),))
        n = backfill_leilosoc(memdb)
        assert n == 1
        row = memdb.execute(
            "SELECT lot_key, listing_id FROM lot_map WHERE source='leilosoc'"
        ).fetchone()
        assert row["lot_key"] == "batch:111"
        assert row["listing_id"] == "leilosoc:9999"

    def test_backfill_legacy_without_batch_id(self, memdb):
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('leilosoc:9876', 'leilosoc', '9876', ?)",
            (json.dumps({}),))
        n = backfill_leilosoc(memdb)
        assert n == 1
        row = memdb.execute(
            "SELECT lot_key FROM lot_map WHERE source='leilosoc'"
        ).fetchone()
        assert "legacy" in row["lot_key"]

    def test_backfill_idempotent(self, memdb):
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('leilosoc:8800', 'leilosoc', '8800', ?)",
            (json.dumps({"batch_id": 55}),))
        n1 = backfill_leilosoc(memdb)
        n2 = backfill_leilosoc(memdb)
        assert n1 == 1 and n2 == 0


# ─── Integration: scraper-level reorder and removal (uses db fixture) ────────

class TestCitiusIntegration:
    """Integration tests using the db fixture (real schema + migration)."""

    def _form(self):
        return """
        <select id="ctl00_ContentPlaceHolder1_ddlTribunais">
          <option value="0">Todos</option><option value="99">Juízo Test</option>
        </select>
        <input id="__VIEWSTATE" value="vs">
        <input id="__EVENTVALIDATION" value="ev">
        <input id="__VIEWSTATEGENERATOR" value="gen">
        """

    def _block(self, html_id, price, desc, registo=""):
        reg_line = f"<strong>Registo: </strong> {registo} <br/>" if registo else ""
        return (
            f'<span><div class="resultadopubvenda">'
            f'<strong>Tipo de Bem:</strong> Imóvel <br/>'
            f'<strong>Estado:</strong> Em venda <br/>'
            f'<strong>Valor Base: </strong> {price} € <br/>'
            f'<strong>Modalidade: </strong> Carta fechada <br/>'
            f'<strong>Descrição do Bem: </strong> {desc} <br/>'
            f'{reg_line}'
            f'<strong>Processo: </strong> 9/21.0TBLSB <br/>'
            f'<strong>Espécie: </strong> Execução Comum <br/>'
            f'<a onclick="javascript:Viewer.Abrir(this, {html_id}, \'ConsultasVenda.aspx/GetHtmlDetails\', \'x\', \'y\')">ver mais</a>'
            f'</div></span>'
        )

    def _page(self, block_list):
        pager = ('<input type="image" id="ctl00_ContentPlaceHolder1_Pager1_btnNextPage" '
                 'name="ctl00$ContentPlaceHolder1$Pager1$btnNextPage" disabled="disabled"/>')
        return (f'<input type="hidden" name="__VIEWSTATE" value="p"/>{pager}'
                f'<span id="ctl00_ContentPlaceHolder1_dlVenda">{"".join(block_list)}</span>')

    def test_three_lots_stable_after_middle_removed(self, db, fake_http):
        from sources import REGISTRY

        apt_a = self._block(11, "25.000,00", "Apartamento T1 rua A", registo="REG-A")
        apt_b = self._block(22, "18.000,00", "Apartamento T1 rua B", registo="REG-B")
        apt_c = self._block(33, "12.000,00", "Apartamento T1 rua C", registo="REG-C")
        pages = {"html": self._page([apt_a, apt_b, apt_c])}

        def handler(method, url, kw):
            if method == "GET":
                return FR(self._form())
            if str(url).endswith("GetHtmlDetails"):
                return FR(json_data={"d": "<div>ok</div>"})
            return FR(pages["html"])

        fake_http(handler)
        REGISTRY["citius"].func(db, max_price=100000)
        id_a = "citius:9210TBLSB"
        id_b = "citius:9210TBLSB-2"
        id_c = "citius:9210TBLSB-3"
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_a,)).fetchone()["price"] == 25000
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_b,)).fetchone()["price"] == 18000
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_c,)).fetchone()["price"] == 12000

        # Remove lot B; rescan.
        pages["html"] = self._page([apt_a, apt_c])
        REGISTRY["citius"].func(db, max_price=100000)

        # A and C must keep their IDs and prices.
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_a,)).fetchone()["price"] == 25000
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_c,)).fetchone()["price"] == 12000
        # B row stays (never deleted), price unchanged.
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_b,)).fetchone()["price"] == 18000

    def test_lot_map_populated_after_scrape(self, db, fake_http):
        from sources import REGISTRY

        block = self._block(55, "8.000,00", "Studio em Torres Novas", registo="REG-TN")
        pages = {"html": self._page([block])}

        def handler(method, url, kw):
            if method == "GET":
                return FR(self._form())
            if str(url).endswith("GetHtmlDetails"):
                return FR(json_data={"d": "<div>ok</div>"})
            return FR(pages["html"])

        fake_http(handler)
        REGISTRY["citius"].func(db, max_price=100000)
        row = db.execute(
            "SELECT lot_key, listing_id FROM lot_map WHERE source='citius'"
        ).fetchone()
        assert row is not None
        assert row["lot_key"].startswith("reg:")
        assert row["listing_id"].startswith("citius:")


class TestLeilosocIntegration:
    """Integration tests for Leilosoc using the db fixture."""

    def _next_data(self, items, has_next=False):
        return json.dumps({
            "props": {"pageProps": {"lots": {
                "items": items,
                "hasNextPage": has_next,
            }}}})

    def _page_html(self, items, has_next=False):
        data = self._next_data(items, has_next)
        links = "".join(
            f'<a href="/en/lot/{it["auctionId"]}/{it["batchId"]}">lot</a>'
            for it in items)
        return f'<script id="__NEXT_DATA__">{data}</script>{links}'

    def _lot(self, auction_id, batch_id, price, title="Apartamento"):
        return {
            "auctionId": auction_id, "batchId": batch_id,
            "title": f"{title} · Lisboa", "description": "desc",
            "currencySymbol": "€", "valueBase": str(price),
            "valueBasePublished": True, "valueMinimumPublished": False,
            "auctionEndDate": "2026-11-01T10:00:00Z",
            "pictureDefault": None, "addressLatitude": None,
            "addressLongitude": None, "addressLocation": "Lisboa",
            "auctionTypeCode": "leilao_online", "processNumber": "",
            "customFieldsValues": {}, "order": batch_id,
        }

    def test_two_lots_same_auction_stable_ids(self, db, fake_http):
        from sources import REGISTRY

        lot_a = self._lot(1001, 10, 30000)
        lot_b = self._lot(1001, 20, 15000, title="Garagem")
        pages = {"html": self._page_html([lot_a, lot_b])}

        def handler(method, url, kw):
            return FR(pages["html"])

        fake_http(handler)
        REGISTRY["leilosoc"].func(db, max_price=100000)
        id_a = "leilosoc:1001"
        id_b = "leilosoc:1001-2"
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_a,)).fetchone()["price"] == 30000
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_b,)).fetchone()["price"] == 15000

        # Remove lot A; rescan.
        pages["html"] = self._page_html([lot_b])
        REGISTRY["leilosoc"].func(db, max_price=100000)
        # Lot B must keep its ID.
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_b,)).fetchone()["price"] == 15000
        # Lot A row unchanged.
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_a,)).fetchone()["price"] == 30000

    def test_budget_change_does_not_reassign_id(self, db, fake_http):
        """A lot that was above max_price in scan 1 and below in scan 2 must get
        a new entry in lot_map, not steal the ID of the first lot."""
        from sources import REGISTRY

        cheap = self._lot(2000, 50, 5000, title="Box")
        expensive = self._lot(2000, 60, 80000, title="Moradia")
        pages = {"html": self._page_html([cheap, expensive])}

        def handler(method, url, kw):
            return FR(pages["html"])

        fake_http(handler)
        # Scan 1: expensive is filtered out; cheap gets first slot.
        REGISTRY["leilosoc"].func(db, max_price=50000)
        id_cheap = "leilosoc:2000"
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_cheap,)).fetchone()["price"] == 5000

        # Scan 2: both visible.
        REGISTRY["leilosoc"].func(db, max_price=100000)
        # Cheap must keep its ID.
        assert db.execute("SELECT price FROM listings WHERE id=?", (id_cheap,)).fetchone()["price"] == 5000
        # Expensive gets a new ID, not the cheap slot.
        expensive_row = db.execute(
            "SELECT id, price FROM listings WHERE source='leilosoc' AND price=80000"
        ).fetchone()
        assert expensive_row is not None
        assert expensive_row["id"] != id_cheap

    def test_pagination_second_page_lots_get_stable_ids(self, db, fake_http):
        from sources import REGISTRY

        lot_p1 = self._lot(3000, 1, 10000)
        lot_p2 = self._lot(3001, 2, 20000)
        page1 = self._page_html([lot_p1], has_next=True)
        page2 = self._page_html([lot_p2], has_next=False)
        call_count = {"n": 0}

        def handler(method, url, kw):
            call_count["n"] += 1
            page = kw.get("params", {}).get("page", 1) if hasattr(kw.get("params", {}), "get") else 1
            return FR(page2 if page == 2 or call_count["n"] > 1 else page1)

        fake_http(handler)
        REGISTRY["leilosoc"].func(db, max_price=100000)
        assert db.execute("SELECT COUNT(*) FROM lot_map WHERE source='leilosoc'").fetchone()[0] >= 1

    def test_lot_map_batch_id_stored(self, db, fake_http):
        from sources import REGISTRY

        lot = self._lot(4000, 77, 25000)
        pages = {"html": self._page_html([lot])}

        def handler(method, url, kw):
            return FR(pages["html"])

        fake_http(handler)
        REGISTRY["leilosoc"].func(db, max_price=100000)
        row = db.execute(
            "SELECT lot_key FROM lot_map WHERE source='leilosoc'"
        ).fetchone()
        assert row and row["lot_key"] == "batch:77"


# ─── Task 1: processo-key canonicalisation ───────────────────────────────────

class TestCanonicalProcesso:
    def test_strips_punctuation_and_uppercases(self):
        from identity_map import canonical_processo
        assert canonical_processo("9/21.0TBLSB") == "9210TBLSB"
        assert canonical_processo("165103/TB.MRA") == "165103TBMRA"
        assert canonical_processo("ABC 123") == "ABC123"

    def test_empty_and_none(self):
        from identity_map import canonical_processo
        assert canonical_processo(None) == ""
        assert canonical_processo("") == ""

    def test_already_canonical_unchanged(self):
        from identity_map import canonical_processo
        assert canonical_processo("9210TBLSB") == "9210TBLSB"


class TestBackfillUsesCanonicalProcesso:
    """backfill_citius must store canonical processo keys, not punctuated raw strings."""

    def test_backfill_stores_canonical_key(self, memdb):
        # Insert a listing whose raw_json has a punctuated processo string.
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('citius:9210TBLSB', 'citius', '9210TBLSB', ?)",
            (json.dumps({"processo": "9/21.0TBLSB", "registo": "R1"}),))
        backfill_citius(memdb)
        row = memdb.execute(
            "SELECT processo FROM lot_map WHERE source='citius'"
        ).fetchone()
        assert row is not None
        assert row["processo"] == "9210TBLSB", (
            f"Expected canonical processo '9210TBLSB', got {row['processo']!r}")

    def test_rescan_finds_backfilled_lot_key(self, memdb):
        """The scraper passes canonical_processo(raw) as processo; backfill must store the same key."""
        memdb.execute(
            "INSERT INTO listings (id, source, external_id, raw_json) VALUES"
            " ('citius:9210TBLSB', 'citius', '9210TBLSB', ?)",
            (json.dumps({"processo": "9/21.0TBLSB", "registo": "REGA"}),))
        backfill_citius(memdb)
        # Simulate the scraper's first rescan: it uses canonical_processo("9/21.0TBLSB")="9210TBLSB"
        res = assign_citius_lot_id(memdb, "9210TBLSB", "9210TBLSB", {"registo": "REGA"})
        assert res.eid == "9210TBLSB", (
            "Rescan must find the existing lot_map row, not allocate a new eid")


class TestMigrateV26ProcessoNormalisation:
    """_migrate_v26 normalises punctuated processo keys from v21 backfill."""

    def _v21_db(self):
        """In-memory DB at schema v21 with a punctuated processo row."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE lot_map (
                source           TEXT NOT NULL,
                processo         TEXT NOT NULL,
                lot_key          TEXT NOT NULL,
                listing_id       TEXT NOT NULL,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                conflict_details TEXT,
                PRIMARY KEY (source, processo, lot_key)
            );
            CREATE INDEX idx_lot_map_listing ON lot_map(source, listing_id);
            CREATE TABLE listings (
                id TEXT PRIMARY KEY,
                source TEXT,
                external_id TEXT,
                raw_json TEXT
            );
        """)
        return conn

    def test_migration_normalises_punctuated_processo(self):
        import db as db_mod

        conn = self._v21_db()
        conn.execute(
            "INSERT INTO lot_map (source, processo, lot_key, listing_id)"
            " VALUES ('citius', '9/21.0TBLSB', 'reg:REGA', 'citius:9210TBLSB')")

        db_mod._migrate_v26(conn)

        rows = conn.execute("SELECT processo FROM lot_map WHERE source='citius'").fetchall()
        assert len(rows) == 1
        assert rows[0]["processo"] == "9210TBLSB"

    def test_migration_idempotent_on_already_canonical(self):
        import db as db_mod

        conn = self._v21_db()
        conn.execute(
            "INSERT INTO lot_map (source, processo, lot_key, listing_id)"
            " VALUES ('citius', '9210TBLSB', 'reg:REGA', 'citius:9210TBLSB')")

        db_mod._migrate_v26(conn)

        rows = conn.execute("SELECT processo FROM lot_map WHERE source='citius'").fetchall()
        assert len(rows) == 1
        assert rows[0]["processo"] == "9210TBLSB"

    def test_migration_collision_records_conflict(self):
        """When both punctuated and canonical rows exist for the same lot_key, the
        punctuated row is removed and conflict_details is set on the canonical row."""
        import db as db_mod

        conn = self._v21_db()
        # Canonical row already present (created by scraper on same process)
        conn.execute(
            "INSERT INTO lot_map (source, processo, lot_key, listing_id)"
            " VALUES ('citius', '9210TBLSB', 'reg:REGA', 'citius:9210TBLSB')")
        # Punctuated row from backfill
        conn.execute(
            "INSERT INTO lot_map (source, processo, lot_key, listing_id)"
            " VALUES ('citius', '9/21.0TBLSB', 'reg:REGA', 'citius:9210TBLSB')")

        db_mod._migrate_v26(conn)

        rows = conn.execute("SELECT processo, conflict_details FROM lot_map WHERE source='citius'").fetchall()
        # Only one row remains (canonical)
        assert len(rows) == 1
        assert rows[0]["processo"] == "9210TBLSB"
        # Conflict is recorded
        details = json.loads(rows[0]["conflict_details"] or "null")
        assert details and "superseded_by" in details


class TestLegacyUpgradeThreeUnits:
    """Full scenario: legacy processo 9/21.0TBLSB with three units and attachments."""

    def _setup_legacy_db(self, memdb):
        """Insert three units from processo 9/21.0TBLSB as if v21 backfill ran."""
        units = [
            ("citius:9210TBLSB", "REGA", "Unit A ground floor"),
            ("citius:9210TBLSB-2", "REGB", "Unit B first floor"),
            ("citius:9210TBLSB-3", "REGC", "Unit C second floor"),
        ]
        for lid, registo, desc in units:
            ext = lid.removeprefix("citius:")
            memdb.execute(
                "INSERT INTO listings (id, source, external_id, raw_json) VALUES (?,?,?,?)",
                (lid, "citius", ext,
                 json.dumps({"processo": "9/21.0TBLSB", "registo": registo, "desc": desc})))
        # Simulate v21 backfill with punctuated processo key.
        for lid, registo, _ in units:
            ext = lid.removeprefix("citius:")
            key = f"reg:{_norm(registo)}"
            memdb.execute(
                "INSERT INTO lot_map (source, processo, lot_key, listing_id) VALUES (?,?,?,?)",
                ("citius", "9/21.0TBLSB", key, lid))
        return units

    def test_after_migration_and_rescan_ids_preserved(self, memdb):
        """After v26 migration, a rescan that drops unit A must preserve B and C IDs."""
        import db as db_mod

        self._setup_legacy_db(memdb)
        db_mod._migrate_v26(memdb)

        # Verify all three rows are now under canonical processo.
        rows = memdb.execute(
            "SELECT lot_key, listing_id FROM lot_map WHERE source='citius' ORDER BY rowid"
        ).fetchall()
        assert all(r["listing_id"].startswith("citius:9210TBLSB") for r in rows)
        assert all(
            memdb.execute("SELECT processo FROM lot_map WHERE listing_id=?", (r["listing_id"],)).fetchone()["processo"] == "9210TBLSB"
            for r in rows
        )

        # Rescan: unit A is absent; B and C still present.
        res_b = assign_citius_lot_id(memdb, "9210TBLSB", "9210TBLSB",
                                      {"registo": "REGB"})
        res_c = assign_citius_lot_id(memdb, "9210TBLSB", "9210TBLSB",
                                      {"registo": "REGC"})
        assert res_b.eid == "9210TBLSB-2", f"Expected 9210TBLSB-2, got {res_b.eid}"
        assert res_c.eid == "9210TBLSB-3", f"Expected 9210TBLSB-3, got {res_c.eid}"

    def test_sparse_suffixes_not_overwritten(self, memdb):
        """After v26 migration, a new lot in the same processo gets the next free suffix."""
        import db as db_mod

        self._setup_legacy_db(memdb)
        db_mod._migrate_v26(memdb)

        # Add a new unit D — should get suffix -4.
        res_d = assign_citius_lot_id(memdb, "9210TBLSB", "9210TBLSB",
                                      {"registo": "REGD"})
        assert res_d.eid == "9210TBLSB-4"


def _norm(s):
    """Local _norm for test use."""
    import re
    return re.sub(r"\s+", "", (s or "").upper())
