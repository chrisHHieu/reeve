"""Columns a turn's save must not touch, and the flags and titles around a session.

The per-turn save rebuilds the record without some of this, and letting the rebuild win would erase
it, so each of these has an `UPDATE` of its own.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class AttributesMixin:
    def _set_column(self, session_id: str, column: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(f"UPDATE sessions SET {column} = ? WHERE session_id = ?", (value, session_id))
            self._conn.commit()

    def set_team(self, session_id: str, team: dict) -> None:
        """Save the session's team tie apart from the turn's save. The upsert never touches `team`,
        because a per-turn save rebuilds the record without it, and letting the rebuild win detached
        workers from their lead's sidebar entry the moment they ran a turn."""
        self._set_column(session_id, "team", json.dumps(team or {}))

    def set_bindings(self, session_id: str, bindings: dict) -> None:
        """Save the session's project bindings apart from the turn's save, the shape of `team`. The
        per-turn upsert never touches this column, so a rebuild cannot unbind a session."""
        self._set_column(session_id, "bindings", json.dumps(bindings or {}))

    def set_revert(self, session_id: str, revert: dict) -> None:
        """Save the message-revert marker apart from the turn's save. The per-turn upsert never
        touches this column, so the next turn cannot clear a staged revert, and staging or clearing
        it must not wait for a turn to land. An empty dict clears it."""
        self._set_column(session_id, "revert", json.dumps(revert or {}))

    def set_environment(
        self, session_id: str, environment_id: Optional[str], workspace: Optional[str] = None
    ) -> bool:
        """Bind the session to an SSH environment, or clear it with None.

        It is its own `UPDATE`, as `set_revert` and `set_team` are, because the per-turn save
        rebuilds the record without the binding and must not clear it. The row is created when it is
        missing, so the binding can land before the first turn (a client picks a host on the welcome
        screen). Clearing means local. `workspace` is rewritten only when given, so a local switch
        that carries no path leaves the current one alone."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, updated_at) VALUES (?, CURRENT_TIMESTAMP)",
                (session_id,),
            )
            if workspace:
                done = self._conn.execute(
                    "UPDATE sessions SET environment_id = ?, workspace = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE session_id = ?",
                    (environment_id or None, workspace, session_id),
                )
            else:
                done = self._conn.execute(
                    "UPDATE sessions SET environment_id = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                    (environment_id or None, session_id),
                )
            self._conn.commit()
        return done.rowcount > 0

    def set_extra_roots(self, session_id: str, extra_roots: list[dict]) -> None:
        """Save just the folders the session was given, apart from its messages. It is used when a
        person adds or removes a folder, which can happen with no active engine."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET extra_roots = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (json.dumps(extra_roots or []), session_id),
            )
            self._conn.commit()

    def set_origin(self, session_id: str, origin: str, origin_label: str = "") -> bool:
        """Mark where a spawned session came from. It is set once at spawn, and `save()` never names
        these columns, so per-turn saves cannot clobber them (the way `pinned` works)."""
        with self._lock:
            done = self._conn.execute(
                "UPDATE sessions SET origin = ?, origin_label = ? WHERE session_id = ?",
                (origin, origin_label or None, session_id),
            )
            self._conn.commit()
        return done.rowcount > 0

    def set_flags(self, session_id: str, *, pinned: Optional[bool] = None, archived: Optional[bool] = None) -> bool:
        """Set the pin and archive flags without touching `updated_at`, so pinning does not reorder
        the list."""
        assignments, args = [], []
        for name, value in (("pinned", pinned), ("archived", archived)):
            if value is not None:
                assignments.append(f"{name} = ?")
                args.append(1 if value else 0)
        if not assignments:
            return False
        with self._lock:
            done = self._conn.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id = ?", (*args, session_id)
            )
            self._conn.commit()
        return done.rowcount > 0

    def rename(self, session_id: str, title: str) -> bool:
        clean = " ".join((title or "").split())[:120]
        if not clean:
            return False
        with self._lock:
            # `renamed = 1` makes the manual title final. Auto-titling skips the session, and
            # `display_title` ignores any generated title already there.
            done = self._conn.execute(
                "UPDATE sessions SET title = ?, renamed = 1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (clean, session_id),
            )
            self._conn.commit()
        return done.rowcount > 0

    def set_auto_title(self, session_id: str, title: str) -> bool:
        """Save a generated title. It has a column of its own and never uses `title`, so a manual
        rename, past or future, always wins. It leaves `updated_at` alone, since a title that lands
        after the turn must not reorder the session list."""
        clean = " ".join((title or "").split())[:60]
        if not clean:
            return False
        with self._lock:
            done = self._conn.execute(
                "UPDATE sessions SET auto_title = ? WHERE session_id = ? AND renamed = 0", (clean, session_id)
            )
            self._conn.commit()
        return done.rowcount > 0

    def title_state(self, session_id: str) -> Optional[dict]:
        """What the auto-title guard needs: whether the person renamed, and whether a generated title
        exists. None when the session has no row yet."""
        with self._lock:
            row = self._conn.execute(
                "SELECT renamed, auto_title FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return {"renamed": bool(row["renamed"]), "auto_title": row["auto_title"]}
