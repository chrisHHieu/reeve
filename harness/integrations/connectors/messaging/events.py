"""What passes between a messaging platform and the rest of the app.

An inbound message is a `MessageEvent` and says where it came from in a `SessionSource`. A
`target` token, `platform:chat_id[:thread]`, is the opaque handle the agent hands back to reply
to that place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class MessageType(str, Enum):
    TEXT = "text"
    COMMAND = "command"
    MEDIA = "media"


def format_target(platform: str, chat_id: str, thread_id: Optional[str] = None) -> str:
    base = f"{platform}:{chat_id}"
    return f"{base}:{thread_id}" if thread_id else base


def parse_target(target: str) -> tuple[str, str, Optional[str]]:
    """`platform:chat_id[:thread]` as (platform, chat_id, thread_id)."""
    parts = (target or "").split(":")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid target {target!r} (expected 'platform:chat_id[:thread]')")
    thread = ":".join(parts[2:])
    return parts[0], parts[1], thread or None


@dataclass
class SessionSource:
    """Where a message came from."""

    platform: str
    chat_id: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    chat_name: Optional[str] = None  # the channel or DM's display name, once resolved
    chat_type: str = "dm"  # "dm", "group" or "channel"
    thread_id: Optional[str] = None

    @property
    def target(self) -> str:
        return format_target(self.platform, self.chat_id, self.thread_id)

    def label(self) -> str:
        who = self.user_name or self.user_id or "?"
        where = {"dm": "DM", "group": "group", "channel": "channel"}.get(self.chat_type, self.chat_type)
        return f"{self.platform} {where} · {who}"


@dataclass
class MessageSource:
    """The structured sidecar of an inbound connector message, kept for display only.

    It is attached (as a plain dict, from `to_dict`) to the stored user message, and the app
    draws a rich card from it. The model-facing `content` stays the framed text, and the sidecar
    is removed before anything reaches a provider. `text` is the raw message, which is what the
    card shows, as opposed to the framed `content`.
    """

    connector: str  # the platform, such as "slack"
    kind: str  # "channel" or "dm"
    channel_id: str
    channel_name: str  # resolved, falling back to the id
    sender_id: str
    sender_name: str  # resolved, falling back to the id
    ts: float  # epoch seconds
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MessageEvent:
    text: str
    source: SessionSource
    message_id: Optional[str] = None
    message_type: MessageType = MessageType.TEXT
    reply_to_message_id: Optional[str] = None
    raw: Any = None
    #: The bot itself was @-mentioned. It is worked out from the platform's raw text when the
    #: message is mapped, because mention tokens are rewritten for display afterwards.
    mentions_me: bool = False

    def tagged_text(self) -> str:
        """The message as it enters the agent's thread: its source, a reply handle, and the text.

        The app's owner is answered with plain assistant text and no `send_message`; a messaging
        platform carries a handle that the agent passes back to `send_message`.
        """
        if self.source.platform == "gui":
            return f"[Owner, in the app]: {self.text}"
        return f"[{self.source.label()} | reply→{self.source.target}]: {self.text}"


@dataclass
class SendResult:
    ok: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


MessageHandler = Callable[[MessageEvent], Awaitable[None]]
