"""
Envia 5 pedidos de informação aos tribunais sobre vendas em processos executivos.
Usa as credenciais SMTP de config.json (secção "notifications").

One-off script, kept for reference: the app's Offers page now prepares letters.

Uso:
    python scripts/send_tribunais.py            -> mostra os emails (não envia)
    python scripts/send_tribunais.py --send     -> envia de facto
"""

import os
import smtplib
import sys
import time
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402

FROM_EMAIL = "adaixojoao@gmail.com"
SENDER_NAME = "João Castro Adaixo"

LISBOA = "lisboa.juizoexecucao@tribunais.org.pt"
ENTRONCAMENTO = "entroncamento.juizoexecucao@tribunais.org.pt"
MOURA = "moura.juizogenerico@tribunais.org.pt"

PROCESSOS = [
    ("24802/02.4TVLSB", "Juízo de Execução de Lisboa – Juiz 4", LISBOA,
     "terreno com a área de 732 m², sito nas Fontainhas"),
    ("298/03.2TBRMR", "Juízo de Execução do Entroncamento – Juiz 1", ENTRONCAMENTO,
     "prédio misto sito em Rio Maior"),
    ("1897/05.3TBSTR", "Juízo de Execução do Entroncamento – Juiz 3", ENTRONCAMENTO,
     "moradia sita na Póvoa de Santarém"),
    ("4413/09.4TVLSB", "Juízo de Execução de Lisboa – Juiz 3", LISBOA,
     "moradia de 5 assoalhadas sita na Guarda"),
    ("165/10.3TBMRA", "Juízo de Competência Genérica de Moura", MOURA,
     "moradia sita em Safara, concelho de Moura"),
]

CORPO = """Exmos. Senhores,

Eu, {nome}, venho por este meio, na qualidade de potencial interessado na aquisição, solicitar a V. Exas. informações relativas à venda do imóvel penhorado no âmbito do processo executivo n.º {proc}, que corre termos no {tribunal}, referente a {imovel}.

Agradecia que me informassem:

1. Se a venda do referido imóvel se encontra ainda ativa;
2. Qual o prazo para a apresentação de propostas;
3. Se é exigida a prestação de caução e, em caso afirmativo, qual o respetivo montante;
4. Os contactos (nome, telefone e endereço eletrónico) do Agente de Execução responsável pelo processo.

Agradeço desde já a atenção dispensada, ficando a aguardar a V. prezada resposta.

Com os melhores cumprimentos,

{nome}
Telefone: +31 6 58821338
Email: {email}
"""


def build(proc, tribunal, to, imovel):
    body = CORPO.format(nome=SENDER_NAME, proc=proc, tribunal=tribunal,
                        imovel=imovel, email=FROM_EMAIL)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Pedido de informação – Processo n.º {proc} – Venda de imóvel"
    msg["From"] = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    return msg


def main():
    send = "--send" in sys.argv
    msgs = [build(*p) for p in PROCESSOS]

    if not send:
        for m in msgs:
            print("=" * 70)
            print("Para:   ", m["To"])
            print("Assunto:", m["Subject"])
            print(m.get_payload(decode=True).decode("utf-8"))
        print("\nModo de pré-visualização. Para enviar: python scripts/send_tribunais.py --send")
        return

    cfg = load_config()["notifications"]
    if not cfg.get("smtp_user") or not cfg.get("smtp_password"):
        sys.exit("Erro: preencha smtp_user e smtp_password em config.json (secção notifications).")

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
        server.starttls()
        server.login(cfg["smtp_user"], cfg["smtp_password"])
        for m in msgs:
            server.sendmail(FROM_EMAIL, [m["To"]], m.as_string())
            print(f"Enviado: {m['Subject']} -> {m['To']}")
            time.sleep(2)
    print("\nConcluído: 5 emails enviados.")


if __name__ == "__main__":
    main()
