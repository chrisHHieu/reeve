"""A record of what had nowhere to go: inbound messages no session was set to handle, and background
turns that failed, so neither disappears without a trace.

Two things write here: a direct message (or any non-channel message) with no session designated to
take it, and a background turn (a channel delivery or a self-wake) that ended in an ERROR event.
The file is capped and keeps the newest. It is for looking at in the app, not a queue: nothing is
sent again.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .json_file import read_json, write_json


@dataclass
class UnroutedItem:
    source: str  # where it came from: "slack:D123", or a session id
    sender: str  # who sent it; "-" when that does not apply, as for a failed turn
    text: str  # the message, or the instruction that failed
    reason: str  # why it ended up here
    ts: float = field(default_factory=time.time)


class UnroutedStore:
    """The last `cap` of them, oldest first in the file."""

    def __init__(self, path: Optional[str | Path] = None, *, cap: int = 200) -> None:
        self.path = Path(path) if path else None
        self._cap = cap
        self._guard = threading.Lock()
        self._items = [UnroutedItem(**stored) for stored in read_json(self.path).get("items", [])]

    def record(self, source: str, sender: str, text: str, reason: str) -> UnroutedItem:
        """Keep one; past the cap the oldest goes."""
        kept = UnroutedItem(source=source or "?", sender=sender or "-", text=text or "", reason=reason)
        with self._guard:
            self._items = (self._items + [kept])[-self._cap :]
            write_json(self.path, {"items": [asdict(item) for item in self._items]})
        return kept

    def list(self, n: int = 100) -> list[dict]:
        """Newest first, for the panel in the app."""
        return [asdict(item) for item in reversed(self._items)][: max(1, n)]
