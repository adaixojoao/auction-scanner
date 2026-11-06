"""Italian floor areas: prefer superficie catastale/commerciale over plot figures."""
from sources.it import _area


def test_superficie_catastale_beats_a_plot_figure_earlier_in_the_text():
    """Piazzetta San Biagio: '80 mq del terreno' then 'superficie catastale mq 84'."""
    text = ("Appartamento di tipo popolare. In particolare trattasi di un immobile posto su due piani "
            "di mq 124 ai quali si uniscono 80 mq del terreno in part. 489 superficie catastale mq 84 "
            "totali; superficie escluse aree scoperte mq 84; Piazzetta San Biagio n. 7")
    assert _area(text) == 84.0


def test_botteghelle_superficie_catastale_is_the_floor():
    text = ("LOTTO abitazione Via botteghelle, n° 28 piano terzo, consistenza 6 vani, "
            "superficie catastale mq 99, rendita catastale euro 371,85")
    assert _area(text) == 99.0


def test_a_plot_only_sale_keeps_the_land_figure():
    assert _area("Terreno agricolo di circa 324 mq in campagna") == 324.0


def test_find_floor_area_skips_mq_del_terreno():
    from common import find_floor_area
    assert find_floor_area("due piani di mq 124 e 80 mq del terreno") == 124.0
    assert find_floor_area("appartamento con terreno pertinenziale di circa 324 mq") is None
