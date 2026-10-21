"""Security, sync and rule alerts (alerts.py) to your phone: ntfy push first, email as the fallback.

Messages never carry balances, amounts or account numbers (anyone glancing at a lock screen could read them), and
sending never blocks or breaks the request that triggered it: failures are logged and dropped.
"""

import logging
import smtplib
import threading
from email.message import EmailMessage

import httpx

from financeapp.config import Settings

log = logging.getLogger("financeapp")


def configured(settings: Settings) -> bool:
    return bool(settings.ntfy_url or (settings.smtp_host and settings.alert_email))


def _ntfy(settings: Settings, title: str, body: str, tags: str, priority: str) -> None:
    headers = {"Title": title, "Tags": tags, "Priority": priority}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"
    r = httpx.post(settings.ntfy_url, content=body.encode(), headers=headers, timeout=10)
    r.raise_for_status()


def _email(settings: Settings, title: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Finance: {title}"
    msg["From"] = settings.smtp_user or settings.alert_email
    msg["To"] = settings.alert_email
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def deliver(settings: Settings, title: str, body: str, tags: str = "lock", priority: str = "high") -> str | None:
    """Sends on the first channel that works. Returns the channel it went out on (ntfy or email), or None."""
    if settings.ntfy_url:
        try:
            _ntfy(settings, title, body, tags, priority)
            return "ntfy"
        except Exception as e:
            log.warning("ntfy alert failed (%s); trying email", type(e).__name__)
    if settings.smtp_host and settings.alert_email:
        try:
            _email(settings, title, body)
            return "email"
        except Exception as e:
            log.warning("email alert failed (%s)", type(e).__name__)
    return None


def send_now(settings: Settings, title: str, body: str, tags: str = "lock") -> bool:
    """Sends on the first channel that works. Returns whether anything was sent."""
    return deliver(settings, title, body, tags) is not None


def send(settings: Settings, title: str, body: str, tags: str = "lock") -> None:
    """Sends in the background."""
    if configured(settings):
        threading.Thread(target=send_now, args=(settings, title, body, tags), daemon=True).start()
