"""Automations and their runs, kept in SQLite.

A row is the record's JSON plus the columns a query filters on (`enabled`, `next_run` for what is
due; `task_id`, `started_at` for a task's history), so finding the due tasks reads only those. The
scheduler and the request handlers share one connection from different threads, behind one lock.
The table and column names are what existing databases have; they do not change.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import ScheduledTask, TaskRun
from .timing import compute_next_run, epoch_now

__all__ = ["TaskStore", "compute_next_run"]

_TABLES = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, next_run REAL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_runs (run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, started_at REAL NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, started_at DESC);
"""

_RUN_SESSION = "__run__"


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_TABLES)
            self._conn.commit()

    def _records(self, sql: str, *args) -> list[dict]:
        """The JSON of every row `sql` selects (its one column is `data`), decoded."""
        with self._lock:
            return [json.loads(data) for (data,) in self._conn.execute(sql, args).fetchall()]

    def _change(self, *statements: tuple[str, tuple]) -> int:
        """Run the statements in one commit; the first one's row count comes back."""
        with self._lock:
            counts = [self._conn.execute(sql, args).rowcount for sql, args in statements]
            self._conn.commit()
        return counts[0]

    # -- tasks ------------------------------------------------------------------------------

    def save(self, task: ScheduledTask) -> ScheduledTask:
        """Insert or replace, working out `next_run` first: a switched-off task has none."""
        task.updated_at = epoch_now()
        task.next_run = compute_next_run(task) if task.enabled else None
        self._change(
            (
                "INSERT OR REPLACE INTO scheduled_tasks (id, enabled, next_run, data) VALUES (?, ?, ?, ?)",
                (task.id, int(bool(task.enabled)), task.next_run, json.dumps(task.to_dict())),
            )
        )
        return task

    def get(self, task_id: str) -> Optional[ScheduledTask]:
        found = self._records("SELECT data FROM scheduled_tasks WHERE id = ?", task_id)
        return ScheduledTask.from_dict(found[0]) if found else None

    def list(self) -> list[ScheduledTask]:
        """Soonest first; tasks with nothing scheduled last."""
        return [ScheduledTask.from_dict(r) for r in self._records("SELECT data FROM scheduled_tasks ORDER BY next_run IS NULL, next_run")]

    def delete(self, task_id: str) -> bool:
        """The task and its whole history; False when there was no such task."""
        removed = self._change(
            ("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,)),
            ("DELETE FROM task_runs WHERE task_id = ?", (task_id,)),
        )
        return removed > 0

    def due(self, *, now: Optional[float] = None) -> list[ScheduledTask]:
        """Switched-on tasks whose time has come, the most overdue first."""
        cutoff = epoch_now() if now is None else now
        found = self._records(
            "SELECT data FROM scheduled_tasks WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ? ORDER BY next_run", cutoff
        )
        return [ScheduledTask.from_dict(r) for r in found]

    # -- runs -------------------------------------------------------------------------------

    def add_run(self, run: TaskRun) -> TaskRun:
        """Record a run, or overwrite it as it moves from running to finished."""
        self._change(
            (
                "INSERT OR REPLACE INTO task_runs (run_id, task_id, started_at, data) VALUES (?, ?, ?, ?)",
                (run.run_id, run.task_id, run.started_at, json.dumps(run.to_dict())),
            )
        )
        return run

    def find_run(self, run_id: str) -> Optional[TaskRun]:
        found = self._records("SELECT data FROM task_runs WHERE run_id = ?", run_id)
        return TaskRun.from_dict(found[0]) if found else None

    def task_for_run_session(self, session_id: str) -> Optional[ScheduledTask]:
        """Which automation a run's conversation (`__run__<run_id>`) belongs to, if any: an approval
        raised inside a run looks here for the grants that cover it."""
        if not session_id.startswith(_RUN_SESSION):
            return None
        run = self.find_run(session_id.removeprefix(_RUN_SESSION))
        return self.get(run.task_id) if run else None

    def runs(self, task_id: str, *, limit: int = 50) -> list[TaskRun]:
        """Newest first. Two runs that started in the same clock tick come in the order they were
        written, the later one first, so "the newest run" is never a coin toss."""
        found = self._records("SELECT data FROM task_runs WHERE task_id = ? ORDER BY started_at DESC, rowid DESC LIMIT ?", task_id, limit)
        return [TaskRun.from_dict(r) for r in found]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
