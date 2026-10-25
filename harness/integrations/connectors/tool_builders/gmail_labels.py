"""Integration tools: gmail_labels (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

import re
from .. import calls
from ..toolkit import attach, function_schema, clamp
from ..profiles import gmail_profile, MAILBOX_ACCOUNT_PROP
from ..headers import google_headers
from ..mail.mime import safe_filename

from typing import Any, Callable, Optional


def gmail_labels_tools(
    secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False
) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    _GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

    def _gmail_call(
        account: str, method: str, path: str, *, params=None, body=None
    ) -> dict[str, Any]:
        email, profile, err = gmail_profile(secrets, account)
        if err:
            return err
        result = calls.request(
            method,
            f"{_GMAIL_API}/{path}",
            headers=google_headers(profile["access_token"]),
            params=params,
            json=body,
        )
        if result.get("ok"):
            result["account"] = email
        return result

    def _gmail_modify(
        account: str, kind: str, ident: str, add: list[str], remove: list[str]
    ) -> dict[str, Any]:
        """messages.modify / threads.modify — the one endpoint behind every label,
        trash-by-label and spam operation Gmail exposes."""
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        if not body:
            return {"error": "nothing to add or remove"}
        return _gmail_call(account, "POST", f"{kind}/{ident}/modify", body=body)

    def _hidden(account: str):
        """(is_hidden or None, error). None = nothing is filtered, so nothing to check."""
        _email, profile, err = gmail_profile(secrets, account)
        if err:
            return None, err
        from .. import gmail_hidden

        return gmail_hidden.checker(secrets, profile["access_token"]), None

    def _metadata(account: str, kind: str, ident: str) -> dict[str, Any]:
        """One message or thread with only the headers a hidden-check reads."""
        return _gmail_call(
            account, "GET", f"{kind}/{ident}", params={"format": "metadata", "metadataHeaders": "From"}
        )

    def _label_list(raw: Any) -> list[str]:
        """Labels arrive as a comma-separated string from the model."""
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [part.strip() for part in str(raw or "").split(",") if part.strip()]

    # -- reading -----------------------------------------------------------------------

    def gmail_search_threads(
        query: str = "", max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        result = _gmail_call(
            account,
            "GET",
            "threads",
            params={"q": query, "maxResults": clamp(max_results)},
        )
        is_hidden, err = _hidden(account)
        if err or not is_hidden or not result.get("ok"):
            return result
        from .. import gmail_hidden

        return gmail_hidden.thread_listing(result, is_hidden, lambda tid: _metadata(account, "threads", tid))

    gmail_search_threads.__name__ = "gmail_search_threads"
    tools.append(
        attach(
            gmail_search_threads,
            function_schema(
                "gmail_search_threads",
                "List Gmail threads, optionally filtered by a Gmail search query "
                "(e.g. 'from:acme is:unread newer_than:7d').",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_get_thread(thread_id: str, account: str = "") -> dict[str, Any]:
        from .. import gmail_hidden

        result = _gmail_call(account, "GET", f"threads/{thread_id}")
        is_hidden, err = _hidden(account)
        if err or not is_hidden or not result.get("ok"):
            return result
        return gmail_hidden.one_thread(result, is_hidden)

    gmail_get_thread.__name__ = "gmail_get_thread"
    tools.append(
        attach(
            gmail_get_thread,
            function_schema(
                "gmail_get_thread",
                "Read a whole Gmail thread: every message in the conversation.",
                {"thread_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["thread_id"],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_list_drafts(max_results: int = 10, account: str = "") -> dict[str, Any]:
        return _gmail_call(
            account, "GET", "drafts", params={"maxResults": clamp(max_results)}
        )

    gmail_list_drafts.__name__ = "gmail_list_drafts"
    tools.append(
        attach(
            gmail_list_drafts,
            function_schema(
                "gmail_list_drafts",
                "List draft emails.",
                {"max_results": {"type": "integer"}, "account": MAILBOX_ACCOUNT_PROP},
                [],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_get_draft(draft_id: str, account: str = "") -> dict[str, Any]:
        from .. import gmail_hidden

        result = _gmail_call(account, "GET", f"drafts/{draft_id}", params={"format": "full"})
        is_hidden, err = _hidden(account)
        if err or not is_hidden or not result.get("ok"):
            return result
        if is_hidden((result.get("data") or {}).get("message") or {}):
            return gmail_hidden.not_found()
        return result

    gmail_get_draft.__name__ = "gmail_get_draft"
    tools.append(
        attach(
            gmail_get_draft,
            function_schema(
                "gmail_get_draft",
                "Read one draft email.",
                {"draft_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["draft_id"],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_list_labels(account: str = "") -> dict[str, Any]:
        return _gmail_call(account, "GET", "labels")

    gmail_list_labels.__name__ = "gmail_list_labels"
    tools.append(
        attach(
            gmail_list_labels,
            function_schema(
                "gmail_list_labels",
                "List every label in the mailbox, with its id. The other label tools "
                "take ids, not names, so this usually comes first.",
                {"account": MAILBOX_ACCOUNT_PROP},
                [],
            ),
            caps=["gmail", "read"],
        )
    )

    # -- drafts and labels -------------------------------------------------------------

    def gmail_create_draft(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        attachments: Optional[list[str]] = None,
        account: str = "",
    ) -> dict[str, Any]:
        import base64 as _b64
        from email.message import EmailMessage

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        if cc:
            message["Cc"] = cc
        if bcc:
            message["Bcc"] = bcc
        message.set_content(body)
        from .. import gmail_attach

        problem, _size = gmail_attach.attach_paths(message, attachments, roots, remote=remote)
        if problem:
            return {"error": problem}
        raw = _b64.urlsafe_b64encode(message.as_bytes()).decode()
        return _gmail_call(account, "POST", "drafts", body={"message": {"raw": raw}})

    gmail_create_draft.__name__ = "gmail_create_draft"
    tools.append(
        attach(
            gmail_create_draft,
            function_schema(
                "gmail_create_draft",
                "Create a draft email. Nothing is sent — the draft waits in Gmail for a "
                "person to review and send it.",
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
            caps=["gmail", "write"],
        )
    )

    def gmail_create_label(name: str, account: str = "") -> dict[str, Any]:
        return _gmail_call(
            account,
            "POST",
            "labels",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )

    gmail_create_label.__name__ = "gmail_create_label"
    tools.append(
        attach(
            gmail_create_label,
            function_schema(
                "gmail_create_label",
                "Create a new Gmail label.",
                {"name": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["name"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_label_message(
        message_id: str, label_ids: str, account: str = ""
    ) -> dict[str, Any]:
        return _gmail_modify(
            account, "messages", message_id, _label_list(label_ids), []
        )

    gmail_label_message.__name__ = "gmail_label_message"
    tools.append(
        attach(
            gmail_label_message,
            function_schema(
                "gmail_label_message",
                "Add labels to one message. label_ids is comma-separated; ids come from "
                "gmail_list_labels.",
                {
                    "message_id": {"type": "string"},
                    "label_ids": {"type": "string"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["message_id", "label_ids"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_label_thread(
        thread_id: str, label_ids: str, account: str = ""
    ) -> dict[str, Any]:
        return _gmail_modify(account, "threads", thread_id, _label_list(label_ids), [])

    gmail_label_thread.__name__ = "gmail_label_thread"
    tools.append(
        attach(
            gmail_label_thread,
            function_schema(
                "gmail_label_thread",
                "Add labels to every message in a thread.",
                {
                    "thread_id": {"type": "string"},
                    "label_ids": {"type": "string"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["thread_id", "label_ids"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_update_message_labels(
        message_id: str, add_label_ids: str = "", remove_label_ids: str = "", account: str = ""
    ) -> dict[str, Any]:
        return _gmail_modify(
            account,
            "messages",
            message_id,
            _label_list(add_label_ids),
            _label_list(remove_label_ids),
        )

    gmail_update_message_labels.__name__ = "gmail_update_message_labels"
    tools.append(
        attach(
            gmail_update_message_labels,
            function_schema(
                "gmail_update_message_labels",
                "Add and remove labels on one message in a single atomic call.",
                {
                    "message_id": {"type": "string"},
                    "add_label_ids": {"type": "string"},
                    "remove_label_ids": {"type": "string"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["message_id"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_unlabel_message(
        message_id: str, label_ids: str, account: str = ""
    ) -> dict[str, Any]:
        return _gmail_modify(
            account, "messages", message_id, [], _label_list(label_ids)
        )

    gmail_unlabel_message.__name__ = "gmail_unlabel_message"
    tools.append(
        attach(
            gmail_unlabel_message,
            function_schema(
                "gmail_unlabel_message",
                "Remove labels from one message.",
                {
                    "message_id": {"type": "string"},
                    "label_ids": {"type": "string"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["message_id", "label_ids"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    def gmail_unlabel_thread(
        thread_id: str, label_ids: str, account: str = ""
    ) -> dict[str, Any]:
        return _gmail_modify(account, "threads", thread_id, [], _label_list(label_ids))

    gmail_unlabel_thread.__name__ = "gmail_unlabel_thread"
    tools.append(
        attach(
            gmail_unlabel_thread,
            function_schema(
                "gmail_unlabel_thread",
                "Remove labels from every message in a thread.",
                {
                    "thread_id": {"type": "string"},
                    "label_ids": {"type": "string"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["thread_id", "label_ids"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    # -- trash and spam ----------------------------------------------------------------
    # Gmail's trash is reversible (untrash below) and nothing here deletes permanently:
    # `gmail.modify` cannot, by design, and no tool asks for a scope that could.

    def gmail_trash_message(message_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_call(account, "POST", f"messages/{message_id}/trash")

    gmail_trash_message.__name__ = "gmail_trash_message"
    tools.append(
        attach(
            gmail_trash_message,
            function_schema(
                "gmail_trash_message",
                "Move one message to the Trash. Reversible with gmail_untrash_message.",
                {"message_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["message_id"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    def gmail_trash_thread(thread_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_call(account, "POST", f"threads/{thread_id}/trash")

    gmail_trash_thread.__name__ = "gmail_trash_thread"
    tools.append(
        attach(
            gmail_trash_thread,
            function_schema(
                "gmail_trash_thread",
                "Move an entire thread to the Trash. Reversible with gmail_untrash_thread.",
                {"thread_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["thread_id"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    def gmail_untrash_message(message_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_call(account, "POST", f"messages/{message_id}/untrash")

    gmail_untrash_message.__name__ = "gmail_untrash_message"
    tools.append(
        attach(
            gmail_untrash_message,
            function_schema(
                "gmail_untrash_message",
                "Restore one message from the Trash.",
                {"message_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["message_id"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_untrash_thread(thread_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_call(account, "POST", f"threads/{thread_id}/untrash")

    gmail_untrash_thread.__name__ = "gmail_untrash_thread"
    tools.append(
        attach(
            gmail_untrash_thread,
            function_schema(
                "gmail_untrash_thread",
                "Restore an entire thread from the Trash.",
                {"thread_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["thread_id"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_mark_message_spam(message_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_modify(account, "messages", message_id, ["SPAM"], ["INBOX"])

    gmail_mark_message_spam.__name__ = "gmail_mark_message_spam"
    tools.append(
        attach(
            gmail_mark_message_spam,
            function_schema(
                "gmail_mark_message_spam",
                "Mark one message as Spam.",
                {"message_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["message_id"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    def gmail_mark_thread_spam(thread_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_modify(account, "threads", thread_id, ["SPAM"], ["INBOX"])

    gmail_mark_thread_spam.__name__ = "gmail_mark_thread_spam"
    tools.append(
        attach(
            gmail_mark_thread_spam,
            function_schema(
                "gmail_mark_thread_spam",
                "Mark an entire thread as Spam.",
                {"thread_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["thread_id"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    def gmail_unmark_message_spam(message_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_modify(account, "messages", message_id, ["INBOX"], ["SPAM"])

    gmail_unmark_message_spam.__name__ = "gmail_unmark_message_spam"
    tools.append(
        attach(
            gmail_unmark_message_spam,
            function_schema(
                "gmail_unmark_message_spam",
                "Take one message out of Spam and back to the Inbox.",
                {"message_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["message_id"],
            ),
            caps=["gmail", "write"],
        )
    )

    def gmail_unmark_thread_spam(thread_id: str, account: str = "") -> dict[str, Any]:
        return _gmail_modify(account, "threads", thread_id, ["INBOX"], ["SPAM"])

    gmail_unmark_thread_spam.__name__ = "gmail_unmark_thread_spam"
    tools.append(
        attach(
            gmail_unmark_thread_spam,
            function_schema(
                "gmail_unmark_thread_spam",
                "Take an entire thread out of Spam and back to the Inbox.",
                {"thread_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["thread_id"],
            ),
            caps=["gmail", "write"],
        )
    )

    _SENSITIVE_LABELS = {"TRASH", "SPAM"}

    def gmail_apply_sensitive_message_label(
        message_id: str, label: str, account: str = ""
    ) -> dict[str, Any]:
        which = (label or "").strip().upper()
        if which not in _SENSITIVE_LABELS:
            return {"error": "label must be TRASH or SPAM"}
        return _gmail_modify(account, "messages", message_id, [which], ["INBOX"])

    gmail_apply_sensitive_message_label.__name__ = "gmail_apply_sensitive_message_label"
    tools.append(
        attach(
            gmail_apply_sensitive_message_label,
            function_schema(
                "gmail_apply_sensitive_message_label",
                "Apply TRASH or SPAM to one message. Same effect as the dedicated trash "
                "and spam tools; kept because Google's own catalogue exposes it.",
                {
                    "message_id": {"type": "string"},
                    "label": {"type": "string", "enum": ["TRASH", "SPAM"]},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["message_id", "label"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    def gmail_apply_sensitive_thread_label(
        thread_id: str, label: str, account: str = ""
    ) -> dict[str, Any]:
        which = (label or "").strip().upper()
        if which not in _SENSITIVE_LABELS:
            return {"error": "label must be TRASH or SPAM"}
        return _gmail_modify(account, "threads", thread_id, [which], ["INBOX"])

    gmail_apply_sensitive_thread_label.__name__ = "gmail_apply_sensitive_thread_label"
    tools.append(
        attach(
            gmail_apply_sensitive_thread_label,
            function_schema(
                "gmail_apply_sensitive_thread_label",
                "Apply TRASH or SPAM to an entire thread.",
                {
                    "thread_id": {"type": "string"},
                    "label": {"type": "string", "enum": ["TRASH", "SPAM"]},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["thread_id", "label"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )


    # -- attachments -------------------------------------------------------------------
    # Google's own Gmail MCP server has NO attachment tool, and neither did we. The gap is
    # narrow but total: `gmail_get_message` already returns each part's filename, mime type,
    # size and `attachmentId`, but never the bytes — Gmail serves those from a separate
    # endpoint. So an agent could see that a PDF was attached and had no way to read it.
    # Verified against a real mailbox 2026-09-06. `gmail.modify` already covers both calls,
    # so this costs no new scope and does not touch the project's verification tier.

    def _gmail_attachment_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten the MIME tree to the parts that are actual attachments.

        Gmail nests parts arbitrarily deep (multipart/mixed wrapping multipart/alternative
        wrapping the body), so the attachments are not at any fixed level. A part counts
        when it has BOTH a filename and an attachmentId: inline bodies carry a filename of
        "" and small inline images carry their bytes in `data` with no id to fetch.
        """
        found: list[dict[str, Any]] = []

        def walk(part: dict[str, Any]) -> None:
            body = part.get("body") or {}
            name = part.get("filename") or ""
            if name and body.get("attachmentId"):
                found.append(
                    {
                        "filename": name,
                        "mime_type": part.get("mimeType") or "",
                        "size": body.get("size"),
                        "attachment_id": body["attachmentId"],
                    }
                )
            for child in part.get("parts") or []:
                walk(child)

        walk(payload or {})
        return found

    def gmail_list_attachments(message_id: str, account: str = "") -> dict[str, Any]:
        from .. import gmail_hidden

        result = _gmail_call(account, "GET", f"messages/{message_id}")
        if result.get("error"):
            return result
        is_hidden, err = _hidden(account)
        if not err and is_hidden and is_hidden(result.get("data") or {}):
            return gmail_hidden.not_found()
        data = result.get("data") or result
        attachments = _gmail_attachment_parts(data.get("payload") or {})
        return {
            "ok": True,
            "account": result.get("account"),
            "message_id": message_id,
            "attachments": attachments,
            "count": len(attachments),
        }

    gmail_list_attachments.__name__ = "gmail_list_attachments"
    tools.append(
        attach(
            gmail_list_attachments,
            function_schema(
                "gmail_list_attachments",
                "List a message's attachments: filename, type, size, and the "
                "attachment_id that gmail_download_attachment needs.",
                {"message_id": {"type": "string"}, "account": MAILBOX_ACCOUNT_PROP},
                ["message_id"],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_download_attachment(
        message_id: str, attachment_id: str, filename: str = "", account: str = ""
    ) -> dict[str, Any]:
        if remote:
            return {
                "error": "saving an attachment is not available in a remote session — "
                "the file would be written to this computer, not the remote host."
            }
        scratch = roots[0] if roots else None
        if scratch is None or not scratch.writable:
            return {
                "error": "no writable session directory to save the attachment into"
            }
        is_hidden, err = _hidden(account)
        if err:
            return err
        if is_hidden:
            from .. import gmail_hidden

            owner = _metadata(account, "messages", message_id)
            if owner.get("ok") and is_hidden(owner.get("data") or {}):
                return gmail_hidden.not_found()
        result = _gmail_call(
            account, "GET", f"messages/{message_id}/attachments/{attachment_id}"
        )
        if result.get("error"):
            return result
        data = result.get("data") or result
        raw = data.get("data")
        if not raw:
            return {"error": "attachment returned no data"}
        import base64 as _b64

        try:
            # Gmail returns base64URL, not standard base64, and strips the padding.
            payload = _b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:
            return {"error": f"could not decode attachment: {exc}"}


        safe = safe_filename(filename or f"attachment-{attachment_id[:8]}")
        target = scratch.path / safe
        counter = 1
        while target.exists():
            stem = re.sub(r"-[0-9]+$", "", target.stem) or "attachment"
            target = scratch.path / f"{stem}-{counter}{target.suffix}"
            counter += 1
        target.write_bytes(payload)
        return {
            "ok": True,
            "account": result.get("account"),
            "path": str(target),
            "size": len(payload),
        }

    gmail_download_attachment.__name__ = "gmail_download_attachment"
    tools.append(
        attach(
            gmail_download_attachment,
            function_schema(
                "gmail_download_attachment",
                "Save one attachment into the session folder. attachment_id comes from "
                "gmail_list_attachments.",
                {
                    "message_id": {"type": "string"},
                    "attachment_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "account": MAILBOX_ACCOUNT_PROP,
                },
                ["message_id", "attachment_id"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )
    return tools
