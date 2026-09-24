"""
Generate proposal letters (cartas) for active Citius judicial sales.
Checks live status on Citius, filters for quality properties, and generates PDFs.
"""

import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("cartas")

CITIUS_URL = "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx"

RUSTICO_KEYWORDS = [
    "mato", "pinhal", "pastagem", "cultura arvense", "sequeiro",
    "oliveir", "vinha", "eucalipt", "sobreir", "pasto",
]
CASA_KEYWORDS = [
    "casa", "habitação", "habitacao", "moradia", "apartamento", "andar",
    "assoalhada", "r/c", "res-do-chao", "rés-do-chão", "prédio urbano",
    "predio urbano", "fração autónoma", "fracao autonoma",
]
TERRENO_CONSTRUCAO_KEYWORDS = [
    "construção urbana", "construcao urbana", "lote", "urbaniz",
]

MIN_HERDADE_M2 = 5000

# ── Multi-country carta templates ────────────────────────────────────────────

CARTA_TEMPLATES = {
    "PT": {
        "subject": "Proposta de Aquisição — Processo {processo}",
        "salutation": "Exmo(a). Sr(a). Juiz / Agente de Execução",
        "carta_fechada": """Exmo(a). Sr(a),

Venho por este meio apresentar proposta de aquisição do imóvel em venda mediante proposta em carta fechada no âmbito do processo acima referido.

Descrição do bem: {title}
Localização: {location}
Área: {area}

PROPOSTA DE AQUISIÇÃO:

   Proponente: {nome}
   NIF: {nif}
   Morada: {morada}
   Email: {email}
   Valor da proposta: EUR {bid} ({bid_text})

Solicito igualmente informação sobre:
   1. O prazo limite para entrega de propostas;
   2. Se é necessário juntar cheque visado de caução e montante;
   3. O local e horário para entrega de propostas;
   4. A data prevista para abertura das propostas.

Encontro-me disponível para qualquer esclarecimento adicional.

Com os melhores cumprimentos,



{nome}
NIF: {nif}""",
        "negociacao": """Exmo(a). Sr(a),

Venho por este meio manifestar o meu interesse na aquisição do imóvel em venda por negociação particular no âmbito do processo acima referido.

Descrição do bem: {title}
Localização: {location}
Área: {area}

Apresento a seguinte proposta de aquisição:

   Valor: EUR {bid} ({bid_text})

Dados do proponente:
   Nome: {nome}
   NIF: {nif}
   Morada: {morada}
   Email: {email}

Solicito que me informem sobre os procedimentos necessários para formalizar a proposta.

Com os melhores cumprimentos,



{nome}
NIF: {nif}""",
    },
    "ES": {
        "subject": "Propuesta de Adquisición — Subasta {processo}",
        "salutation": "Estimado/a Sr./Sra. Letrado/a de la Administración de Justicia",
        "carta_fechada": """Estimado/a Sr./Sra.,

Por medio de la presente, me dirijo a usted para presentar oferta de adquisición del bien inmueble objeto de subasta en el procedimiento arriba referenciado.

Descripción del bien: {title}
Localización: {location}
Superficie: {area}

OFERTA DE ADQUISICIÓN:

   Licitador: {nome}
   NIF/NIE: {nif}
   Domicilio: {morada}
   Email: {email}
   Importe ofertado: EUR {bid}

Solicito asimismo información sobre:
   1. El plazo límite para la presentación de ofertas;
   2. Si es necesario constituir depósito previo y su importe;
   3. El lugar y horario de presentación de ofertas;
   4. La fecha prevista para la apertura de plicas.

Quedo a su disposición para cualquier aclaración.

Atentamente,



{nome}
NIF/NIE: {nif}""",
        "negociacao": """Estimado/a Sr./Sra.,

Me pongo en contacto con ustedes para manifestar mi interés en la adquisición del inmueble en venta directa en el marco del procedimiento arriba referenciado.

Descripción del bien: {title}
Localización: {location}
Superficie: {area}

Presento la siguiente oferta:

   Importe: EUR {bid}

Datos del comprador:
   Nombre: {nome}
   NIF/NIE: {nif}
   Domicilio: {morada}
   Email: {email}

Quedo a su disposición para formalizar la propuesta.

Atentamente,



{nome}
NIF/NIE: {nif}""",
    },
    "FR": {
        "subject": "Offre d'Acquisition — Dossier {processo}",
        "salutation": "Maître / Madame, Monsieur le Juge",
        "carta_fechada": """Maître / Madame, Monsieur,

J'ai l'honneur de vous soumettre une offre d'acquisition pour le bien immobilier mis en vente aux enchères judiciaires dans le cadre de la procédure susmentionnée.

Description du bien: {title}
Localisation: {location}
Surface: {area}

OFFRE D'ACQUISITION:

   Acquéreur: {nome}
   Passeport/ID: {nif}
   Adresse: {morada}
   Email: {email}
   Montant proposé: EUR {bid}

Je sollicite également les informations suivantes:
   1. La date limite de dépôt des offres;
   2. Les modalités de constitution de la consignation;
   3. Le lieu et les horaires de dépôt des plis;
   4. La date d'ouverture des plis.

Je reste à votre disposition pour tout renseignement complémentaire.

Veuillez agréer, Maître, l'expression de mes salutations distinguées,



{nome}""",
        "negociacao": """Maître / Madame, Monsieur,

Je me permets de vous contacter afin de manifester mon intérêt pour l'acquisition du bien immobilier en vente dans le cadre de la procédure susmentionnée.

Description du bien: {title}
Localisation: {location}
Surface: {area}

Je vous soumets l'offre suivante:

   Montant: EUR {bid}

Coordonnées:
   Nom: {nome}
   Passeport/ID: {nif}
   Adresse: {morada}
   Email: {email}

Dans l'attente de votre retour, veuillez agréer mes salutations distinguées,



{nome}""",
    },
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
}

