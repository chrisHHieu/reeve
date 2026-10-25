"""Messages from senders who are not allowed yet, kept and not lost.

The gateway drops a message from a sender who is not on the allow-list; the list is closed by
default. Dropping it silently made the first contact clumsy: the sender had to write once to
appear under "Recent senders", be allowed, and then write again. A parked message is shown on the
connector page and resolved in one step: dismiss it, allow the sender, or allow the sender and
deliver the original message, with nothing to send twice.

It is stored as JSON with a cap, like the unrouted store. Unlike that one it is a queue, because
allow-and-deliver feeds the parked message back through the normal inbound path.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ParkedMessage:
    platform: str  # "slack", "telegram", …
    chat_id: str
    user_id: str
    text: str
    chat_name: Optional[str] = None  # resolved, falling back to the id
    user_name: Optional[str] = None  # resolved, falling back to the id
    chat_type: str = "channel"  # "channel", "group" or "dm"
    thread_id: Optional[str] = None
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class ParkedStore:
    def __init__(self, path: Optional[str | Path] = None, *, cap: int = 100) -> None:
        self.path = Path(path) if path else None
        self._cap = cap
        self._lock = threading.Lock()
        self._items: list[ParkedMessage] = []
        self._load()

    def _load(self) -> None:
        if not (self.path and self.path.is_file()):
            return
        try:
            known = set(ParkedMessage.__dataclass_fields__)
            rows = json.loads(self.path.read_text(encoding="utf-8")).get("items", [])
            # A row parked before the relay was removed carries a `team_id`, which is ignored.
            self._items = [ParkedMessage(**{k: v for k, v in row.items() if k in known}) for row in rows]
        except (OSError, ValueError, TypeError):
            self._items = []  # a corrupt file must never stop the server from starting

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"items": [asdict(i) for i in self._items]}, indent=2), encoding="utf-8")
        except OSError:
            pass  # saving is best effort; memory stays the authority

    def park(self, **fields) -> ParkedMessage:
        item = ParkedMessage(**fields)
        with self._lock:
            self._items = [*self._items, item][-self._cap:]
            self._save()
        return item

    def list(self, platform: Optional[str] = None) -> list[dict]:
        """Newest first."""
        with self._lock:
            return [asdict(i) for i in reversed(self._items) if platform is None or i.platform == platform]

    def pop(self, item_id: str) -> Optional[ParkedMessage]:
        with self._lock:
            for position, item in enumerate(self._items):
                if item.id == item_id:
                    del self._items[position]
                    self._save()
                    return item
        return None
