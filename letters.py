"""
letters.py — every letter the app writes, for every kind of sale.

How you actually bid depends on the sale (its *channel*):

  letter  the offer is itself a letter: Portuguese court sales by carta fechada or
          negociação particular, and offers to banks / servicers
  online  bids go in on a website: e-leilões, Portal das Finanças, Portuguese
          auction houses, the Spanish BOE auction portal, biddit
  lawyer  only a lawyer can bid, at a court hearing: French ventes judiciaires

Each sale also gets the *letter types* that make sense for it — the offer, an
information request to the court, instructions to a lawyer — and
build_letter() is the only place letter text is written. The Offers page
preview, its PDF, its e-mail and `python scraper.py --cartas` all use it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable

from common import has_term, parse_dt, parse_price

LOG = logging.getLogger("letters")

# ─── How each source sells ───────────────────────────────────────────

PT_COURT_SOURCES = {"citius"}
PT_BANK_SOURCES = {"novobanco", "cgd", "santander", "bpi", "imobancos", "bcp", "whitestar"}
ES_COURT_SOURCES = {"spain", "aeat", "subastasactivas"}
ES_SERVICER_SOURCES = {"sareb", "haya", "servihabitat"}
FR_COURT_SOURCES = {"france", "encheres_publiques"}
ONLINE_SOURCES = {"eleiloes", "financas", "leilosoc", "centroleiloes", "bidleiloeira",
                  "spain", "aeat", "subastasactivas", "biddit"}

SELLER_NAMES = {
    "novobanco": "Novo Banco", "cgd": "Caixa Geral de Depósitos", "santander": "Banco Santander Totta",
    "bpi": "Banco BPI", "imobancos": "Imobancos", "bcp": "Millennium bcp", "whitestar": "Whitestar",
    "sareb": "Sareb", "haya": "Haya Real Estate", "servihabitat": "Servihabitat",
}


def place_of(item: dict) -> str:
    """"Moura, Beja"; a town that is also its district is named once."""
    parts = [p for p in (item.get("concelho"), item.get("district")) if p]
    if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
        parts = parts[:1]
    return ", ".join(parts)


def _raw(item: dict) -> dict:
    try:
        return json.loads(item.get("raw_json") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def sale_kind(modalidade: str | None) -> str:
    m = (modalidade or "").lower()
    if "negoci" in m or "direct" in m or "private" in m:
        return "negociacao"
    if "adjudica" in m:
        return "adjudicacao"
    return "carta_fechada"


def channel(item: dict) -> str:
    src = item.get("source") or ""
    if src in FR_COURT_SOURCES:
        return "lawyer"
    if src in ONLINE_SOURCES:
        return "online"
    return "letter"


def guidance(item: dict) -> str:
    """One paragraph for the Offers page: how this kind of sale is actually bid on."""
    src, country = item.get("source") or "", item.get("country") or "PT"
    if src in PT_COURT_SOURCES:
        return ("Portuguese court sale. A sealed-bid offer (carta fechada) goes to the court in a sealed "
                "envelope before the deadline; in negociação particular you deal with the agente de "
                "execução. In carta fechada, offers below 85% of the valor base are normally not accepted.")
    if src == "eleiloes":
        return ("Online auction on e-leiloes.pt: bids are placed on the site (Cartão de Cidadão or Chave "
                "Móvel Digital). Log your bid here to track it; a letter to the court can only ask for "
                "information.")
    if src == "financas":
        return "Tax-authority sale: offers are submitted electronically on Portal das Finanças."
    if src in ("leilosoc", "centroleiloes", "bidleiloeira"):
        return "Auction house: bids are placed on its website. Log your bid here to track it."
    if src in PT_BANK_SOURCES or src in ES_SERVICER_SOURCES:
        return ("Bank-owned property: this is a negotiation, not an auction. Send an offer to the "
                "seller and expect a counter-offer.")
    if src in ES_COURT_SOURCES:
        return ("Spanish court and tax auctions are bid online at subastas.boe.es. You need a digital "
                "certificate or Cl@ve, and a deposit (5% of the auction value in court auctions) before "
                "bidding. A letter cannot bid: the information request asks the court or authority "
                "about occupancy, visits and debts before you commit.")
    if src in FR_COURT_SOURCES:
        return ("French court sale: bidding happens at a hearing at the tribunal judiciaire, and only a "
                "lawyer registered at that court's bar can bid for you. You give them a bank cheque "
                "(chèque de banque) for 10% of the starting price, at least €3,000. For 10 days after the "
                "sale anyone may outbid the winner by 10% (surenchère).")
    if src == "biddit":
        return "Belgian notary auction on biddit.be: bids are placed online. Log your bid here to track it."
    return (f"No sale-specific guidance for {country} yet. This is a general offer letter; check with "
            "the authority how offers have to be submitted.")


# ─── Amounts ─────────────────────────────────────────────────────────

_UNITS = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove",
          "dez", "onze", "doze", "treze", "catorze", "quinze", "dezasseis", "dezassete",
          "dezoito", "dezanove"]
_TENS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
         "oitenta", "noventa"]
_HUNDREDS = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
             "seiscentos", "setecentos", "oitocentos", "novecentos"]


def _below_1000(n: int) -> str:
    if n == 100:
        return "cem"
    parts = []
    if n >= 100:
        parts.append(_HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10])
        n %= 10
    if n or not parts:
        parts.append(_UNITS[n])
    return " e ".join(parts)


def _joiner(rest: int) -> str:
    # "dois mil e quinhentos", "dois mil e cinquenta", but "dois mil trezentos e dez"
    return " e " if rest < 100 or rest % 100 == 0 else " "


def por_extenso(value: float) -> str:
    """4000 → "quatro mil euros", 2500 → "dois mil e quinhentos euros"."""
    euros = int(round(value * 100)) // 100
    cents = int(round(value * 100)) % 100
    millions, rest = divmod(euros, 1_000_000)
    thousands, units = divmod(rest, 1000)

    parts = []
    if millions:
        parts.append("um milhão" if millions == 1 else f"{_below_1000(millions)} milhões")
    if thousands:
        chunk = "mil" if thousands == 1 else f"{_below_1000(thousands)} mil"
        parts.append((_joiner(rest) if millions and not units else " " if parts else "") + chunk)
    if units:
        parts.append((_joiner(units) if parts else "") + _below_1000(units))
    words = "".join(parts).strip() or "zero"
    if millions and not rest:
        words += " de"
    words += " euro" if euros == 1 else " euros"
    if cents:
        words += f" e {_below_1000(cents)} {'cêntimo' if cents == 1 else 'cêntimos'}"
    return words


def format_bid(value: float) -> str:
    """4000 → "4.000,00" (the format the amount field and PT letters use)."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_bid(text) -> float | None:
    """"4.000,00" / "4000" / "4 000" → 4000.0"""
    return parse_price(text)


