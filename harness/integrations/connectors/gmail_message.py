"""Reading a Gmail message resource: headers, addresses, text, attachments. Pure, no network."""

from __future__ import annotations

import base64
import re
from email.utils import formataddr, getaddresses, parseaddr
from typing import Any


def header(message: dict[str, Any], name: str) -> str:
    for h in (message.get("payload") or {}).get("headers") or []:
        if str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value") or "").strip()
    return ""


def addresses(*values: str) -> list[str]:
    """Distinct addresses (case-insensitive) from any number of header values, as
    `Name <addr>` when the header had a name, in the order first seen."""
    seen: set[str] = set()
    out: list[str] = []
    for name, addr in getaddresses([v for v in values if v]):
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(formataddr((name, addr)) if name else addr)
    return out


def without(addresses: list[str], mine: str) -> list[str]:
    return [a for a in addresses if parseaddr(a)[1].lower() != mine.lower()]


def text_of(payload: dict[str, Any]) -> str:
    """The readable body of a message: its text/plain part, else its HTML with tags removed."""
    found: dict[str, str] = {}

    def walk(part: dict[str, Any]) -> None:
        data = (part.get("body") or {}).get("data")
        mime = part.get("mimeType") or ""
        if data and mime in ("text/plain", "text/html") and mime not in found:
            found[mime] = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    if "text/plain" in found:
        return found["text/plain"].strip()
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"<[^>]+>", "", found.get("text/html", ""))).strip()


def parts_with_files(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The attachments of a message: parts with both a filename and an attachmentId."""
    found: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        if part.get("filename") and (part.get("body") or {}).get("attachmentId"):
            found.append(part)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    return found


def call_factory(secrets: Any):
    """`call(account, method, path, params=, body=)` against the account's mailbox, stamping the
    account on success. Never raises; a failure is `{"error": ...}`."""
    from . import calls
    from .headers import google_headers
    from .profiles import gmail_profile
    def call(account: str, method: str, path: str, *, params=None, body=None) -> dict[str, Any]:
        email, profile, err = gmail_profile(secrets, account)
        if err:
            return err
        result = calls.request(
            method, f"{API}/{path}", headers=google_headers(profile["access_token"]), params=params, json=body
        )
        if result.get("ok"):
            result["account"] = email
        return result

    return call


API = "https://gmail.googleapis.com/gmail/v1/users/me"
