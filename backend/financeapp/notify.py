"""Security, sync and rule alerts (alerts.py) to your phone: ntfy push first, email as the fallback.

Messages never carry balances, amounts or account numbers (anyone glancing at a lock screen could read them), and
sending never blocks or breaks the request that triggered it: failures are logged and dropped.
"""

import logging
import smtplib
import threading
from datetime import UTC, datetime
from email.message import EmailMessage
from urllib.parse import urlsplit

import httpx

from financeapp.config import Settings

log = logging.getLogger("financeapp")
PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5, "urgent": 5}

# The last time anything was sent, and how it went, for Settings → Alerts. Kept in memory: it starts over when the
# app restarts. `reason` is always one of our own sentences, never an exception's text.
_last: dict | None = None
_lock = threading.Lock()


class SendError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def last_attempt() -> dict | None:
    with _lock:
        return dict(_last) if _last else None


def _record(ok: bool, channel: str | None, reason: str | None) -> None:
    global _last
    with _lock:
        _last = {"at": datetime.now(UTC).isoformat(), "ok": ok, "channel": channel, "reason": reason}


def configured(settings: Settings) -> bool:
    return bool(settings.ntfy_url or (settings.smtp_host and settings.alert_email))


def ntfy_target(url: str) -> tuple[str, str]:
    """NTFY_URL (https://ntfy.sh/your-topic) as the server to publish to and the topic."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SendError("NTFY_URL needs to be a full address, like https://ntfy.sh/your-topic.")
    server, _, topic = url.rpartition("/")
    if not topic or "." in topic or server.endswith(":/"):
        raise SendError("NTFY_URL needs the topic at the end, like https://ntfy.sh/your-topic.")
    return server + "/", topic


def _ntfy(settings: Settings, title: str, body: str, tags: str, priority: str, click: str | None = None) -> None:
    # Published as JSON, not with Title/Tags headers: headers can only carry plain ASCII, so a title naming a
    # category like "Café" (or any dash or accent) would fail before it left the app.
    server, topic = ntfy_target(settings.ntfy_url)
    message: dict = {
        "topic": topic,
        "title": title,
        "message": body,
        "tags": [t for t in tags.split(",") if t],
        "priority": PRIORITY.get(priority, 3),
    }
    if click:
        message["click"] = click  # tapping the notification opens the app
    headers = {"Authorization": f"Bearer {settings.ntfy_token}"} if settings.ntfy_token else {}
    try:
        r = httpx.post(server, json=message, headers=headers, timeout=10)
    except httpx.TimeoutException:
        raise SendError("The ntfy server didn't answer in time.") from None
    except httpx.HTTPError:
        raise SendError("Couldn't reach the ntfy server. Check the address and that the computer is online.") from None
    if r.status_code in (401, 403):
        raise SendError(f"The ntfy server refused it ({r.status_code}): the topic needs NTFY_TOKEN, or it's wrong.")
    if r.status_code == 429:
        raise SendError("The ntfy server is limiting how many messages it takes (429). Later ones will retry.")
    if r.status_code >= 400:
        raise SendError(f"The ntfy server answered with an error ({r.status_code}).")


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


def deliver(
    settings: Settings, title: str, body: str, tags: str = "lock", priority: str = "high", click: str | None = None
) -> str | None:
    """Sends on the first channel that works. Returns the channel it went out on (ntfy or email), or None."""
    reason = None
    if settings.ntfy_url:
        try:
            _ntfy(settings, title, body, tags, priority, click)
            _record(True, "ntfy", None)
            return "ntfy"
        except SendError as e:
            reason = e.reason
        except Exception as e:
            reason = "Something went wrong sending to ntfy."
            log.warning("ntfy alert failed (%s)", type(e).__name__)
        log.warning("ntfy alert failed: %s", reason)
    if settings.smtp_host and settings.alert_email:
        try:
            _email(settings, title, body + (f"\n\n{click}" if click else ""))
            _record(True, "email", reason)  # the reason ntfy didn't work, if it was tried
            return "email"
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused):
            email_reason = "The email server turned it down. Check SMTP_USER, SMTP_PASSWORD and ALERT_EMAIL."
        except smtplib.SMTPException:
            email_reason = "The email server didn't take it. Check the SMTP settings in .env."
        except OSError:
            email_reason = "Couldn't reach the email server. Check SMTP_HOST and SMTP_PORT."
        except Exception as e:
            email_reason = "Something went wrong sending the email."
            log.warning("email alert failed (%s)", type(e).__name__)
        log.warning("email alert failed: %s", email_reason)
        reason = f"{reason} {email_reason}" if reason else email_reason
    _record(False, None, reason)
    return None


def send_now(settings: Settings, title: str, body: str, tags: str = "lock") -> bool:
    """Sends on the first channel that works. Returns whether anything was sent."""
    return deliver(settings, title, body, tags) is not None


def send(settings: Settings, title: str, body: str, tags: str = "lock") -> None:
    """Sends in the background."""
    if configured(settings):
        threading.Thread(target=send_now, args=(settings, title, body, tags), daemon=True).start()
