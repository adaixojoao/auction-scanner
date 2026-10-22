"""Beta spec reference tests (collection contracts, evidence store, bindings)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from collection_contracts import (
    PartitionResult, Availability, classify_lot_record, usable_hazards, content_hash,
)
from evidence_store import install_schema, write_observation, write_owned, current, EvidenceConflict
from evidence_bindings import gallery_bindings, photo_attribution, archive_directory, BoundedCache


def test_partial_does_not_establish_absence():
    p = PartitionResult('court-a', 'scope-1', False, False,
                        frozenset({'lot-1'}), 'page_error')
    assert not p.supports_absence('lot-2', 'court-a', 'scope-1')


def test_scope_change_does_not_establish_absence():
    p = PartitionResult('court-a', 'new-budget', True, True, frozenset())
    assert not p.supports_absence('lot-2', 'court-a', 'old-budget')
    assert p.supports_absence('lot-2', 'court-a', 'new-budget')


def test_wrong_lot_and_missing_hazard():
    assert classify_lot_record('lot-1', 'lot-2', 'available') == 'unknown'
    assert classify_lot_record('lot-1', 'lot-1', None) == 'unknown'
    assert not usable_hazards({'flood_m': 0, 'fire_danger': {}})
    assert usable_hazards({'flood_m': 0, 'fire_danger': {'high_days_2090': 0}})
    assert not usable_hazards({'flood_m': True, 'fire_danger': {'high_days_2090': 0}})


def test_availability_expiry():
    now = datetime.now(timezone.utc)
    a = Availability('available', 'lot-1', 'synthetic',
                     'matched_affirmative_sale_state', now - timedelta(hours=1), now)
    assert not a.current_available('lot-1', now)


def test_full_gallery_and_attribution():
    urls = [f'https://example.invalid/{i}.jpg' for i in range(7)]
    a = gallery_bindings(urls, 2)
    b = gallery_bindings(urls + ['https://example.invalid/new.jpg'], 2)
    assert a['gallery_membership'] != b['gallery_membership']
    assert a['selected_inputs'] == b['selected_inputs']
    assert a['selected_inputs'] != gallery_bindings(list(reversed(urls)), 2)['selected_inputs']
    assert photo_attribution({'shows_house': 'true'}) == 'unknown'
    assert photo_attribution({'shows_house': True}) == 'confirmed_by_model'
    assert archive_directory('a:b') != archive_directory('a/b')


def test_stale_evidence_write_is_refused():
    db = sqlite3.connect(':memory:')
    install_schema(db)
    db.commit()
    context = {'id': 'lot-1', 'position': 'old'}
    reader = lambda connection, ident: context
    old_hash = content_hash(context)
    write_observation(db, 'lot-1', 'water', {'near': True}, {'source': 'synthetic'},
                      0, old_hash, reader)
    context['position'] = 'new'
    with pytest.raises(EvidenceConflict):
        write_observation(db, 'lot-1', 'water', {'near': False}, {'source': 'synthetic'},
                          1, old_hash, reader)
    assert db.execute('SELECT COUNT(*) FROM evidence_observations').fetchone()[0] == 1
    db.close()


def test_evidence_renewal_retains_content_revision():
    db = sqlite3.connect(':memory:')
    install_schema(db)
    db.commit()
    context = {'id': 'lot-1'}
    reader = lambda connection, ident: context
    digest = content_hash(context)
    a = write_observation(db, 'lot-1', 'water', None, {'source': 'synthetic'}, 0, digest, reader)
    b = write_observation(db, 'lot-1', 'water', None, {'source': 'synthetic'}, 1, digest, reader)
    assert a['revision'] == b['revision'] == 1
    assert a['observation_id'] != b['observation_id']
    db.close()


def test_bounded_cache():
    cache = BoundedCache(limit=2)
    for key in ('a', 'b', 'c'):
        cache.get_or_compute(key, lambda: 1)
    assert list(cache.items) == ['b', 'c']


def test_null_history_does_not_exclude_unrelated_listing():
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE listings(id TEXT);
      CREATE TABLE carta_log(listing_id TEXT, is_offer INTEGER);
      INSERT INTO listings VALUES('lot-1');
      INSERT INTO carta_log VALUES(NULL,1);''')
    count = db.execute('''SELECT COUNT(*) FROM listings l WHERE NOT EXISTS (
      SELECT 1 FROM carta_log c WHERE c.listing_id=l.id AND c.is_offer=1)''').fetchone()[0]
    assert count == 1
    db.close()


# --- Beyond the reference: the repo's own guards --------------------------------

def test_unregistered_producer_cannot_write_and_nan_fails_before_writing():
    db = sqlite3.connect(':memory:')
    install_schema(db)
    db.commit()
    ctx = {'id': 'lot-1'}
    with pytest.raises(ValueError):
        write_owned(db, 'lot-1', 'legal_clearance', {'ok': True}, {}, 0, content_hash(ctx), lambda c, i: ctx)
    with pytest.raises(ValueError):
        write_owned(db, 'lot-1', 'water', {'near': float('nan')}, {}, 0, content_hash(ctx), lambda c, i: ctx)
    assert current(db, 'lot-1', 'water') is None
    write_owned(db, 'lot-1', 'water', {'near': False}, {'source': 'synthetic'}, 0, content_hash(ctx), lambda c, i: ctx)
    assert current(db, 'lot-1', 'water')['payload'] == {'near': False}
    db.close()
