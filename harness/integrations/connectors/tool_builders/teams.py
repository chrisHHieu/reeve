"""Microsoft Teams tools through Microsoft Graph: chats, teams and channels.

Work and school accounts only: a personal Microsoft account has no Teams in Graph. Reads run
unasked (chats with their last line, a chat's messages, the teams and channels the person is in,
people in the organization); sending, posting and reacting ask. Messages arrive as HTML and reach
the model as text.

Reading a channel's posts is left out on purpose: it needs `ChannelMessage.Read.All`, which only a
tenant administrator can grant, and asking for it in the sign-in would stop every person whose
admin has not, for all the other tools too. Posting to a channel needs no admin.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable, Optional
from urllib.parse import quote

from .. import calls
from ..headers import graph_headers
from ..profiles import GENERIC_ACCOUNT_PROP, account_profile, account_result
from ..toolkit import attach, clamp, function_schema

GRAPH = "https://graph.microsoft.com/v1.0"
_REACTIONS = ("like", "heart", "laugh", "surprised", "sad", "angry")
_EMAILS = {"type": "array", "items": {"type": "string"}, "description": "Work email addresses."}


_BREAK = re.compile(r"<\s*(br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def chat_text(content: str) -> str:
    """A chat message's HTML as the words a person reads. Inline marks (bold, a link, a mention)
    stay on their line; only a break or the end of a paragraph, list item or row starts a new one.
    (`html_to_text` is for pages: it puts every run of text on a line of its own.)"""
    text = _TAG.sub("", _BREAK.sub("\n", content))
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in html.unescape(text).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def text_of(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    content = body.get("content") or ""
    return chat_text(content) if body.get("contentType") == "html" else content.strip()


def who(message: dict[str, Any]) -> str:
    sender = message.get("from") or {}
    return ((sender.get("user") or sender.get("application") or {}).get("displayName")) or "someone"


def message_row(m: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"id": m.get("id"), "from": who(m), "date": m.get("createdDateTime"), "text": text_of(m)}
    if m.get("deletedDateTime"):
        row["deleted"] = True
    if m.get("lastEditedDateTime"):
        row["edited"] = True
    if m.get("attachments"):
        row["attachments"] = [a.get("name") or a.get("contentUrl") for a in m["attachments"] if a.get("name") or a.get("contentUrl")]
    if m.get("reactions"):
        row["reactions"] = [r.get("reactionType") for r in m["reactions"]]
    if m.get("replyToId"):
        row["reply_to"] = m["replyToId"]
    return row


def teams_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def graph(account: str, method: str, path: str, *, headers: Optional[dict[str, str]] = None, **options: Any) -> dict[str, Any]:
        aid, profile, failure = account_profile(secrets, "teams", account, "access_token")
        if failure:
            return failure
        sent = {**graph_headers(profile["access_token"]), **(headers or {})}
        return account_result(aid, calls.request(method, GRAPH + path, headers=sent, **options))

    def bare(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**{k: v for k, v in result.items() if k != "data"}, **extra} if result.get("ok") else result

    def values(result: dict[str, Any]) -> list[dict[str, Any]]:
        return (result.get("data") or {}).get("value") or []

    def add(fn: Callable[..., Any], description: str, props: dict[str, Any], required: list[str], kind: str) -> None:
        schema = function_schema(fn.__name__, description, {**props, "account": GENERIC_ACCOUNT_PROP}, required)
        tools.append(attach(fn, schema, caps=["teams", kind]))

    def where(chat_id: str, team_id: str, channel_id: str) -> tuple[str, str]:
        """(the messages path of a chat or a channel, why it cannot be named)."""
        if chat_id:
            return f"/chats/{quote(chat_id)}/messages", ""
        if team_id and channel_id:
            return f"/teams/{quote(team_id)}/channels/{quote(channel_id)}/messages", ""
        return "", "name a chat_id, or a team_id with a channel_id"

    def teams_list_chats(query: str = "", max_results: int = 30, account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", "/me/chats", params={
            "$expand": "members,lastMessagePreview", "$orderby": "lastMessagePreview/createdDateTime desc",
            "$top": clamp(max_results, default=30, ceiling=50)})
        if not result.get("ok"):
            return result
        wanted = query.strip().lower()
        rows = []
        for chat in values(result):
            names = [m.get("displayName") for m in chat.get("members") or [] if m.get("displayName")]
            title = chat.get("topic") or ", ".join(names)
            if wanted and wanted not in title.lower() and not any(wanted in (n or "").lower() for n in names):
                continue
            last = chat.get("lastMessagePreview") or {}
            rows.append({"id": chat.get("id"), "title": title, "kind": chat.get("chatType"),
                         **({"last": {"from": who(last), "date": last.get("createdDateTime"), "text": text_of(last)[:200]}} if last else {})})
        return bare(result, chats=rows)

    add(teams_list_chats, "List your Teams chats, the most recently active first, each with who is in it and its last message. "
        "`query` narrows them to a person's name or a chat's topic.",
        {"query": {"type": "string"}, "max_results": {"type": "integer"}}, [], "read")

    def teams_read_chat(chat_id: str, max_results: int = 30, account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", f"/chats/{quote(chat_id)}/messages", params={"$top": clamp(max_results, default=30, ceiling=50)})
        if not result.get("ok"):
            return result
        messages = [m for m in values(result) if m.get("messageType", "message") == "message"]
        messages.sort(key=lambda m: m.get("createdDateTime") or "")
        return bare(result, messages=[message_row(m) for m in messages])

    add(teams_read_chat, "Read a chat's recent messages, oldest first. chat_id comes from teams_list_chats.",
        {"chat_id": {"type": "string"}, "max_results": {"type": "integer"}}, ["chat_id"], "read")

    def teams_list_teams(account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", "/me/joinedTeams", params={"$select": "id,displayName,description"})
        if not result.get("ok"):
            return result
        return bare(result, teams=[{"id": t.get("id"), "name": t.get("displayName"), "about": t.get("description") or ""} for t in values(result)])

    add(teams_list_teams, "List the teams you are a member of.", {}, [], "read")

    def teams_list_channels(team_id: str, account: str = "") -> dict[str, Any]:
        result = graph(account, "GET", f"/teams/{quote(team_id)}/channels", params={"$select": "id,displayName,description,membershipType"})
        if not result.get("ok"):
            return result
        return bare(result, channels=[{"id": c.get("id"), "name": c.get("displayName"), "kind": c.get("membershipType")} for c in values(result)])

    add(teams_list_channels, "List a team's channels with the ids a post needs.", {"team_id": {"type": "string"}}, ["team_id"], "read")

    def teams_find_people(query: str, max_results: int = 10, account: str = "") -> dict[str, Any]:
        safe = query.replace('"', "")
        result = graph(account, "GET", "/users", headers={"ConsistencyLevel": "eventual"}, params={
            "$search": f'"displayName:{safe}" OR "mail:{safe}"', "$top": clamp(max_results, default=10, ceiling=25),
            "$select": "displayName,mail,userPrincipalName,jobTitle,department"})
        if not result.get("ok"):
            return result
        return bare(result, people=[{"name": u.get("displayName"), "email": u.get("mail") or u.get("userPrincipalName"),
                                     **({"title": u["jobTitle"]} if u.get("jobTitle") else {})} for u in values(result)])

    add(teams_find_people, "Find people in your organization by name or email, for a chat.",
        {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"], "read")

    def teams_send_chat(text: str, chat_id: str = "", to: Optional[list[str]] = None, topic: str = "", html: bool = False, account: str = "") -> dict[str, Any]:
        if not text.strip():
            return {"error": "a message needs text"}
        target = chat_id
        if not target:
            people = [e.strip() for e in to or [] if e and e.strip()]
            if not people:
                return {"error": "name a chat_id, or the people (to) to start or continue a chat with"}
            me = graph(account, "GET", "/me", params={"$select": "id"})
            if not me.get("ok"):
                return me
            members = [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"],
                        "user@odata.bind": f"{GRAPH}/users('{quote(p)}')"} for p in [(me.get("data") or {}).get("id"), *people]]
            body: dict[str, Any] = {"chatType": "oneOnOne" if len(people) == 1 else "group", "members": members}
            if topic and len(people) > 1:
                body["topic"] = topic
            # A one-on-one chat that already exists comes back as itself, so this never makes a twin.
            made = graph(account, "POST", "/chats", json=body)
            if not made.get("ok"):
                return made
            target = (made.get("data") or {}).get("id")
        sent = graph(account, "POST", f"/chats/{quote(target)}/messages", json={"body": {"contentType": "html" if html else "text", "content": text}})
        return bare(sent, chat_id=target, message_id=(sent.get("data") or {}).get("id")) if sent.get("ok") else sent

    add(teams_send_chat, "Send a Teams chat message: into a chat (chat_id), or to people by email (to), which opens the "
        "one-on-one or group chat with them, or continues it.",
        {"text": {"type": "string"}, "chat_id": {"type": "string"}, "to": _EMAILS, "topic": {"type": "string"}, "html": {"type": "boolean"}},
        ["text"], "write")

    def teams_post_channel(team_id: str, channel_id: str, text: str, subject: str = "", reply_to: str = "", html: bool = False, account: str = "") -> dict[str, Any]:
        if not text.strip():
            return {"error": "a post needs text"}
        path = f"/teams/{quote(team_id)}/channels/{quote(channel_id)}/messages"
        body: dict[str, Any] = {"body": {"contentType": "html" if html else "text", "content": text}}
        if reply_to:
            path += f"/{quote(reply_to)}/replies"
        elif subject:
            body["subject"] = subject
        posted = graph(account, "POST", path, json=body)
        return bare(posted, message_id=(posted.get("data") or {}).get("id"), url=(posted.get("data") or {}).get("webUrl")) if posted.get("ok") else posted

    add(teams_post_channel, "Post in a team's channel, or reply in a post's thread (reply_to: the post's id). Ids come from teams_list_channels.",
        {"team_id": {"type": "string"}, "channel_id": {"type": "string"}, "text": {"type": "string"}, "subject": {"type": "string"},
         "reply_to": {"type": "string"}, "html": {"type": "boolean"}}, ["team_id", "channel_id", "text"], "write")

    def teams_react(message_id: str, reaction: str = "like", chat_id: str = "", team_id: str = "", channel_id: str = "",
                    remove: bool = False, account: str = "") -> dict[str, Any]:
        if reaction not in _REACTIONS:
            return {"error": f"reaction is one of {', '.join(_REACTIONS)}"}
        base, refusal = where(chat_id, team_id, channel_id)
        if refusal:
            return {"error": refusal}
        action = "unsetReaction" if remove else "setReaction"
        return bare(graph(account, "POST", f"{base}/{quote(message_id)}/{action}", json={"reactionType": reaction}), reaction=reaction, removed=remove)

    add(teams_react, f"React to a message in a chat or a channel ({', '.join(_REACTIONS)}), or take your reaction back (remove).",
        {"message_id": {"type": "string"}, "reaction": {"type": "string", "enum": list(_REACTIONS)}, "chat_id": {"type": "string"},
         "team_id": {"type": "string"}, "channel_id": {"type": "string"}, "remove": {"type": "boolean"}}, ["message_id"], "write")
    return tools
