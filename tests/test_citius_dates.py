"""Citius deadlines: no 9999 sentinel, a per-listing reference, or date unknown."""
from sources.pt import _citius_date, citius_listing, citius_reference_url, parse_citius_item


def test_citius_9999_placeholder_is_not_a_deadline():
    assert _citius_date("Data da venda: 01/01/9999") is None
    assert _citius_date("Data da venda: 04/03/9999") is None
    assert _citius_date("Data da venda: 15/10/2099") == "2099-10-15T00:00:00"
    assert _citius_date("Prazo para apresentação de propostas: 20/11/2026") == "2026-11-20T00:00:00"


def test_citius_listing_uses_processo_as_the_reference_and_drops_9999():
    f = parse_citius_item(
        "Tipo de Bem: Imóvel Estado: Em venda Valor Base: 30.000,00 € Data da venda: 01/01/9999 "
        "Modalidade: Venda mediante propostas em carta fechada "
        "Descrição do Bem: Moradia sita na freguesia de Safara, concelho de Moura "
        "Processo: 5328/03.5TVLSB Espécie: Execução Ordinária",
        "Juízo de Execução de Lisboa")
    assert f["date_end"] is None
    row = citius_listing(f, "5328035TVLSB")
    assert row["date_end"] is None
    assert "5328" in row["url"] and "consultasvenda" in row["url"]
    assert row["url"] == citius_reference_url("5328/03.5TVLSB")


def test_listing_info_says_date_unknown_for_a_missing_citius_deadline():
    import listing_info

    def ends(item):
        return next(f["value"] for f in listing_info.facts(item) if f["label"] == "Ends")

    assert ends({"source": "citius", "date_end": None, "price": 30000,
                 "raw_json": '{"processo": "5328/03.5TVLSB"}'}) == "date unknown"
    assert ends({"source": "citius", "date_end": "9999-01-01T00:00:00", "price": 30000,
                 "raw_json": '{"processo": "5328/03.5TVLSB"}'}) == "date unknown"
