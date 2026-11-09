"""Bare ownership / future possession — classify and score (phase 1: PT/ES/IT)."""
from __future__ import annotations

import json

import bare_ownership
import costs
import guesses
import life_tables
from scoring import score


def _item(**kw):
    row = {
        "source": "test", "country": "PT", "title": "Moradia",
        "description": "", "price": 30000, "area_m2": 90, "tipo": "moradia",
        "raw_json": "{}",
    }
    row.update(kw)
    return row


def test_classify_bare_pt_and_skip_usufruct_only():
    bare = bare_ownership.classify(_item(
        title="Nua Propriedade de moradia sita em JUSTEUS",
        description="NUA PROPRIEDADE de moradia em Tondela, usufruto vitalício.",
        source="eleiloes",
    ))
    assert bare["kind"] == "bare"

    only = bare_ownership.classify(_item(
        title="USUFRUTO - TERRENO RÚSTICO",
        description="Usufruto do prédio rústico em Gaeiras.",
        source="eleiloes", price=37000, tipo="terreno",
    ))
    assert only["kind"] == "skip" and "usufruct" in only["why"].lower()


def test_classify_quinhao_is_not_a_candidate():
    # User case NP1271802026-style: a hereditary share is not bare ownership of the home.
    got = bare_ownership.classify(_item(
        title="Quinhão hereditário do executado sobre o prédio urbano",
        description="Penhora de quinhão hereditário. Usufruto mencionado no registo.",
        source="eleiloes",
    ))
    assert got["kind"] == "skip"


def test_classify_old_rent_leilosoc_style():
    # leilosoc:46265 — T1 Lisboa arrendado a 137,30 €/mês, prédio pré-1951.
    got = bare_ownership.classify(_item(
        source="leilosoc", external_id="46265",
        title="Apartamento T1 · Penha de França, Lisboa",
        description=(
            "Apartamento T1 com 49 m². Imóvel arrendado pelo valor de 137,30€/ mês; "
            "Prédio inscrito na matriz anteriormente a 1951 - Isento de Licença."
        ),
        price=140000, area_m2=49, tipo="apartamento t1",
    ))
    assert got["kind"] == "old_rent"
    assert got["rent_eur_month"] == 137.30


def test_classify_purge_only_when_stated():
    stated = bare_ownership.classify(_item(
        title="Moradia T2 em Águeda",
        description="O usufruto caduca com a venda judicial nos termos do art. 824.º n.º 2 do CC.",
        source="eleiloes",
    ))
    assert stated["kind"] == "purge" and stated["purge"] is True

    silent = bare_ownership.classify(_item(
        title="Moradia com inquilino",
        description="Imóvel ocupado pelo executado. Sem menção a extinção do ónus.",
        source="eleiloes",
    ))
    assert silent["kind"] != "purge"


def test_classify_it_nuda_with_stated_ages():
    got = bare_ownership.classify(_item(
        country="IT", source="pvp_giustizia",
        title="Quota del 50% della nuda proprietà di autorimessa · Pietramontecorvino",
        description=(
            "nuda proprietà di autorimessa a Pietramontecorvino, "
            "con usufrutto a favore di due coniugi (85 e 75 anni)."
        ),
        price=19840,
    ))
    # Share of a garage — skip as not the whole home / fractional.
    assert got["kind"] == "skip"

    whole = bare_ownership.classify(_item(
        country="IT", source="pvp_giustizia",
        title="Nuda proprietà su appartamento posto al piano terreno",
        description="Nuda proprietà su appartamento; usufruttuario di 82 anni.",
        price=52000, area_m2=70, tipo="appartamento",
    ))
    assert whole["kind"] == "bare"
    assert any(h["age"] == 82 for h in whole["holders"])


def test_classify_es_nuda_propiedad():
    got = bare_ownership.classify(_item(
        country="ES", source="spain",
        title="Datos de la subasta",
        description="NUDA PROPIEDAD DE VIVIENDA EN VALENCIA. Usufructo vitalicio.",
        price=41279, area_m2=77,
        raw_json=json.dumps({"nuda_propiedad": True}),
    ))
    assert got["kind"] == "bare"


def test_other_modes_still_skip_usufruct_bare_does_not():
    row = _item(
        title="Nuda proprietà su appartamento",
        description="Usufrutto vitalizio a favore di persona di 78 anni.",
        country="IT", price=25000, area_m2=65,
    )
    home, home_why = score(row, mode="home")
    assert home == 0 and "usufruct" in home_why[0].lower()
    bare, bare_why = score(row, mode="bare")
    assert bare > 0
    assert any("78" in r or "years" in r for r in bare_why)


def test_age_guess_is_conservative_and_charged():
    assert guesses.usufructuary_age_guess([{"age": 80}]) is None
    g = guesses.usufructuary_age_guess([])
    assert g["age"] == guesses.DEFAULT_USUFRUCT_AGE and "conservative" in g["basis"]

    row = _item(
        title="Nua propriedade de moradia em Oeiras",
        description="NUA PROPRIEDADE. Usufruto vitalício (idade não indicada).",
        price=28000, area_m2=80,
    )
    sc, reasons = score(row, mode="bare")
    assert sc > 0
    assert any("educated guess" in r for r in reasons)
    assert any(str(guesses.DEFAULT_USUFRUCT_AGE) in r for r in reasons)


def test_life_table_and_tax_split_are_labelled():
    wait = life_tables.wait_years([{"age": 80, "sex": "F"}], country="PT")
    assert wait["years"] and wait["source"] and "INE" in wait["source"]
    split = costs.usufruct_value_share("PT", 80)
    assert split["bare"] is not None and "CIMT" in split["source"]
    assert split["bare"] + split["usufruct"] == 1.0


def test_present_value_and_irr_estimates():
    pv = bare_ownership.present_value(free_value=100000, wait_years=10, discount=0.06)
    assert 55000 < pv < 60000
    with_rent = bare_ownership.present_value(
        free_value=100000, wait_years=10, discount=0.06, rent_eur_year=1647.6)
    assert with_rent > pv
    irr = bare_ownership.irr_estimate(pay=25000, free_value=100000, wait_years=12)
    assert irr is not None and irr > 0


def test_joint_survivor_uses_longer_wait():
    one = life_tables.wait_years([{"age": 85}, {"age": 75}], country="IT", joint_survivor=False)
    both = life_tables.wait_years([{"age": 85}, {"age": 75}], country="IT", joint_survivor=True)
    assert both["years"] >= one["years"]
    assert "last survivor" in both["basis"]


def test_eleiloes_style_simultaneous_successive():
    # User case NP1268412026-style: usufruto simultâneo e sucessivo.
    got = bare_ownership.classify(_item(
        title="Moradia em Tondela — nua propriedade",
        description=(
            "Nua propriedade da moradia. Usufruto simultâneo e sucessivo "
            "a favor de dois titulares. Hipoteca anterior à penhora."
        ),
        source="eleiloes", price=57000,
    ))
    assert got["kind"] == "bare" and got["joint_survivor"] is True
