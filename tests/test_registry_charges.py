"""Reading a Spanish registry certificate (2026-11-18): what passes to the buyer
of a small-claim court sale besides the bid. The fixtures copy the layouts of
real certificates on the BOE (continuous, lettered, field lists, a court
request bound in front) with made-up names, places and numbers."""
import json

import pytest

import registry_charges as rc
from sale_floors import legal_floor, rank_share

FILLER = (" Registro de la Propiedad de Villanueva, finca registral 4.321, tomo 100, libro 20, folio 5, "
          "inscripcion a favor de la titular, fecha de la inscripcion, euros. ") * 3


def _cert(body: str, head: str = "") -> str:
    return (head + " CERTIFICO: Que para cumplimentar el mandamiento librado en el Procedimiento de "
            "Ejecución de Títulos Judiciales número 1500/2020, he examinado los libros." + FILLER + body
            + " Lo que antecede está conforme con los asientos de su procedencia." + FILLER)


EARLIER_SEIZURE_IN_WORDS = _cert(
    "TERCERO: Que la finca de que se trata aparece GRAVADA con:\n"
    "Un EMBARGO a favor de la Comunidad de Propietarios Edificio Ejemplo, seguido con el número 700/2.015, "
    "en reclamación de SEIS MIL NOVECIENTOS TREINTA Y SIETE EUROS de principal, más DOS MIL OCHENA Y DOS EUROS "
    "para intereses y costas. Objeto de la anotación la letra C.\n"
    "Según resulta de la Anotación Letra: C. La anotación preventiva de embargo letra C queda PRORROGADA por "
    "Diligencia de Ordenación de fecha uno de mayo.\n"
    "Un EMBARGO a favor de la Comunidad de Propietarios Edificio Ejemplo, con el número 1500/2020, en "
    "reclamación de NUEVE MIL CIEN EUROS de principal, más DOS MIL SETECIENTOS TREINTA EUROS. Anotación Letra D.\n"
    "DILIGENCIA DE COMUNICACION A LOS TITULARES POSTERIORES: no existen.")

FIELD_LIST = _cert(
    "TERCERO: CARGAS: Que dicha finca se encuentra gravada con las cargas siguientes:\n"
    "- HIPOTECA: hipoteca a favor de: Ana Ejemplo. Capital principal: 35.000 euros Costas: 1.750 euros. "
    "El préstamo garantizado con esta hipoteca ha dado lugar a la anotación letra H, en cuyo procedimiento se ha "
    "dictado mandamiento ordenando la expedición de la presente certificación.\n"
    "- anotación de embargo administrativo, a favor de la Tesorería General de la Seguridad Social, por un total "
    "de 4.174,34 euros del principal; recargo de apremio de 834,90 euros. Anotación letra A.\n"
    "Villanueva veinticuatro de junio del año dos mil veintiuno\n"
    "anotación de embargo judicial. A favor de: Empresa Ejemplo SL. Principal: 19.879,87 euros. Intereses y "
    "costas: 5.963,96 euros. Anotación letra F. Esta finca queda afecta al pago del impuesto, habiéndose "
    "satisfecho la cantidad de 209,06 euros por autoliquidación.\n"
    "embargo objeto de su anotación letra H, en reclamación de veintiocho mil euros de principal, "
    "procedimiento 1500/2020.\n")

OWN_MORTGAGE_ONLY = _cert(
    "La finca antes descrita APARECE GRAVADA CON LAS SIGUIENTES CARGAS. A).- Con una HIPOTECA a favor de la "
    "entidad Banco Ejemplo SA, en garantía de un préstamo de SETENTA Y CUATRO MIL DOSCIENTOS SESENTA EUROS de "
    "principal, en la que se tasó a efectos de subasta en CIENTO CUARENTA Y DOS MIL QUINIENTOS EUROS. SE HACE "
    "CONSTAR expresamente que la HIPOTECA objeto de la inscripción 9ª a favor de la entidad actora se encuentra "
    "SUBSISTENTE Y SIN CANCELAR.")

COURT_REQUEST = (" Remita certificación de los derechos de cualquier naturaleza que existan sobre el bien, en "
                 "especial, relación completa de las cargas inscritas que lo graven o, en su caso, que se halla "
                 "libre de cargas. " + FILLER)


def test_amounts_in_words_and_figures():
    assert rc.words_to_number("seis mil novecientos treinta y siete".split()) == 6937
    assert rc.words_to_number("un millon doscientos mil".split()) == 1_200_000
    found, unread = rc.amounts("de 955,84 euros de principal; intereses y costas de 307,84 euros")
    assert found == [955.84, 307.84] and not unread
    found, unread = rc.amounts("trece mil trescientos sesenta y seis euros con ochenta centimos")
    assert found == [pytest.approx(13366.80)]


