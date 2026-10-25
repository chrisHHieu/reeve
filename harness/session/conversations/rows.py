"""Reading a `sessions` row: JSON columns, the title it shows, and columns an older file may lack."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

PLACEHOLDER_TITLE = "New session"


def json_column(raw: Optional[str], kind: type, empty: Any):
    """A JSON column read defensively. Missing, malformed or the wrong shape all give `empty`."""
    if not raw:
        return empty
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return empty
    return value if isinstance(value, kind) else empty


def roots_of(raw: Optional[str]) -> list[dict]:
    return json_column(raw, list, [])


def mapping_of(raw: Optional[str]) -> dict:
    return json_column(raw, dict, {})


def display_title(row: sqlite3.Row) -> Optional[str]:
    """The title every read path shows. A manual rename wins, then the generated title, then the
    first-line snapshot `save()` wrote."""
    if row["renamed"]:
        return row["title"]
    return row["auto_title"] or row["title"]


def column(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """A column that a row from an older database may not have."""
    return row[key] if key in row.keys() else default


def inline_messages_of(raw: Optional[str]) -> list[dict]:
    """The messages an older version kept inline in the row."""
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []


def title_from(messages: list[dict]) -> str:
    """The first line of the first thing the person said, or the placeholder."""
    from harness.llm.attachments import content_to_text

    from ..turns import is_user_turn

    for message in messages:
        if is_user_turn(message):
            text = content_to_text(message.get("content"), image_placeholder="").strip()
            if text:
                return text.splitlines()[0][:60]
    return PLACEHOLDER_TITLE


def summary_of(row: sqlite3.Row) -> dict[str, Any]:
    """The fields of a record that the index alone can answer."""
    return {
        "session_id": row["session_id"],
        "workspace": row["workspace"],
        "model": row["model"],
        "mode": row["mode"],
        "thinking": column(row, "thinking"),
        "title": display_title(row),
        "agent": row["agent"] or "reeve",
        "updated_at": row["updated_at"],
        "pinned": bool(row["pinned"]),
        "archived": bool(row["archived"]),
        "origin": row["origin"],
        "origin_label": row["origin_label"],
        "team": mapping_of(column(row, "team")),
        # A row from before these columns existed reads as an ordinary local root session.
        "kind": column(row, "kind") or "root",
        "plan": bool(column(row, "plan", False)),
        # The SSH binding rides the list, so the sidebar can badge a remote row without a request
        # for each row.
        "environment_id": column(row, "environment_id") or None,
    }
