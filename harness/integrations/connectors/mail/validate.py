"""The check made when a mailbox is connected."""

from __future__ import annotations

from typing import Any

from .servers import auth_hint, imap_connection, resolve_servers, smtp_connection


def validate_email_account(creds: dict[str, Any]) -> tuple[bool, str, str]:
    """(ok, identity, error). Logging in over IMAP, opening INBOX, and logging in over SMTP must
    all work. A mailbox with IMAP switched off, common on accounts managed by an organization,
    then fails in the connect wizard with a message that says what to do, and not at the first
    tool call."""
    servers, why = resolve_servers(creds)
    if servers is None:
        return False, "", why
    address = str(creds.get("address") or "")
    password = creds.get("app_password", "")

    inbox = ""
    try:
        imap = imap_connection(servers.imap_host, servers.imap_port)
        try:
            imap.login(address, password)
            status, data = imap.select('"INBOX"', readonly=True)
            if status == "OK" and data and data[0]:
                inbox = data[0].decode(errors="replace")
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except Exception as exc:
        return False, "", f"IMAP check failed: {exc}.{auth_hint(servers)}"

    try:
        smtp = smtp_connection(servers.smtp_host, servers.smtp_port)
        try:
            smtp.login(address, password)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
    except Exception as exc:
        return False, "", f"SMTP check failed: {exc}.{auth_hint(servers)}"

    return True, address + (f" · INBOX: {inbox} messages" if inbox else ""), ""
