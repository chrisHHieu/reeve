"""The WhatsApp MCP server a session arms: the tools, forwarded to the daemon.

One runs per session, in the session's folder (the harness pins a connector's local server there),
and holds no connection of its own: each call goes to the daemon with that folder, which is how the
file rules know whose folder it is. The read tools declare `readOnlyHint`, and the connector gates
this server by its hints, so a read runs and a send, a reaction or a read receipt waits for the
person.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

#: The read tools: they run without asking.
READ_ONLY = (
    "whatsapp_status", "whatsapp_chats", "whatsapp_read", "whatsapp_search", "whatsapp_contacts", "whatsapp_download",
)


def forwarder(workspace: str) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    from .client import DaemonDown, request

    def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            return request("POST", "/call", {"tool": tool, "args": args, "workspace": workspace})
        except DaemonDown as exc:
            return {"error": str(exc)}

    return call


def build_server(call: Callable[[str, dict[str, Any]], dict[str, Any]]) -> Any:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    mcp = FastMCP("whatsapp_personal")
    read_only = ToolAnnotations(readOnlyHint=True)

    @mcp.tool(annotations=read_only)
    def whatsapp_status() -> dict:
        """Whether the person's WhatsApp is linked and connected, and how much history arrived."""
        return call("status", {})

    @mcp.tool(annotations=read_only)
    def whatsapp_chats(query: str = "", unread_only: bool = False, limit: int = 30) -> dict:
        """The person's WhatsApp chats, most recent first, each with its last message.
        `query` matches a chat's name the way the phone's search does (accents ignored)."""
        return call("chats", {"query": query, "unread_only": unread_only, "limit": limit})

    @mcp.tool(annotations=read_only)
    def whatsapp_read(chat: str, limit: int = 40, before: Optional[int] = None, around: str = "") -> dict:
        """Messages of one chat in time order. `chat` is a name, a phone number or a chat id.
        `before` (unix seconds) pages back; `around` (a message id) shows that message in context.
        Each message carries its id, used to reply, react or mark it read."""
        return call("read", {"chat": chat, "limit": limit, "before": before, "around": around})

    @mcp.tool(annotations=read_only)
    def whatsapp_search(query: str, chat: str = "", sender: str = "", since: Optional[int] = None,
                        until: Optional[int] = None, limit: int = 20) -> dict:
        """Messages holding every word of `query` (accents ignored), newest first, across all chats
        or one; `sender` narrows to one person, `since`/`until` to unix seconds."""
        return call("search", {"query": query, "chat": chat, "sender": sender, "since": since, "until": until, "limit": limit})

    @mcp.tool(annotations=read_only)
    def whatsapp_contacts(query: str = "", limit: int = 30) -> dict:
        """The person's WhatsApp contacts with their numbers; `query` matches names or digits."""
        return call("contacts", {"query": query, "limit": limit})

    @mcp.tool(annotations=read_only)
    def whatsapp_download(chat: str, message_id: str) -> dict:
        """Save a message's photo, video, voice note or document into this session's `whatsapp/`
        folder, to read, view or transcribe it."""
        return call("download", {"chat": chat, "message_id": message_id})

    @mcp.tool()
    def whatsapp_send(chat: str, text: str = "", file: str = "", reply_to: str = "", voice: bool = False) -> dict:
        """Send a message as the person, to someone they already talk to. `reply_to` (a message id)
        quotes that message. `file` is a path in this session's folder: images and videos show
        inline, `voice=true` sends an .ogg/Opus file as a voice note, anything else is a document.
        Each send is approved by the person; write as they would."""
        return call("send", {"chat": chat, "text": text, "file": file, "reply_to": reply_to, "voice": voice})

    @mcp.tool()
    def whatsapp_react(chat: str, message_id: str, emoji: str) -> dict:
        """React to a message with one emoji; an empty emoji takes the reaction back."""
        return call("react", {"chat": chat, "message_id": message_id, "emoji": emoji})

    @mcp.tool()
    def whatsapp_mark_read(chat: str, message_ids: list[str]) -> dict:
        """Mark received messages as read (the sender sees blue ticks)."""
        return call("mark_read", {"chat": chat, "message_ids": message_ids})

    return mcp


def main() -> int:
    build_server(forwarder(os.getcwd())).run()
    return 0
