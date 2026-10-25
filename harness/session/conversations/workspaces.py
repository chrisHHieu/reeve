"""The folders sessions have used, and the sessions that are children of others."""

from __future__ import annotations

import os


class WorkspacesMixin:
    def touch_workspace(self, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO workspaces (path, last_used) VALUES (?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
                (path,),
            )
            self._conn.commit()

    def recent_workspaces(self, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM workspaces ORDER BY last_used DESC LIMIT ?", (limit,)
            ).fetchall()
        return [row["path"] for row in rows]

    def canonicalize_workspaces(self) -> None:
        """Rewrite every stored folder to its real path, and merge the folders that were the same
        place under two spellings, keeping the latest use."""
        with self._lock:
            for (workspace,) in self._conn.execute(
                "SELECT DISTINCT workspace FROM sessions WHERE workspace IS NOT NULL"
            ).fetchall():
                real = os.path.realpath(workspace)
                if real != workspace:
                    self._conn.execute("UPDATE sessions SET workspace = ? WHERE workspace = ?", (real, workspace))
            latest: dict[str, str] = {}
            for path, last in self._conn.execute("SELECT path, last_used FROM workspaces").fetchall():
                real = os.path.realpath(path)
                if real not in latest or (last or "") > latest[real]:
                    latest[real] = last
            self._conn.execute("DELETE FROM workspaces")
            for path, last in latest.items():
                self._conn.execute("INSERT OR REPLACE INTO workspaces (path, last_used) VALUES (?, ?)", (path, last))
            self._conn.commit()

    def children_of(self, parent_id: str) -> list[str]:
        """The ids of a session's child sessions, in the order they were created.

        It is a query of its own and not a filtered `list()`. The delete cascade and the runs registry
        need the children while every listing hides them, and `rowid` is the creation order where
        `updated_at`, at second granularity, ties."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id FROM sessions "
                "WHERE parent_session = ? AND COALESCE(kind, 'root') = 'subagent' ORDER BY rowid",
                (parent_id,),
            ).fetchall()
        return [row["session_id"] for row in rows]

    def is_child(self, session_id: str) -> bool:
        """Whether the id names a session a `task` tool spawned. It reads one column and not the whole
        transcript, because the manager's `mark_idle` guards run for every session on every turn and
        none of them should pay for a transcript to learn a boolean."""
        with self._lock:
            row = self._conn.execute("SELECT kind FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return row is not None and (row["kind"] or "root") == "subagent"
