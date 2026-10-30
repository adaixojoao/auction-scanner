"""Traps behind the top picks of 2026-10-31, read by hand: a mobile home, a
house still being built and sold as part of a building, and a plot passed off
as the home's floor."""
import pytest

from deep_check import text_findings
from scoring import score
from sources.it import _area


def home(**fields):
    return {"title": "Moradia", "tipo": "moradia", "country": "PT", "source": "imovirtual", "price": 15000,
            "area_m2": 34, "raw_json": "{}", **fields}


@pytest.mark.parametrize("mode", ["home", "invest"])
def test_a_mobile_home_is_not_a_house(mode):
    item = home(title="Casa Mobile Homme T3 mobilado", description="venda casa mobihomme t 3 mobilada , cuidada")
    sc, reasons = score(item, mode=mode)
    assert sc == 0 and "mobile home" in reasons[0]
    assert any(level == "reject" and "mobile home" in r for level, r in text_findings(item))


def test_a_house_still_being_built_and_owned_in_part_is_out():
    item = home(source="whitestar", price=55000, area_m2=128,
                description="Moradia em fase de construção. Somos proprietários de 171m2 e a totalidade do imóvel é "
                            "de 232m2. Escriturável ao abrigo do Simplex e em simultâneo com o proprietário do "
                            "remanescente da área total.")
    sc, _ = score(item, mode="invest")
    assert sc == 0
    found = text_findings(item)
    assert any("share" in r for _, r in found) and any("still being built" in r for _, r in found)


def test_an_ordinary_new_build_word_is_not_a_trap():
    item = home(description="Moradia T3 com construção de qualidade, ano de construção 2005.")
    assert not any("still being built" in r for _, r in text_findings(item))


@pytest.mark.parametrize("text,area", [
    ("Appartamento costituito da un caseggiato rustico in pietra con terreno pertinenziale di circa 324 mq, "
     "composto da ingresso, soggiorno, cucina, bagno e camera", None),
    ("Appartamento di 95 mq di 5 vani corredato di garage di 30mq", 95),
    ("Appezzamento di terreno agricolo di 12.500 mq", 12500),
    ("Terreno edificabile di 2.000 mq", 2000),
])
def test_an_italian_home_is_never_sized_by_its_plot(text, area):
    assert _area(text) == area


def test_the_deep_check_says_when_the_stored_size_is_the_land():
    item = home(source="pvp_giustizia", country="IT", area_m2=324,
                description="Appartamento con terreno pertinenziale di circa 324 mq composto da ingresso e camera")
    assert any("is the land" in r for _, r in text_findings(item))
