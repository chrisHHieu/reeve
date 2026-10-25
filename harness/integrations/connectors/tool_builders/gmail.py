"""The core Gmail tools: search, read one message, and send a new email."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Callable, Optional

from .. import calls, gmail_attach
from ..gmail_hidden import checker, not_found
from ..headers import google_headers
from ..profiles import MAILBOX_ACCOUNT_PROP, gmail_profile
from ..toolkit import attach, function_schema

_MESSAGES = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


def _without_hidden(result: dict[str, Any], is_hidden: Callable, token: str) -> dict[str, Any]:
    """A search result with the messages hidden by the person's filters left out.

    "Never show agents" is enforced here, without a sound: a matching hit is omitted with no
    tombstone, and the count travels in the `_display` sidecar for the person's tool card and the
    audit row, never in what the agent can read.
    """
    data = dict(result.get("data") or {})
    kept, hidden = [], 0
    for message in data.get("messages") or []:
        meta = calls.request(
            "GET",
            f"{_MESSAGES}/{message.get('id')}",
            headers=google_headers(token),
            params={"format": "metadata", "metadataHeaders": "From"},
        )
        detail = meta.get("data") if meta.get("ok") else None
        # A metadata miss lets the message through: an id alone reveals nothing, and
        # `gmail_get_message` checks again before any content flows.
        if isinstance(detail, dict) and is_hidden(detail):
            hidden += 1
        else:
            kept.append(message)
    if not hidden:
        return result
    data["messages"] = kept
    if isinstance(data.get("resultSizeEstimate"), int):
        data["resultSizeEstimate"] = max(0, data["resultSizeEstimate"] - hidden)
    return {"ok": True, "data": data, "_display": {"hidden_by_filters": hidden, "connector": "gmail"}}


def gmail_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    def gmail_search_messages(query: str, max_results: int = 10, account: str = "") -> dict[str, Any]:
        email, profile, err = gmail_profile(secrets, account)
        if err:
            return err
        token = profile["access_token"]
        result = calls.request(
            "GET",
            _MESSAGES,
            headers=google_headers(token),
            params={"q": query, "maxResults": max(1, min(int(max_results or 10), 20))},
        )
        is_hidden = checker(secrets, token)
        if result.get("ok") and is_hidden:
            result = _without_hidden(result, is_hidden, token)
        if result.get("ok"):
            result["account"] = email
        return result

    def gmail_get_message(message_id: str, account: str = "") -> dict[str, Any]:
        email, profile, err = gmail_profile(secrets, account)
        if err:
            return err
        token = profile["access_token"]
        result = calls.request(
            "GET", f"{_MESSAGES}/{message_id}", headers=google_headers(token), params={"format": "full"}
        )
        is_hidden = checker(secrets, token)
        if result.get("ok") and is_hidden:
            data = result.get("data") or {}
            if isinstance(data, dict) and is_hidden(data):
                # The same answer as a message that is not there: the agent must not be able to
                # tell "filtered" from "gone", since a tombstone invites probing.
                return not_found()
        if result.get("ok"):
            result["account"] = email
        return result

    def gmail_send_email(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        attachments: Optional[list[str]] = None,
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = gmail_profile(secrets, account)
        if err:
            return err
        message = EmailMessage()
        message["To"], message["Subject"] = to, subject
        if cc:
            message["Cc"] = cc
        if bcc:
            message["Bcc"] = bcc
        message.set_content(body)
        problem, _size = gmail_attach.attach_paths(message, attachments, roots, remote=remote)
        if problem:
            return {"error": problem}
        result = calls.request(
            "POST",
            f"{_MESSAGES}/send",
            headers=google_headers(profile["access_token"]),
            json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")},
        )
        if result.get("ok"):
            result["account"] = email
        return result

    return [
        attach(
            gmail_search_messages,
            function_schema(
                "gmail_search_messages",
                "Search Gmail messages using Gmail query syntax.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["gmail", "read"],
        ),
        attach(
            gmail_get_message,
            function_schema(
                "gmail_get_message",
                "Read a Gmail message by ID.",
                {"message_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["message_id"],
            ),
            caps=["gmail", "read"],
        ),
        attach(
            gmail_send_email,
            function_schema(
                "gmail_send_email",
                "Send a NEW email through Gmail. To answer a message use gmail_reply, which keeps it "
                "in the thread. Requires user approval; the "
                "`account` argument names the sending mailbox on the approval card.",
                {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "cc": {"type": "string"},
                    "bcc": {"type": "string"},
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths of files inside the session's directories to attach",
                    },
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["to", "subject", "body"],
            ),
            approval=True,
            caps=["gmail", "write"],
        ),
    ]
