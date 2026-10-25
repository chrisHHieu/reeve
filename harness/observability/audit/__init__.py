"""A durable local audit log of connector and tool actions."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from harness.integrations.connectors import connector_for_tool

from .redaction import resource_of, sanitize_args, truncate

__all__ = ["AuditStore"]

_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    agent TEXT,
    workspace TEXT,
    connector TEXT,
    tool TEXT,
    stage TEXT,
    status TEXT,
    approval TEXT,
    args TEXT,
    result_preview TEXT,
    reason TEXT,
    resource TEXT,
    call_id TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cache_read INTEGER DEFAULT 0,
    cache_write INTEGER DEFAULT 0
)
"""

# Columns that older databases lack (2026-08-12): `call_id` joins a row to the tool call it describes,
# and the token columns are the per-event metering. The cache columns (2026-08-22) are the cached
# share of a check, without which the metering could only see fresh tokens and under-reported cost.
_ADDED_COLUMNS = (
    ("call_id", "TEXT"),
    ("tokens_in", "INTEGER DEFAULT 0"),
    ("tokens_out", "INTEGER DEFAULT 0"),
    ("cache_read", "INTEGER DEFAULT 0"),
    ("cache_write", "INTEGER DEFAULT 0"),
)

_LIST_CEILING = 500


class AuditStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_TABLE)
        for column, declaration in _ADDED_COLUMNS:
            try:
                self._conn.execute(f"ALTER TABLE audit_events ADD COLUMN {column} {declaration}")
            except sqlite3.OperationalError:
                pass  # the column is already there, so this file was migrated before
        self._conn.commit()

    def append(self, event: dict[str, Any]) -> None:
        tool = str(event.get("tool") or event.get("tool_name") or "")
        connector = str(event.get("connector") or connector_for_tool(tool) or "")
        arguments = event.get("arguments") or {}
        resource = resource_of(tool, arguments, event.get("result") or {})
        row = (
            event.get("session_id") or "",
            event.get("agent") or "",
            event.get("workspace") or "",
            connector,
            tool,
            event.get("stage") or "",
            event.get("status") or "",
            event.get("approval") or "",
            json.dumps(sanitize_args(tool, arguments), default=str),
            truncate(str(event.get("result_preview") or "")),
            truncate(str(event.get("reason") or "")),
            truncate(str(resource or "")),
            str(event.get("call_id") or ""),
            int(event.get("tokens_in") or 0),
            int(event.get("tokens_out") or 0),
            int(event.get("cache_read") or 0),
            int(event.get("cache_write") or 0),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events (session_id, agent, workspace, connector, tool, stage,"
                " status, approval, args, result_preview, reason, resource, call_id, tokens_in,"
                " tokens_out, cache_read, cache_write)"
                " VALUES (" + ", ".join("?" * 17) + ")",
                row,
            )
            self._conn.commit()

    def list(
        self,
        *,
        limit: int = 100,
        session_id: Optional[str] = None,
        connector: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        for column, value in (("session_id", session_id), ("connector", connector), ("tool", tool)):
            if value:
                clauses.append(f"{column} = ?")
                args.append(value)
        query = "SELECT * FROM audit_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit or 100), _LIST_CEILING)))
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            try:
                event["args"] = json.loads(event.get("args") or "{}")
            except json.JSONDecodeError:
                event["args"] = {}
            events.append(event)
        return events

    def close(self) -> None:
        self._conn.close()
