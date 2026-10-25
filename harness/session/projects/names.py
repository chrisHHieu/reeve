"""Names a person gives to project memories, as aliases over project keys."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

NAME_KINDS = ("memory",)
MAX_NAME_CHARS = 60


class ProjectNames:
    """One table in the session database. A name never moves data, it only points at a key."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS project_names (
                    kind TEXT NOT NULL, name TEXT NOT NULL, key TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (kind, name)
                )"""
            )
            self._conn.commit()

    def name_current(self, kind: str, name: str, key: str) -> dict[str, Any]:
        name = (name or "").strip()[:MAX_NAME_CHARS]
        if kind not in NAME_KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        if not name:
            raise ValueError("empty name")
        with self._lock:
            self._conn.execute(
                "INSERT INTO project_names (kind, name, key) VALUES (?, ?, ?) "
                "ON CONFLICT (kind, name) DO UPDATE SET key = excluded.key, "
                "last_used_at = datetime('now')",
                (kind, name, key),
            )
            self._conn.commit()
        return {"kind": kind, "name": name, "key": key}

    def resolve(self, kind: str, name: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT key FROM project_names WHERE kind = ? AND name = ?", (kind, name)
            ).fetchone()
        return row[0] if row else None

    def touch(self, kind: str, name: str) -> None:
        """Mark a binding as just used, so the most recently used come first."""
        with self._lock:
            self._conn.execute(
                "UPDATE project_names SET last_used_at = datetime('now') WHERE kind = ? AND name = ?",
                (kind, name),
            )
            self._conn.commit()

    def list(self, kind: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        query = (
            "SELECT name, key, last_used_at FROM project_names "
            "WHERE kind = ? ORDER BY last_used_at DESC, name"
        )
        args: list[Any] = [kind]
        if limit:
            query += " LIMIT ?"
            args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [{"name": row[0], "key": row[1], "last_used_at": row[2]} for row in rows]

    def forget(self, kind: str, name: str) -> bool:
        with self._lock:
            done = self._conn.execute(
                "DELETE FROM project_names WHERE kind = ? AND name = ?", (kind, name)
            )
            self._conn.commit()
        return done.rowcount > 0
