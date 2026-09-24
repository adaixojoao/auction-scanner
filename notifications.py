"""
Email notifications for high-scoring listings (SMTP).

"New" means "never e-mailed before" (alert_log), not "inserted in this run", so
a failed send is retried next run and a listing is never mailed twice.
"""
from __future__ import annotations

import html
import logging
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from common import COUNTRY_NAMES, utcnow
from db import load_listings, mark_alerted, not_yet_alerted
from scoring import FORCED_SOURCES

LOG = logging.getLogger("auction-scanner")

CHANNEL = "email"


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def _in_budget(item, max_price) -> bool:
    return (item.get("price") or 0) <= max_price and (item.get("current_bid") or 0) <= max_price


def _send_email(notify_cfg: dict, subject: str, html_body: str, plain: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = notify_cfg.get("from_email") or notify_cfg["smtp_user"]
    msg["To"] = ", ".join(notify_cfg["to_emails"])
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP(notify_cfg["smtp_host"], notify_cfg["smtp_port"], timeout=30) as server:
            server.starttls()
            server.login(notify_cfg["smtp_user"], notify_cfg["smtp_password"])
            server.sendmail(msg["From"], notify_cfg["to_emails"], msg.as_string())
        LOG.info(f"Email sent to {notify_cfg['to_emails']}: {subject}")
        return True
    except Exception as e:
        LOG.error(f"Failed to send email: {e}")
        return False


_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def send_letter(smtp_cfg: dict, to: str, subject: str, text: str, pdf: bytes, filename: str,
                *, reply_to: str = "") -> str | None:
    """E-mail one letter with its PDF attached, through the SMTP account in
    Settings → E-mail. A copy goes to that account. Returns None when sent,
    otherwise the reason in plain words."""
    if not all(smtp_cfg.get(k) for k in ("smtp_host", "smtp_user", "smtp_password")):
        return "E-mail is not set up: fill in the SMTP server, user and password under Settings → E-mail."
    if not _ADDRESS.fullmatch(to or ""):
        return f"Not an e-mail address: {to!r}" if to else "Who should receive it? Fill in the address."
    sender = smtp_cfg.get("from_email") or smtp_cfg["smtp_user"]
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        if reply_to and reply_to != sender:
            msg["Reply-To"] = reply_to
        msg.set_content(text)
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
        with smtplib.SMTP(smtp_cfg["smtp_host"], int(smtp_cfg.get("smtp_port") or 587), timeout=30) as server:
            server.starttls()
            server.login(smtp_cfg["smtp_user"], smtp_cfg["smtp_password"])
            server.send_message(msg, to_addrs=[to, sender])
    except (smtplib.SMTPException, OSError, ValueError) as e:
        LOG.error(f"Letter e-mail to {to} failed: {e}")
        return f"The e-mail could not be sent: {e}"
    LOG.info(f"Letter e-mailed to {to}: {subject}")
    return None


def send_alerts(db, notify_cfg: dict, score_fn=None, max_price: float = 50000,
                filters: dict | None = None):
    """E-mail listings scoring >= min_score. `score_fn` is ignored (kept for old callers)."""
    if not notify_cfg.get("enabled"):
        return
    if not notify_cfg.get("to_emails"):
        LOG.warning("Notifications enabled but no to_emails configured")
        return

    min_score = notify_cfg.get("min_score", 60)
    now = utcnow()
    alerts = [it for it in load_listings(db, filters=filters, now=now)
              if it["score"] >= min_score and _in_budget(it, max_price)]
    if notify_cfg.get("send_on", "new") == "new":
        fresh = not_yet_alerted(db, CHANNEL, [it["id"] for it in alerts])
        alerts = [it for it in alerts if it["id"] in fresh]
    alerts.sort(key=lambda it: -it.get("rank", it["score"]))

    if not alerts:
        LOG.info(f"No listings scoring >= {min_score} to e-mail about")
        return

    ok = _send_email(
        notify_cfg,
        f"Auction Scanner: {len(alerts)} listings scoring {min_score}+",
        _build_email_html(alerts, max_price, now, min_score),
        "\n".join(f"[{it['score']:.0f}] {it['title']} — {it.get('url') or ''}" for it in alerts[:50]),
    )
    if ok:
        mark_alerted(db, CHANNEL, [it["id"] for it in alerts])


def _build_email_html(alerts: list, max_price: float, now: datetime, min_score: float = 60) -> str:
    rows_html = []
    for item in alerts[:50]:
        score = item["score"]
        price_str = f"&euro;{item['price']:,.0f}" if item.get("price") else "?"
        bid_str = f"&euro;{item['current_bid']:,.0f}" if item.get("current_bid") else "-"
        loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
        color = "#2d8a4e" if score >= 75 else "#c9971a" if score >= 60 else "#888"
        rows_html.append(f"""
        <tr>
            <td style="font-weight:bold;color:{color}">{score:.0f}</td>
            <td>{_esc(COUNTRY_NAMES.get(item.get("country") or "PT", "?"))}</td>
            <td><a href="{_esc(item.get('url') or '#')}">{_esc((item.get('title') or '?')[:60])}</a></td>
            <td>{price_str}</td>
            <td>{bid_str}</td>
            <td>{_esc(loc)}</td>
            <td>{_esc((item.get('date_end') or '-')[:10])}</td>
            <td style="font-size:11px;color:#666">{_esc(', '.join(item['reasons'])[:80])}</td>
        </tr>""")

    return f"""
    <html>
    <body style="font-family:Calibri,Arial,sans-serif;max-width:900px;margin:0 auto;padding:20px">
        <h2 style="color:#1a365d">Auction Scanner Alert</h2>
        <p style="color:#555">
            {now:%d %b %Y %H:%M} UTC &mdash;
            {len(alerts)} listings scoring {min_score:.0f}+ &mdash;
            Budget: &euro;{max_price:,.0f}
        </p>
        <table style="border-collapse:collapse;width:100%;font-size:13px" border="1" cellpadding="6">
            <tr style="background:#1a365d;color:white">
                <th>Score</th><th>Country</th><th>Title</th>
                <th>Price</th><th>Bid</th><th>Location</th>
                <th>Ends</th><th>Why</th>
            </tr>
            {''.join(rows_html)}
        </table>
        <p style="color:#999;font-size:11px;margin-top:20px">
            Sent by EU Auction Scanner. Configure in config.json.
        </p>
    </body>
    </html>"""


def send_weekly_digest(db, notify_cfg: dict, score_fn=None, max_price: float = 50000,
                       top_n: int = 15, filters: dict | None = None):
    """Weekly top-N digest, highlighting forced/mandatory sales."""
    if not notify_cfg.get("to_emails"):
        LOG.warning("Weekly digest: no to_emails configured")
        return

    now = utcnow()
    scored = [it for it in load_listings(db, filters=filters, now=now) if _in_budget(it, max_price)]
    scored.sort(key=lambda it: -it.get("rank", it["score"]))
    top = scored[:top_n]
    if not top:
        LOG.info("Weekly digest: no listings to report")
        return

    forced = [it for it in top if it.get("source") in FORCED_SOURCES]
    regular = [it for it in top if it.get("source") not in FORCED_SOURCES]
    _send_email(
        notify_cfg,
        f"Weekly Auction Digest: Top {len(top)} Picks ({now:%d %b %Y})",
        _build_digest_html(forced, regular, top, max_price, now),
        f"Top {len(top)} auction picks this week — {len(forced)} forced sales, {len(regular)} regular.",
    )


def _build_digest_html(forced, regular, top, max_price, now):
    def _row(item, highlight=False):
        score = item["score"]
        bg = "background:#1a2e1a;" if highlight else ""
        price_str = f"&euro;{item['price']:,.0f}" if item.get("price") else "?"
        bid_str = f"&euro;{item['current_bid']:,.0f}" if item.get("current_bid") else "-"
        min_str = f"&euro;{item['min_price']:,.0f}" if item.get("min_price") else "no min"
        loc = ", ".join(filter(None, [item.get("concelho"), item.get("district")]))
        color = "#2d8a4e" if score >= 75 else "#c9971a" if score >= 60 else "#888"
        source = item.get("source", "?")
        forced_tag = (' <span style="background:#c9971a;color:#000;padding:1px 4px;border-radius:3px;'
                      'font-size:10px">FORCED</span>') if source in FORCED_SOURCES else ""
        return f"""<tr style="{bg}">
            <td style="font-weight:bold;color:{color};font-size:18px;text-align:center">{score:.0f}</td>
            <td>{_esc(COUNTRY_NAMES.get(item.get("country") or "PT", "?"))}<br><span style="font-size:10px;color:#888">{_esc(source)}</span></td>
            <td><a href="{_esc(item.get('url') or '#')}" style="color:#3b82f6">{_esc((item.get('title') or '?')[:80])}</a>{forced_tag}</td>
            <td style="white-space:nowrap">{price_str}<br><span style="font-size:10px;color:#888">min: {min_str}</span></td>
            <td>{bid_str}</td>
            <td>{_esc(loc)}</td>
            <td>{_esc((item.get('date_end') or '-')[:10])}</td>
            <td style="font-size:11px;color:#666">{_esc(', '.join(item['reasons'])[:80])}</td>
        </tr>"""

    rows = "".join(_row(it, highlight=it.get("source") in FORCED_SOURCES) for it in top)
    return f"""
    <html>
    <body style="font-family:Calibri,Arial,sans-serif;max-width:1000px;margin:0 auto;padding:20px;background:#0f172a;color:#e2e8f0">
        <h1 style="color:white;margin-bottom:4px">Weekly Auction Digest</h1>
        <p style="color:#94a3b8;margin-bottom:20px">
            {now:%d %b %Y} &mdash; Top {len(top)} picks &mdash; Budget: &euro;{max_price:,.0f}
        </p>
        <div style="display:flex;gap:16px;margin-bottom:24px">
            <div style="background:#1e293b;padding:16px 24px;border-radius:8px;border:1px solid #334155">
                <div style="color:#94a3b8;font-size:12px">FORCED SALES</div>
                <div style="color:#22c55e;font-size:28px;font-weight:700">{len(forced)}</div>
            </div>
            <div style="background:#1e293b;padding:16px 24px;border-radius:8px;border:1px solid #334155">
                <div style="color:#94a3b8;font-size:12px">REGULAR AUCTIONS</div>
                <div style="color:#3b82f6;font-size:28px;font-weight:700">{len(regular)}</div>
            </div>
        </div>
        <p style="color:#eab308;font-size:14px;padding:12px;background:#1e293b;border-left:4px solid #eab308;border-radius:4px">
            Highlighted rows are <strong>forced/mandatory sales</strong> (court, tax authority).
            Check each sale's minimum-offer rules before bidding.
        </p>
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin-top:16px;background:#1e293b" border="1" cellpadding="6" bordercolor="#334155">
            <tr style="background:#0f172a;color:#94a3b8">
                <th>Score</th><th>Country</th><th>Title</th>
                <th>Price / Min</th><th>Bid</th><th>Location</th>
                <th>Ends</th><th>Why</th>
            </tr>
            {rows}
        </table>
        <p style="color:#64748b;font-size:11px;margin-top:20px">
            EU Auction Scanner &mdash; Weekly Digest. Green highlighted rows = forced/tax sales.
        </p>
    </body>
    </html>"""
