"""The inbox store: items on disk, each resolved once, with a wait for the answer.

An item goes pending, then resolved, and only once: the first answer wins and later ones do
nothing. That makes it safe to answer from any surface (the app, a chat connector, the composer
after a session is resumed).
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .items import (
    KIND_APPROVAL,
    KIND_NOTIFICATION,
    KIND_PLAN,
    KIND_QUESTION,
    KIND_TOOL,
    STATE_PENDING,
    STATE_RESOLVED,
    VIS_INBOX,
    InboxItem,
    now,
)


class InboxStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._items: dict[str, InboxItem] = {}
        self._waiters: dict[str, asyncio.Event] = {}
        self._load()

    # -- disk ------------------------------------------------------------------------------

    def _load(self) -> None:
        if not (self.path and self.path.is_file()):
            return
        for raw in json.loads(self.path.read_text(encoding="utf-8")).get("items", []):
            item = InboxItem(**raw)
            self._items[item.id] = item

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": [asdict(item) for item in self._items.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- adding ----------------------------------------------------------------------------

    def add(
        self,
        session_id: str,
        kind: str,
        title: str,
        *,
        body: str = "",
        inbox: str = "default",
        visibility: str = VIS_INBOX,
        data: Optional[dict[str, Any]] = None,
        options=None,
        allow_text: bool = True,
        multi: bool = False,
        header: str = "",
        questions=None,
        tool_call_id: Optional[str] = None,
    ) -> InboxItem:
        # A resumed turn raises the same prompt again; it gets the item it already has, answered
        # or not, instead of asking the person twice.
        if tool_call_id:
            existing = self.for_tool_call(session_id, tool_call_id)
            if existing is not None:
                return existing
        item = InboxItem(
            id=uuid.uuid4().hex,
            session_id=session_id,
            kind=kind,
            title=title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=dict(data or {}),
            options=list(options or []),
            allow_text=bool(allow_text),
            multi=bool(multi),
            header=str(header or ""),
            questions=list(questions or []),
            tool_call_id=tool_call_id,
        )
        with self._lock:
            self._items[item.id] = item
            self._save()
        return item

    def for_tool_call(self, session_id: str, tool_call_id: str) -> Optional[InboxItem]:
        for item in self._items.values():
            if item.session_id == session_id and item.tool_call_id == tool_call_id:
                return item
        return None

    def _add_with_data(self, kind: str, session_id, title, *, body="", inbox="default",
                       visibility=VIS_INBOX, data=None, tool_call_id=None) -> InboxItem:
        return self.add(
            session_id, kind, title, body=body, inbox=inbox, visibility=visibility,
            data=data, tool_call_id=tool_call_id,
        )

    def add_approval(self, session_id, title, **fields) -> InboxItem:
        """`data` carries the automation run an approval belongs to (task id and title, and the
        target a standing approval would cover), which the card needs to offer "allow every time"."""
        return self._add_with_data(KIND_APPROVAL, session_id, title, **fields)

    def add_plan(self, session_id, title, **fields) -> InboxItem:
        return self._add_with_data(KIND_PLAN, session_id, title, **fields)

    def add_tool_request(self, session_id, title, **fields) -> InboxItem:
        return self._add_with_data(KIND_TOOL, session_id, title, **fields)

    def add_question(
        self, session_id, title, *, body="", inbox="default", visibility=VIS_INBOX, options=None,
        allow_text=True, multi=False, header="", questions=None, tool_call_id=None,
    ) -> InboxItem:
        return self.add(
            session_id, KIND_QUESTION, title, body=body, inbox=inbox, visibility=visibility,
            options=options, allow_text=allow_text, multi=multi, header=header,
            questions=questions, tool_call_id=tool_call_id,
        )

    def add_notification(self, session_id, title, *, body="", inbox="default", visibility=VIS_INBOX) -> InboxItem:
        return self.add(session_id, KIND_NOTIFICATION, title, body=body, inbox=inbox, visibility=visibility)

    # -- reading ---------------------------------------------------------------------------

    def get(self, item_id: str) -> Optional[InboxItem]:
        return self._items.get(item_id)

    def list(
        self,
        *,
        session_id: Optional[str] = None,
        state: Optional[str] = None,
        inbox: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> list[InboxItem]:
        wanted = {"session_id": session_id, "state": state, "inbox": inbox, "visibility": visibility}
        items = [
            item
            for item in self._items.values()
            if all(value is None or getattr(item, field) == value for field, value in wanted.items())
        ]
        return sorted(items, key=lambda item: item.created_at)

    def pending(self, session_id: Optional[str] = None) -> list[InboxItem]:
        return self.list(session_id=session_id, state=STATE_PENDING)

    # -- answering -------------------------------------------------------------------------

    def resolve(self, item_id: str, resolution: str) -> bool:
        """Answer an item. Only the first answer counts (later ones return False), and it releases
        whoever is waiting on the item."""
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.state == STATE_RESOLVED:
                return False
            item.state = STATE_RESOLVED
            item.resolution = resolution
            item.resolved_at = now()
            self._save()
        waiter = self._waiters.get(item_id)
        if waiter is not None:
            waiter.set()
        return True

    def resolve_session(self, session_id: str, resolution: str = "session deleted") -> int:
        """Close what a deleted session left pending, since nobody can answer it any more. Returns
        how many items were closed."""
        return sum(1 for item in self.pending(session_id) if self.resolve(item.id, resolution))

    async def wait(self, item_id: str) -> str:
        """The answer to an item, waiting for it if there is none yet. The approver parks the
        agent here until a person answers."""
        item = self._items.get(item_id)
        if item is not None and item.state == STATE_RESOLVED:
            return item.resolution or ""
        await self._waiters.setdefault(item_id, asyncio.Event()).wait()
        answered = self._items.get(item_id)
        return (answered.resolution if answered else "") or ""

    def reconcile_on_resume(self, session_id: str) -> dict:
        """What to show when a person takes a session back: its pending items, to be answered in
        one place from now on, and a recap of what was answered while they were away."""
        return {
            "pending": [asdict(item) for item in self.pending(session_id)],
            "recap": [asdict(item) for item in self.list(session_id=session_id, state=STATE_RESOLVED)],
        }
