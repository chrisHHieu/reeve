"""The email tools: list folders, search, read, download an attachment, send.

Credentials come from the secret store when a tool runs and never enter a prompt. Reads are
non-destructive and there are no delete, move or flag tools. Sending and saving an attachment
need approval. Sending is deliberately one step: SMTP only, with no copy appended to Sent
afterwards, so a failure cannot leave a message that was delivered but looks failed and tempts
a retry into sending twice (Gmail files the copy in Sent on its own).
"""

from __future__ import annotations

import email as email_lib
import imaplib
import mimetypes
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any, Callable, Optional

from harness.config.roots import RootDir
from harness.safety.secrets import SecretStore

from ..toolkit import attach, function_schema
from .imap_query import build_search_criteria, fetch_message, parse_list_line, quote, select_readonly
from .mime import decode_mime_header, extract_text_body, list_attachment_parts, safe_filename
from .servers import auth_hint, imap_connection, imap_login, resolve_servers, smtp_connection, smtp_login

MAX_SEARCH_RESULTS = 25
MAX_FOLDERS = 50

_NOT_CONNECTED = {"error": "email is not connected; add it in Manage → Integrations"}
_FOLDER = {"type": "string", "description": "Folder the uid lives in, default INBOX."}
_UID = {"type": "string", "description": "UID from email_search."}


class _Mailbox:
    """The connected mailbox, as the tools use it."""

    def __init__(self, secrets: SecretStore, imap_factory, smtp_factory) -> None:
        self.secrets = secrets
        self.imap_factory = imap_factory
        self.smtp_factory = smtp_factory

    def profile(self) -> Optional[dict[str, Any]]:
        profile = self.secrets.get("email:default") or {}
        return profile if profile.get("address") and profile.get("app_password") else None

    def open(self):
        """(imap, error): an error is a tool result."""
        profile = self.profile()
        if profile is None:
            return None, _NOT_CONNECTED
        servers, why = resolve_servers(profile)
        if servers is None:
            return None, {"error": why}
        try:
            return imap_login(profile, servers, self.imap_factory), None
        except Exception as exc:
            return None, {"error": f"IMAP login failed: {exc}.{auth_hint(servers)}"}

    @staticmethod
    def close(imap) -> None:
        try:
            imap.logout()
        except Exception:
            pass


def _envelope(uid: bytes, fetched) -> Optional[dict[str, Any]]:
    header_bytes = meta_bytes = b""
    for item in fetched:
        if isinstance(item, tuple):
            meta_bytes += item[0]
            header_bytes += item[1]
        elif isinstance(item, bytes):
            meta_bytes += item
    headers = email_lib.message_from_bytes(header_bytes)
    return {
        "uid": uid.decode(),
        "date": decode_mime_header(headers.get("Date", "")),
        "from": decode_mime_header(headers.get("From", "")),
        "to": decode_mime_header(headers.get("To", "")),
        "subject": decode_mime_header(headers.get("Subject", "")),
        "unread": b"\\Seen" not in meta_bytes,
        "has_attachments": b'"ATTACHMENT"' in meta_bytes.upper(),
    }


def _folder_message_count(imap, name: str) -> Optional[int]:
    try:
        status, data = imap.status(quote(name), "(MESSAGES)")
        if status == "OK" and data and data[0]:
            found = re.search(rb"MESSAGES\s+(\d+)", data[0])
            if found:
                return int(found.group(1))
    except Exception:
        pass
    return None