def format_amount(value: float | None, country: str) -> str:
    """An amount the way a letter in that country writes it."""
    if value is None:
        return "____"
    if country == "FR":
        return f"{value:,.0f} €".replace(",", "\u202f") if value == int(value) else \
            f"{value:,.2f} €".replace(",", "\u202f").replace(".", ",")
    if country in ("PT", "ES", "IT", "DE", "NL", "HR"):
        return format_bid(value)
    return f"{value:,.2f}"


def _round_down(value: float, step: int = 500) -> float:
    return max(step, (int(value) // step) * step)


# ─── Property classes (PT sealed-bid suggestions) ────────────────────

# Whole-word terms (common.term_regex); "*" = prefix. "casa" no longer matches "Casal".
RUSTICO_KEYWORDS = [
    "mato", "pinhal", "pastagem", "cultura arvense", "sequeiro",
    "oliveir*", "vinha", "eucalipt*", "sobreir*", "pasto", "finca rústica",
]
CASA_KEYWORDS = [
    "casa", "casas", "habitação", "moradia", "apartamento", "andar",
    "assoalhada*", "r/c", "rés-do-chão", "prédio urbano", "fração autónoma",
    "vivienda", "piso", "chalet", "maison", "appartement", "logement",
]
TERRENO_CONSTRUCAO_KEYWORDS = [
    "construção urbana", "lote", "urbaniz*", "solar", "terrain à bâtir",
]

MIN_HERDADE_M2 = 5000


def classify_property(title: str, description: str, area_m2: float) -> str | None:
    combined = f"{title} {description}"
    area = area_m2 or 0

    is_casa = has_term(combined, CASA_KEYWORDS, negations=False)
    is_terreno = has_term(combined, TERRENO_CONSTRUCAO_KEYWORDS, negations=False)
    is_rustico_small = has_term(combined, RUSTICO_KEYWORDS, negations=False) and area < MIN_HERDADE_M2
    is_herdade = area >= MIN_HERDADE_M2

    if is_casa:
        return "CASA"
    if is_terreno:
        return "TERRENO_CONSTRUCAO"
    if is_herdade:
        return "HERDADE"
    if is_rustico_small:
        return None
    return "IMOVEL"


def suggest_bid(category: str, price: float | None, area_m2: float | None) -> tuple[str, str]:
    """Fixed low offers for Portuguese sales (sensible for negociação particular)."""
    if category == "TERRENO_CONSTRUCAO":
        return "5.000,00", "cinco mil euros"
    if category == "CASA":
        if price and price > 5000:
            return "4.000,00", "quatro mil euros"
        return "2.500,00", "dois mil e quinhentos euros"
    if category == "HERDADE":
        if area_m2 and area_m2 > 10000:
            return "1.500,00", "mil e quinhentos euros"
        return "1.000,00", "mil euros"
    return "1.000,00", "mil euros"


PT_PRESETS = ["1.000,00", "2.500,00", "4.000,00", "5.000,00", "7.500,00", "10.000,00"]


def _percent_presets(price: float | None) -> list[str]:
    if not price:
        return []
    return [format_bid(_round_down(price * p / 100)) for p in (50, 60, 70, 80, 90, 100)]


# ─── Letter types ────────────────────────────────────────────────────

@dataclass
class Ctx:
    """Everything a template needs, computed once."""
    item: dict
    raw: dict
    country: str
    title: str
    loc: str
    area: str
    processo: str
    bid: str            # as typed: "4.000,00"
    amount: str         # formatted for the letter's country
    bid_text: str
    p: dict             # proponente

    @property
    def nome(self):
        return self.p.get("nome", "")

    @property
    def nif(self):
        return self.p.get("nif", "")

    @property
    def morada(self):
        """On one line, for inside a sentence or list."""
        return ", ".join(x.strip() for x in (self.p.get("morada") or "").splitlines() if x.strip())

    @property
    def email(self):
        return self.p.get("email", "")

    @property
    def telefone(self):
        return self.p.get("telefone", "")


@dataclass(frozen=True)
class LetterType:
    key: str
    country: str
    label: str                 # English, for the app
    is_offer: bool             # carries an amount; sending it counts as an offer
    applies: Callable[[dict, dict], bool]
    render: Callable[[Ctx], tuple[list[str], str, str]]   # recipient lines, subject, body
    amount_label: str = "Your offer (EUR)"
    suggest: Callable[[dict], str] | None = None
    presets: Callable[[dict], list[str]] | None = None

    def public(self, item: dict) -> dict:
        return {"key": self.key, "label": self.label, "is_offer": self.is_offer,
                "amount_label": self.amount_label,
                "suggested": self.suggest(item) if self.suggest else "",
                "presets": self.presets(item) if self.presets else []}


def _pt_suggest(item: dict) -> str:
    cat = classify_property(item.get("title") or "", item.get("description") or "",
                            item.get("area_m2") or 0) or "IMOVEL"
    return suggest_bid(cat, item.get("price"), item.get("area_m2"))[0]


def _pct_suggest(pct: int):
    def suggest(item: dict) -> str:
        return format_bid(_round_down(item["price"] * pct / 100)) if item.get("price") else ""
    return suggest


def _price_suggest(item: dict) -> str:
    return format_bid(item["price"]) if item.get("price") else ""


def _contact_lines(c: Ctx, id_label: str) -> str:
    lines = [f"   {id_label}: {c.nif}", f"   {c.morada}", f"   Email: {c.email}"]
    if c.telefone:
        lines.append(f"   Tel.: {c.telefone}")
    return "\n".join(lines)


# Portugal ------------------------------------------------------------

def _pt_letter_body(kind: str, *, nome, nif, morada, email, title, loc, area,
                    valor, valor_texto) -> str:
    """Body of the Portuguese carta, with accents. The PDF code runs it through
    _safe_latin1() when only Helvetica is available."""
    dados = (
        f"Dados do proponente:\n"
        f"   Nome: {nome}\n"
        f"   NIF: {nif}\n"
        f"   Morada: {morada}\n"
        f"   Email: {email}"
    )
    bem = (
        f"Descrição do bem: {title}\n"
        f"Localização: {loc}\n"
        f"Área: {area}\n\n"
    )
    fecho = f"Com os melhores cumprimentos,\n\n\n\n{nome}\nNIF: {nif}"
    if kind == "negociacao":
        return (
            "Exmo(a). Sr(a),\n\n"
            "Venho por este meio manifestar o meu interesse na aquisição do imóvel "
            "em venda por negociação particular no âmbito do processo acima referido.\n\n"
            f"{bem}"
            "Apresento a seguinte proposta de aquisição:\n\n"
            f"   Valor: EUR {valor} ({valor_texto})\n\n"
            f"{dados}\n\n"
            "Solicito que me informem sobre:\n"
            "   1. Os procedimentos necessários para formalizar a proposta;\n"
            "   2. Se é necessário depósito de caução e respetivo montante;\n"
            "   3. O contacto direto do encarregado da venda.\n\n"
            "Encontro-me disponível para qualquer esclarecimento adicional "
            "e para deslocação ao imóvel para visita.\n\n"
            f"{fecho}"
        )
    if kind == "adjudicacao":
        return (
            "Exmo(a). Sr(a),\n\n"
            "Venho por este meio manifestar o meu interesse na aquisição do imóvel "
            "no âmbito do processo acima referido, atualmente em fase de adjudicação.\n\n"
            f"{bem}"
            "Caso ainda seja possível apresentar proposta, ofereço:\n\n"
            f"   Valor: EUR {valor} ({valor_texto})\n\n"
            f"{dados}\n\n"
            "Solicito informação sobre o estado atual da venda e se ainda é "
            "possível apresentar proposta.\n\n"
            f"{fecho}"
        )
    return (
        "Exmo(a). Sr(a),\n\n"
        "Venho por este meio apresentar proposta de aquisição do imóvel "
        "em venda mediante proposta em carta fechada no âmbito do processo "
        "acima referido.\n\n"
        f"{bem}"
        "PROPOSTA DE AQUISIÇÃO:\n\n"
        f"   Proponente: {nome}\n"
        f"   NIF: {nif}\n"
        f"   Morada: {morada}\n"
        f"   Email: {email}\n"
        f"   Valor da proposta: EUR {valor} ({valor_texto})\n\n"
        "Solicito igualmente informação sobre:\n"
        "   1. O prazo limite para entrega de propostas;\n"
        "   2. Se é necessário juntar cheque visado de caução e montante;\n"
        "   3. O local e horário para entrega de propostas;\n"
        "   4. A data prevista para abertura das propostas.\n\n"
        "Encontro-me disponível para qualquer esclarecimento adicional.\n\n"
        f"{fecho}"
    )


def _pt_court_recipient(c: Ctx) -> list[str]:
    return ["Exmo(a). Sr(a). Juiz / Agente de Execução", *([c.raw["tribunal"]] if c.raw.get("tribunal") else [])]


def _pt_offer(kind: str):
    def render(c: Ctx):
        body = _pt_letter_body(kind, nome=c.nome, nif=c.nif, morada=c.morada, email=c.email,
                               title=c.title, loc=c.loc, area=c.area, valor=c.bid, valor_texto=c.bid_text)
        return _pt_court_recipient(c), f"Proposta de Aquisição - Processo {c.processo}", body
    return render


def _pt_info(c: Ctx):
    tribunal = f", que corre termos no {c.raw['tribunal']}" if c.raw.get("tribunal") else ""
    where = f" ({c.loc})" if c.loc else ""
    body = (
        "Exmos. Senhores,\n\n"
        f"Eu, {c.nome}, venho por este meio, na qualidade de potencial interessado na aquisição, "
        f"solicitar a V. Exas. informações relativas à venda do imóvel no âmbito do processo "
        f"n.º {c.processo or '____'}{tribunal}, referente a: {c.title}{where}.\n\n"
        "Agradecia que me informassem:\n\n"
        "   1. Se a venda do referido imóvel se encontra ainda ativa;\n"
        "   2. Qual o prazo para a apresentação de propostas;\n"
        "   3. Se é exigida a prestação de caução e, em caso afirmativo, qual o respetivo montante;\n"
        "   4. Os contactos (nome, telefone e endereço eletrónico) do Agente de Execução "
        "responsável pelo processo.\n\n"
        "Agradeço desde já a atenção dispensada, ficando a aguardar a V. prezada resposta.\n\n"
        "Com os melhores cumprimentos,\n\n\n\n"
        f"{c.nome}\nNIF: {c.nif}\nEmail: {c.email}" + (f"\nTelefone: {c.telefone}" if c.telefone else "")
    )
    return (["Exmos. Senhores", *([c.raw["tribunal"]] if c.raw.get("tribunal") else [])],
            f"Pedido de informação – Processo n.º {c.processo or '____'} – Venda de imóvel", body)


def _reference(c: Ctx) -> str:
    return c.item.get("url") or c.item.get("external_id") or ""


def _pt_bank(c: Ctx):
    name = SELLER_NAMES.get(c.item.get("source"))
    seller = name or "o vendedor"
    body = (
        "Exmos. Senhores,\n\n"
        f"Venho por este meio apresentar uma proposta de aquisição do imóvel abaixo identificado, "
        f"comercializado por {seller}.\n\n"
        f"Imóvel: {c.title}\n"
        f"Localização: {c.loc}\n"
        f"Área: {c.area}\n"
        f"Referência: {_reference(c)}\n\n"
        f"   Proposta: EUR {c.bid} ({c.bid_text})\n\n"
        f"Dados do proponente:\n   Nome: {c.nome}\n{_contact_lines(c, 'NIF')}\n\n"
        "Agradeço que me informem se a proposta é aceite ou, não o sendo, qual o valor que "
        "estariam dispostos a considerar, bem como a disponibilidade para visitar o imóvel.\n\n"
        "Com os melhores cumprimentos,\n\n\n\n"
        f"{c.nome}\nNIF: {c.nif}"
    )
    return (["Exmos. Senhores", *([name, "Departamento de Imóveis"] if name else [])],
            f"Proposta de aquisição — {c.title[:60]}", body)


# Spain ---------------------------------------------------------------

def _es_authority(c: Ctx) -> tuple[list[str], bool]:
    """(recipient lines, is it a court?)"""
    sub = c.item.get("external_id") or ""
    court = sub.startswith(("SUB-JA", "SUB-JV")) or c.item.get("source") == "spain" and not sub.startswith("SUB-AT")
    authority = c.raw.get("autoridad")
    if c.item.get("source") == "aeat" or sub.startswith("SUB-AT"):
        return ["Agencia Estatal de Administración Tributaria",
                authority or "Dependencia Regional de Recaudación"], False
    if court:
        return ["Sr./Sra. Letrado/a de la Administración de Justicia",
                authority or c.item.get("district") or "Juzgado"], True
    return ["A la autoridad gestora de la subasta", authority or c.item.get("district") or ""], False


def _es_info(c: Ctx):
    recipient, court = _es_authority(c)
    sub = c.item.get("external_id") or ""
    exp = f", expediente {c.raw['expediente']}" if c.raw.get("expediente") else ""
    body = (
        "Estimado/a Sr./Sra.:\n\n"
        f"Me dirijo a {'ese Juzgado' if court else 'ustedes'} como posible licitador en la subasta "
        f"{sub}{exp}, publicada en el Portal de Subastas del BOE, relativa al siguiente bien:\n\n"
        f"   {c.title}\n   {c.loc}\n\n"
        "Antes de consignar el depósito y pujar, les agradecería que me informaran, en la medida "
        "en que conste en el expediente, sobre:\n\n"
        "   1. La situación posesoria del inmueble: si está ocupado y, en su caso, si los "
        "ocupantes tienen título (arrendamiento u otro);\n"
        "   2. La posibilidad de visitar el inmueble;\n"
        "   3. Las cargas o gravámenes anteriores que subsistirán tras la adjudicación;\n"
        "   4. Cualquier deuda conocida de comunidad de propietarios o de IBI.\n\n"
        "Quedo a su disposición para cualquier aclaración.\n\n"
        "Atentamente,\n\n\n\n"
        f"{c.nome}\n{_contact_lines(c, 'NIF/NIE/Pasaporte').replace('   ', '')}"
    )
    return recipient, f"Solicitud de información — Subasta {sub}", body


def _es_servicer(c: Ctx):
    name = SELLER_NAMES.get(c.item.get("source"))
    seller = name or "el vendedor"
    body = (
        "Estimados señores:\n\n"
        f"Por la presente les presento una oferta de compra por el siguiente inmueble, que "
        f"comercializa {seller}:\n\n"
        f"   Inmueble: {c.title}\n"
        f"   Ubicación: {c.loc}\n"
        f"   Superficie: {c.area}\n"
        f"   Referencia: {_reference(c)}\n\n"
        f"   Importe ofertado: {c.amount} €\n\n"
        f"Datos del ofertante:\n   Nombre: {c.nome}\n{_contact_lines(c, 'NIF/NIE/Pasaporte')}\n\n"
        "Les agradecería que me comunicaran si aceptan la oferta o, en su defecto, el importe "
        "que estarían dispuestos a considerar, así como la posibilidad de visitar el inmueble.\n\n"
        "Atentamente,\n\n\n\n"
        f"{c.nome}"
    )
    recipient = [name, "Departamento comercial"] if name else ["A la atención del vendedor"]
    return recipient, f"Oferta de compra — {c.title[:60]}", body


# France --------------------------------------------------------------

_FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
              "septembre", "octobre", "novembre", "décembre"]


def _fr_date(c: Ctx) -> str:
    dt = parse_dt(c.raw.get("audience") or c.item.get("date_end"))
    if not dt:
        return "____"
    text = f"{dt.day} {_FR_MONTHS[dt.month - 1]} {dt.year}"
    if dt.hour or dt.minute:
        text += f" à {dt.hour}h{dt.minute:02d}"
    return text


def _fr_tribunal(c: Ctx) -> str:
    return c.raw.get("tribunal") or (f"Tribunal judiciaire de {c.item['district']}"
                                     if c.item.get("district") else "tribunal judiciaire")


def _fr_signature(c: Ctx) -> str:
    return (f"{c.nome}\nPièce d'identité / NIF : {c.nif}\n{c.p.get('morada', '')}\n{c.email}"
            + (f"\nTél. : {c.telefone}" if c.telefone else ""))


def _fr_mandat(c: Ctx):
    tribunal = _fr_tribunal(c)
    mise = format_amount(c.item.get("price"), "FR") if c.item.get("price") else "____"
    city = c.raw.get("tribunal_ville") or (tribunal.split(" de ", 1)[1] if " de " in tribunal
                                           else c.item.get("district") or "________")
    body = (
        "Maître,\n\n"
        f"Je souhaite me porter acquéreur du bien mis en vente aux enchères publiques devant le "
        f"{tribunal}, à l'audience du {_fr_date(c)}, et je sollicite votre représentation pour "
        "porter les enchères en mon nom.\n\n"
        f"   Bien : {c.title}\n"
        f"   Situation : {c.loc}\n"
        f"   Mise à prix : {mise}\n"
        f"   Annonce : {_reference(c)}\n\n"
        f"Je vous donne mandat de porter les enchères jusqu'à un montant maximum de {c.amount} "
        "(hors frais), montant que vous voudrez bien ne pas dépasser.\n\n"
        "Je vous remercie de bien vouloir me confirmer :\n"
        "   1. votre accord pour me représenter à cette audience ;\n"
        "   2. le montant de vos honoraires et une estimation des frais de la vente ;\n"
        "   3. les modalités de la consignation (chèque de banque ou caution bancaire de 10 % "
        "de la mise à prix, avec un minimum de 3 000 €) ;\n"
        "   4. la liste des pièces à vous fournir.\n\n"
        "Je reste à votre disposition pour tout renseignement complémentaire.\n\n"
        "Je vous prie d'agréer, Maître, l'expression de mes salutations distinguées.\n\n\n\n"
        f"{_fr_signature(c)}"
    )
    return (["Maître ________________", f"Avocat au Barreau de {city}"],
            f"Demande de représentation — vente judiciaire du {_fr_date(c)} — {tribunal}", body)


def _fr_info(c: Ctx):
    tribunal = _fr_tribunal(c)
    avocat = c.raw.get("avocat_nom")
    body = (
        "Maître,\n\n"
        f"Intéressé par le bien mis en vente aux enchères devant le {tribunal} à l'audience du "
        f"{_fr_date(c)} ({c.title}, {c.loc}), dont vous êtes l'avocat poursuivant, je vous serais "
        "reconnaissant de bien vouloir m'adresser :\n\n"
        "   1. le cahier des conditions de vente ;\n"
        "   2. les dates et modalités des visites ;\n"
        "   3. toute information sur l'occupation du bien (libre ou occupé, bail en cours) ;\n"
        "   4. les diagnostics techniques et, le cas échéant, le montant des charges de copropriété.\n\n"
        "Je vous remercie par avance de votre retour.\n\n"
        "Je vous prie d'agréer, Maître, l'expression de mes salutations distinguées.\n\n\n\n"
        f"{_fr_signature(c)}"
    )
    return ([f"Maître {avocat}" if avocat else "Maître (avocat poursuivant)", tribunal],
            f"Demande de renseignements — vente du {_fr_date(c)}, {tribunal}", body)


def _fr_offre(c: Ctx):
    body = (
        "Madame, Monsieur,\n\n"
        "Je vous fais part de mon intérêt pour le bien suivant et vous soumets une offre d'achat :\n\n"
        f"   Bien : {c.title}\n"
        f"   Situation : {c.loc}\n"
        f"   Surface : {c.area}\n"
        f"   Référence : {_reference(c)}\n\n"
        f"   Montant de l'offre : {c.amount}\n\n"
        "Je vous remercie de me faire savoir si cette offre peut être acceptée ou, à défaut, le "
        "montant que vous seriez disposés à considérer, ainsi que les possibilités de visite.\n\n"
        "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.\n\n\n\n"
        f"{_fr_signature(c)}"
    )
    return ["Madame, Monsieur"], f"Offre d'achat — {c.title[:60]}", body


# Everything else: the general templates --------------------------------

def _generic(c: Ctx):
    tmpl = GENERIC_TEMPLATES.get(c.country, GENERIC_TEMPLATES["DEFAULT"])
    kind = "negociacao" if sale_kind(c.raw.get("modalidade")) == "negociacao" else "carta_fechada"
    body = tmpl[kind].format(title=c.title, location=c.loc, area=c.area, nome=c.nome, nif=c.nif,
                             morada=c.morada, email=c.email, bid=c.bid, bid_text=c.bid_text,
                             processo=c.processo)
    recipient = [tmpl["salutation"], *([c.raw["tribunal"]] if c.raw.get("tribunal") else [])]
    return recipient, tmpl["subject"].format(processo=c.processo).replace("—", "-"), body


def _is(country: str, sources: set[str] | None = None, kind: str | None = None):
    def applies(item: dict, raw: dict) -> bool:
        if (item.get("country") or "PT") != country:
            return False
        if sources is not None and item.get("source") not in sources:
            return False
        return kind is None or sale_kind(raw.get("modalidade")) == kind
    return applies


def _other_country(item: dict, raw: dict) -> bool:
    return (item.get("country") or "PT") not in ("PT", "ES", "FR")


LETTER_TYPES: list[LetterType] = [
    LetterType("pt_carta_fechada", "PT", "Sealed-bid offer (proposta em carta fechada)", True,
               _is("PT", PT_COURT_SOURCES, "carta_fechada"), _pt_offer("carta_fechada"),
               suggest=_pt_suggest, presets=lambda i: PT_PRESETS),
    LetterType("pt_negociacao", "PT", "Offer, private negotiation (negociação particular)", True,
               _is("PT", PT_COURT_SOURCES, "negociacao"), _pt_offer("negociacao"),
               suggest=_pt_suggest, presets=lambda i: PT_PRESETS),
    LetterType("pt_adjudicacao", "PT", "Offer, adjudication stage (adjudicação)", True,
               _is("PT", PT_COURT_SOURCES, "adjudicacao"), _pt_offer("adjudicacao"),
               suggest=_pt_suggest, presets=lambda i: PT_PRESETS),
    LetterType("pt_banco", "PT", "Purchase offer to the bank", True,
               _is("PT", PT_BANK_SOURCES), _pt_bank,
               suggest=_pct_suggest(75), presets=lambda i: _percent_presets(i.get("price"))),
    LetterType("pt_info", "PT", "Information request to the court", False,
               _is("PT", PT_COURT_SOURCES | {"eleiloes"}), _pt_info),
    LetterType("es_oferta", "ES", "Purchase offer to the servicer (oferta de compra)", True,
               _is("ES", ES_SERVICER_SOURCES), _es_servicer,
               suggest=_pct_suggest(75), presets=lambda i: _percent_presets(i.get("price"))),
    LetterType("es_info", "ES", "Information request to the court (solicitud de información)",
               False, _is("ES", ES_COURT_SOURCES), _es_info),
    LetterType("fr_mandat", "FR", "Instructions to your lawyer (mandat d'enchérir)", True,
               _is("FR", FR_COURT_SOURCES), _fr_mandat, amount_label="Your maximum bid (EUR)",
               suggest=_price_suggest, presets=lambda i: _percent_presets((i.get("price") or 0) * 2)),
    LetterType("fr_info", "FR", "Information request to the seller's lawyer",
               False, _is("FR", FR_COURT_SOURCES), _fr_info),
    LetterType("fr_offre", "FR", "Purchase offer to the seller (offre d'achat)", True,
               lambda i, r: False, _fr_offre,
               suggest=_pct_suggest(75), presets=lambda i: _percent_presets(i.get("price"))),
    LetterType("generic_offer", "*", "Purchase offer (general template)", True,
               _other_country, _generic, suggest=_pt_suggest, presets=lambda i: PT_PRESETS),
]
_BY_KEY = {t.key: t for t in LETTER_TYPES}
OFFER_TYPES = {t.key for t in LETTER_TYPES if t.is_offer} | {"online"}


def letter_types_for(item: dict) -> list[LetterType]:
    """Letters that make sense for this sale, the usual one first. An online
    auction may have none (its bid is logged, not written)."""
    raw = _raw(item)
    found = [t for t in LETTER_TYPES if t.applies(item, raw)]
    if not found and channel(item) != "online":
        # A source with no letters of its own: the country's plain offer letter.
        country = item.get("country") or "PT"
        fallback = {"PT": f"pt_{sale_kind(raw.get('modalidade'))}", "ES": "es_oferta",
                    "FR": "fr_offre"}.get(country, "generic_offer")
        found = [_BY_KEY[fallback]]
    return found


def get_type(key: str | None, item: dict) -> LetterType | None:
    types = letter_types_for(item)
    if key:
        return next((t for t in types if t.key == key), None)
    return types[0] if types else None


# ─── Building a letter ───────────────────────────────────────────────

SUBJECT_LABELS = {"PT": "Assunto: ", "ES": "Asunto: ", "FR": "Objet : "}


def split_letter(text: str) -> dict | None:
    """A letter's text back into its parts: sender, place and date, recipient,
    subject line, body. They are separated by the first four blank lines, as
    Letter.text writes them. None if the text no longer has that shape."""
    blocks = (text or "").replace("\r\n", "\n").split("\n\n", 4)
    if len(blocks) < 5 or not all(b.strip() for b in blocks[:4]):
        return None
    sender, place_date, recipient, subject_line, body = blocks
    return {"sender": sender.splitlines(), "place_date": place_date.strip(),
            "recipient": recipient.splitlines(), "subject_line": " ".join(subject_line.split()),
            "body": body.strip("\n")}


@dataclass
class Letter:
    country: str
    sender: list[str]
    place_date: str
    recipient: list[str]
    subject: str
    body: str
    processo: str = ""
    to_email: str = ""
    kind: str = "carta_fechada"
    type_key: str = ""
    item_id: str = ""
    is_offer: bool = True
    ref: str = ""
    extra: dict = field(default_factory=dict)
    edited_text: str = ""      # the user's own version of the whole letter, if they edited it

    @property
    def subject_line(self) -> str:
        return SUBJECT_LABELS.get(self.country, "") + self.subject

    @property
    def generated_text(self) -> str:
        return "\n".join([*self.sender, "", self.place_date, "", *self.recipient, "",
                          self.subject_line, "", self.body])

    @property
    def text(self) -> str:
        """What the preview, the PDF and the e-mail contain."""
        return self.edited_text or self.generated_text

    def with_text(self, text: str | None) -> "Letter":
        """This letter as the user edited it. An edited subject line becomes the
        e-mail subject. Unchanged or empty text leaves the letter as generated."""
        text = (text or "").replace("\r\n", "\n").strip()
        if not text or text == self.generated_text.strip():
            return self
        parts = split_letter(text)
        subject = self.subject
        if parts:
            subject = parts["subject_line"]
            for label in (*SUBJECT_LABELS.values(), "Subject: "):
                if label and subject.startswith(label):
                    subject = subject[len(label):]
                    break
        return replace(self, edited_text=text, subject=" ".join(subject.split()))

    @property
    def filename(self) -> str:
        ref = re.sub(r"[^A-Za-z0-9.-]+", "-", self.processo or self.item_id or "sem-processo").strip("-")
        prefix = {"PT": "carta", "ES": "carta", "FR": "lettre"}.get(self.country, "letter")
        if not self.is_offer:
            prefix = {"PT": "pedido-info", "ES": "solicitud-info", "FR": "demande-info"}.get(
                self.country, "info-request")
        return f"{prefix}_{ref}.pdf"


_MONTH_NAMES = {
    "PT": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
           "setembro", "outubro", "novembro", "dezembro"],
    "ES": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"],
    "FR": _FR_MONTHS,
    "IT": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
           "settembre", "ottobre", "novembre", "dicembre"],
}


