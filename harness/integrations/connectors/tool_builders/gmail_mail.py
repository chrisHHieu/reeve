"""Gmail: the mail a person writes back — reply, forward, and the drafts that get sent.

`gmail_send_email` starts a NEW conversation: it has no thread, no `In-Reply-To`, no
`References`. So "answer that email" was a fresh message the recipient's client could not
file under the original. A reply is a different act, not a send with a longer subject:

  - it goes to the person the original came from (its `Reply-To`, else `From`) — the model
    is never asked to guess an address it can read off the message;
  - it carries `threadId` plus `In-Reply-To` / `References` and a `Re:` subject, which is what
    makes Gmail (and every other client) put it in the same conversation.

Two properties are kept from the read tools:

  - "Never show agents" applies to the message being answered. A hidden message is answered
    with the same 404 a missing one gets, so a reply cannot be used to probe for it.
  - Every send reports who it actually went to, because the approval card shows only the
    arguments and a reply's recipients are worked out here, not typed by the model.
"""
from __future__ import annotations

from .. import calls
from ..toolkit import attach, function_schema
from ..profiles import gmail_profile, MAILBOX_ACCOUNT_PROP
from ..headers import google_headers

import base64
import re
from pathlib import Path
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Callable, Optional

from ..gmail_message import API as _API, addresses, call_factory, header, parts_with_files, text_of, without
from .. import gmail_attach, gmail_hidden

_FILES = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Paths of files inside the session's directories to attach",
}
_HEADERS = ["From", "To", "Cc", "Reply-To", "Subject", "Message-ID", "References", "Date"]


