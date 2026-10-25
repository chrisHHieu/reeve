"""What the inbox holds: the kinds of item, their states, and the record itself."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

KIND_APPROVAL = "approval"
KIND_QUESTION = "question"
KIND_NOTIFICATION = "notification"
KIND_DIRECTORY = "directory"  # the agent asks for a folder to be granted
KIND_PLAN = "plan"  # the agent presents a plan to be approved
KIND_TOOL = "tool"  # the agent asks for a missing command-line tool to be installed

STATE_PENDING = "pending"
STATE_RESOLVED = "resolved"

# Where a pending prompt was raised. A session with a window raises it inline: the prompt is
# parked on the server and sent again on reconnect. A run with no window (an automation, a
# self-wake) leaves the default. The record is the same either way, and nothing reads the
# difference any more: both fields stay so a store written by an older version still loads.
VIS_INLINE = "inline"
VIS_INBOX = "inbox"

_ARG_LIMIT = 80


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def args_preview(arguments: Optional[dict], *, limit: int = 240) -> str:
    """One line saying what a tool call would do, for the body of an approval card, so the card
    shows the path or content and not just the tool's name."""
    shown: list[str] = []
    for key, value in (arguments or {}).items():
        text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
        text = " ".join(str(text).split())
        if len(text) > _ARG_LIMIT:
            text = text[: _ARG_LIMIT - 1] + "…"
        shown.append(f"{key}: {text}")
    line = " · ".join(shown)
    return line[: limit - 1] + "…" if len(line) > limit else line


@dataclass
class InboxItem:
    id: str
    session_id: str
    kind: str
    title: str
    body: str = ""
    state: str = STATE_PENDING
    resolution: Optional[str] = None  # approval: allow / deny / always; question: the answer text
    inbox: str = "default"  # the named inbox this item is delivered to
    created_at: str = field(default_factory=now)
    resolved_at: Optional[str] = None
    visibility: str = VIS_INBOX
    # The tool call this prompt blocks. It is saved so a restart can rebuild the wait and carry
    # the turn on, and it makes an item unique per (session_id, tool_call_id).
    tool_call_id: Optional[str] = None
    # Question fields. An option is a plain string or a {label, description, recommended, preview}
    # object; items saved with plain strings stay valid. `allow_text` keeps a typed answer possible
    # when options exist.
    options: list = field(default_factory=list)
    allow_text: bool = True
    multi: bool = False  # more than one option may be chosen
    header: str = ""  # the short chip on the card
    # A grouped form holds up to 4 {question, header, options, allow_text, multi} entries shown as
    # a stepper. The singular title and options above then describe the first one, so a surface that
    # knows nothing of groups still shows something sensible, and the resolution is a JSON object
    # string keyed by header or question.
    questions: list[dict] = field(default_factory=list)
    # Per-kind payload: directory (suggested path, writable), plan (its text), approval (the
    # automation run it belongs to, for the "allow every time" choice).
    data: dict[str, Any] = field(default_factory=dict)
