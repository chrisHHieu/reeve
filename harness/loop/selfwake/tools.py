"""The tools an agent calls to schedule its own resumption."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from .records import WakeRefused
from .store import WakeStore


def selfwake_tools(store: WakeStore, session_id: str, *, depth_of: Optional[Callable[[], int]] = None) -> list:
    """Suspend and wake on a time, a finished job or an event.

    `depth_of` reads the chain depth of the turn making the call. It is a callable and not a number
    because the engine is built once per session and cached while the depth changes every turn, the
    shape `auto_memory_enabled` uses and for the same reason."""

    def depth() -> int:
        return depth_of() if depth_of is not None else 0

    def refused(exc: WakeRefused) -> dict:
        return {"error": str(exc), "reason": "wake_refused"}

    def sleep_until(when_iso: str, note: str = "") -> dict:
        """Suspend and wake this session at an ISO-8601 timestamp (timezone-aware; bare
        timestamps are read as UTC). Use it to wait for a TIME without burning context while
        idle — for a relative wait ("check again in 5 minutes"), compute the timestamp from
        the `Now:` line in your context. Do not use it to wait for a background shell
        command: `run_shell` with `run_in_background` tells you when the command ends, and
        `notify_when` tells you when it prints the line you are waiting for."""
        when = datetime.fromisoformat(when_iso)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        try:
            wake = store.add_timer(session_id, when, note=note, depth=depth())
        except WakeRefused as exc:
            return refused(exc)
        return {"ok": True, "wake_id": wake.id, "fire_at": wake.fire_at}

    def wake_on(job_id: str, note: str = "") -> dict:
        """Suspend and wake this session when a background subagent (`job_id`, from `task`
        with `background=true`) completes.

        A background shell command needs no wake: it reports its own end as a message, and
        the line `notify_when` waits for, while it runs.
        """
        try:
            wake = store.add_completion(session_id, job_id, note=note, depth=depth())
        except WakeRefused as exc:
            return refused(exc)
        return {"ok": True, "wake_id": wake.id, "job_id": job_id}

    def wake_on_event(event_key: str, note: str = "") -> dict:
        """Suspend and wake this session when a named event (`event_key`) fires — e.g. a
        connector/webhook signal an Ops agent watches for."""
        try:
            wake = store.add_event(session_id, event_key, note=note, depth=depth())
        except WakeRefused as exc:
            return refused(exc)
        return {"ok": True, "wake_id": wake.id, "event_key": event_key}

    return [sleep_until, wake_on, wake_on_event]