def test_a_garbled_amount_is_unread_not_small():
    found, unread = rc.amounts("seis mil euros de principal, mas dos mil ochena y dos euros para costas")
    assert found == [6000.0] and unread


def test_a_valuation_or_a_tax_paid_is_not_a_debt():
    found, _ = rc.amounts("se tasa la finca hipotecada, a efectos de ejecucion, en la cantidad de "
                          "quinientos sesenta y dos mil euros. liberada por la cantidad de 45,10 euros")
    assert found == []


def test_an_earlier_seizure_with_a_garbled_amount_leaves_the_charges_unknown():
    cert = rc.read_certificate(EARLIER_SEIZURE_IN_WORDS, {"1500/2020"})
    others = [e for e in cert["entries"] if not e["own"]]
    assert cert["status"] == "read" and any(e["own"] for e in cert["entries"])
    assert others and all(e["amount"] is None for e in others)
    state, _, words = rc.summary(cert)
    assert state == "unknown" and "seizure (amount not readable)" in words


def test_another_creditors_case_near_the_top_is_not_this_sales_case():
    head = " Anotación de embargo seguida en el procedimiento número 700/2015. " + FILLER
    assert rc.procedures(rc.normalize(_cert("x", head))) == {"1500/2020"}


def test_field_list_with_seizures_ahead_of_the_creditor():
    cert = rc.read_certificate(FIELD_LIST, {"1500/2020"})
    others = sorted(e["amount"] for e in cert["entries"] if not e["own"])
    assert others == [pytest.approx(5009.24), pytest.approx(25843.83)]     # the tax paid is left out
    state, amount, words = rc.summary(cert)
    assert state == "stated" and amount == pytest.approx(30853.07)
    assert "counted as staying with the property" in words


def test_the_creditors_own_mortgage_alone_passes_nothing():
    cert = rc.read_certificate(OWN_MORTGAGE_ONLY)
    assert [e["own"] for e in cert["entries"]] == [True]
    assert rc.summary(cert)[0] == "none"


def test_the_court_request_saying_free_of_charges_is_not_the_answer():
    ocr_slip = _cert("TERCERO: aparece gravada con: hipoteea a favor de Banco Ejemplo, 100.000 euros.",
                     head=COURT_REQUEST)
    cert = rc.read_certificate(ocr_slip)
    assert cert["status"] == "unclear" and rc.summary(cert)[0] == "unknown"
    free = rc.read_certificate(_cert("CARGAS: libre de cargas.", head=COURT_REQUEST))
    assert free["free"] and rc.summary(free)[0] == "none"


def test_scans_garble_and_bundles_are_left_to_the_person():
    assert rc.read_certificate("x" * 50)["status"] == "unreadable"
    bundle = _cert("CARGAS: " + " ".join(f"finca registral {n}.000 hipoteca a favor de Banco." for n in range(10, 20)))
    assert rc.read_certificate(bundle) == {"status": "unclear", "why": "it covers several properties",
                                           "cases": ["1500/2020"]}
    assert rc.summary({"status": "scanned"})[0] == "unknown"


def test_document_titles():
    for title, kind in {"CERTIFICACION DE CARGAS": "charges", "certificacion cargas": "charges",
                        "CERTIFICADO DE CARGAS ART. 688 LEC": "charges", "certificacion de cargass": "charges",
                        "Certificación de dominio y cargas.": "charges", "NOTA SIMPLE ACTUALIZADA": "charges",
                        "0 - GUIA para comprender el contenido de la certificación de cargas": "other",
                        "EDICTO SUBASTA": "notice", "INFORME VALORACIÓN DEL PERITO": "valuation",
                        "Decreto despacho ejecucion": "order"}.items():
        assert rc.classify(title) == kind, title


def test_the_case_number_inside_the_boe_account():
    assert rc.case_from_account("4035 0000 05 1942 16") == "1942/2016"
    assert rc.case_from_account("2922 0000 06 0862 16") == "862/2016"
    assert rc.case_from_account("ES12 3456") is None


