"""The inbox: what other agents need from a person, kept in one queue across sessions.

An item is an approval, a question, a notification, a folder request, a plan or a tool request.
Chat connectors and the app are different ways to read and answer the same items.
"""

from __future__ import annotations

from .approver import inbox_approver
from .items import (
    KIND_APPROVAL,
    KIND_DIRECTORY,
    KIND_NOTIFICATION,
    KIND_PLAN,
    KIND_QUESTION,
    KIND_TOOL,
    STATE_PENDING,
    STATE_RESOLVED,
    VIS_INBOX,
    VIS_INLINE,
    InboxItem,
    args_preview,
)
from .store import InboxStore

__all__ = [
    "KIND_APPROVAL", "KIND_DIRECTORY", "KIND_NOTIFICATION", "KIND_PLAN", "KIND_QUESTION", "KIND_TOOL",
    "STATE_PENDING", "STATE_RESOLVED", "VIS_INBOX", "VIS_INLINE",
    "InboxItem", "InboxStore", "args_preview", "inbox_approver",
]
