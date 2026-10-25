"""How long the linked device keeps messages, and how much it keeps.

The person chooses on the connector's page: 30 days, 90 days, a year or forever, 90 until they
choose, written to `keep.json` beside the store. The daemon drops older messages when it starts,
after each history chunk and hourly; the page reads what is kept and can clear it without
unlinking. Chats and contacts stay, being the names a send and a search by name need.

`KeptMessages` is the part of `Store` that does this, apart so the store stays one file's worth.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

#: How long messages are kept, in days, until the person chooses; None keeps every message.
KEEP_FILE = "keep.json"
DEFAULT_KEEP_DAYS = 90
KEEP_CHOICES: tuple[Optional[int], ...] = (30, 90, 365, None)
DAY_S = 86_400


def keep_days(folder: Path) -> Optional[int]:
    """The account's choice of how long messages stay; a missing or unreadable file is the default."""
    try:
        days = json.loads((folder / KEEP_FILE).read_text(encoding="utf-8")).get("days", DEFAULT_KEEP_DAYS)
    except (OSError, ValueError, AttributeError):
        return DEFAULT_KEEP_DAYS
    return days if days in KEEP_CHOICES else DEFAULT_KEEP_DAYS


def set_keep_days(folder: Path, days: Optional[int]) -> None:
    if days not in KEEP_CHOICES:
        raise ValueError(f"keep messages for one of {KEEP_CHOICES} days (None keeps them all), not {days!r}")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (KEEP_FILE + ".tmp")
    tmp.write_text(json.dumps({"days": days}), encoding="utf-8")
    tmp.replace(folder / KEEP_FILE)


class KeptMessages:
    """What `Store` keeps of a message's lifetime. It uses the store's connection, lock and path."""

    _db: sqlite3.Connection
    _lock: threading.Lock
    path: Path

    def stats(self) -> dict[str, Any]:
        """What this device keeps: how many messages, in how many chats, since when, and the
        file's size on disk."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT chat) AS chats, MIN(ts) AS oldest FROM messages"
            ).fetchone()
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return {"messages": row["n"], "chats": row["chats"], "oldest": row["oldest"], "bytes": size}

    def forget_before(self, ts: int, *, compact: bool = False) -> int:
        """Drop every message older than `ts` with the reactions to it. Returns how many went.
        Chats and contacts stay: they are names, which a send and a search by name still need."""
        with self._lock, self._db:
            gone = self._db.execute("DELETE FROM messages WHERE ts < ?", (int(ts),)).rowcount
            self._drop_orphan_reactions()
        if compact and gone:
            self._compact()
        return gone

    def forget_messages(self) -> int:
        """Drop every kept message and reaction, and give the space back to the disk."""
        with self._lock, self._db:
            gone = self._db.execute("DELETE FROM messages").rowcount
            self._db.execute("DELETE FROM reactions")
        self._compact()
        return gone

    def keep_for(self, days: Optional[int], *, now: Optional[float] = None, compact: bool = False) -> int:
        """Apply a keep choice: messages older than `days` go; None keeps them all."""
        if days is None:
            return 0
        return self.forget_before(int((time.time() if now is None else now) - days * DAY_S), compact=compact)

    def _drop_orphan_reactions(self) -> None:
        self._db.execute(
            "DELETE FROM reactions WHERE NOT EXISTS (SELECT 1 FROM messages m "
            "WHERE m.chat = reactions.chat AND m.id = reactions.target_id)"
        )

    def _compact(self) -> None:
        # Deleted rows leave free pages the file keeps; VACUUM returns them. It cannot run while
        # another connection is mid-write, and the space is then reused instead: not an error.
        with self._lock:
            try:
                self._db.execute("VACUUM")
            except sqlite3.OperationalError:
                pass
