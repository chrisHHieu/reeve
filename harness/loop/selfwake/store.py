"""The wake store: pending wakes on disk, and the rule for which requests may become one."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from ..budgets import Budgets
from .records import (
    FIRED_RETENTION_DAYS,
    KIND_COMPLETION,
    KIND_EVENT,
    KIND_TIMER,
    MAX_PENDING_WAKES_PER_SESSION,
    MAX_WAKE_DEPTH,
    STATE_DUE,
    STATE_FIRED,
    STATE_PENDING,
    WAKE_TURNS_PER_HOUR,
    Wake,
    WakeRefused,
    now,
)


class WakeStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._wakes: dict[str, Wake] = {}
        self.budgets = Budgets()
        if self.path and self.path.is_file():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for entry in saved.get("wakes", []):
                wake = Wake(**entry)
                self._wakes[wake.id] = wake
            self.budgets = Budgets.from_dict(saved.get("budgets"))

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "wakes": [asdict(wake) for wake in self._wakes.values()],
            "budgets": self.budgets.to_dict(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- creating ----------------------------------------------------------------------------

    def _admit(self, *, session_id: str, kind: str, identity: tuple, depth: int) -> Optional[Wake]:
        """The existing wake this request duplicates, or None when a new one may be made. It raises
        `WakeRefused` when the request must not become a wake at all, and is called with the lock
        held.

        There is one gate for all three kinds, so a limit can never be enforced on timers and
        forgotten for completions."""
        if depth > MAX_WAKE_DEPTH:
            raise WakeRefused(
                f"this session has opened {depth} turns for itself in a row without "
                f"anyone replying, and the limit is {MAX_WAKE_DEPTH}. Do the work now or "
                "report what is blocking you, instead of scheduling another wake."
            )
        live = [wake for wake in self._wakes.values() if wake.state != STATE_FIRED]
        for wake in live:
            if wake.session_id == session_id and wake.kind == kind and wake.identity() == identity:
                # The same request twice is one request. Returning the existing wake keeps the count
                # honest and spares the session a second turn for the same event.
                return wake
        held = [wake for wake in live if wake.session_id == session_id]
        if len(held) >= MAX_PENDING_WAKES_PER_SESSION:
            raise WakeRefused(
                f"this session already holds {len(held)} pending wakes, the limit is "
                f"{MAX_PENDING_WAKES_PER_SESSION}. Wait for one of them, or cancel what "
                "is no longer needed, before scheduling another."
            )
        return None

    def _add(self, session_id: str, kind: str, identity: tuple, depth: int, **fields) -> Wake:
        with self._lock:
            existing = self._admit(session_id=session_id, kind=kind, identity=identity, depth=depth)
            if existing is not None:
                return existing
            wake = Wake(uuid.uuid4().hex, session_id, kind, depth=depth, **fields)
            self._wakes[wake.id] = wake
            self._save()
        return wake

    def add_timer(self, session_id: str, fire_at: datetime, *, note: str = "", depth: int = 0) -> Wake:
        when = fire_at.isoformat()
        return self._add(session_id, KIND_TIMER, ("timer", when, note), depth, fire_at=when, note=note)

    def add_completion(self, session_id: str, job_id: str, *, note: str = "", depth: int = 0) -> Wake:
        return self._add(session_id, KIND_COMPLETION, ("completion", job_id), depth, job_id=job_id, note=note)

    def add_event(self, session_id: str, event_key: str, *, note: str = "", depth: int = 0) -> Wake:
        return self._add(session_id, KIND_EVENT, ("event", event_key), depth, event_key=event_key, note=note)

    # -- reading and settling ----------------------------------------------------------------

    def due(self, current: Optional[datetime] = None) -> list[Wake]:
        """The timer wakes whose time has passed, and the completion and event wakes marked due."""
        current = current or now()
        ready = []
        for wake in self._wakes.values():
            if wake.state not in (STATE_PENDING, STATE_DUE):
                continue
            if wake.kind == KIND_TIMER and wake.fire_at and datetime.fromisoformat(wake.fire_at) <= current:
                ready.append(wake)
            elif wake.kind in (KIND_COMPLETION, KIND_EVENT) and wake.state == STATE_DUE:
                ready.append(wake)
        return ready

    def complete_job(self, job_id: str) -> list[Wake]:
        """Mark the completion wakes for `job_id` due, since the job exited, and return them."""
        return self._mark_due(lambda wake: wake.kind == KIND_COMPLETION and wake.job_id == job_id)

    def fire_event(self, event_key: str) -> list[Wake]:
        """Mark the on-event wakes for `event_key` due, since a connector or webhook fired, and
        return them."""
        return self._mark_due(lambda wake: wake.kind == KIND_EVENT and wake.event_key == event_key)

    def _mark_due(self, matches: Callable[[Wake], bool]) -> list[Wake]:
        marked = []
        with self._lock:
            for wake in self._wakes.values():
                if wake.state == STATE_PENDING and matches(wake):
                    wake.state = STATE_DUE
                    marked.append(wake)
            if marked:
                self._save()
        return marked

    def mark_fired(self, wake_id: str) -> None:
        with self._lock:
            wake = self._wakes.get(wake_id)
            if wake is not None:
                wake.state = STATE_FIRED
                wake.fired_at = now().isoformat()
                self._expire_locked()
                self._save()

    def _expire_locked(self) -> None:
        """Drop the audit trail past the retention window. The lock is held."""
        cutoff = now() - timedelta(days=FIRED_RETENTION_DAYS)
        stale = [
            wake_id
            for wake_id, wake in self._wakes.items()
            if wake.state == STATE_FIRED and wake.fired_at and datetime.fromisoformat(wake.fired_at) < cutoff
        ]
        for wake_id in stale:
            del self._wakes[wake_id]

    def pending(self, session_id: Optional[str] = None) -> list[Wake]:
        return [
            wake
            for wake in self._wakes.values()
            if wake.state != STATE_FIRED and (session_id is None or wake.session_id == session_id)
        ]

    def take_turn_budget(self, session_id: str, *, cap: int = WAKE_TURNS_PER_HOUR) -> bool:
        """Count one wake-driven turn against this session's rolling hour.

        The thing that opens the turn calls it, and not the wake that asked for it. A wake created
        while over budget would otherwise be born spent, and the refusal would be reported against the
        wrong request."""
        with self._lock:
            allowed = self.budgets.take(f"wake:{session_id}", cap)
            self._save()
        return allowed