def _today_for_country(country: str, place: str = "", when: date | None = None) -> str:
    """The place-and-date line, as that country writes it: "Guarda, 24 de
    setembro de 2026", "Nîmes, le 24 septembre 2026". Without a place, just the date."""
    d = when or date.today()
    if country in ("PT", "ES"):
        text = f"{d.day} de {_MONTH_NAMES[country][d.month - 1]} de {d.year}"
    elif country == "FR":
        text = f"le {d.day} {_FR_MONTHS[d.month - 1]} {d.year}"
    elif country == "IT":
        text = f"{d.day} {_MONTH_NAMES['IT'][d.month - 1]} {d.year}"
    elif country == "DE":
        text = f"den {d.strftime('%d.%m.%Y')}"
    elif country == "HR":
        text = d.strftime("%d.%m.%Y.")
    elif country in ("NL", "BE"):
        text = d.strftime("%d-%m-%Y")
    else:
        text = d.strftime("%Y-%m-%d")
    if place:
        return f"{place}, {text}"
    return text[0].upper() + text[1:]


def build_letter(item: dict, bid: str, bid_text: str | None, proponente: dict,
                 letter_type: str | None = None) -> Letter:
    """The letter for one listing. `bid` is "4.000,00"; an empty bid_text is
    written out in words (Portuguese letters only). `letter_type` picks one of
    letter_types_for(item); None means the usual one."""
    raw = _raw(item)
    country = item.get("country") or "PT"
    ltype = get_type(letter_type, item)
    if ltype is None:
        if letter_type:
            raise ValueError(f"no letter of type {letter_type!r} for {item.get('id')}")
        ltype = _BY_KEY["generic_offer"]      # an online-only sale: a plain offer, if asked
    processo = str(raw.get("processo") or item.get("external_id") or "").split(",")[0].strip()
    loc = place_of(item)
    value = parse_bid(bid)
    if not bid_text and country == "PT":
        bid_text = por_extenso(value) if value else ""
    if country == "PT":
        area = f"{item['area_m2']:.0f} m²" if item.get("area_m2") else "área não especificada"
    else:
        area = f"{item['area_m2']:,.0f} m²" if item.get("area_m2") else "n/a"

    ctx = Ctx(item=item, raw=raw, country=country, title=(item.get("title") or "Imóvel")[:120],
              loc=loc, area=area, processo=processo, bid=bid or "____",
              amount=format_amount(value, country), bid_text=bid_text or "", p=proponente or {})
    recipient, subject, body = ltype.render(ctx)

    p = proponente or {}
    id_label = "NIF" if country == "PT" else "NIF/ID"
    sender = [p.get("nome", ""), f"{id_label}: {p.get('nif', '')}",
              *[line.strip() for line in (p.get("morada") or "").splitlines() if line.strip()]]
    if p.get("telefone"):
        sender.append(f"Tel.: {p['telefone']}")

    if ltype.key == "fr_mandat":
        to_email = ""                      # your own lawyer, whom you choose
    elif ltype.key == "fr_info":
        to_email = raw.get("avocat_email") or ""
    else:
        to_email = raw.get("agente_email") or raw.get("autoridad_email") or ""
    return Letter(
        country=country,
        sender=sender,
        place_date=_today_for_country(country, (p.get("localidade") or "").strip()),
        recipient=[r for r in recipient if r],
        subject=subject.replace("—", "-") if country == "PT" else subject,
        body=body,
        processo=processo,
        to_email=to_email,
        kind=sale_kind(raw.get("modalidade")),
        type_key=ltype.key,
        item_id=item.get("id") or "",
        is_offer=ltype.is_offer,
        ref=f"Ref: {raw.get('processo') or item.get('id', '')}",
        extra={"bid_text": bid_text or ""},
    )


