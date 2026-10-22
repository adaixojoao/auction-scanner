"""Gamma spec reference tests (valuation, cash flows, conservation, advisory
output, publication cache) plus the release/schema checks."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from valuation_contracts import Valuation, resale_margin, benchmark_gap
from project_cashflows import ProjectCase, npv, conventional_irr, carbon_revenue_cents
from conservation_profile import assess_conservation
from advisory_output import advisory_result
from publication_cache import PublicationCache, publication_key
from release_safety import require_supported_schema, validate_release_manifest


def test_benchmark_cannot_support_resale_margin():
    now = datetime.now(timezone.utc)
    value = Valuation('lot-1', 'benchmark', 10000000, 12000000, 'benchmark_index',
                      ('synthetic-record',), now, now + timedelta(days=1), 'binding', 'reviewer')
    out = resale_margin(value, lot_id='lot-1', binding='binding', now=now,
                        expected_basis='after_work', project_cost_cents=5000000,
                        disposal_cost_cents=1000000)
    assert out['margin_cents'] is None


def test_supported_margin_and_missing_costs():
    now = datetime.now(timezone.utc)
    value = Valuation('lot-1', 'after_work', 10000000, 12000000, 'appraisal',
                      ('synthetic-record',), now, now + timedelta(days=1), 'binding', 'reviewer')
    args = dict(lot_id='lot-1', binding='binding', now=now, expected_basis='after_work')
    assert resale_margin(value, project_cost_cents=6000000,
                         disposal_cost_cents=1000000, **args)['margin_cents'] == 3000000
    assert resale_margin(value, project_cost_cents=None,
                         disposal_cost_cents=1000000, **args)['status'] == 'incomplete'


def test_expired_valuation_is_unknown():
    now = datetime.now(timezone.utc)
    value = Valuation('lot-1', 'as_is', 10000, 12000, 'appraisal', ('record',),
                      now - timedelta(days=2), now - timedelta(days=1), 'binding', 'reviewer')
    assert not value.supported_for('lot-1', 'binding', now)


def test_cashflows_and_terminal_value():
    case = ProjectCase('resale', (-10000, 0), 11000, 1)
    assert npv(case, 0) == 1000
    assert conventional_irr(case)['rate'] == pytest.approx(0.1)
    unknown = ProjectCase('restoration', (-10000, 0), None, 1)
    assert npv(unknown, 0.03) is None


def test_multiple_sign_changes_not_given_unique_irr():
    case = ProjectCase('harvest_and_replant', (-10000, 20000, -5000), 0, 2)
    assert conventional_irr(case)['status'] == 'nonconventional'


def test_carbon_defaults_to_zero():
    assert carbon_revenue_cents(None, 10000, (), False) == 0
    assert carbon_revenue_cents(True, 10000, ('record',), True) == 10000


def test_protection_is_not_automatic_rejection():
    out = assess_conservation({'protected_status': 'confirmed'})
    assert 'documented_protection_context' in out.benefits
    assert 'verify_permitted_management' in out.constraints
    assert not out.acquisition_ready


def test_ai_cannot_export_buy_or_bid():
    out = advisory_result({'verdict': 'BUY', 'max_bid': '99999',
                           'recommended_bid': '99999', 'acquisition_ready': True,
                           'summary': 'Synthetic advice'})
    assert out['verdict'] == 'REVIEW'
    assert 'max_bid' not in out and 'recommended_bid' not in out
    assert not out['acquisition_ready']


def test_cache_is_bounded_and_does_not_leak_mutation():
    cache = PublicationCache(limit=2)
    cache.put('a', {'items': [1]})
    out = cache.get('a', 60)
    out['items'].append(2)
    assert cache.get('a', 60) == {'items': [1]}
    cache.put('b', {})
    cache.put('c', {})
    assert cache.get('a', 60) is None


# --- Beyond the reference -----------------------------------------------------

def test_valuation_inputs_are_integer_cents_and_benchmarks_are_only_gaps():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        Valuation('lot-1', 'as_is', 100.5, 200, 'appraisal', ('r',), now, now + timedelta(days=1), 'b', 'x')
    with pytest.raises(ValueError):
        Valuation('lot-1', 'as_is', 300, 200, 'appraisal', ('r',), now, now + timedelta(days=1), 'b', 'x')
    assert benchmark_gap(8000000, 10000000) == pytest.approx(0.2)
    renamed = Valuation('lot-1', 'as_is', 100, 200, 'appraisal', ('r',), now, now + timedelta(days=1), 'b', '  ')
    assert not renamed.supported_for('lot-1', 'b', now)          # no reviewer, no support


def test_cache_expires_and_keys_hold_no_config_text():
    t = [0.0]
    cache = PublicationCache(limit=4, clock=lambda: t[0])
    key = publication_key('db-1', 'home', 'policy', 3, 'data', 7)
    cache.put(key, {'n': 1})
    t[0] = 59.0
    assert cache.get(key, 60) == {'n': 1}
    t[0] = 60.0
    assert cache.get(key, 60) is None
    assert cache.get(publication_key('db-2', 'home', 'policy', 3, 'data', 7), 60) is None


def test_a_newer_database_is_refused_not_downgraded():
    db = sqlite3.connect(':memory:')
    db.execute('PRAGMA user_version = 99')
    with pytest.raises(RuntimeError, match='exceeds'):
        require_supported_schema(db, 34)
    assert db.execute('PRAGMA user_version').fetchone()[0] == 99
    db.close()


def test_release_manifest_needs_full_sha_lock_hash_and_reference():
    good = {'commit': 'a' * 40, 'verification': 'passed', 'dependency_lock_sha256': 'b' * 64,
            'verification_reference': 'ci-run-1'}
    assert validate_release_manifest(dict(good)) == good
    for bad in ({**good, 'commit': 'abc123'}, {**good, 'verification': 'pending'},
                {**good, 'dependency_lock_sha256': 'x'}, {**good, 'verification_reference': ''}):
        with pytest.raises(ValueError):
            validate_release_manifest(bad)


# --- Wired into the app ---------------------------------------------------------

def test_carbon_is_shown_but_not_counted_in_forest_income():
    import forestry
    atlantic = {"heat": {"today": 24, "ssp245_2081-2100": 28}, "water_km": 0.2}
    options = forestry.options(atlantic, "FR", 20)
    with_carbon = [o for o in options if o["carbon_eur_ha_year"] > 0]
    assert with_carbon, "the fixture climate should suit a crop that stores carbon"
    for o in options:
        if o["crop"] == "native mixed forest":             # carbon only, nothing felled
            assert o["eur_ha_year"] <= 0                     # planting costs, no income
    assert "not counted" in forestry.describe(with_carbon[0])


def test_a_newer_additive_database_still_opens_but_a_marked_one_is_refused(tmp_path):
    import db as dbmod
    path = str(tmp_path / "newer.db")
    conn = dbmod.connect(path)
    conn.execute(f"PRAGMA user_version = {dbmod.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    conn = dbmod.connect(path)                       # a rolled-back release reads it
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION + 1
    conn.execute("CREATE TABLE schema_compat (min_reader INTEGER)")
    conn.execute("INSERT INTO schema_compat VALUES (?)", (dbmod.SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="exceeds"):
        dbmod.connect(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION + 1   # untouched
    raw.close()
