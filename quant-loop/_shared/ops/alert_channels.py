"""Telegram and Email alert channels for the AlertSink protocol (H7).

Two additional sinks that plug into the existing
:class:`_shared.ops.alerting.AlertSink` protocol via duck typing (no explicit
inheritance needed — just an ``emit`` method).

- :class:`TelegramSink` — sends alerts via the Telegram Bot API
  (``POST https://api.telegram.org/bot{token}/sendMessage``).
- :class:`EmailSink` — sends alerts via SMTP.

Both support per-rule **cooldown** (rate limiting): once an alert with a given
``rule`` is sent, subsequent alerts for the same rule within ``cooldown_sec``
are silently dropped. This prevents alert storms from a persistent condition.

References:
  - :mod:`_shared.ops.alerting` — AlertSink protocol, Alert dataclass.
  - Telegram Bot API docs (core.telegram.org/bots/api).
  - :mod:`smtplib` stdlib docs for SMTP usage.
"""
from __future__ import annotations

import json
import smtplib
import time
import urllib.request
from email.mime.text import MIMEText
from typing import Dict, Optional

from _shared.ops.alerting import Alert, AlertLevel


def alert_message(alert: Alert) -> str:
    """Format an Alert into a human-readable markdown string.

    Parameters
    ----------
    alert:
        The :class:`Alert` to format.

    Returns
    -------
    str
        Multi-line markdown text suitable for Telegram or email body.
    """
    lines = [
        f"*{alert.level}* — `{alert.rule}`",
        f"",
        f"{alert.message}",
    ]
    if alert.context:
        lines.append("")
        lines.append("_Context:_")
        for k, v in alert.context.items():
            lines.append(f"  • `{k}`: {v}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TelegramSink
# ---------------------------------------------------------------------------

class TelegramSink:
    """Sends alerts via the Telegram Bot API.

    Parameters
    ----------
    bot_token:
        Telegram bot API token (from @BotFather).
    chat_id:
        Target chat / channel ID.
    timeout_sec:
        HTTP request timeout.
    cooldown_sec:
        Per-rule cooldown in seconds. 0 disables rate limiting.

    Usage::

        sink = TelegramSink(bot_token="123:abc", chat_id="-100123456")
        alerter = Alerter(sinks=[sink])
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout_sec: float = 10.0,
        cooldown_sec: float = 0.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_sec = timeout_sec
        self.cooldown_sec = cooldown_sec
        self._last_sent: Dict[str, float] = {}

    def _in_cooldown(self, alert: Alert, now: float) -> bool:
        """Check per-rule cooldown. Returns True if alert should be suppressed."""
        if self.cooldown_sec <= 0:
            return False
        last = self._last_sent.get(alert.rule)
        if last is not None and now - last < self.cooldown_sec:
            return True
        self._last_sent[alert.rule] = now
        return False

    def emit(self, alert: Alert) -> None:
        """Send the alert as a Telegram message.

        Raises:
            urllib.error.URLError: if the request fails.
        """
        if self._in_cooldown(alert, time.time()):
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": alert_message(alert),
            "parse_mode": "Markdown",
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec):
            pass


# ---------------------------------------------------------------------------
# EmailSink
# ---------------------------------------------------------------------------

class EmailSink:
    """Sends alerts via SMTP.

    Parameters
    ----------
    smtp_host:
        SMTP server hostname (e.g. ``"smtp.gmail.com"``).
    smtp_port:
        SMTP server port (e.g. 587 for STARTTLS, 465 for SSL).
    sender:
        Sender email address.
    password:
        SMTP authentication password / app-specific password.
    recipients:
        List of recipient email addresses.
    use_tls:
        Whether to use STARTTLS (default True).
    timeout_sec:
        SMTP connection timeout.
    cooldown_sec:
        Per-rule cooldown in seconds. 0 disables rate limiting.

    Usage::

        sink = EmailSink(
            smtp_host="smtp.gmail.com", smtp_port=587,
            sender="alerts@example.com", password="secret",
            recipients=["trader@example.com"],
        )
        alerter = Alerter(sinks=[sink])
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        sender: str,
        password: str,
        recipients: list,
        use_tls: bool = True,
        timeout_sec: float = 10.0,
        cooldown_sec: float = 0.0,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.password = password
        self.recipients = list(recipients)
        self.use_tls = use_tls
        self.timeout_sec = timeout_sec
        self.cooldown_sec = cooldown_sec
        self._last_sent: Dict[str, float] = {}

    def _in_cooldown(self, alert: Alert, now: float) -> bool:
        if self.cooldown_sec <= 0:
            return False
        last = self._last_sent.get(alert.rule)
        if last is not None and now - last < self.cooldown_sec:
            return True
        self._last_sent[alert.rule] = now
        return False

    def _build_message(self, alert: Alert) -> MIMEText:
        """Build the email MIMEText message."""
        subject = f"[{alert.level}] {alert.rule}"
        body = alert_message(alert)
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        return msg

    def emit(self, alert: Alert) -> None:
        """Send the alert as an email via SMTP.

        Raises:
            smtplib.SMTPException: if the send fails.
        """
        if self._in_cooldown(alert, time.time()):
            return

        msg = self._build_message(alert)

        with smtplib.SMTP(self.smtp_host, self.smtp_port,
                          timeout=self.timeout_sec) as server:
            if self.use_tls:
                server.starttls()
            server.login(self.sender, self.password)
            server.sendmail(self.sender, self.recipients, msg.as_string())


__all__ = [
    "TelegramSink",
    "EmailSink",
    "alert_message",
]
