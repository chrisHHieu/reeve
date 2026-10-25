"""Outlook's mail and people tools through Microsoft Graph.

Reads run unasked: who the account is (with its time zone, which every meeting time needs), search,
one message whole with its attachments listed, a conversation, folders, and people. Everything that
sends, files or saves asks. Bodies are read as text (`Prefer: outlook.body-content-type="text"`),
so a message reaches the model as words, not markup.
"""

from __future__ import annotations

import base64
from typing import Any, Callable, Optional
from urllib.parse import quote

from .. import calls, session_files
from ..headers import graph_headers
from ..profiles import GENERIC_ACCOUNT_PROP, account_profile, account_result
from ..toolkit import attach, clamp, function_schema

GRAPH = "https://graph.microsoft.com/v1.0"
#: Graph refuses a sendMail body over 4 MB, and base64 adds a third.
MAX_ATTACH = 3 * 1024 * 1024
#: The longest body one message of a conversation shows; `outlook_read_message` shows it whole.
BODY_IN_THREAD = 4000
_TEXT = {"Prefer": 'outlook.body-content-type="text"'}
_LIST = "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments,flag,conversationId,importance"
#: Well-known folder names Graph takes in place of an id.
FOLDERS = ("inbox", "sentitems", "drafts", "archive", "deleteditems", "junkemail", "outbox")
_IDS = {"type": "array", "items": {"type": "string"}}
_EMAILS = {"type": "array", "items": {"type": "string"}, "description": "Email addresses."}


def address(entry: Optional[dict[str, Any]]) -> str:
    who = (entry or {}).get("emailAddress") or {}
    name, mail = who.get("name") or "", who.get("address") or ""
    return f"{name} <{mail}>" if name and mail and name != mail else (mail or name)


def recipients(emails: Optional[list[str]]) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": e.strip()}} for e in emails or [] if e and e.strip()]


def message_row(m: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"id": m.get("id"), "subject": m.get("subject") or "(no subject)", "from": address(m.get("from")),
                           "received": m.get("receivedDateTime"), "preview": m.get("bodyPreview") or ""}
    if not m.get("isRead", True):
        row["unread"] = True
    if m.get("hasAttachments"):
        row["attachments"] = True
    if (m.get("flag") or {}).get("flagStatus") == "flagged":
        row["flagged"] = True
    if m.get("importance") == "high":
        row["important"] = True
    if m.get("conversationId"):
        row["conversation_id"] = m["conversationId"]
    return row


