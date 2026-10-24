"""Alerts: WhatsApp (Twilio sandbox) and email for a match (PLAN.md §3.5, H16–21).

    POST /matches/{id}/alert   -> alert_match(id)   (demo button)
    after every sweep          -> auto_alert(profile_id): new, relevant, open ELIGIBLE matches

A channel without credentials reports "not_configured" and the message preview is still returned,
so the demo can show what would be sent. alerted_at is set only when a channel actually sent.
"""
import asyncio
import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import select

import api.config  # noqa: F401  (loads .env)
from agents.schemas import IST
from api.db import SessionLocal
from api.matcher import ELIGIBLE, inr
from api.models import Match

log = logging.getLogger("delivery.alerts")

VERDICT_LABEL = {"ELIGIBLE": "Eligible", "CHECK_DOCS": "Check docs", "NOT_ELIGIBLE": "Not eligible"}
VERDICT_ICON = {"ELIGIBLE": "🟢", "CHECK_DOCS": "🟠", "NOT_ELIGIBLE": "🔴"}
REASON_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️"}
WHATSAPP_MAX = 1500        # Twilio caps a WhatsApp body at 1600 characters
AUTO_ALERT_MAX = 5         # per sweep, so a big sweep can't flood the phone


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def auto_alert_enabled() -> bool:
    return _env("AUTO_ALERT", "true").lower() in ("1", "true", "yes")


# ---- message ---------------------------------------------------------------------------------

def _closes(end: datetime) -> str:
    return end.astimezone(IST).strftime("%d %b, %I:%M %p")


def format_message(m: dict) -> str:
    """WhatsApp/email text for one match: tender, value, deadline, verdict, reasons, checklist, link."""
    t = m["tender"]
    head = [f"{VERDICT_ICON.get(m['verdict'], '')} TenderHawk · {VERDICT_LABEL.get(m['verdict'], m['verdict'])}",
            f"*{t['title']}*"]
    meta = [x for x in (t.get("buyer"), inr(t["est_value"]) if t.get("est_value") else None,
                        f"closes {_closes(t['end_date'])}") if x]
    head.append(" · ".join(meta))
    lines = [f"{REASON_ICON.get(r['kind'], '•')} {r['text']}" for r in m["reasons"]]
    tail = []
    if m["missing_docs"]:
        tail.append("📄 Documents: " + "; ".join(m["missing_docs"]))
    tail.append(f"🔗 {t['source_url']}")

    body = "\n".join(head + lines + tail)
    while len(body) > WHATSAPP_MAX and lines:          # drop reasons from the end, keep head and link
        lines.pop()
        body = "\n".join(head + lines + ["…"] + tail)
    return body[:WHATSAPP_MAX]


# ---- channels --------------------------------------------------------------------------------

def _whatsapp_number(n: str) -> str:
    n = n.strip()
    return n if n.startswith("whatsapp:") else f"whatsapp:{n}"


async def send_whatsapp(to: str, body: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    sid, token, sender = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN"), _env("TWILIO_WHATSAPP_FROM")
    if not (sid and token and sender and to):
        return "not_configured"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as c:
            r = await c.post(url, auth=(sid, token),
                             data={"From": _whatsapp_number(sender), "To": _whatsapp_number(to), "Body": body})
    except httpx.HTTPError as e:
        log.warning("whatsapp send failed: %r", e)
        return f"failed: {type(e).__name__}"
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        log.warning("twilio %s: %s", r.status_code, msg)
        return f"failed: {msg}"[:300]
    return "sent"


def _smtp_send(to: str, subject: str, body: str) -> str:
    url = _env("SMTP_URL")
    if not (url and to):
        return "not_configured"
    u = urlparse(url)
    if u.scheme not in ("smtp", "smtps") or not u.hostname:
        return "failed: SMTP_URL must look like smtp://user:pass@host:587 or smtps://user:pass@host:465"
    user, password = unquote(u.username or ""), unquote(u.password or "")
    sender = _env("ALERT_EMAIL_FROM") or user
    if not sender:
        return "failed: set ALERT_EMAIL_FROM"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body.replace("*", ""))
    try:
        if u.scheme == "smtps":
            smtp = smtplib.SMTP_SSL(u.hostname, u.port or 465, timeout=20, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(u.hostname, u.port or 587, timeout=20)
        with smtp:
            if u.scheme == "smtp":
                smtp.starttls(context=ssl.create_default_context())
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        log.warning("email send failed: %r", e)
        return f"failed: {type(e).__name__}"
    return "sent"


async def send_email(to: str, subject: str, body: str) -> str:
    return await asyncio.to_thread(_smtp_send, to, subject, body)


# ---- matches ---------------------------------------------------------------------------------

def _load(match_id: int) -> dict | None:
    with SessionLocal() as db:
        m = db.get(Match, match_id)
        if not m:
            return None
        t, p = m.tender, m.profile
        return {
            "id": m.id, "verdict": m.verdict, "reasons": list(m.reasons or []),
            "missing_docs": list(m.missing_docs or []),
            "tender": {"title": t.title, "buyer": t.buyer, "est_value": t.est_value, "end_date": t.end_date,
                       "source_url": t.source_url},
            "profile": {"phone": p.phone, "email": p.email},
        }


def _mark_alerted(match_id: int) -> None:
    with SessionLocal() as db:
        db.get(Match, match_id).alerted_at = datetime.now(IST)
        db.commit()


async def alert_match(match_id: int) -> dict | None:
    """Send this match on every configured channel. None if the match doesn't exist."""
    m = await asyncio.to_thread(_load, match_id)
    if m is None:
        return None
    body = format_message(m)
    # ALERT_WHATSAPP_TO (the stage phone joined to the sandbox) wins over the profile's number.
    wa_to = _env("ALERT_WHATSAPP_TO") or (m["profile"]["phone"] or "")
    mail_to = _env("ALERT_EMAIL_TO") or (m["profile"]["email"] or "")
    subject = f"TenderHawk: {VERDICT_LABEL.get(m['verdict'], m['verdict'])} · {m['tender']['title'][:80]}"
    whatsapp, email = await asyncio.gather(send_whatsapp(wa_to, body), send_email(mail_to, subject, body))
    sent = "sent" in (whatsapp, email)
    if sent:
        await asyncio.to_thread(_mark_alerted, match_id)
    return {"match_id": match_id, "sent": sent, "channels": {"whatsapp": whatsapp, "email": email},
            "preview": body}


def _pending_eligible(profile_id: int) -> list[int]:
    with SessionLocal() as db:
        q = (select(Match).where(Match.profile_id == profile_id, Match.verdict == ELIGIBLE,
                                 Match.relevant.is_(True), Match.alerted_at.is_(None)))
        now = datetime.now(IST)
        ms = [m for m in db.scalars(q) if m.tender.end_date > now]
        ms.sort(key=lambda m: -(m.fit_score or 0))
        return [m.id for m in ms[:AUTO_ALERT_MAX]]


async def auto_alert(profile_id: int) -> int:
    """Alert new ELIGIBLE matches that were never alerted. Returns how many were sent."""
    if not auto_alert_enabled():
        return 0
    sent = 0
    for mid in await asyncio.to_thread(_pending_eligible, profile_id):
        try:
            res = await alert_match(mid)
            sent += bool(res and res["sent"])
        except Exception as e:  # an alert must never break a sweep
            log.warning("auto-alert failed for match %s: %r", mid, e)
    return sent