# ─── PDF ─────────────────────────────────────────────────────────────

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_DEJAVU = {
    "DejaVuSans.ttf": "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf": "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf",
}


def _unicode_fonts() -> tuple[str, str] | None:
    """DejaVu (full Unicode) — downloaded once into fonts/. None if unavailable."""
    paths = {name: os.path.join(FONT_DIR, name) for name in _DEJAVU}
    if not all(os.path.exists(p) for p in paths.values()):
        try:
            os.makedirs(FONT_DIR, exist_ok=True)
            for name, url in _DEJAVU.items():
                if not os.path.exists(paths[name]):
                    LOG.info(f"Downloading {name} for PDF letters...")
                    urllib.request.urlretrieve(url, paths[name])
        except Exception as e:
            LOG.warning(f"Could not download DejaVu font ({e}); PDFs will drop accents.")
            return None
    return paths["DejaVuSans.ttf"], paths["DejaVuSans-Bold.ttf"]


# Helvetica (the fallback when DejaVu cannot be downloaded) prints Latin-1:
# every Portuguese, Spanish and French accent. Only what is outside it is replaced.
_LATIN1_SUBSTITUTES = {
    "\u202f": " ", "\u2009": " ", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
    "…": "...", "€": "EUR", "œ": "oe", "Œ": "OE", "ł": "l", "ć": "c", "č": "c", "š": "s", "ž": "z",
}


