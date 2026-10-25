"""One-time move of the SQLite fact store into memory files.

The global facts (`memories` rows, scope "global") become files in the user memory — who
the person is, loaded in every project; workspace facts become `project` notes in their
project's directory. Only live rows move (a superseded row's replacement is the fact).
Every row, superseded ones included, is first exported whole to
`<user memory>/.history/sqlite-memories-<stamp>.json`, and a marker file makes the move
run once. The table itself is left in the database, untouched.

Reads the table with `sqlite3` directly, not through the old store class, so the move
keeps working after that class is gone.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

MARKER = ".migrated-from-sqlite"

#: A preference is guidance on how to work with the person; everything else is about who
#: they are (`feedback` / `user`).
_TYPE_OF_KIND = {"preference": "feedback"}


def _rows(db: Path) -> list[dict[str, Any]]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("select * from memories order by id")]
    except sqlite3.Error:
        return []  # no table: nothing was ever stored this way
    finally:
        con.close()


def _title(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get("summary") or row.get("content") or "").split())
    return text[:80] or "memory"


def migrate_sqlite_facts(
    db: Path,
    user_memory: Any,
    *,
    project_memory_for: Optional[Callable[[str], Any]] = None,
) -> dict[str, Any]:
    """Move the live SQLite facts into files, once. Returns what it did."""
    root = Path(user_memory.root)
    marker = root / MARKER
    if marker.exists():
        return {"skipped": "already migrated"}
    rows = _rows(Path(db)) if Path(db).is_file() else []
    moved = {"user": 0, "project": 0, "skipped": 0}
    user_memory.ensure()
    if rows:
        history = root / ".history"
        history.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (history / f"sqlite-memories-{stamp}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    for row in rows:
        if row.get("superseded_at"):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        description = " ".join(str(row.get("summary") or content).split())[:200]
        scope = str(row.get("scope") or "")
        if scope == "global":
            target = user_memory
            note_type = _TYPE_OF_KIND.get(str(row.get("kind") or ""), "user")
            bucket = "user"
        elif scope == "workspace" and row.get("workspace") and project_memory_for is not None:
            target = project_memory_for(str(row["workspace"]))
            note_type, bucket = "project", "project"
        else:
            moved["skipped"] += 1
            continue
        res = target.save_memory(title=_title(row), description=description, note_type=note_type, body=content)
        moved[bucket if res.get("ok") else "skipped"] += 1
    marker.write_text(json.dumps({"at": time.time(), **moved}), encoding="utf-8")
    return moved
