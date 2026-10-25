"""Which servers a mailbox lives on, and how to connect to them.

One connector covers Gmail, iCloud, Fastmail and any other IMAP host. A person gives an address
and an app password, and the servers are inferred from the domain; the advanced fields override
the inference.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from typing import Any, Optional

TIMEOUT = 30.0


@dataclass(frozen=True)
class EmailServers:
    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587  # 587 starts TLS on the open connection, 465 is TLS from the first byte


_ICLOUD = EmailServers("imap.mail.me.com", 993, "smtp.mail.me.com", 587)
_GMAIL = EmailServers("imap.gmail.com", 993, "smtp.gmail.com", 587)
_PRESETS: dict[str, EmailServers] = {
    "gmail.com": _GMAIL,
    "googlemail.com": _GMAIL,
    "icloud.com": _ICLOUD,
    "me.com": _ICLOUD,
    "mac.com": _ICLOUD,
    "fastmail.com": EmailServers("imap.fastmail.com", 993, "smtp.fastmail.com", 465),
}


def resolve_servers(profile: dict[str, Any]) -> tuple[Optional[EmailServers], str]:
    """(servers, "") for a stored profile, or (None, why) when none can be worked out. The
    advanced fields win over the preset for the address's domain."""
    address = str(profile.get("address") or "").strip()
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    preset = _PRESETS.get(domain)

    def text(key: str, fallback: str) -> str:
        return str(profile.get(key) or "").strip() or fallback

    def port(key: str, fallback: int) -> int:
        raw = str(profile.get(key) or "").strip()
        try:
            return int(raw) if raw else fallback
        except ValueError:
            return fallback

    imap_host = text("imap_host", preset.imap_host if preset else "")
    smtp_host = text("smtp_host", preset.smtp_host if preset else "")
    if not imap_host or not smtp_host:
        return None, (
            f"no server preset for '{domain or address}' — fill in the IMAP and SMTP "
            "host fields in the connector settings"
        )
    return (
        EmailServers(
            imap_host=imap_host,
            imap_port=port("imap_port", preset.imap_port if preset else 993),
            smtp_host=smtp_host,
            smtp_port=port("smtp_port", preset.smtp_port if preset else 587),
        ),
        "",
    )


def auth_hint(servers: EmailServers) -> str:
    """What to check when a login is refused."""
    if servers.imap_host.endswith(".gmail.com"):
        return (
            " For Gmail, check that 2-Step Verification is on and that this is an app "
            "password from myaccount.google.com/apppasswords — not your account password."
        )
    return " Check the address and app password in the connector settings."


def imap_connection(host: str, port: int) -> imaplib.IMAP4_SSL:
    return imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT)


def smtp_connection(host: str, port: int) -> smtplib.SMTP:
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
    smtp = smtplib.SMTP(host, port, timeout=TIMEOUT)
    smtp.starttls(context=ssl.create_default_context())
    return smtp


def imap_login(profile, servers: EmailServers, factory) -> imaplib.IMAP4:
    imap = factory(servers.imap_host, servers.imap_port)
    imap.login(profile["address"], profile["app_password"])
    return imap


def smtp_login(profile, servers: EmailServers, factory) -> smtplib.SMTP:
    smtp = factory(servers.smtp_host, servers.smtp_port)
    smtp.login(profile["address"], profile["app_password"])
    return smtp