def _safe_latin1(text: str) -> str:
    if not text:
        return ""
    for old, new in _LATIN1_SUBSTITUTES.items():
        text = text.replace(old, new)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def letter_pdf(letter: Letter, path: str | None = None) -> bytes:
    """Render a letter as PDF (as edited, if it was). Writes it to `path` if
    given; returns the bytes."""
    return text_pdf(letter.text, ref=letter.ref, path=path)


def text_pdf(text: str, *, ref: str = "", path: str | None = None) -> bytes:
    """A letter's text as a PDF: sender, date on the right, recipient, subject in
    bold, body. Text that no longer has that shape is printed as it is."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)
    fonts = _unicode_fonts()
    if fonts:
        pdf.add_font("DejaVu", "", fonts[0])
        pdf.add_font("DejaVu", "B", fonts[1])
        fn, s = "DejaVu", (lambda t: t or "")
    else:
        fn, s = "Helvetica", _safe_latin1

    parts = split_letter(text)
    if parts:
        for i, line in enumerate(parts["sender"]):
            pdf.set_font(fn, "B" if i == 0 else "", 11 if i == 0 else 10)
            pdf.cell(0, 6 if i == 0 else 5, s(line), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        pdf.set_font(fn, size=10)
        pdf.cell(0, 5, s(parts["place_date"]), new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(6)
        for i, line in enumerate(parts["recipient"]):
            pdf.set_font(fn, "B" if i == 0 else "", 10)
            pdf.cell(0, 5, s(line), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        pdf.set_font(fn, "B", 10)
        pdf.multi_cell(0, 5, s(parts["subject_line"]))
        pdf.ln(4)
        body = parts["body"]
    else:
        body = text
    pdf.set_font(fn, size=10)
    pdf.multi_cell(0, 5, s(body), align="L")   # justified text would stretch the indented lists
    if ref:
        pdf.ln(10)
        pdf.set_font(fn, size=7)
        pdf.cell(0, 4, s(ref), new_x="LMARGIN", new_y="NEXT")

    data = bytes(pdf.output())
    if path:
        with open(path, "wb") as f:
            f.write(data)
    return data


# ─── General templates (other countries) ─────────────────────────────

GENERIC_TEMPLATES = {
    "DE": {
        "subject": "Gebot — Zwangsversteigerung {processo}",
        "salutation": "Sehr geehrte Damen und Herren",
        "carta_fechada": """Sehr geehrte Damen und Herren,

