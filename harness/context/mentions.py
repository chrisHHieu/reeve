"""Which conversation answers which Slack thread, for the mention router.

When @Reeve is mentioned in a channel no conversation follows, the router starts one that owns
that thread and replies in it. This file is how the next mention in the same thread finds that
conversation instead of starting another: one record a thread, keyed by its target string
(`"slack:C0123:1700….000100"`, or `"slack:T…/C…:ts"` through the relay). The key is exactly what
the conversation passes to `send_message` and what its standing grant names, so a single string
serves the lookup, the delivery and the permission.

It is also where that grant lives on: each time an engine is built, `get_engine` derives
`permissions.task_rules` from it, so replying in the thread stays approved across a restart.
Deleting the conversation deletes its records, as it does its subscriptions.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MentionThread:
    thread_target: str  # "platform:chat_id:thread_ts": where replies go and what the grant names
    session_id: str
    channel: str  # "platform:chat_id", the thread left off; for clean-up and for reading the file


class MentionSessionStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._by_target: dict[str, MentionThread] = {}  # in the order threads were first seen
        if self.path is not None and self.path.is_file():
            for stored in json.loads(self.path.read_text(encoding="utf-8")).get("threads", []):
                thread = MentionThread(**stored)
                self._by_target.setdefault(thread.thread_target, thread)

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"threads": [asdict(t) for t in self._by_target.values()]}, indent=2), encoding="utf-8")

    def _owned_by(self, session_id: str) -> list[str]:
        return [target for target, thread in self._by_target.items() if thread.session_id == session_id]

    def set(self, thread_target: str, session_id: str, channel: str) -> MentionThread:
        """Record the thread, or hand it to another conversation: a new one started after the old
        one was deleted takes the record over."""
        with self._lock:
            thread = self._by_target.setdefault(thread_target, MentionThread(thread_target, session_id, channel))
            thread.session_id, thread.channel = session_id, channel
            self._save()
            return thread

    def remove_session(self, session_id: str) -> None:
        """A deleted conversation lets go of every thread it had."""
        with self._lock:
            owned = self._owned_by(session_id)
            for target in owned:
                self._by_target.pop(target)
            if owned:
                self._save()

    def get(self, thread_target: str) -> Optional[str]:
        thread = self._by_target.get(thread_target)
        return thread.session_id if thread is not None else None

    def targets_for(self, session_id: str) -> list[str]:
        """The threads a conversation answers: the grants to give it again on a rebuild."""
        return self._owned_by(session_id)

    def all(self) -> list[MentionThread]:
        return [*self._by_target.values()]