def gmail_mail_tools(
    secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False
) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    call = call_factory(secrets)

    def original(account: str, message_id: str, *, full: bool = False) -> tuple[dict[str, Any], str]:
        """(message, mailbox address) — or ({"error"}, "") when it cannot be answered."""
        email, profile, err = gmail_profile(secrets, account)
        if err:
            return err, ""
        params: dict[str, Any] = {"format": "full"} if full else {"format": "metadata", "metadataHeaders": _HEADERS}
        got = calls.request(
            "GET", f"{_API}/messages/{message_id}", headers=google_headers(profile["access_token"]), params=params
        )
        if not got.get("ok"):
            return got, ""
        message = got.get("data") or {}
        is_hidden = gmail_hidden.checker(secrets, profile["access_token"])
        if is_hidden and is_hidden(message):
            return gmail_hidden.not_found(), ""
        return message, email

    def compose(
        *,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body: str,
        reply_to: Optional[dict[str, Any]] = None,
        attachments: Optional[list[str]] = None,
        carried: Optional[list[tuple[str, bytes, str]]] = None,
    ) -> dict[str, Any]:
        """The `message` resource Gmail takes: `raw`, and a `threadId` for a reply."""
        if not to:
            return {"error": "no recipient: the message has no one to go to"}
        msg = EmailMessage()
        try:
            msg["To"] = ", ".join(to)
            if cc:
                msg["Cc"] = ", ".join(cc)
            if bcc:
                msg["Bcc"] = ", ".join(bcc)
            msg["Subject"] = subject
            if reply_to:
                mid = header(reply_to, "Message-ID")
                if mid:
                    msg["In-Reply-To"] = mid
                    msg["References"] = f"{header(reply_to, 'References')} {mid}".strip()
        except ValueError as exc:  # a line break in a header would be an injected header
            return {"error": f"a header value is not allowed: {exc}"}
        msg.set_content(body)
        used = 0
        for name, data, mime in carried or []:
            problem, used = gmail_attach.attach_bytes(msg, name, data, mime, used)
            if problem:
                return {"error": problem}
        problem, used = gmail_attach.attach_paths(msg, attachments, roots, remote=remote, used=used)
        if problem:
            return {"error": problem}
        out: dict[str, Any] = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")}
        if reply_to and reply_to.get("threadId"):
            out["threadId"] = reply_to["threadId"]
        return out

    def reply_message(
        account: str, message_id: str, body: str, reply_all: bool, cc: str, bcc: str, attachments: Optional[list[str]]
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        source, mine = original(account, message_id)
        if source.get("error"):
            return source, [], []
        sender = header(source, "Reply-To") or header(source, "From")
        if parseaddr(header(source, "From"))[1].lower() == mine.lower():
            sender = header(source, "To")  # answering something I sent: it goes to who I wrote to
        to = without(addresses(sender), mine) or addresses(sender)
        cc_all = addresses(cc)
        if reply_all:
            to = without(addresses(sender, header(source, "To")), mine) or to
            cc_all = addresses(header(source, "Cc"), cc)
            cc_all = [a for a in without(cc_all, mine) if parseaddr(a)[1].lower() not in {parseaddr(t)[1].lower() for t in to}]
        subject = header(source, "Subject")
        if not re.match(r"(?i)^\s*re:", subject):
            subject = f"Re: {subject}".strip()
        message = compose(
            to=to, cc=cc_all, bcc=addresses(bcc), subject=subject, body=body, reply_to=source, attachments=attachments
        )
        return message, to, cc_all

    def register(fn, name, description, props, required, *, approval=False):
        fn.__name__ = name
        props = {**props, "account": MAILBOX_ACCOUNT_PROP}
        tools.append(attach(fn, function_schema(name, description, props, required), approval=approval, caps=["gmail", "write"]))

    def sent(result: dict[str, Any], to: list[str], cc: list[str]) -> dict[str, Any]:
        if result.get("ok"):
            result["sent_to"], result["sent_cc"] = to, cc
        return result

    # -- reply and forward -------------------------------------------------------------

    def gmail_reply(
        message_id: str,
        body: str,
        reply_all: bool = False,
        cc: str = "",
        bcc: str = "",
        attachments: Optional[list[str]] = None,
        account: str = "",
    ):
        message, to, cc_all = reply_message(account, message_id, body, reply_all, cc, bcc, attachments)
        if message.get("error"):
            return message
        return sent(call(account, "POST", "messages/send", body=message), to, cc_all)

    register(
        gmail_reply,
        "gmail_reply",
        "Reply to a message, in its thread. It goes to the sender (or their Reply-To) without you "
        "naming anyone; reply_all also includes everyone on To and Cc. Subject `Re:` and the "
        "threading headers are set. Requires approval. To write the reply for a person to check first, "
        "use gmail_create_reply_draft.",
        {
            "message_id": {"type": "string", "description": "The message being answered"},
            "body": {"type": "string"},
            "reply_all": {"type": "boolean"},
            "cc": {"type": "string", "description": "Extra Cc, comma-separated"},
            "bcc": {"type": "string"},
            "attachments": _FILES,
        },
        ["message_id", "body"],
        approval=True,
    )

    def gmail_create_reply_draft(
        message_id: str,
        body: str,
        reply_all: bool = False,
        cc: str = "",
        bcc: str = "",
        attachments: Optional[list[str]] = None,
        account: str = "",
    ):
        message, to, cc_all = reply_message(account, message_id, body, reply_all, cc, bcc, attachments)
        if message.get("error"):
            return message
        return sent(call(account, "POST", "drafts", body={"message": message}), to, cc_all)

    register(
        gmail_create_reply_draft,
        "gmail_create_reply_draft",
        "Write a reply as a draft in the message's thread. Nothing is sent; a person reviews it in "
        "Gmail (or you send it with gmail_send_draft).",
        {
            "message_id": {"type": "string"},
            "body": {"type": "string"},
            "reply_all": {"type": "boolean"},
            "cc": {"type": "string"},
            "bcc": {"type": "string"},
            "attachments": _FILES,
        },
        ["message_id", "body"],
    )

    def carried_files(account: str, source: dict[str, Any]) -> tuple[list[tuple[str, bytes, str]], str]:
        """The original's attachments as (name, bytes, mime), or ([], the reason one could not be read)."""
        files: list[tuple[str, bytes, str]] = []
        for part in parts_with_files(source.get("payload") or {}):
            got = call(account, "GET", f"messages/{source.get('id')}/attachments/{part['body']['attachmentId']}")
            data = (got.get("data") or {}).get("data") if isinstance(got.get("data"), dict) else None
            if not data:
                return [], f"could not read the attachment {part['filename']!r}: {got.get('error') or 'no data'}"
            files.append((part["filename"], base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)), part.get("mimeType") or ""))
        return files, ""

    def gmail_forward(
        message_id: str,
        to: str,
        note: str = "",
        cc: str = "",
        bcc: str = "",
        include_attachments: bool = True,
        attachments: Optional[list[str]] = None,
        account: str = "",
    ):
        source, _mine = original(account, message_id, full=True)
        if source.get("error"):
            return source
        carried: list[tuple[str, bytes, str]] = []
        if include_attachments:
            carried, problem = carried_files(account, source)
            if problem:
                return {"error": problem}
        head = "\n".join(
            f"{label}: {header(source, label)}" for label in ("From", "Date", "Subject", "To") if header(source, label)
        )
        text = f"{note.strip()}\n\n" if note.strip() else ""
        text += f"---------- Forwarded message ---------\n{head}\n\n{text_of(source.get('payload') or {})}\n"
        subject = header(source, "Subject")
        if not re.match(r"(?i)^\s*fwd?:", subject):
            subject = f"Fwd: {subject}".strip()
        recipients, copies = addresses(to), addresses(cc)
        message = compose(
            to=recipients, cc=copies, bcc=addresses(bcc), subject=subject, body=text, attachments=attachments, carried=carried
        )
        if message.get("error"):
            return message
        result = sent(call(account, "POST", "messages/send", body=message), recipients, copies)
        if result.get("ok"):
            result["attached"] = [name for name, _d, _m in carried] + [Path(a).name for a in attachments or []]
        return result

    register(
        gmail_forward,
        "gmail_forward",
        "Forward a message to someone, with the original's text quoted below your note and its "
        "attachments carried over (include_attachments=false to leave them out). `attachments` adds "
        "your own files. Requires approval.",
        {
            "message_id": {"type": "string"},
            "to": {"type": "string", "description": "Recipients, comma-separated"},
            "note": {"type": "string", "description": "Your words above the forwarded text"},
            "cc": {"type": "string"},
            "bcc": {"type": "string"},
            "include_attachments": {"type": "boolean", "description": "Carry the original's files (default true)"},
            "attachments": _FILES,
        },
        ["message_id", "to"],
        approval=True,
    )

    # -- drafts ------------------------------------------------------------------------

    def gmail_send_draft(draft_id: str, account: str = ""):
        return call(account, "POST", "drafts/send", body={"id": draft_id})

    register(
        gmail_send_draft,
        "gmail_send_draft",
        "Send an existing draft as it is now. Requires approval. Read it first with gmail_get_draft.",
        {"draft_id": {"type": "string"}},
        ["draft_id"],
        approval=True,
    )

    def gmail_update_draft(
        draft_id: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        attachments: Optional[list[str]] = None,
        account: str = "",
    ):
        current = call(account, "GET", f"drafts/{draft_id}", params={"format": "minimal"})
        if current.get("error"):
            return current
        thread = ((current.get("data") or {}).get("message") or {}).get("threadId")
        message = compose(
            to=addresses(to), cc=addresses(cc), bcc=addresses(bcc), subject=subject, body=body, attachments=attachments
        )
        if message.get("error"):
            return message
        if thread:  # a reply draft stays in its conversation
            message["threadId"] = thread
        return call(account, "PUT", f"drafts/{draft_id}", body={"message": message})

    register(
        gmail_update_draft,
        "gmail_update_draft",
        "Replace a draft's recipients, subject, body and attachments. The whole draft is rewritten, so "
        "pass every field and file you want to keep. A reply draft stays in its thread.",
        {
            "draft_id": {"type": "string"},
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "cc": {"type": "string"},
            "bcc": {"type": "string"},
            "attachments": _FILES,
        },
        ["draft_id", "to", "subject", "body"],
    )

    def gmail_delete_draft(draft_id: str, account: str = ""):
        return call(account, "DELETE", f"drafts/{draft_id}")

    register(
        gmail_delete_draft,
        "gmail_delete_draft",
        "Delete a draft permanently (a draft has no Trash). Requires approval.",
        {"draft_id": {"type": "string"}},
        ["draft_id"],
        approval=True,
    )
    return tools