hiermit möchte ich ein Gebot für die im oben genannten Zwangsversteigerungsverfahren angebotene Immobilie abgeben.

Beschreibung: {title}
Lage: {location}
Fläche: {area}

GEBOT:

   Bieter: {nome}
   Ausweis-Nr.: {nif}
   Anschrift: {morada}
   E-Mail: {email}
   Gebotsbetrag: EUR {bid}

Ich bitte um Auskunft über:
   1. Die Frist zur Abgabe von Geboten;
   2. Ob eine Sicherheitsleistung erforderlich ist und in welcher Höhe;
   3. Den Ort und die Zeit der Gebotsöffnung.

Für Rückfragen stehe ich gerne zur Verfügung.

Mit freundlichen Grüßen,



{nome}""",
        "negociacao": """Sehr geehrte Damen und Herren,

ich interessiere mich für den Erwerb der oben genannten Immobilie und möchte folgendes Angebot unterbreiten:

Beschreibung: {title}
Lage: {location}
Fläche: {area}

Angebotspreis: EUR {bid}

Meine Kontaktdaten:
   Name: {nome}
   Ausweis-Nr.: {nif}
   Anschrift: {morada}
   E-Mail: {email}

Mit freundlichen Grüßen,



{nome}""",
    },
    "IT": {
        "subject": "Offerta di Acquisto — Procedura {processo}",
        "salutation": "Egregio/a Signor/a Giudice / Delegato alla vendita",
        "carta_fechada": """Egregio/a Signor/a,

