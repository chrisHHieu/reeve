"""A wake: a promise to open a session's next turn later, and the limits that keep it a promise.

**Every limit in the turn loop bounds work inside a turn.** `max_iterations`, the repeat guard and
the output budget count within one turn and reset when it ends. A self-wake is the one mechanism that
opens a new turn from inside a turn, so none of them apply to it. `turn → sleep_until → wake → turn →
sleep_until → …` was unbounded, and one turn scheduling `k` wakes made the next tick deliver up to `k`
turns. Three bounds close it, and each answers a different question:

    depth      "is this chain making progress, or looping?": consecutive wake-driven turns with
               no person in between, reset by the person typing (see `MAX_WAKE_DEPTH`).
    budget     "is this session simply busy?": wake-driven turns per rolling hour
               (`harness.loop.budgets.HourlyBudget`), which survives a restart.
    pending    "is this request even distinct?": duplicate wakes collapse, and a session may
               hold only so many at once (`MAX_PENDING_WAKES_PER_SESSION`).

**A past fire time is left alone, deliberately.** `sleep_until` with a timestamp that has already
gone makes the wake due on the next tick, which is the honest reading of the request: the model read
the `Now:` line a moment before the call landed, so "wake at 12:00:00" meant "wake now". Clamping it
would change behaviour and buy nothing, since a model that wanted to spin would write `now + 1s`
instead. The hourly budget is what makes spinning unprofitable, which is why the bound lives there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

KIND_TIMER = "timer"
KIND_COMPLETION = "completion"
KIND_EVENT = "event"  # wake when a named connector or webhook event fires

STATE_PENDING = "pending"
STATE_DUE = "due"
STATE_FIRED = "fired"

# Consecutive turns this session may open for itself with nobody in between. Ten is generous for
# polling a deploy and far short of a runaway; what it bounds is a model that keeps deciding "one
# more check" without ever reporting back.
MAX_WAKE_DEPTH = 10

# Wake-driven turns per rolling hour, per session. It sits above the team backstop's 60 because a
# self-wake is the documented way to poll, but low enough that a loop is held to about one turn a
# minute.
WAKE_TURNS_PER_HOUR = 60

# Wakes one session may hold at once. A turn that schedules more than this is not planning, it is
# enumerating.
MAX_PENDING_WAKES_PER_SESSION = 16

# Fired wakes are kept as a short audit trail, then dropped. Without this the file grows for the life
# of the install: `mark_fired` never removed anything, and a save rewrites every record.
FIRED_RETENTION_DAYS = 7


def now() -> datetime:
    return datetime.now(timezone.utc)


class WakeRefused(RuntimeError):
    """A wake was not created. The message reaches the model as written, so it says what the limit
    is and what to do instead."""


@dataclass
class Wake:
    id: str
    session_id: str
    kind: str
    state: str = STATE_PENDING
    fire_at: Optional[str] = None  # ISO time, for a timer wake
    job_id: Optional[str] = None  # for a completion wake
    event_key: Optional[str] = None  # for an on-event wake
    note: str = ""
    created_at: str = field(default_factory=lambda: now().isoformat())
    # How many turns this session has already opened for itself in this chain. It rides the wake and
    # not the engine, because the engine is rebuilt on restart and a chain is exactly what must not
    # come back as a fresh zero.
    depth: int = 0
    # When this wake was consumed. Used only to expire the audit trail.
    fired_at: Optional[str] = None

    def identity(self) -> tuple:
        """What makes two wakes the same request.

        It depends on the kind, because the three are identified by different things: a timer by when
        it fires, a completion by which job, an event by which key. The note belongs to a timer's
        identity only. Polling the same instant for two different reasons is two requests, while
        waiting on one job is waiting on one job however it is described."""
        if self.kind == KIND_COMPLETION:
            return ("completion", self.job_id)
        if self.kind == KIND_EVENT:
            return ("event", self.event_key)
        return ("timer", self.fire_at, self.note)
