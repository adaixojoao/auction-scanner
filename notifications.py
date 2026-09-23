"""
Email notification system for high-scoring auction listings.
Sends HTML emails via SMTP when new interesting listings appear.
"""

import logging
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

LOG = logging.getLogger("auction-scanner")

COUNTRY_NAMES = {
    "PT": "Portugal", "ES": "Spain", "FR": "France",
    "IT": "Italy", "HR": "Croatia", "NL": "Netherlands",
    "DE": "Germany", "GR": "Greece", "BE": "Belgium",
    "RO": "Romania", "PL": "Poland", "CY": "Cyprus",
}

FORCED_SOURCES = {"financas", "zvg", "anaf", "aeat", "pvp_giustizia", "poland", "greece", "cyprus", "citius"}


def send_alerts(db: sqlite3.Connection, notify_cfg: dict, score_fn, max_price: float = 50000):
    if not notify_cfg.get("enabled"):
        return

    if not notify_cfg.get("to_emails"):
        LOG.warning("Notifications enabled but no to_emails configured")
        return

    min_score = notify_cfg.get("min_score", 60)
    send_on = notify_cfg.get("send_on", "new")

    now = datetime.now(timezone.utc)
    where = "is_new = 1" if send_on == "new" else "1=1"

    rows = db.execute(f"""
        SELECT * FROM listings
        WHERE (price <= ? OR price IS NULL)
          AND (current_bid <= ? OR current_bid IS NULL OR current_bid = 0)
          AND (date_end IS NULL OR date_end > ?)
          AND ({where})
        ORDER BY price ASC
    """, (max_price * 2, max_price, now.isoformat())).fetchall()

    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    items = [dict(zip(cols, r)) for r in rows]

    alerts = []
    for item in items:
        score, reasons = score_fn(item)
        if score >= min_score:
            alerts.append((item, score, reasons))

    alerts.sort(key=lambda x: -x[1])

    if not alerts:
        LOG.info(f"No listings scoring >= {min_score} to notify about")
        return

    LOG.info(f"Sending alert email: {len(alerts)} listings scoring >= {min_score}")

    html = _build_email_html(alerts, max_price, now)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Auction Scanner: {len(alerts)} listings scoring {min_score}+"
    msg["From"] = notify_cfg["from_email"] or notify_cfg["smtp_user"]
    msg["To"] = ", ".join(notify_cfg["to_emails"])

    plain = f"{len(alerts)} auction listings scored {min_score}+ — check your email client for the full report."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(notify_cfg["smtp_host"], notify_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(notify_cfg["smtp_user"], notify_cfg["smtp_password"])
            server.sendmail(
                msg["From"],
                notify_cfg["to_emails"],
                msg.as_string(),
            )
        LOG.info(f"Alert email sent to {notify_cfg['to_emails']}")
    except Exception as e:
        LOG.error(f"Failed to send alert email: {e}")


def _build_email_html(alerts: list, max_price: float, now: datetime) -> str:
    rows_html = []
    for item, score, reasons in alerts[:50]:
        price_str = f"&euro;{item['price']:,.0f}" if item.get("price") else "?"
        bid_str = f"&euro;{item['current_bid']:,.0f}" if item.get("current_bid") else "-"
        loc = ", ".join(filter(None, [item.get("concelho", ""), item.get("district", "")]))
        ends = item["date_end"][:10] if item.get("date_end") else "-"
        title = (item.get("title") or "?")[:60]
        url = item.get("url") or "#"
        country = COUNTRY_NAMES.get(item.get("country", "PT"), "?")
        flags = ", ".join(reasons)[:50]
        color = "#2d8a4e" if score >= 75 else "#c9971a" if score >= 60 else "#888"

        rows_html.append(f"""
        <tr>
            <td style="font-weight:bold;color:{color}">{score:.0f}</td>
            <td>{country}</td>
            <td><a href="{url}">{title}</a></td>
            <td>{price_str}</td>
            <td>{bid_str}</td>
            <td>{loc}</td>
            <td>{ends}</td>
            <td style="font-size:11px;color:#666">{flags}</td>
        </tr>""")

    return f"""
    <html>
    <body style="font-family:Calibri,Arial,sans-serif;max-width:900px;margin:0 auto;padding:20px">
        <h2 style="color:#1a365d">Auction Scanner Alert</h2>
        <p style="color:#555">
            {now.strftime('%d %b %Y %H:%M UTC')} &mdash;
            {len(alerts)} listings scoring 60+ &mdash;
            Budget: &euro;{max_price:,.0f}
        </p>
        <table style="border-collapse:collapse;width:100%;font-size:13px" border="1" cellpadding="6">
            <tr style="background:#1a365d;color:white">
                <th>Score</th><th>Country</th><th>Title</th>
                <th>Price</th><th>Bid</th><th>Location</th>
                <th>Ends</th><th>Flags</th>
            </tr>
            {''.join(rows_html)}
        </table>
        <p style="color:#999;font-size:11px;margin-top:20px">
            Sent by EU Auction Scanner. Configure in config.json.
        </p>
    </body>
    </html>"""


def send_weekly_digest(db: sqlite3.Connection, notify_cfg: dict, score_fn, max_price: float = 50000, top_n: int = 15):
    """Send weekly top-N digest email focused on forced/mandatory sales.
    These are the lottery picks — properties that legally must sell."""
    if not notify_cfg.get("to_emails"):
        LOG.warning("Weekly digest: no to_emails configured")
        return

    now = datetime.now(timezone.utc)
    rows = db.execute("""
        SELECT * FROM listings
        WHERE (price <= ? OR price IS NULL)
          AND (current_bid <= ? OR current_bid IS NULL OR current_bid = 0)
          AND (date_end IS NULL OR date_end > ?)
        ORDER BY price ASC
    """, (max_price * 2, max_price, now.isoformat())).fetchall()

    cols = [d[0] for d in db.execute("SELECT * FROM listings LIMIT 0").description]
    items = [dict(zip(cols, r)) for r in rows]

    scored = []
    for item in items:
        score, reasons = score_fn(item)
        scored.append((item, score, reasons))

    scored.sort(key=lambda x: -x[1])
    top = scored[:top_n]

    if not top:
        LOG.info("Weekly digest: no listings to report")
        return

    forced = [(it, sc, rs) for it, sc, rs in top if it.get("source") in FORCED_SOURCES]
    regular = [(it, sc, rs) for it, sc, rs in top if it.get("source") not in FORCED_SOURCES]

    html = _build_digest_html(forced, regular, top, max_price, now, top_n)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Weekly Auction Digest: Top {len(top)} Picks ({now.strftime('%d %b %Y')})"
    msg["From"] = notify_cfg.get("from_email") or notify_cfg["smtp_user"]
    msg["To"] = ", ".join(notify_cfg["to_emails"])

    plain = f"Top {len(top)} auction picks this week — {len(forced)} forced sales, {len(regular)} regular. Check your email for the full report."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(notify_cfg["smtp_host"], notify_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(notify_cfg["smtp_user"], notify_cfg["smtp_password"])
            server.sendmail(msg["From"], notify_cfg["to_emails"], msg.as_string())
        LOG.info(f"Weekly digest sent to {notify_cfg['to_emails']}")
    except Exception as e:
        LOG.error(f"Failed to send weekly digest: {e}")


def _build_digest_html(forced, regular, top, max_price, now, top_n):
    def _row(item, score, reasons, highlight=False):
        bg = "background:#1a2e1a;" if highlight else ""
        price_str = f"&euro;{item['price']:,.0f}" if item.get("price") else "?"
        bid_str = f"&euro;{item['current_bid']:,.0f}" if item.get("current_bid") else "-"
        min_str = f"&euro;{item['min_price']:,.0f}" if item.get("min_price") else "no min"
        loc = ", ".join(filter(None, [item.get("concelho", ""), item.get("district", "")]))
        ends = item["date_end"][:10] if item.get("date_end") else "-"
        title = (item.get("title") or "?")[:80]
        url = item.get("url") or "#"
        country = COUNTRY_NAMES.get(item.get("country", "PT"), "?")
        flags = ", ".join(reasons)[:80]
        color = "#2d8a4e" if score >= 75 else "#c9971a" if score >= 60 else "#888"
        source = item.get("source", "?")
        forced_tag = ' <span style="background:#c9971a;color:#000;padding:1px 4px;border-radius:3px;font-size:10px">FORCED</span>' if source in FORCED_SOURCES else ""

        return f"""<tr style="{bg}">
            <td style="font-weight:bold;color:{color};font-size:18px;text-align:center">{score:.0f}</td>
            <td>{country}<br><span style="font-size:10px;color:#888">{source}</span></td>
            <td><a href="{url}" style="color:#3b82f6">{title}</a>{forced_tag}</td>
            <td style="white-space:nowrap">{price_str}<br><span style="font-size:10px;color:#888">min: {min_str}</span></td>
            <td>{bid_str}</td>
            <td>{loc}</td>
            <td>{ends}</td>
            <td style="font-size:11px;color:#666">{flags}</td>
        </tr>"""

    rows = []
    for item, score, reasons in top:
        is_forced = item.get("source") in FORCED_SOURCES
        rows.append(_row(item, score, reasons, highlight=is_forced))

    return f"""
    <html>
    <body style="font-family:Calibri,Arial,sans-serif;max-width:1000px;margin:0 auto;padding:20px;background:#0f172a;color:#e2e8f0">
        <h1 style="color:white;margin-bottom:4px">Weekly Auction Digest</h1>
        <p style="color:#94a3b8;margin-bottom:20px">
            {now.strftime('%d %b %Y')} &mdash;
            Top {len(top)} picks &mdash;
            Budget: &euro;{max_price:,.0f}
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
            Highlighted rows are <strong>forced/mandatory sales</strong> — these legally must sell.
            You can often bid as low as you want. It's a lottery with potentially huge upside.
        </p>

        <table style="border-collapse:collapse;width:100%;font-size:13px;margin-top:16px;background:#1e293b" border="1" cellpadding="6" bordercolor="#334155">
            <tr style="background:#0f172a;color:#94a3b8">
                <th>Score</th><th>Country</th><th>Title</th>
                <th>Price / Min</th><th>Bid</th><th>Location</th>
                <th>Ends</th><th>Flags</th>
            </tr>
            {''.join(rows)}
        </table>
        <p style="color:#64748b;font-size:11px;margin-top:20px">
            EU Auction Scanner &mdash; Weekly Digest. Green highlighted rows = forced/tax sales.
        </p>
    </body>
    </html>"""
