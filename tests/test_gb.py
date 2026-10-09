"""Clive Emson lot cards, as seen on the properties page."""
from costs import euros_from
from scoring import property_kind


CLIVE = """
<a href="/properties/268/156/"><span class="lotNum">LOT 156</span>
<h3 class="LotHeading">VACANT GROUND-FLOOR STUDIO FLAT</h3>
<p class="LotLocation">Ryde - Isle of Wight</p>
<div class="statusBox status_available"><label>AVAILABLE AT</label><strong>£40,000</strong></div></a>
<a href="/properties/268/12/"><h3 class="LotHeading">OVER 13.5 ACRES OF WOODLAND</h3>
<p class="LotLocation">Tunbridge Wells - Kent</p><strong>£80,000</strong></a>
<a href="/properties/268/40/"><h3 class="LotHeading">FREEHOLD LAND</h3>
<p class="LotLocation">St. Leonards-on-Sea - East Sussex</p><strong>£1</strong></a>
<a href="/properties/268/9/"><h3 class="LotHeading">TERRACED HOUSE</h3>
<p class="LotLocation">Margate - Kent</p><div class="statusBox status_soldprior"><strong>Sold Prior</strong></div>
<strong>£90,000</strong></a>
"""


def test_clive_emson_keeps_a_guide_and_drops_a_nominal_or_sold_lot():
    from sources.gb import parse_clive_emson
    rows = parse_clive_emson(CLIVE)
    assert [r["external_id"] for r in rows] == ["268-156", "268-12"]
    flat, wood = rows
    assert flat["price"] == euros_from(40000, "GBP")
    assert flat["tipo"] == "house" and flat["concelho"] == "Ryde"
    assert flat["district"] == "Isle of Wight"
    assert flat["url"] == "https://www.cliveemson.co.uk/properties/268/156/"
    assert "£40,000" in flat["description"] and "2026-10-08" in flat["description"]
    assert property_kind(flat) == "home"
    assert wood["tipo"] == "terreno" and wood["area_m2"] == round(13.5 * 4046.86)
    assert wood["district"] == "Kent" and property_kind(wood) == "rural_plot"
    assert parse_clive_emson("") == []


def test_euros_from_uses_the_ecb_rate_and_refuses_an_unknown_currency():
    from costs import ECB_PER_EUR, euros_from as convert
    assert convert(40000, "GBP") == round(40000 / ECB_PER_EUR["GBP"])
    assert convert(100000, "chf") == round(100000 / ECB_PER_EUR["CHF"])
    assert convert(10, "USD") is None
    assert convert(0, "GBP") is None