def make_email_tools(
    secrets: SecretStore,
    *,
    roots: Optional[list[RootDir]] = None,
    remote: bool = False,
    imap_factory: Callable[[str, int], imaplib.IMAP4] = imap_connection,
    smtp_factory: Callable[[str, int], smtplib.SMTP] = smtp_connection,
) -> list[Callable[..., Any]]:
    # `remote` is an SSH-bound session. Reading and sending mail still work there, but a path on
    # this machine's disk does not mean anything: the session's files are on the remote host.
    box = _Mailbox(secrets, imap_factory, smtp_factory)

    def email_list_folders() -> dict[str, Any]:
        imap, err = box.open()
        if err:
            return err
        try:
            status, lines = imap.list()
            if status != "OK":
                return {"error": "could not list folders"}
            folders = []
            for line in lines[:MAX_FOLDERS]:
                name = parse_list_line(line) if isinstance(line, bytes) else None
                if name is None:
                    continue
                entry: dict[str, Any] = {"name": name}
                count = _folder_message_count(imap, name)
                if count is not None:
                    entry["messages"] = count
                folders.append(entry)
            return {"ok": True, "folders": folders}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            box.close(imap)

    def email_search(
        folder: str = "INBOX",
        from_address: str = "",
        to_address: str = "",
        subject: str = "",
        text: str = "",
        since: str = "",
        before: str = "",
        unread_only: bool = False,
        max_results: int = 10,
    ) -> dict[str, Any]:
        criteria, why = build_search_criteria(
            from_address=from_address, to_address=to_address, subject=subject, text=text,
            since=since, before=before, unread_only=bool(unread_only),
        )
        if criteria is None:
            return {"error": why}
        imap, err = box.open()
        if err:
            return err
        try:
            refused = select_readonly(imap, folder)
            if refused:
                return {"error": refused}
            status, data = imap.uid("SEARCH", criteria)
            if status != "OK":
                return {"error": "search failed"}
            uids = (data[0] or b"").split()
            limit = max(1, min(int(max_results or 10), MAX_SEARCH_RESULTS))
            messages = []
            for uid in reversed(uids[-limit:]):  # UIDs ascend, so the newest is last
                status, fetched = imap.uid(
                    "FETCH", uid.decode(),
                    "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] FLAGS BODYSTRUCTURE)",
                )
                if status == "OK" and fetched:
                    messages.append(_envelope(uid, fetched))
            return {"ok": True, "folder": folder, "total_matches": len(uids), "messages": messages}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            box.close(imap)

    def email_read(uid: str, folder: str = "INBOX") -> dict[str, Any]:
        imap, err = box.open()
        if err:
            return err
        try:
            refused = select_readonly(imap, folder)
            if refused:
                return {"error": refused}
            msg = fetch_message(imap, str(uid))
            if msg is None:
                return {"error": f"message {uid} not found in {folder}"}
            attachments = [
                {
                    "filename": name,
                    "content_type": part.get_content_type(),
                    "size": len(part.get_payload(decode=True) or b""),
                }
                for name, part in list_attachment_parts(msg)
            ]
            return {
                "ok": True,
                "uid": str(uid),
                "folder": folder,
                **{key: decode_mime_header(msg.get(header, ""))
                   for key, header in (("from", "From"), ("to", "To"), ("cc", "Cc"),
                                       ("date", "Date"), ("subject", "Subject"))},
                "body": extract_text_body(msg),
                "attachments": attachments,
            }
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            box.close(imap)

    def _free_name(folder: Path, name: str) -> Path:
        """`name` in `folder`, or `<stem>-1`, `<stem>-2`… when it is taken."""
        target = folder / safe_filename(name)
        counter = 1
        while target.exists():
            stem = re.sub(r"-[0-9]+$", "", target.stem) or "attachment"
            target = folder / f"{stem}-{counter}{target.suffix}"
            counter += 1
        return target

    def email_download_attachment(uid: str, filename: str, folder: str = "INBOX") -> dict[str, Any]:
        if remote:
            return {
                "error": "saving an attachment is not available in a remote session — "
                "the file would be written to this computer, not the remote host."
            }
        scratch = roots[0] if roots else None
        if scratch is None or not scratch.writable:
            return {"error": "no writable session directory to save the attachment into"}
        imap, err = box.open()
        if err:
            return err
        try:
            refused = select_readonly(imap, folder)
            if refused:
                return {"error": refused}
            msg = fetch_message(imap, str(uid))
            if msg is None:
                return {"error": f"message {uid} not found in {folder}"}
            parts = list_attachment_parts(msg)
            for name, part in parts:
                if name == filename:
                    payload = part.get_payload(decode=True) or b""
                    target = _free_name(scratch.path, name)
                    target.write_bytes(payload)
                    return {"ok": True, "path": str(target), "size": len(payload)}
            return {"error": f"no attachment named {filename!r}; message has {[n for n, _ in parts]}"}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            box.close(imap)

    def _reply_headers(msg: EmailMessage, uid: str, folder: str, subject: str):
        """Thread `msg` under the message `uid`. (subject to use, error result or None)."""
        imap, err = box.open()
        if err:
            return subject, err
        try:
            refused = select_readonly(imap, folder)
            if refused:
                return subject, {"error": refused}
            status, data = imap.uid(
                "FETCH", str(uid), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES SUBJECT)])"
            )
            if status != "OK" or not data or not isinstance(data[0], tuple):
                return subject, {"error": f"reply target {uid} not found in {folder}"}
            original = email_lib.message_from_bytes(data[0][1])
            original_id = str(original.get("Message-ID", "")).strip()
            if original_id:
                msg["In-Reply-To"] = original_id
                references = str(original.get("References", "")).strip()
                msg["References"] = f"{references} {original_id}".strip()
            if not subject:
                earlier = decode_mime_header(original.get("Subject", ""))
                subject = earlier if earlier.lower().startswith("re:") else f"Re: {earlier}"
            return subject, None
        except Exception as exc:
            return subject, {"error": str(exc)}
        finally:
            box.close(imap)

    def email_send(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        reply_to_uid: str = "",
        reply_to_folder: str = "INBOX",
        attachments: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if remote and attachments:
            return {
                "error": "email attachments are not available in a remote session — "
                "the file would be read from this computer, not the remote host."
            }
        profile = box.profile()
        if profile is None:
            return _NOT_CONNECTED
        servers, why = resolve_servers(profile)
        if servers is None:
            return {"error": why}

        msg = EmailMessage()
        display = str(profile.get("display_name") or "").strip()
        msg["From"] = formataddr((display, profile["address"])) if display else profile["address"]
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        msg["Message-ID"] = make_msgid(domain=profile["address"].rsplit("@", 1)[-1])

        final_subject = subject
        if reply_to_uid:
            final_subject, failure = _reply_headers(msg, reply_to_uid, reply_to_folder, subject)
            if failure:
                return failure
        msg["Subject"] = final_subject
        msg.set_content(body)

        allowed = [root.path for root in (roots or [])]
        for raw_path in attachments or []:
            path = Path(str(raw_path)).expanduser().resolve()
            if not any(path.is_relative_to(root) for root in allowed):
                return {"error": f"attachment {raw_path} is outside the session's directories"}
            if not path.is_file():
                return {"error": f"attachment not found: {raw_path}"}
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = kind.split("/", 1)
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

        try:
            smtp = smtp_login(profile, servers, box.smtp_factory)
        except Exception as exc:
            return {"error": f"SMTP login failed: {exc}.{auth_hint(servers)}"}
        try:
            smtp.send_message(msg)
        except Exception as exc:
            return {"error": f"send failed: {exc}"}
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return {"ok": True, "message_id": msg["Message-ID"], "subject": final_subject}

    def tool(fn, description: str, properties: dict[str, Any], required: list[str], *, approval: bool, caps):
        return attach(fn, function_schema(fn.__name__, description, properties, required),
                      approval=approval, caps=caps)

    return [
        tool(email_list_folders, "List the connected mailbox's folders and message counts.",
             {}, [], approval=False, caps=["email", "read"]),
        tool(
            email_search,
            "Search the connected mailbox. Returns newest-first envelopes (uid, date, "
            "from, to, subject, unread, has_attachments). Never marks messages read.",
            {
                "folder": {"type": "string", "description": "Mailbox folder, default INBOX."},
                "from_address": {"type": "string", "description": "Match sender."},
                "to_address": {"type": "string", "description": "Match recipient."},
                "subject": {"type": "string", "description": "Match subject substring."},
                "text": {"type": "string", "description": "Match anywhere in the message."},
                "since": {"type": "string", "description": "On/after this date, YYYY-MM-DD."},
                "before": {"type": "string", "description": "Before this date, YYYY-MM-DD."},
                "unread_only": {"type": "boolean"},
                "max_results": {"type": "integer", "description": "Default 10, max 25."},
            },
            [], approval=False, caps=["email", "read"],
        ),
        tool(
            email_read,
            "Read one email by uid: headers, text body, and attachment names/sizes "
            "(use email_download_attachment to save one). Never marks messages read.",
            {"uid": _UID, "folder": _FOLDER}, ["uid"], approval=False, caps=["email", "read"],
        ),
        tool(
            email_download_attachment,
            "Save one attachment from an email into the session's primary directory "
            "and return the saved path. Requires user approval.",
            {
                "uid": _UID,
                "filename": {"type": "string", "description": "Attachment filename as listed by email_read."},
                "folder": _FOLDER,
            },
            ["uid", "filename"], approval=True, caps=["email", "read"],
        ),
        tool(
            email_send,
            "Send an email from the connected account. Requires user approval. To reply "
            "to a message pass reply_to_uid (threading headers and Re: subject are set "
            "automatically; leave subject empty to reuse the original).",
            {
                "to": {"type": "string", "description": "Recipient address(es), comma-separated."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body."},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
                "reply_to_uid": {"type": "string", "description": "UID of the message being replied to."},
                "reply_to_folder": {"type": "string", "description": "Folder of reply_to_uid, default INBOX."},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths within the session's directories to attach.",
                },
            },
            ["to", "subject", "body"], approval=True, caps=["email", "write"],
        ),
    ]
