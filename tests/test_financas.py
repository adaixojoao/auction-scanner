"""Portal das Finanças sales, read with the owner's account (sources/pt.py).
Fixtures are trimmed copies of the pages' shape, with invented data."""
import json

import accounts
from conftest import FakeResponse
from sources import REGISTRY, load_all

load_all()


def _card(cat, numero, venda, mode, base, last, ends):
    return f"""<td><div class="card card-list"><div class="slideshow-wrapper"><img class="img-fluid"
      src="https://static.portaldasfinancas.gov.pt/app/sigvec_static/at_fisc/2025/x/{venda}.jpg"/></div>
      <div class="card-body"><div class="card-title"><span class="title-item">{cat}</span>
      <span class="title-item">{numero}</span></div><span class="label label-primary-border">{mode}</span>
      <strong>Valor base</strong><strong>Última licitação</strong><strong>{base} €</strong><strong>{last} €</strong>
      <strong>Encerra a {ends} às  10:00h</strong><div id="btnDetalhe" class="btn" value="{venda}">Ver detalhe</div>
      </div></div></td>"""


LIST = ("<table id='tabelaBens'><tr>"
        + _card("Imóveis", "1465.2025.7", "111", "Leilão", "2.507,00", "3.505,00", "2099-09-26")
        + _card("Imóveis", "0884.2024.1", "222", "Leilão", "38.991,05", "43.800,00", "2099-09-27")
        + _card("Veículos", "3190.2026.2", "333", "Carta Fechada", "556,01", "-", "2099-09-28")
        + _card("Móveis\xa0e\xa0Imóveis", "3191.2026.1", "444", "Leilão", "900,00", "-", "2099-09-28")
        + "</tr></table>")
DETAIL = """<html><body><h2>Detalhe da Venda</h2> Informação do bem Casa de R/C e 1º andar com quintal,
  com a área de 120 m2, inscrita na matriz sob o artigo 123, com o valor patrimonial tributário de € 9.000,00.
  Localização: GUARDA / PINHEL / ALVERCA DA BEIRA Ver no Mapa Fiel Depositário: NOME INVENTADO
  Data/hora limites para a aceitação das propostas: 2099-09-10 22:00 a 2099-09-26 10:00
  <img src="https://static.portaldasfinancas.gov.pt/app/sigvec_static/at_fisc/2025/14652025000007/14652025000007000102.jpg">
  <img src="https://static.portaldasfinancas.gov.pt/app/sigvec_static/at_fisc/2026/99992026000001/99992026000001000101.jpg">
  </body></html>"""


def test_financas_reads_the_property_sales_within_budget(db, fake_http, monkeypatch):
    monkeypatch.setattr(accounts, "account_session", lambda db, cfg, site, **k: signed_in)
    calls = []

    def handler(method, url, kw):
        calls.append((url, kw.get("params")))
        if url.endswith("/vendasat/detalhe"):
            return FakeResponse(DETAIL)
        resp = FakeResponse(LIST)
        resp.url = url
        return resp
    signed_in = fake_http(handler)
    assert REGISTRY["financas"].func(db, max_price=30000, config={}) == 1     # not the €43,800 one, not a car
    row = db.execute("SELECT * FROM listings WHERE source='financas'").fetchone()
    assert row["id"] == "financas:1465.2025.7" and row["price"] == 2507 and row["current_bid"] == 3505
    assert row["concelho"] == "Pinhel" and row["freguesia"] == "Alverca Da Beira" and row["area_m2"] == 120
    assert row["date_end"] == "2099-09-26T10:00:00" and "Modalidade: Leilão" in row["description"]
    assert row["url"] == "https://vendas.portaldasfinancas.gov.pt/vendasat/detalhe?venda=111"
    raw = json.loads(row["raw_json"])
    assert raw["fotos"] == ["https://static.portaldasfinancas.gov.pt/app/sigvec_static/at_fisc/2025/"
                            "14652025000007/14652025000007000102.jpg"]          # not another sale's photo
    assert "NOME INVENTADO" not in row["description"] + row["raw_json"]         # the keeper's name is not kept
    # The page once per sale.
    REGISTRY["financas"].func(db, max_price=30000, config={})
    assert sum(1 for u, _ in calls if u.endswith("/vendasat/detalhe")) == 1


def test_without_an_account_financas_is_quiet(db, monkeypatch):
    monkeypatch.setattr(accounts, "account_session", lambda db, cfg, site, **k: None)
    assert REGISTRY["financas"].func(db, max_price=30000, config={}) == 0
