"""`notify_when`: wake the session the first time a background job prints a line that matches.

A background job reports itself when it ENDS (`shell_jobs.watch_background`). A dev server or a
watcher never ends, and what the model waits for is a line: "ready on :3000", "compiled", "listening".
It used to find that line by sleeping and polling (`sleep 10; curl …`, `shell_task_output` again and
again: 164 sleeps and 91 polls in the store to 2026-10-12). Now the job's own reader, which sees every
line anyway, compares each with the pattern and, on the first match, hands the session one message
with that line and what came before it. The job keeps running; its end is reported as before, and
that report says when the line never came.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Optional

#: Characters of output, up to the matching line, the message carries.
MATCH_TAIL_CHARS = 1_500


class LineWatch:
    """One pattern on one job, fired at most once. Fed on the job's reader thread."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._rx = re.compile(pattern)
        self._lock = threading.Lock()
        self._lines = 0
        self.fired = False
        self._pending: Optional[dict[str, Any]] = None
        self._listener: Optional[Callable[[dict[str, Any]], None]] = None

    @staticmethod
    def compile_error(pattern: str) -> Optional[str]:
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"notify_when is not a valid regular expression: {exc}"
        return None

    def feed(self, text: str, task: Any) -> None:
        """One line the job printed. The first that matches becomes the message."""
        if not text:
            return
        with self._lock:
            self._lines += 1
            if self.fired or not self._rx.search(text):
                return
            self.fired = True
            info = {
                "event": "matched",
                "task_id": task.id,
                "command": task.command,
                "pattern": self.pattern,
                "line": text.rstrip("\r\n")[:500],
                "line_number": self._lines,
                "output": task.tail(MATCH_TAIL_CHARS),
                "generation": task.generation,
            }
            listener = self._listener
            if listener is None:
                # The reader starts with the process, before the watcher attaches: a server that
                # is ready at once must not be missed, so the match waits for its listener.
                self._pending = info
                return
        _deliver(listener, info)

    def attach(self, listener: Optional[Callable[[dict[str, Any]], None]]) -> None:
        """Where the match goes (the completion listener's channel); a match already seen is
        delivered now."""
        if listener is None:
            return
        with self._lock:
            self._listener = listener
            pending, self._pending = self._pending, None
        if pending is not None:
            _deliver(listener, pending)


def _deliver(listener: Callable[[dict[str, Any]], None], info: dict[str, Any]) -> None:
    try:
        listener(info)
    except Exception:  # noqa: BLE001 — the notification never breaks the job
        pass


def started(task_id: str, command: str, notify_when: Optional[str]) -> dict[str, Any]:
    """What `run_shell` answers for a job it put in the background: how its news will arrive."""
    watched = (
        f"You will also be told the first time it prints a line matching {notify_when!r}. "
        if notify_when
        else ""
    )
    return {
        "task_id": task_id,
        "command": command,
        "status": "running",
        "note": (
            f"{watched}You will be told when it finishes — do not poll it. Do other work, "
            "or end your reply: the result arrives as a follow-up message. "
            "shell_task_output reads output before then; shell_task_kill stops it."
        ),
    }
