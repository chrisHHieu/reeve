"""`ConversationStore`: session storage kept in files and shared by every surface.

Under a base folder (by default `~/.config/Reeve/`):

    reeve.db                   SQLite index: one row per session, plus the workspaces used
    conversations/<id>.jsonl   the append-only message log, one file per conversation

A turn appends only its new messages and never rewrites history. A row that older versions wrote with
its messages inline is moved to a `.jsonl` the first time it is loaded or saved.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from ..schema import open_schema
from ..sessions import SessionRecord
from ..transcript_file import TranscriptFile
from .attributes import AttributesMixin
from .rows import PLACEHOLDER_TITLE, inline_messages_of, mapping_of, roots_of, summary_of, title_from, column
from .workspaces import WorkspacesMixin

__all__ = ["ConversationStore", "title_from"]

# The columns an upsert writes on a first save. `parent_session`, `seed_length`, `kind`, `revert`
# and `environment_id` are insert-only and never in the DO UPDATE. The per-turn save rebuilds the
# record without its lineage, and letting the rebuild win would erase a fork's lineage on its first
# turn, demote a child session to a visible root (`kind` defaults to "root" on that rebuild), clear
# a staged message revert, or unbind a remote session from its host. `set_team` documents the same
# trap for `team`.
_UPSERT = """
INSERT INTO sessions (session_id, workspace, model, mode, title, agent, n_msgs, messages, extra_roots, grants, compaction, team, bindings, thinking, parent_session, seed_length, kind, revert, plan, environment_id, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(session_id) DO UPDATE SET
    workspace = excluded.workspace, model = excluded.model, mode = excluded.mode,
    -- The first snapshot wins, except over the display placeholder that an older binding seed
    -- saved ("New session"). That string is not a title, and without this a bound session's real
    -- first turn could never replace it. A manual rename (renamed=1) is always final.
    title = CASE
        WHEN COALESCE(sessions.title, '') = ''
            OR (sessions.renamed = 0 AND sessions.title = 'New session')
        THEN COALESCE(excluded.title, sessions.title)
        ELSE sessions.title
    END,
    agent = excluded.agent,
    n_msgs = excluded.n_msgs, messages = NULL, extra_roots = excluded.extra_roots,
    grants = excluded.grants, compaction = excluded.compaction,
    thinking = excluded.thinking,
    plan = excluded.plan,
    updated_at = CURRENT_TIMESTAMP
"""


class ConversationStore(AttributesMixin, WorkspacesMixin):
    def __init__(self, base_dir: str | Path, *, db_path: str | Path | None = None) -> None:
        """`db_path` lets the caller that owns the state folder decide the database file; without
        it the file is `reeve.db` under `base_dir`."""
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        self.conv_dir = self.base / "conversations"
        self.conv_dir.mkdir(exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.base / "reeve.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        open_schema(self._conn)
        self._backfill_counts()

    # -- the files -----------------------------------------------------------------------

    def _file(self, sid: str) -> Path:
        return self.conv_dir / f"{sid}.jsonl"

    def transcript_path(self, sid: str) -> Path:
        """Where a session's transcript lives, for a reader that scans it without loading the
        session (the work queue reads only its receipts)."""
        return self._file(sid)

    def _transcript(self, sid: str) -> TranscriptFile:
        return TranscriptFile(self._file(sid))

    def _read_jsonl(self, sid: str) -> Optional[list[dict]]:
        return self._transcript(sid).read()

    def _append(self, sid: str, messages: list[dict]) -> None:
        self._transcript(sid).append(messages)

    def _inline_messages(self, sid: str) -> list[dict]:
        """The messages an older version kept inline in the row, or an empty list."""
        row = self._conn.execute("SELECT messages FROM sessions WHERE session_id = ?", (sid,)).fetchone()
        return inline_messages_of(row["messages"] if row else None)

    def _backfill_counts(self) -> None:
        """Once per session: move any inline blob into a `.jsonl` and keep the title and message
        count in the index. Rows that are already migrated are skipped on later starts."""
        with self._lock:
            rows = self._conn.execute("SELECT session_id, messages, n_msgs, title, renamed FROM sessions").fetchall()
            for row in rows:
                sid = row["session_id"]
                # An older remote-binding seed saved the display placeholder as a real title. That
                # row has to be read again so the first-line snapshot can replace it. A manual rename
                # is never a placeholder, even when the text is the same.
                placeholder = row["title"] == PLACEHOLDER_TITLE and not row["renamed"]
                if self._file(sid).exists() and row["title"] and row["n_msgs"] and not placeholder:
                    continue  # already migrated
                if self._file(sid).exists():
                    messages = self._read_jsonl(sid) or []
                elif row["messages"]:
                    messages = inline_messages_of(row["messages"])
                    if messages:
                        self._append(sid, messages)
                    self._conn.execute("UPDATE sessions SET messages = NULL WHERE session_id = ?", (sid,))
                else:
                    messages = []
                # A row with no messages has no real title yet. The display placeholder is never
                # saved, or the first turn's snapshot could not land: the upsert treats it as
                # replaceable and `display_title` would treat it as a fact.
                snapshot = row["title"] or (title_from(messages) if messages else None)
                if placeholder:
                    snapshot = title_from(messages) if messages else None
                self._conn.execute(
                    "UPDATE sessions SET n_msgs = ?, title = ? WHERE session_id = ?", (len(messages), snapshot, sid)
                )
            self._conn.commit()

    # -- saving and loading --------------------------------------------------------------

    def _write_messages(self, sid: str, messages: list[dict]) -> None:
        log = self._transcript(sid)
        existing = log.count()
        if len(messages) > existing:
            log.append(messages[existing:])
        elif len(messages) < existing:  # rare, and not append-only
            log.rewrite(messages)
        elif messages and log.last() != messages[-1]:
            # The same count with a different tail: the last message was updated in place. An
            # external child's live progress text is rewritten as its CLI streams, and the final
            # answer replaces it. The append-only path would swallow that, so the file is rewritten.
            log.rewrite(messages)

    def save(self, record: SessionRecord, *, touch: bool = True) -> None:
        sid = record.session_id
        with self._lock:
            # A row from before the files existed is moved into its `.jsonl` first.
            if not self._file(sid).exists():
                legacy = self._inline_messages(sid)
                if legacy:
                    self._append(sid, legacy)
            self._write_messages(sid, record.messages)

            # Only a conversation with messages earns a snapshot. A row made before any turn (the
            # remote binding's seed) must keep its title NULL. Saving the placeholder would block the
            # first turn's real title for good, because the upsert keeps the old title and the
            # fallback in `title_from` is for display only.
            title = record.title or (title_from(record.messages) if record.messages else None)
            self._conn.execute(
                _UPSERT,
                (
                    sid,
                    record.workspace,
                    record.model,
                    record.mode,
                    title,
                    record.agent,
                    len(record.messages),
                    json.dumps(record.extra_roots or []),
                    json.dumps(record.grants or {}),
                    json.dumps(record.compaction or {}),
                    json.dumps(record.team or {}),
                    json.dumps(record.bindings or {}),
                    record.thinking,
                    record.parent_session,
                    int(record.seed_length or 0),
                    record.kind or "root",
                    json.dumps(record.revert or {}),
                    1 if record.plan else 0,
                    record.environment_id or None,
                ),
            )
            self._conn.commit()
        # A remote binding's workspace is a POSIX path on someone else's machine, and it must not
        # enter the local "recent projects" list that every surface offers to open.
        if touch:
            self.touch_workspace(record.workspace)

    def load(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return None
        messages = self._read_jsonl(session_id)
        if messages is None:
            messages = inline_messages_of(row["messages"])
        return SessionRecord(
            **summary_of(row),
            messages=messages,
            message_count=len(messages),
            extra_roots=roots_of(column(row, "extra_roots")),
            grants=mapping_of(column(row, "grants")),
            compaction=mapping_of(column(row, "compaction")),
            bindings=mapping_of(column(row, "bindings")),
            parent_session=column(row, "parent_session"),
            seed_length=int(column(row, "seed_length") or 0),
            revert=mapping_of(column(row, "revert")),
        )

    def list(self, *, workspace: Optional[str] = None, include_children: bool = False) -> list[SessionRecord]:
        """Session rows, newest first. By default only roots. A `kind = "subagent"` row is a child of
        another session, and every listing there is (the sidebar, recent-work recall, search_history)
        wants the sessions a person can open. A fork stays, since it shares `parent_session` as
        lineage but its kind is "root". `include_children=True` gives the raw set."""
        clauses: list[str] = []
        args: list[Any] = []
        if workspace is not None:
            clauses.append("workspace = ?")
            args.append(workspace)
        if not include_children:
            # COALESCE, because a row from before the column reads NULL if the ALTER was missed.
            clauses.append("COALESCE(kind, 'root') != 'subagent'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM sessions {where} ORDER BY pinned DESC, updated_at DESC", args
            ).fetchall()
        return [SessionRecord(**summary_of(row), messages=[], message_count=row["n_msgs"] or 0) for row in rows]

    def names(self):
        """The table of project-name aliases, riding this store's connection."""
        from ..projects import ProjectNames

        if not hasattr(self, "_names"):
            self._names = ProjectNames(self._conn, self._lock)
        return self._names

    def delete(self, session_id: str) -> bool:
        with self._lock:
            done = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            self._conn.commit()
        self._transcript(session_id).delete()
        return done.rowcount > 0

    def close(self) -> None:
        self._conn.close()
