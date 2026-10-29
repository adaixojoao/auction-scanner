"""Words in the ad the scanner missed on the 2026-10-29 deep dive: a buyer
already through, half of a property, and restoration read as light work."""
import pytest

from confirm import gone_reason
from scoring import condition, is_percent_share


@pytest.mark.parametrize("text", [
    "SOUS OFFRE D’ACHAT ACCEPTÉE – DOSSIER EN COURS CHEZ LE NOTAIRE *** LIMERAY – MAISON DE BOURG",
    "Lovely cottage, now under offer.",
    "Moradia T3 com proposta aceite.",
])
def test_an_ad_that_says_a_buyer_is_through_is_gone(text):
    assert gone_reason({"title": "Maison", "description": text}).startswith("under offer")


@pytest.mark.parametrize("text", [
    "Sous offre ? Non : maison libre à la vente, faites une offre.",
    "Aceitamos propostas.",
    # Court-sale terms, not a buyer through (e-leilões, 2026-10-29).
    "Caso a Massa Insolvente seja de uma pessoa coletiva, ao valor da proposta aceite será acrescentado o IVA.",
])
def test_an_ordinary_ad_is_not_under_offer(text):
    assert gone_reason({"title": "Casa", "description": text}) is None


def test_half_of_a_flat_sold_with_the_other_half_is_a_share():
    text = ("Apartamento localizado no R/c no centro de Almada com 4 assoalhadas. Trata-se de uma metade "
            "e vende-se em conjunto com a outra metade pelo valor total de 275.000€.")
    assert is_percent_share(text)
    assert not is_percent_share("Terreno com metade da área em pinhal.")


@pytest.mark.parametrize("text,state", [
    ("Casa para restaurar no centro de Taragoña.", "heavy"),
    ("La casa requiere de reforma importante.", "heavy"),
    ("Une fois rénovée, cette maison sera parfaite pour y résider.", "some"),
    ("Apartamento no centro a necessitar de algumas intervenções.", "some"),
    ("Piano primo di palazzo ristrutturato in zona centrale. Condizioni discrete.", "some"),
])
def test_restoration_wording_is_read_as_work(text, state):
    assert condition({"title": "Casa", "description": text, "raw_json": "{}"}) == state