Con la presente intendo presentare un'offerta di acquisto per l'immobile oggetto di vendita giudiziaria nell'ambito della procedura in oggetto.

Descrizione del bene: {title}
Ubicazione: {location}
Superficie: {area}

OFFERTA DI ACQUISTO:

   Offerente: {nome}
   Codice Fiscale/Passaporto: {nif}
   Indirizzo: {morada}
   Email: {email}
   Importo offerto: EUR {bid}

Chiedo inoltre informazioni su:
   1. Il termine per la presentazione delle offerte;
   2. Se è richiesta una cauzione e il relativo importo;
   3. Il luogo e l'orario di presentazione delle buste;
   4. La data di apertura delle offerte.

Resto a disposizione per qualsiasi chiarimento.

Distinti saluti,



{nome}""",
        "negociacao": """Egregio/a Signor/a,

Mi rivolgo a Lei per manifestare il mio interesse nell'acquisto dell'immobile in vendita nell'ambito della procedura sopra indicata.

Descrizione del bene: {title}
Ubicazione: {location}
Superficie: {area}

Offerta: EUR {bid}

Dati dell'acquirente:
   Nome: {nome}
   Codice Fiscale/Passaporto: {nif}
   Indirizzo: {morada}
   Email: {email}

Distinti saluti,



