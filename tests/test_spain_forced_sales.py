"""Forced sales outside Italy (2026-10-31): Spain's court and Social Security
auctions were not forced sales to forced_sale(), so a forced-sales list had no
Spain; and the Social Security feed's shares, timeshare weeks and garages
passed for whole homes."""
import json

import scoring


def _sale(source, text, title="Finca Urbana", country="ES"):
    return {"source": source, "country": country, "title": title, "description": text,
            "tipo": "inmueble", "raw_json": json.dumps({})}


def test_court_and_social_security_sales_are_forced():
    for source in ("spain", "tgss", "eactivos", "astalegale", "eleiloes", "edikte", "fina"):
        assert scoring.forced_sale(_sale(source, "Vivienda")), source


def test_the_state_selling_its_own_building_is_not_forced():
    for source in ("hacienda", "boe_public", "ran"):
        assert not scoring.forced_sale(_sale(source, "Vivienda")), source


def test_a_fifth_part_of_the_pleno_dominio_is_a_share():
    assert scoring.is_percent_share("1/5 PARTE DEL PLENO DOMINIO DE VIVIENDA SITA EN CL OLM,23 ELCHE")


def test_a_percent_of_the_pd_is_a_share_but_all_of_it_is_not():
    assert scoring.is_percent_share("25% P.D VIVIENDA + ANEJO EN CABAÑAS RARAS")
    assert scoring.is_percent_share("FINCA URBANA EN MADRID 50% P. D. VIVIENDA")
    assert not scoring.is_percent_share("100% P.D. VIVIENDA EN VILLARRUBIA SANTIAGO")


def test_an_ava_parte_indivisa_with_a_week_is_a_timeshare():
    text = ("UNA CINCUENTA Y DOS AVA PARTE INDIVISA QUE DA DERECHO DE PROPIEDAD Y USO EXCLUSIVO "
            "DURANTE EL PERIODO SEMANAL NUM 24 DEL CALENDARIO DEL APARTAMENTO NUM 61")
    assert scoring.is_percent_share(text)
    assert scoring.is_timeshare(text)
    assert scoring.is_timeshare("1/48 AVA PARTE INDIVISA QUE CONLLEVA EL USO EXCLUSIVO DE LA SEMANA Nº 18")


def test_a_whole_flat_is_not_a_share():
    text = ("100% DEL PLENO DOMINIO CON ARREGLO A SU REGIMEN MATRIMONIAL. VIVIENDA Nº5 EN PLANTA "
            "SEGUNDA IZQUIERDA, SITA EN LA CALLE ODIEL Nº4 DE ALGECIRAS")
    assert not scoring.is_percent_share(text)
    assert not scoring.is_timeshare(text)


def test_a_registry_entry_for_a_parking_space_is_not_a_home():
    item = _sale("spain", "NÚMERO DOSCIENTOS CINCUENTA Y DOS.- PLAZA DE APARCAMIENTO número 252, "
                          "no vinculada a vivienda, en planta de sótano", title="Datos de la subasta")
    assert scoring.property_kind(item) == "other"
