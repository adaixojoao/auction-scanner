"""Citius guidance separates stored evidence from verified procedure."""
import json

import pytest

from letters import guidance


def _item(label=None, deadline=None):
    return {"source": "citius", "country": "PT", "date_end": deadline,
            "raw_json": json.dumps({"modalidade": label})}


@pytest.mark.parametrize("label, expected", [
    ("Carta fechada", "sealed-proposal delivery method"),
    ("Negociação particular", "authorised person handling the sale"),
    ("Leilão eletrónico", "official auction link"),
    ("Leilão presencial", "venue, time"),
])
def test_supported_methods_show_their_own_next_action(label, expected):
    text = guidance(_item(label))
    assert "Next action:" in text
    assert expected in text
    assert "raw_json.modalidade" in text
    assert repr(label) in text
    assert "Deadline unknown or invalid" in text
    assert "85%" not in text
    assert "does not confirm that the sale is active" in text


@pytest.mark.parametrize("label", [
    None, "Adjudicação", "Carta fechada ou negociação particular",
])
def test_uncertain_methods_require_confirmation(label):
    text = guidance(_item(label))
    assert "Sale method unconfirmed" in text
    assert "Do not assume sealed bids" in text


@pytest.mark.parametrize("deadline", ["9999-01-01", "not a date"])
def test_unknown_dates_are_not_reported_as_submission_cutoffs(deadline):
    text = guidance(_item("Carta fechada", deadline))
    assert "Deadline unknown or invalid" in text
    assert "Stored date_end" not in text


def test_parseable_date_is_only_stored_evidence():
    text = guidance(_item("Carta fechada", "2026-10-16"))
    assert "Stored date_end = '2026-10-16'" in text
    assert "must be confirmed against the current notice" in text
    assert "not permission to submit until midnight" in text


def test_malformed_json_stays_information_first():
    item = {**_item(), "raw_json": "[]"}
    assert "Sale method unconfirmed" in guidance(item)


def test_non_citius_guidance_is_preserved():
    text = guidance({"source": "eleiloes", "country": "PT"})
    assert "Online auction on e-leiloes.pt" in text
    assert "raw_json.modalidade" not in text