BOE_GENERAL_WITH_DOCS = """<table><tr><th>Tipo de subasta</th><td>JUDICIAL EN VÍA DE APREMIO</td></tr>
  <tr><th>Cuenta expediente</th><td>4035 0000 05 1500 20</td></tr>
  <tr><th>Cantidad reclamada</th><td>8.000,00 €</td></tr>
  <tr><th>Valor subasta</th><td>150.000,00 €</td></tr></table>
  <div class="caja gris"><h5 class="legend">Documentos</h5><ul class="enlaces">
  <li><a href="verDocumento.php?idSub=SUB-JA-2026-9&amp;idDoc=1-aa">EDICTO SUBASTA</a></li>
  <li><a href="verDocumento.php?idSub=SUB-JA-2026-9&amp;idDoc=2-bb">CERTIFICACION DE CARGAS</a></li></ul></div>
  <div class="caja gris"><h5 class="legend">Imágenes y fotografías</h5><ul class="enlaces">
  <li><a href="verDocumento.php?idSub=SUB-JA-2026-9&amp;idDoc=3-cc"><img alt="fachada"/></a></li></ul></div>"""


def test_the_boe_document_list():
    from sources.es import spain_details
    _, extra = spain_details({"general": BOE_GENERAL_WITH_DOCS})
    assert [(d["kind"], d["path"]) for d in extra["boe_docs"]] == [
        ("notice", "verDocumento.php?idSub=SUB-JA-2026-9&idDoc=1-aa"),
        ("charges", "verDocumento.php?idSub=SUB-JA-2026-9&idDoc=2-bb")]


def _small_claim(add, ext="SUB-JA-2026-9", docs=True):
    raw = {"tipo_subasta": "JUDICIAL EN VÍA DE APREMIO", "claim_eur": 8000, "expediente": "4035 0000 05 1500 20",
           "detail_v": 3, "cargas": "ver certificado de cargas"}
    if docs:
        raw["boe_docs"] = [{"title": "CERTIFICACION DE CARGAS", "kind": "charges",
                            "path": f"verDocumento.php?idSub={ext}&idDoc=2-bb"}]
    return add("spain", ext, "ES", title="Vivienda en Villanueva", tipo="inmueble", price=150000,
               date_end="2099-12-01", raw_json=json.dumps(raw))


def test_a_small_claim_sales_certificate_is_read_once(db, add, fake_http, monkeypatch):
    from conftest import FakeResponse
    from sources.es import fetch_spain_certificates
    _small_claim(add)
    add("spain", "SUB-JA-2026-10", "ES", title="Vivienda", price=150000, date_end="2099-12-01",
        raw_json=json.dumps({"tipo_subasta": "JUDICIAL EN VÍA DE APREMIO", "claim_eur": 140000,
                             "boe_docs": [{"kind": "charges", "path": "verDocumento.php?x=1"}]}))
    monkeypatch.setattr(rc, "pdf_text", lambda content: FIELD_LIST)
    session = fake_http(lambda m, url, kw: FakeResponse("%PDF"))
    assert fetch_spain_certificates(db, session) == 1
    assert [c[1] for c in session.calls] == [
        "https://subastas.boe.es/verDocumento.php?idSub=SUB-JA-2026-9&idDoc=2-bb"]   # not the large claim
    item = dict(db.execute("SELECT * FROM listings WHERE id = 'spain:SUB-JA-2026-9'").fetchone())
    floor = legal_floor(item)
    assert floor["charges_state"] == "stated" and floor["charges"] == pytest.approx(30853.07)
    assert any("registry certificate lists" in c for c in floor["caveats"])
    share, _ = rank_share(item, 150000)
    assert share == pytest.approx((8000 + 30853.07) / 150000)
    assert fetch_spain_certificates(db, session) == 0 and len(session.calls) == 1


def test_a_scan_or_a_missing_certificate_is_said_plainly(db, add, fake_http, monkeypatch):
    from conftest import FakeResponse
    from listing_info import facts, related
    from sources.es import fetch_spain_certificates
    _small_claim(add)
    _small_claim(add, "SUB-JA-2026-11", docs=False)
    monkeypatch.setattr(rc, "pdf_text", lambda content: "")
    fetch_spain_certificates(db, fake_http(lambda m, url, kw: FakeResponse("%PDF")))
    scanned = dict(db.execute("SELECT * FROM listings WHERE id = 'spain:SUB-JA-2026-9'").fetchone())
    shown = {f["label"]: f["value"] for f in facts(scanned)}
    assert "a scan the scanner cannot read" in shown["Registry certificate"]
    assert any(r["label"].startswith("Registry charges certificate (BOE)") for r in related(db, scanned))
    missing = dict(db.execute("SELECT * FROM listings WHERE id = 'spain:SUB-JA-2026-11'").fetchone())
    assert "no registry certificate is published" in {f["label"]: f["value"] for f in facts(missing)}[
        "Registry certificate"]
