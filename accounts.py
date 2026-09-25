"""accounts.py — signing in to sites that show their best sales only to account holders.

The owner's own accounts (Portal das Finanças, and any other site that gets a
login connector here). How it works:

- Settings → Accounts takes a username and a password per site. The password is
  encrypted with Windows DPAPI before it is written to config.json: only this
  Windows user on this PC can read it back. It is never sent to the browser,
  never logged, and config.json is not in git.
- A scraper asks `account_session(db, cfg, site)` for a signed-in session. If the
  site has no account it gets None and skips quietly.
- Signing in is tried once per scan. A code asked by SMS, a wrong password, a
  CAPTCHA or a page that changed stop that site for LOGIN_PAUSE_HOURS and send
  one Telegram message: retrying could lock the owner out, and bot checks are
  never worked around.

A connector is a function (session, username, password) -> None that leaves the
session signed in, or raises LoginNeedsCode / LoginFailed. Add one to LOGINS.
"""
from __future__ import annotations

import base64
import re
import sys
from datetime import timedelta
from html import unescape
from urllib.parse import urljoin, urlparse

from common import LOG, make_session, parse_dt, utcnow, utcnow_iso

LOGIN_PAUSE_HOURS = 24


class LoginNeedsCode(RuntimeError):
    """The site asked for a second factor (an SMS code): the owner must sign in by hand."""


class LoginFailed(RuntimeError):
    """Wrong username or password, a CAPTCHA, or a login page the connector does not know."""


# ─── The password, encrypted for this Windows user ──────────────────

def protect(text: str) -> str:
    """Encrypt with Windows DPAPI (CryptProtectData) → base64."""
    return base64.b64encode(_dpapi(text.encode("utf-8"), encrypt=True)).decode("ascii")