# English fallback
CARTA_TEMPLATES["DEFAULT"] = {
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
}

_MONTH_NAMES = {
    "PT": ["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"],
    "ES": ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"],
    "FR": ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"],
    "IT": ["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"],
}


def _today_for_country(country: str, place: str = "Guarda") -> str:
    from datetime import date
    d = date.today()
    if country in _MONTH_NAMES:
        return f"{place}, {d.day} de {_MONTH_NAMES[country][d.month-1]} de {d.year}"
    if country == "DE":
        return f"{place}, den {d.strftime('%d.%m.%Y')}"
    if country == "HR":
        return f"{place}, {d.strftime('%d.%m.%Y.')}"
    if country == "NL":
        return f"{place}, {d.strftime('%d-%m-%Y')}"
    return f"{place}, {d.strftime('%Y-%m-%d')}"


def build_carta_for_country(item: dict, raw: dict, bid: str, bid_text: str,
                            proponente: dict, country: str) -> str:
    tmpl = CARTA_TEMPLATES.get(country, CARTA_TEMPLATES["DEFAULT"])
    modalidade = raw.get("modalidade", "").lower()
    is_neg = "negoci" in modalidade or "direct" in modalidade or "private" in modalidade
    body_key = "negociacao" if is_neg else "carta_fechada"

    processo = raw.get("processo", item.get("external_id", ""))
    loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
    area = f"{item['area_m2']:,.0f} m²" if item.get("area_m2") else "n/a"

    header = (
        f"{proponente['nome']}\n"
        f"NIF/ID: {proponente['nif']}\n"
        f"{proponente['morada']}\n\n"
        f"{_today_for_country(country, proponente.get('localidade') or 'Guarda')}\n\n"
        f"{tmpl['salutation']}\n"
        f"{raw.get('tribunal', '')}\n\n"
        f"{tmpl['subject'].format(processo=processo)}\n\n"
    )

    body = tmpl[body_key].format(
        title=item.get("title", ""),
        location=loc,
        area=area,
        nome=proponente["nome"],
        nif=proponente["nif"],
        morada=proponente["morada"],
        email=proponente.get("email", ""),
        bid=bid,
        bid_text=bid_text,
        processo=processo,
    )

    return header + body


