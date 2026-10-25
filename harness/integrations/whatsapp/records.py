"""What one WhatsApp message is, as the store keeps it: no protobuf, no library types.

A JID is WhatsApp's address: `<number>@s.whatsapp.net` for a person, `<id>@g.us` for a group,
`<id>@lid` for a person known only by a hidden id. It is kept as the string WhatsApp prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

PERSON = "s.whatsapp.net"
GROUP = "g.us"
HIDDEN = "lid"

#: What a message is. `edit`, `revoke` and `reaction` change another message and are folded
#: into it by the store rather than shown as messages of their own.
KINDS = (
    "text", "image", "video", "audio", "voice", "document", "sticker", "location", "contact",
    "poll", "call", "system", "edit", "revoke", "reaction", "other",
)


def is_group(jid: str) -> bool:
    return jid.endswith("@" + GROUP)


def number_of(jid: str) -> str:
    """The phone number a person's JID carries, or "" (a group, a hidden id)."""
    user, _, server = jid.partition("@")
    return user.split(":", 1)[0] if server == PERSON else ""


def bare(jid: str) -> str:
    """A JID without its device part: `84901234567:12@s.whatsapp.net` is the same person as
    `84901234567@s.whatsapp.net`, and the store keys people by the second."""
    user, at, server = jid.partition("@")
    return f"{user.split(':', 1)[0]}{at}{server}" if at else jid


@dataclass
class Message:
    chat: str
    id: str
    sender: str
    from_me: bool
    ts: int
    kind: str = "text"
    text: str = ""
    #: The message this one quotes (a reply), or the one it edits, revokes or reacts to.
    target_id: str = ""
    sender_name: str = ""
    #: For media: mimetype, file name, size, seconds; enough to say what it is and to fetch it.
    media: Optional[dict[str, Any]] = None
    mentions: list[str] = field(default_factory=list)


@dataclass
class Chat:
    jid: str
    name: str = ""
    unread: Optional[int] = None
    archived: Optional[bool] = None
    pinned: Optional[bool] = None
    muted_until: Optional[int] = None
    last_ts: Optional[int] = None


@dataclass
class Contact:
    jid: str
    #: The name the person saved in their phone's address book.
    name: str = ""
    #: The name the contact set for themselves.
    push_name: str = ""