def unprotect(blob: str) -> str:
    return _dpapi(base64.b64decode(blob), encrypt=False).decode("utf-8")


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("stored passwords need Windows (DPAPI)")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), buf), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if encrypt:
        ok = fn(ctypes.byref(src), "auction-scanner", None, None, None, 0, ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise RuntimeError("Windows could not " + ("encrypt" if encrypt else "decrypt") + " the password")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ─── Stored accounts ────────────────────────────────────────────────

def credentials(cfg: dict, site: str) -> tuple[str, str] | None:
    acc = ((cfg.get("accounts") or {}).get(site) or {})
    if not acc.get("username") or not acc.get("secret"):
        return None
    try:
        return acc["username"], unprotect(acc["secret"])
    except Exception as e:  # noqa: BLE001: another PC or Windows user cannot read it
        LOG.warning(f"Account {site}: the stored password cannot be read here ({type(e).__name__})")
        return None


def account_changes(site: str, username: str, password: str | None) -> dict:
    """The config change that stores an account; a blank password keeps the old one."""
    change = {"username": username.strip()}
    if password:
        change["secret"] = protect(password)
    return {"accounts": {site: change}}


def public_view(cfg: dict) -> dict:
    """What the Settings page may see: the username and whether a password is stored."""
    return {site: {"name": info["name"], "username": ((cfg.get("accounts") or {}).get(site) or {}).get("username", ""),
                   "has_password": bool(((cfg.get("accounts") or {}).get(site) or {}).get("secret")),
                   "note": info["note"]}
            for site, info in LOGINS.items()}


# ─── Signing in ─────────────────────────────────────────────────────

def _pause_key(site: str) -> str:
    return f"login_paused:{site}"


def account_session(db, cfg: dict, site: str, *, session=None):
    """A session signed in to `site`, or None (no account, or paused after a
    failure). A failure pauses the site and tells the owner once."""
    from db import get_kv, set_kv
    creds = credentials(cfg, site)
    if not creds:
        return None
    paused = parse_dt(get_kv(db, _pause_key(site)) or "")
    if paused and utcnow() - paused < timedelta(hours=LOGIN_PAUSE_HOURS):
        LOG.info(f"Account {site}: paused since {paused:%Y-%m-%d %H:%M} after a failed sign-in")
        return None
    session = session or make_session(timeout=30)
    try:
        LOGINS[site]["login"](session, *creds)
    except (LoginNeedsCode, LoginFailed) as e:
        set_kv(db, _pause_key(site), utcnow_iso())
        _tell_owner(cfg, site, e)
        LOG.warning(f"Account {site}: {e}")
        return None
    set_kv(db, _pause_key(site), "")
    return session


def _tell_owner(cfg: dict, site: str, error: Exception):
    try:
        from telegram_alert import _tg, send_telegram
        tg = _tg(cfg)
        if tg:
            what = ("asked for a code by SMS — sign in by hand once, or turn the SMS step off"
                    if isinstance(error, LoginNeedsCode) else str(error))
            send_telegram(tg["token"], tg["chat_id"],
                          f"🔐 <b>{LOGINS[site]['name']}</b>: sign-in stopped — {what}. "
                          f"Not tried again for {LOGIN_PAUSE_HOURS}h (Settings → Accounts).")
    except Exception:  # noqa: BLE001: a message must never break a scan
        LOG.exception("Could not send the sign-in message")


def _auto_forms(session, resp, hops: int = 4):
    """Follow the self-submitting forms single sign-on uses to carry the session
    back to the site (hidden inputs + document.forms[0].submit())."""
    for _ in range(hops):
        m = re.search(r'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', resp.text, re.S | re.I)
        if not m or "submit()" not in resp.text:
            return resp
        fields = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', m.group(2), re.I))
        if not fields:
            return resp
        action = urljoin(resp.url, unescape(m.group(1)))
        resp = session.post(action, data={k: unescape(v) for k, v in fields.items()})
    return resp


# ── Portal das Finanças and the other AT/Segurança Social services (acesso.gov.pt) ──

ACESSO = "https://www.acesso.gov.pt/v2/"


def login_acesso_gov(session, username: str, password: str, *, start: str) -> None:
    """Sign in with NIF and password on acesso.gov.pt, the single sign-on of
    the Autoridade Tributária, and come back to `start` signed in."""
    page = session.get(start)
    page.raise_for_status()
    host = urlparse(start).hostname
    if urlparse(page.url).hostname == host:
        return                                       # still signed in from before
    token = re.search(r"token:\s*`([^`]+)`", page.text)
    part = re.search(r"partID:\s*`([^`]*)`", page.text)
    path = re.search(r"path:\s*`([^`]*)`", page.text)
    if not token or not part:
        raise LoginFailed("the acesso.gov.pt login page has changed")
    resp = session.post(urljoin(ACESSO, "login"), data={
        "username": username, "password": password, "selectedAuthMethod": "N", "authVersion": "2",
        "_csrf": token.group(1), "partID": part.group(1), "path": path.group(1) if path else "",
    })
    text = resp.text.lower()
    if "codigosms2fa" in text or "reenviarcodigo" in text:
        raise LoginNeedsCode("acesso.gov.pt asked for an SMS code")
    if "captcha" in text:
        raise LoginFailed("acesso.gov.pt showed a CAPTCHA")
    resp = _auto_forms(session, resp)
    if urlparse(resp.url).hostname != host:
        wrong = re.search(r'"errorMsg"\s*:\s*"([^"]{3,120})"', resp.text)
        raise LoginFailed(f"acesso.gov.pt did not sign in ({wrong.group(1) if wrong else 'wrong NIF or password?'})")


FINANCAS_SALES = "https://vendas.portaldasfinancas.gov.pt/vendasat/lista/vendas"

LOGINS = {
    "financas": {
        "name": "Portal das Finanças",
        "note": "NIF and the Portal das Finanças password. If your account asks for an SMS code, "
                "the app cannot sign in on its own and will tell you.",
        "login": lambda session, user, pwd: login_acesso_gov(session, user, pwd, start=FINANCAS_SALES),
    },
}