def _safe_latin1(text: str) -> str:
    if not text:
        return ""
    replacements = {
        'ã': 'a', 'õ': 'o', 'ç': 'c', 'é': 'e',
        'ê': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'á': 'a', 'à': 'a', 'ô': 'o', 'â': 'a',
        'Ã': 'A', 'Ç': 'C', 'É': 'E', 'Í': 'I',
        'Ó': 'O', 'Ú': 'U', 'Ô': 'O',
        'º': 'o', 'ª': 'a',
        '–': '-', '—': '-', '“': '"', '”': '"',
        '‘': "'", '’': "'", '…': '...', '²': '2',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode('latin-1', errors='replace').decode('latin-1')


def classify_property(title: str, description: str, area_m2: float) -> str | None:
    combined = (title + " " + description).lower()
    area = area_m2 or 0

    is_casa = any(k in combined for k in CASA_KEYWORDS)
    is_terreno = any(k in combined for k in TERRENO_CONSTRUCAO_KEYWORDS)
    is_rustico_small = any(k in combined for k in RUSTICO_KEYWORDS) and area < MIN_HERDADE_M2
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


def check_citius_active(processes: dict[str, str]) -> dict[str, str]:
    """Check which processes are active on Citius.

    Args:
        processes: {process_number: tribunal_name}

    Returns:
        {process_number: estado_string} — "Em venda" means active
    """
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    r = session.get(CITIUS_URL, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    tribunal_options = {
        o.get_text(strip=True): o["value"]
        for o in soup.select("#ctl00_ContentPlaceHolder1_ddlTribunais option")
        if o["value"] != "0"
    }

    by_tribunal = defaultdict(list)
    for proc, trib in processes.items():
        by_tribunal[trib].append(proc)

    results = {}

    for trib_name, proc_list in by_tribunal.items():
        trib_id = None
        for opt_name, opt_val in tribunal_options.items():
            if trib_name in opt_name or opt_name in trib_name:
                trib_id = opt_val
                break
        if not trib_id:
            key = trib_name.split(" - ")[0]
            for opt_name, opt_val in tribunal_options.items():
                if key in opt_name:
                    trib_id = opt_val
                    break

        if not trib_id:
            for proc in proc_list:
                results[proc] = "TRIBUNAL NAO ENCONTRADO"
            continue

        try:
            r0 = session.get(CITIUS_URL, timeout=15)
            s0 = BeautifulSoup(r0.text, "html.parser")
            vs = s0.select_one("#__VIEWSTATE")["value"]
            ev = s0.select_one("#__EVENTVALIDATION")["value"]
            vsg = s0.select_one("#__VIEWSTATEGENERATOR")["value"]
        except Exception:
            for proc in proc_list:
                results[proc] = "ERRO"
            continue

        data = {
            "__EVENTTARGET": "", "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg,
            "__VIEWSTATEENCRYPTED": "", "__EVENTVALIDATION": ev,
            "ctl00$ContentPlaceHolder1$ddlTribunais": trib_id,
            "ctl00$ContentPlaceHolder1$txtCalendarDesde": "01/01/2000",
            "ctl00$ContentPlaceHolder1$txtCalendarAte": "31/12/2030",
            "ctl00$ContentPlaceHolder1$chkDatas": "on",
            "ctl00$ContentPlaceHolder1$ddlTiposBem": "0",
            "ctl00$ContentPlaceHolder1$ddlModalidades": "0",
            "ctl00$ContentPlaceHolder1$ddlEstados": "0",
            "ctl00$ContentPlaceHolder1$btnSearch": "Pesquisar",
        }

        try:
            r2 = session.post(CITIUS_URL, data=data, timeout=30)
            page_text = r2.text
        except Exception:
            for proc in proc_list:
                results[proc] = "ERRO"
            continue

        for proc in proc_list:
            if proc in page_text:
                idx = page_text.find(proc)
                snippet = page_text[max(0, idx - 2000):idx + 2000]
                snippet_soup = BeautifulSoup(snippet, "html.parser")
                snippet_text = snippet_soup.get_text(" ", strip=True)

                estado_m = re.search(
                    r"Estado:\s*([^\n]+?)(?:Valor|Modalidade|Tipo|$)",
                    snippet_text, re.I,
                )
                if estado_m:
                    results[proc] = estado_m.group(1).strip()
                else:
                    for pat in ["Em venda", "Vendido", "Suspenso", "Concluído", "Sem efeito", "Deserto"]:
                        if pat.lower() in snippet_text.lower():
                            results[proc] = pat
                            break
                    else:
                        results[proc] = "ENCONTRADO"
            else:
                results[proc] = "NAO ENCONTRADO"

        LOG.info(f"  Checked {trib_name}: {len(proc_list)} processes")
        time.sleep(1)

    return results


def generate_cartas(
    db: sqlite3.Connection,
    score_fn,
    categorize_fn,
    proponente: dict,
    out_dir: str,
    max_price: float = 50000,
    top_n: int = 15,
    filters: dict | None = None,
) -> list[dict]:
    """Find top properties, check if active, generate PDF letters.

    Args:
        score_fn, categorize_fn: ignored; kept so old callers still work.
            Scores come from db.load_listings(), the same as every other view.
        proponente: {"nome": ..., "nif": ..., "morada": ..., "email": ...,
                     "localidade": town printed next to the date (default Guarda)}

    Returns:
        List of generated carta info dicts.
    """
    missing = [k for k in ("nome", "nif", "morada") if not (proponente or {}).get(k)]
    if missing:
        LOG.error(f"config.json proponente is missing {', '.join(missing)}; no cartas generated.")
        return []
    try:
        from fpdf import FPDF
    except ImportError:
        LOG.error("fpdf2 not installed. Run: pip install fpdf2")
        return []

    import urllib.request
    font_dir = os.path.join(os.path.dirname(__file__), "fonts")
    os.makedirs(font_dir, exist_ok=True)
    font_path = os.path.join(font_dir, "DejaVuSans.ttf")
    font_bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
    use_dejavu = True
    if not (os.path.exists(font_path) and os.path.exists(font_bold)):
        try:
            LOG.info("Downloading DejaVu font for PDF generation...")
            urllib.request.urlretrieve(
                "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf",
                font_path,
            )
            urllib.request.urlretrieve(
                "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf",
                font_bold,
            )
        except Exception as e:
            LOG.warning(f"Could not download DejaVu font: {e}. Falling back to Helvetica.")
            use_dejavu = False

    from db import load_listings
    # Same visibility rules as the report and dashboard: no expired, duplicate,
    # stale or filtered listings.
    items = load_listings(db, filters=filters, where="country='PT' AND source='citius'")

    # Score and classify
    candidates = []
    for it in items:
        if it["category"] != "imoveis":
            continue
        sc, reasons = it["score"], it["reasons"]
        if sc == 0:
            continue

        bid = it.get("current_bid") or 0
        if bid > 10000:
            continue

        title = it.get("title") or ""
        desc = it.get("description") or ""
        area = it.get("area_m2") or 0

        cat = classify_property(title, desc, area)
        if cat is None:
            continue

        candidates.append((it, sc, reasons, cat))

    candidates.sort(key=lambda x: -x[1])
    candidates = candidates[:top_n * 2]

    if not candidates:
        LOG.info("No quality properties found for cartas")
        return []

    # Build process -> tribunal map for active check
    proc_trib = {}
    proc_data = {}
    for it, sc, reasons, cat in candidates:
        raw = {}
        if it.get("raw_json"):
            try:
                raw = json.loads(it["raw_json"])
            except Exception:
                pass
        processo = raw.get("processo", "")
        proc_key = processo.split(",")[0].strip() if processo else ""
        tribunal = raw.get("tribunal", "")
        if proc_key and tribunal:
            proc_trib[proc_key] = tribunal
            proc_data[proc_key] = (it, sc, reasons, cat, raw)

    LOG.info(f"Checking {len(proc_trib)} processes on Citius...")
    print(f"\nVerificando {len(proc_trib)} processos no Citius...\n")

    estados = check_citius_active(proc_trib)

    active_procs = []
    for proc, estado in estados.items():
        is_active = "em venda" in estado.lower()
        marker = "OK" if is_active else "XX"
        cat = proc_data[proc][3] if proc in proc_data else "?"
        print(f"  [{marker}] {proc} — {estado} ({cat})")
        if is_active:
            active_procs.append(proc)

    print(f"\n{len(active_procs)} de {len(estados)} ativas\n")

    if not active_procs:
        print("Nenhuma ativa. Nao foram geradas cartas.")
        return []

    # Generate PDFs
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("carta_") and f.endswith(".pdf"):
            os.remove(os.path.join(out_dir, f))

    nome = proponente["nome"]
    nif = proponente["nif"]
    morada = proponente["morada"]
    email = proponente.get("email", "")

    from datetime import date
    d = date.today()
    today = f"{d.day} de {_MONTH_NAMES['PT'][d.month - 1]} de {d.year}"
    localidade = proponente.get("localidade") or "Guarda"

    generated = []
    count = 0

    for proc in active_procs:
        if proc not in proc_data:
            continue
        it, sc, reasons, cat, raw = proc_data[proc]
        valor, valor_texto = suggest_bid(cat, it.get("price"), it.get("area_m2"))

        count += 1
        processo = raw.get("processo", "")
        tribunal = raw.get("tribunal", "")
        modalidade = raw.get("modalidade", "")
        title_short = (it.get("title") or "Imóvel")[:80]
        loc = ", ".join(filter(None, [it.get("concelho", ""), it.get("district", "")]))
        area_str = f"{it['area_m2']:.0f} m²" if it.get("area_m2") else "área não especificada"

        is_negociacao = "negociação particular" in modalidade.lower() or "negociacao particular" in modalidade.lower()
        is_adjudicacao = "adjudica" in modalidade.lower()

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=25)

        if use_dejavu:
            pdf.add_font("DejaVu", "", font_path)
            pdf.add_font("DejaVu", "B", font_bold)
            fn, s = "DejaVu", lambda t: t or ""
        else:
            fn, s = "Helvetica", _safe_latin1

        pdf.set_font(fn, "B", 11)
        pdf.cell(0, 6, s(nome), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(fn, size=10)
        pdf.cell(0, 5, f"NIF: {nif}", new_x="LMARGIN", new_y="NEXT")
        pdf.multi_cell(0, 5, s(morada))
        pdf.ln(8)
        pdf.cell(0, 5, s(f"{localidade}, {today}"), new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(6)

        pdf.set_font(fn, "B", 10)
        pdf.cell(0, 5, s("Exmo(a). Sr(a). Juiz / Agente de Execução"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(fn, size=10)
        pdf.cell(0, 5, s(tribunal), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)

        proc_key = processo.split(",")[0].strip()
        pdf.set_font(fn, "B", 10)
        pdf.cell(0, 5, s(f"Assunto: Proposta de Aquisição - Processo {proc_key}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)
        pdf.set_font(fn, size=10)
        body = s(_pt_letter_body(
            "negociacao" if is_negociacao else "adjudicacao" if is_adjudicacao else "carta_fechada",
            nome=nome, nif=nif, morada=morada, email=email, title=title_short, loc=loc,
            area=area_str, valor=valor, valor_texto=valor_texto,
        ))

        pdf.multi_cell(0, 5, body)
        pdf.ln(10)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 4, _safe_latin1(f"Ref: {processo} | {cat}"), new_x="LMARGIN", new_y="NEXT")

        filename = f"carta_{count:02d}_{proc_key.replace('/', '-')}_{valor.replace('.', '').replace(',', '_')}EUR.pdf"
        filepath = os.path.join(out_dir, filename)
        pdf.output(filepath)

        mod_label = "NEG.PARTICULAR" if is_negociacao else "ADJUDICACAO" if is_adjudicacao else "CARTA FECHADA"
        print(f"  [{count}] EUR {valor} | {mod_label} | {cat}")
        print(f"       {s(title_short)[:60]}")
        print(f"       {s(loc)}")
        print(f"       -> {filename}")
        print()

        generated.append({
            "processo": proc_key,
            "tribunal": tribunal,
            "modalidade": mod_label,
            "valor": valor,
            "categoria": cat,
            "filepath": filepath,
        })

    total = sum(
        float(g["valor"].replace(".", "").replace(",", "."))
        for g in generated
    )
    print(f"=== {count} cartas geradas em {out_dir} ===")
    print(f"Exposicao total: EUR {total:,.2f}")

    import subprocess, sys
    if sys.platform == "win32" and generated:
        subprocess.Popen(f'explorer "{out_dir}"')

    return generated
