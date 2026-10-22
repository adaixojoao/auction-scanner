"""Alpha reference tests (offer_safety, durable_outbox), as the brief gives them."""
import sqlite3

import pytest

from durable_outbox import claim, enqueue, finish, install_schema
from offer_safety import SafetyError, approve, build_scenario, parse_offer_text


@pytest.mark.parametrize('value', ['-1000', '', 'EUR 4000', 'NaN', '4,000.50', True, 4000.5])
def test_bad_bid(value):
    with pytest.raises(SafetyError):
        parse_offer_text(value)


def test_exact_bid():
    assert parse_offer_text('4.000,50') == 400050


def test_unknown_cost_is_not_affordable():
    c = dict(obligations=None, taxes=100, fees=100, works=100, reserve=100)
    s = build_scenario(10000, 50000, c, {'lot': 'synthetic'})
    assert s.status == 'incomplete'
    assert s.required_cents is None
    assert not approve('binding_offer', s, {})['authorised_acquisition']


def test_obligations_count():
    c = dict(obligations=1500000, taxes=0, fees=0, works=0, reserve=0)
    s = build_scenario(4000000, 5000000, c, {})
    assert s.required_cents == 5500000
    assert s.status == 'over_budget'


def test_idempotency_and_claim():
    db = sqlite3.connect(':memory:')
    install_schema(db)
    a = enqueue(db, 'synthetic-key', {'letter': 'synthetic'})
    assert enqueue(db, 'synthetic-key', {'letter': 'synthetic'}) == a
    with pytest.raises(SafetyError):
        enqueue(db, 'synthetic-key', {'letter': 'changed'})
    token = claim(db, a, 'worker-1')
    assert token and claim(db, a, 'worker-2') is None
    finish(db, a, token, 'delivery_unknown')
    assert claim(db, a, 'worker-2') is None
    db.close()