def outlook_mail_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def graph(account: str, method: str, path: str, *, headers: Optional[dict[str, str]] = None, **options: Any) -> dict[str, Any]:
        aid, profile, failure = account_profile(secrets, "outlook", account, "access_token")
        if failure:
            return failure
        sent = {**graph_headers(profile["access_token"]), **(headers or {})}
        return account_result(aid, calls.request(method, GRAPH + path, headers=sent, **options))

    def bare(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**{k: v for k, v in result.items() if k != "data"}, **extra} if result.get("ok") else result

    def add(fn: Callable[..., Any], description: str, props: dict[str, Any], required: list[str], kind: str) -> None:
        schema = function_schema(fn.__name__, description, {**props, "account": GENERIC_ACCOUNT_PROP}, required)
        tools.append(attach(fn, schema, caps=["outlook", kind]))

    def files(paths: Optional[list[str]]) -> tuple[list[dict[str, Any]], str]:
        """Attachments for an outgoing message, from the session's folders only."""
        out, total = [], 0
        for raw in paths or []:
            file, refusal = session_files.outgoing(raw, roots, remote=remote, limit=MAX_ATTACH, service="Outlook")
            if refusal:
                return [], refusal
            total += file.stat().st_size
            if total > MAX_ATTACH:
                return [], f"attachments total more than {MAX_ATTACH // (1024 * 1024)} MB; share a larger file with a OneDrive link"
            out.append({"@odata.type": "#microsoft.graph.fileAttachment", "name": file.name,
                        "contentBytes": base64.b64encode(file.read_bytes()).decode("ascii")})
        return out, ""

    def outlook_whoami(account: str = "") -> dict[str, Any]:
        me = graph(account, "GET", "/me", params={"$select": "displayName,mail,userPrincipalName"})
        if not me.get("ok"):
            return me
        box = graph(account, "GET", "/me/mailboxSettings", params={"$select": "timeZone,language"})
        info = me.get("data") or {}
        settings = (box.get("data") or {}) if box.get("ok") else {}
        return bare(me, name=info.get("displayName"), email=info.get("mail") or info.get("userPrincipalName"),
                    time_zone=settings.get("timeZone"), language=(settings.get("language") or {}).get("locale"))

    add(outlook_whoami, "The Outlook account this connection uses: name, email, and the mailbox's time zone (use it for meeting times).", {}, [], "read")

    def outlook_search_messages(
        query: str = "", folder: str = "", unread_only: bool = False, from_address: str = "", since: str = "",
        has_attachments: bool = False, max_results: int = 20, account: str = "",
    ) -> dict[str, Any]:
        base = f"/me/mailFolders/{quote(folder)}/messages" if folder else "/me/messages"
        params: dict[str, Any] = {"$top": clamp(max_results, default=20, ceiling=50), "$select": _LIST}
        if query:
            # Graph does not combine $search with $filter or $orderby; a search ranks by relevance.
            params["$search"] = f'"{query}"'
        else:
            where = []
            if unread_only:
                where.append("isRead eq false")
            if from_address:
                where.append(f"from/emailAddress/address eq '{from_address.replace(chr(39), chr(39) * 2)}'")
            if since:
                where.append(f"receivedDateTime ge {since}")
            if has_attachments:
                where.append("hasAttachments eq true")
            if where:
                params["$filter"] = " and ".join(where)
            params["$orderby"] = "receivedDateTime desc"
        result = graph(account, "GET", base, params=params)
        if not result.get("ok"):
            return result
        return bare(result, messages=[message_row(m) for m in (result.get("data") or {}).get("value") or []])

    add(outlook_search_messages,
        "Search or list mail, newest first. `query` is a full-text search (from:, subject:, hasattachments:yes work in it); "
        "without one, filter by folder (inbox, sentitems, drafts, archive, deleteditems or a folder id), unread, sender, "
        "a date (since, ISO 8601) or attachments.",
        {"query": {"type": "string"}, "folder": {"type": "string"}, "unread_only": {"type": "boolean"},
         "from_address": {"type": "string"}, "since": {"type": "string"}, "has_attachments": {"type": "boolean"},
         "max_results": {"type": "integer"}}, [], "read")

    def outlook_read_message(message_id: str, account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", f"/me/messages/{quote(message_id)}", headers=_TEXT, params={
            "$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,conversationId,isRead,flag,categories,importance"})
        m = result.get("data") if result.get("ok") else None
        if m is None:
            return result
        out = bare(result, **message_row({**m, "id": message_id}))
        out.pop("preview", None)
        out.update({"to": [address(r) for r in m.get("toRecipients") or []], "cc": [address(r) for r in m.get("ccRecipients") or []],
                    "body": (m.get("body") or {}).get("content", ""), **({"categories": m["categories"]} if m.get("categories") else {})})
        if m.get("hasAttachments"):
            listed = graph(account, "GET", f"/me/messages/{quote(message_id)}/attachments", params={"$select": "id,name,size,contentType,isInline"})
            out["attachment_list"] = [{"id": a.get("id"), "name": a.get("name"), "bytes": a.get("size"), "type": a.get("contentType")}
                                      for a in (listed.get("data") or {}).get("value") or [] if not a.get("isInline")]
        return out

    add(outlook_read_message, "Read one message whole: sender, recipients, the body as text, and its attachments with their ids.",
        {"message_id": {"type": "string"}}, ["message_id"], "read")

    def outlook_read_thread(conversation_id: str, account: str = "") -> dict[str, Any]:
        safe = conversation_id.replace("'", "''")
        result = graph(account, "GET", "/me/messages", headers=_TEXT, params={
            "$filter": f"conversationId eq '{safe}'", "$top": 50, "$select": "id,subject,from,toRecipients,receivedDateTime,body,hasAttachments"})
        if not result.get("ok"):
            return result
        messages = sorted((result.get("data") or {}).get("value") or [], key=lambda m: m.get("receivedDateTime") or "")
        rows = []
        for m in messages:
            text = (m.get("body") or {}).get("content", "")
            rows.append({"id": m.get("id"), "from": address(m.get("from")), "to": [address(r) for r in m.get("toRecipients") or []],
                         "received": m.get("receivedDateTime"), "body": text if len(text) <= BODY_IN_THREAD else text[:BODY_IN_THREAD] + "…",
                         **({"attachments": True} if m.get("hasAttachments") else {})})
        return bare(result, subject=(messages[0].get("subject") if messages else ""), messages=rows)

    add(outlook_read_thread, "Read a whole conversation, oldest first. conversation_id comes from a search or a message read.",
        {"conversation_id": {"type": "string"}}, ["conversation_id"], "read")

    def outlook_list_folders(account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", "/me/mailFolders", params={"$top": 100, "$select": "id,displayName,unreadItemCount,totalItemCount,childFolderCount"})
        if not result.get("ok"):
            return result
        return bare(result, folders=[{"id": f.get("id"), "name": f.get("displayName"), "unread": f.get("unreadItemCount"),
                                      "total": f.get("totalItemCount")} for f in (result.get("data") or {}).get("value") or []])

    add(outlook_list_folders, "List the mailbox's folders with their ids and unread counts.", {}, [], "read")

    def outlook_find_people(query: str, max_results: int = 10, account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", "/me/people", params={"$search": f'"{query}"', "$top": clamp(max_results, default=10, ceiling=25),
                                                              "$select": "displayName,scoredEmailAddresses,jobTitle,department"})
        if not result.get("ok"):
            return result
        return bare(result, people=[{"name": p.get("displayName"), "emails": [e.get("address") for e in p.get("scoredEmailAddresses") or []],
                                     **({"title": p["jobTitle"]} if p.get("jobTitle") else {})} for p in (result.get("data") or {}).get("value") or []])

    add(outlook_find_people, "Find a person's email by name among the people this account works and writes with, and its contacts.",
        {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"], "read")

    def outlook_download_attachment(message_id: str, attachment_id: str, account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", f"/me/messages/{quote(message_id)}/attachments/{quote(attachment_id)}")
        item = result.get("data") if result.get("ok") else None
        if item is None:
            return result
        if not item.get("contentBytes"):
            return {"error": "this attachment is an item or a link, not a file"}
        target, refusal = session_files.landing(item.get("name") or "attachment", roots, remote=remote)
        if refusal:
            return {"error": refusal}
        payload = base64.b64decode(item["contentBytes"])
        target.write_bytes(payload)
        return bare(result, path=str(target), size=len(payload))

    add(outlook_download_attachment, "Save a message's attachment into the session folder. attachment_id comes from outlook_read_message.",
        {"message_id": {"type": "string"}, "attachment_id": {"type": "string"}}, ["message_id", "attachment_id"], "write")

    def outlook_send_mail(
        to: list[str], subject: str, body: str, cc: Optional[list[str]] = None, bcc: Optional[list[str]] = None,
        html: bool = False, attachments: Optional[list[str]] = None, important: bool = False, account: str = "",
    ) -> dict[str, Any]:
        if isinstance(to, str):
            to = [p for p in to.replace(";", ",").split(",")]
        if not recipients(to):
            return {"error": "a message needs at least one recipient"}
        parts, refusal = files(attachments)
        if refusal:
            return {"error": refusal}
        message: dict[str, Any] = {"subject": subject, "body": {"contentType": "HTML" if html else "Text", "content": body},
                                   "toRecipients": recipients(to), "ccRecipients": recipients(cc), "bccRecipients": recipients(bcc)}
        if parts:
            message["attachments"] = parts
        if important:
            message["importance"] = "high"
        return bare(graph(account, "POST", "/me/sendMail", json={"message": message, "saveToSentItems": True}), sent_to=to)

    add(outlook_send_mail, "Send an email. attachments are paths of files in the session folder (3 MB in all).",
        {"to": _EMAILS, "subject": {"type": "string"}, "body": {"type": "string"}, "cc": _EMAILS, "bcc": _EMAILS,
         "html": {"type": "boolean"}, "attachments": _IDS, "important": {"type": "boolean"}}, ["to", "subject", "body"], "write")

    def outlook_reply(message_id: str, body: str, reply_all: bool = False, attachments: Optional[list[str]] = None, account: str = "") -> dict[str, Any]:
        parts, refusal = files(attachments)
        if refusal:
            return {"error": refusal}
        action = "createReplyAll" if reply_all else "createReply"
        draft = graph(account, "POST", f"/me/messages/{quote(message_id)}/{action}",
                      json={"comment": body, **({"message": {"attachments": parts}} if parts else {})})
        made = draft.get("data") if draft.get("ok") else None
        if made is None:
            return draft
        return bare(graph(account, "POST", f"/me/messages/{quote(made['id'])}/send"), replied_to=message_id, reply_all=reply_all)

    add(outlook_reply, "Reply to a message (reply_all for everyone on it). The thread is quoted below the text; attachments from the session folder.",
        {"message_id": {"type": "string"}, "body": {"type": "string"}, "reply_all": {"type": "boolean"}, "attachments": _IDS},
        ["message_id", "body"], "write")

    def outlook_forward(message_id: str, to: list[str], comment: str = "", account: str = "") -> dict[str, Any]:
        if not recipients(to):
            return {"error": "a forward needs at least one recipient"}
        return bare(graph(account, "POST", f"/me/messages/{quote(message_id)}/forward",
                          json={"comment": comment, "toRecipients": recipients(to)}), forwarded_to=to)

    add(outlook_forward, "Forward a message, with its attachments, and a note above it.",
        {"message_id": {"type": "string"}, "to": _EMAILS, "comment": {"type": "string"}}, ["message_id", "to"], "write")

    def outlook_draft(
        action: str, draft_id: str = "", to: Optional[list[str]] = None, subject: Optional[str] = None,
        body: Optional[str] = None, cc: Optional[list[str]] = None, html: bool = False, account: str = "",
    ) -> dict[str, Any]:
        if action == "send":
            if not draft_id:
                return {"error": "send needs draft_id"}
            return bare(graph(account, "POST", f"/me/messages/{quote(draft_id)}/send"), sent=draft_id)
        message: dict[str, Any] = {}
        if subject is not None:
            message["subject"] = subject
        if body is not None:
            message["body"] = {"contentType": "HTML" if html else "Text", "content": body}
        if to is not None:
            message["toRecipients"] = recipients(to)
        if cc is not None:
            message["ccRecipients"] = recipients(cc)
        if action == "create":
            made = graph(account, "POST", "/me/messages", json=message)
            return bare(made, draft_id=(made.get("data") or {}).get("id")) if made.get("ok") else made
        if action == "update":
            if not (draft_id and message):
                return {"error": "update needs draft_id and a field to change"}
            return bare(graph(account, "PATCH", f"/me/messages/{quote(draft_id)}", json=message), draft_id=draft_id)
        return {"error": "action is create, update or send"}

    add(outlook_draft, "Drafts: create one (it waits in Drafts for the person), update it, or send it.",
        {"action": {"type": "string", "enum": ["create", "update", "send"]}, "draft_id": {"type": "string"}, "to": _EMAILS,
         "subject": {"type": "string"}, "body": {"type": "string"}, "cc": _EMAILS, "html": {"type": "boolean"}}, ["action"], "write")

    def outlook_organize(
        message_ids: list[str], move_to: str = "", read: Optional[bool] = None, flag: str = "",
        categories: Optional[list[str]] = None, account: str = "",
    ) -> dict[str, Any]:
        if not message_ids:
            return {"error": "name at least one message"}
        change: dict[str, Any] = {}
        if read is not None:
            change["isRead"] = read
        if flag:
            if flag not in ("flagged", "complete", "notFlagged"):
                return {"error": "flag is flagged, complete or notFlagged"}
            change["flag"] = {"flagStatus": flag}
        if categories is not None:
            change["categories"] = categories
        if not (change or move_to):
            return {"error": "nothing to do: move_to, read, flag or categories"}
        done, failed = [], []
        for mid in message_ids[:100]:
            if change:
                step = graph(account, "PATCH", f"/me/messages/{quote(mid)}", json=change)
                if not step.get("ok"):
                    failed.append({"id": mid, "error": step.get("error")})
                    continue
            if move_to:
                step = graph(account, "POST", f"/me/messages/{quote(mid)}/move", json={"destinationId": move_to})
                if not step.get("ok"):
                    failed.append({"id": mid, "error": step.get("error")})
                    continue
            done.append(mid)
        return {"ok": not failed, "done": len(done), **({"failed": failed} if failed else {})}

    add(outlook_organize,
        "File messages: move them (move_to: a folder id or inbox, archive, deleteditems, junkemail), mark read or unread, "
        "flag (flagged, complete, notFlagged) or set categories. Deleting is moving to deleteditems, which Outlook keeps for a while.",
        {"message_ids": _IDS, "move_to": {"type": "string"}, "read": {"type": "boolean"}, "flag": {"type": "string"},
         "categories": _IDS}, ["message_ids"], "write")
    return tools