{nome}""",
    },
    "NL": {
        "subject": "Bod — Executieveiling {processo}",
        "salutation": "Geachte heer/mevrouw",
        "carta_fechada": """Geachte heer/mevrouw,

Hierbij doe ik een bod op het onroerend goed dat wordt geveild in het kader van bovengenoemde executieprocedure.

Omschrijving: {title}
Locatie: {location}
Oppervlakte: {area}

BOD:

   Bieder: {nome}
   Paspoort/ID: {nif}
   Adres: {morada}
   E-mail: {email}
   Bedrag: EUR {bid}

Ik verzoek u mij te informeren over:
   1. De uiterste termijn voor het indienen van biedingen;
   2. Of een waarborgsom vereist is en het bedrag daarvan;
   3. De locatie en tijd van de biedopening.

Met vriendelijke groet,



{nome}""",
        "negociacao": """Geachte heer/mevrouw,

Ik heb interesse in de aankoop van bovengenoemd onroerend goed en doe hierbij het volgende bod:

Omschrijving: {title}
Locatie: {location}
Oppervlakte: {area}

Bod: EUR {bid}

Mijn gegevens:
   Naam: {nome}
   Paspoort/ID: {nif}
   Adres: {morada}
   E-mail: {email}

Met vriendelijke groet,



{nome}""",
    },
    "HR": {
        "subject": "Ponuda za kupnju — Predmet {processo}",
        "salutation": "Poštovani/a",
        "carta_fechada": """Poštovani/a,

Ovim putem podnosim ponudu za kupnju nekretnine koja se prodaje u okviru gore navedenog postupka.

Opis nekretnine: {title}
Lokacija: {location}
Površina: {area}

PONUDA ZA KUPNJU:

   Ponuditelj: {nome}
   Putovnica/OIB: {nif}
   Adresa: {morada}
   E-pošta: {email}
   Ponuđeni iznos: EUR {bid}

Molim Vas da me obavijestite o:
   1. Roku za dostavu ponuda;
   2. Je li potrebno položiti jamčevinu i u kojem iznosu;
   3. Mjestu i vremenu otvaranja ponuda.

S poštovanjem,



{nome}""",
        "negociacao": """Poštovani/a,

Zainteresiran/a sam za kupnju gore navedene nekretnine i podnosim sljedeću ponudu:

Opis: {title}
Lokacija: {location}
Površina: {area}

Ponuda: EUR {bid}

Podaci kupca:
   Ime i prezime: {nome}
   Putovnica/OIB: {nif}
   Adresa: {morada}
   E-pošta: {email}

S poštovanjem,



{nome}""",
    },
    "DEFAULT": {
        "subject": "Purchase Offer — Case {processo}",
        "salutation": "Dear Sir/Madam",
        "carta_fechada": """Dear Sir/Madam,

I hereby submit a purchase offer for the property being sold in the above-referenced judicial proceedings.

Property description: {title}
Location: {location}
Area: {area}

PURCHASE OFFER:

   Bidder: {nome}
   Passport/ID: {nif}
   Address: {morada}
   Email: {email}
   Offered amount: EUR {bid}

I would also appreciate information on:
   1. The deadline for submitting offers;
   2. Whether a deposit is required and the amount;
   3. The location and time for offer submission;
   4. The date of offer opening.

I remain available for any further clarification.

Kind regards,



{nome}""",
        "negociacao": """Dear Sir/Madam,

I am interested in purchasing the above-mentioned property and submit the following offer:

Description: {title}
Location: {location}
Area: {area}

Offer: EUR {bid}

Buyer details:
   Name: {nome}
   Passport/ID: {nif}
   Address: {morada}
   Email: {email}

Kind regards,



{nome}""",
    },
}
