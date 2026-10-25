"""The sessions table: its shape, and how an older database is brought up to it.

A database written by an earlier version lacks some columns. Each is added with an `ALTER` that is
allowed to fail when the column is already there, so the same code opens a new file and an old one.
"""

from __future__ import annotations

import sqlite3

# Created with the table. A new database gets exactly these.
CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, mode TEXT,
    title TEXT, agent TEXT DEFAULT 'reeve', n_msgs INTEGER DEFAULT 0, messages TEXT,
    extra_roots TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
    origin TEXT, origin_label TEXT,
    auto_title TEXT, renamed INTEGER DEFAULT 0, thinking TEXT,
    kind TEXT DEFAULT 'root',
    plan INTEGER DEFAULT 0,
    environment_id TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workspaces (
    path TEXT PRIMARY KEY, last_used TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added over time, in the order they arrived. The notes say what each one is for, and which
# of them `save()` writes only on INSERT, because a per-turn save rebuilds the record without them
# and must not erase what was set elsewhere.
ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("title", "TEXT"),
    ("n_msgs", "INTEGER DEFAULT 0"),
    ("agent", "TEXT DEFAULT 'reeve'"),
    ("extra_roots", "TEXT"),
    ("pinned", "INTEGER DEFAULT 0"),
    ("archived", "INTEGER DEFAULT 0"),
    ("origin", "TEXT"),
    ("origin_label", "TEXT"),
    ("auto_title", "TEXT"),
    ("renamed", "INTEGER DEFAULT 0"),
    ("grants", "TEXT"),
    ("compaction", "TEXT"),
    ("team", "TEXT"),
    ("bindings", "TEXT"),
    ("thinking", "TEXT"),
    # Fork lineage: the parent's id and how many of its messages seeded this session. NULL and 0
    # for every ordinary session.
    ("parent_session", "TEXT"),
    ("seed_length", "INTEGER DEFAULT 0"),
    # 'root' for existing rows and forks, 'subagent' for a session a parent's `task` tool spawned.
    # The default backfills every older row in one statement.
    ("kind", "TEXT DEFAULT 'root'"),
    # The message-revert marker, as JSON, NULL when nothing is staged. INSERT-only in `save()`.
    ("revert", "TEXT"),
    # The plan overlay: 0 off, 1 on. It is a separate axis from `mode` and rides the per-turn
    # save as `mode` does.
    ("plan", "INTEGER DEFAULT 0"),
    # The SSH environment whose host owns the workspace, NULL for a local session. INSERT-only in
    # `save()` and written through `set_environment`, the way `kind` is.
    ("environment_id", "TEXT"),
)


def open_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(CREATE_SESSIONS)
    for name, column_type in ADDED_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {column_type}")
        except sqlite3.OperationalError:
            pass  # the column is already there
    conn.commit()
