"""Rolling-window budgets for work the runtime starts on its own.

A budget is needed wherever a turn can lead to another turn without a person in between:
the model schedules a wake, the wake opens a turn, that turn schedules another. Nothing in
the turn loop bounds that, because every limit it has bounds work WITHIN a turn —
`max_iterations`, the repeat guard, the output budget. A chain of turns is a different
shape and needs its own counter.

This is the shape the team backstop already had (`teams/registry.py::count_wake`), lifted
out so there is one implementation of "count one automatic wake against a rolling hour,
and say no past the cap". Written against an injected clock so it is testable without
waiting for an hour to roll, and serialisable so a budget survives the restart that a
runaway chain would otherwise be able to reset.

Deliberately not a scheduler, a queue or a rate limiter: it answers one question — may
this automatic wake happen — and it answers it in a way that can be persisted next to
whatever owns the counter. `take` is the whole API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def hour_key(now: Optional[datetime] = None) -> str:
    """The bucket a `take` counts against: UTC, to the hour.

    UTC rather than local so a daylight-saving transition cannot produce a 23- or
    25-hour bucket, and coarse enough that the counter is one small string on disk.
    """
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H"
    )


@dataclass
class HourlyBudget:
    """One rolling-hour allowance, persisted as two fields."""

    hour: str = ""
    count: int = 0

    def take(self, cap: int, *, now: Optional[datetime] = None) -> bool:
        """Count one use against the current hour. False means the cap is reached.

        A new hour resets the count, so the answer is "not right now" rather than "never"
        — a chain that is refused at 60 wakes spends the next hour's allowance as soon as
        it arrives, and a person who types resets the CHAIN separately (`wake_depth`),
        which is the difference between "this is busy" and "this is stuck".
        """
        if cap <= 0:
            return False
        current = hour_key(now)
        if self.hour != current:
            self.hour, self.count = current, 0
        if self.count >= cap:
            return False
        self.count += 1
        return True

    def remaining(self, cap: int, *, now: Optional[datetime] = None) -> int:
        if self.hour != hour_key(now):
            return max(0, cap)
        return max(0, cap - self.count)

    def to_dict(self) -> dict[str, Any]:
        return {"hour": self.hour, "count": self.count}

    @classmethod
    def from_dict(cls, raw: Any) -> "HourlyBudget":
        """Tolerant, because this is read back off disk and a corrupt counter must not
        take the wake system with it: an unreadable budget reads as a fresh one."""
        if not isinstance(raw, dict):
            return cls()
        hour = raw.get("hour")
        count = raw.get("count")
        if not isinstance(hour, str) or not isinstance(count, int) or isinstance(count, bool):
            return cls()
        return cls(hour=hour, count=max(0, count))


@dataclass
class Budgets:
    """Several named budgets sharing one serialised record.

    A dict of `HourlyBudget` rather than one flat counter, because the two things worth
    counting here — wakes and background children — have different caps and a shared
    counter would let a busy wake hour silence delegation.
    """

    _budgets: dict[str, HourlyBudget] = field(default_factory=dict)

    def take(self, name: str, cap: int, *, now: Optional[datetime] = None) -> bool:
        budget = self._budgets.setdefault(name, HourlyBudget())
        return budget.take(cap, now=now)

    def remaining(self, name: str, cap: int, *, now: Optional[datetime] = None) -> int:
        budget = self._budgets.get(name)
        return cap if budget is None else budget.remaining(cap, now=now)

    def to_dict(self) -> dict[str, Any]:
        return {name: b.to_dict() for name, b in self._budgets.items()}

    @classmethod
    def from_dict(cls, raw: Any) -> "Budgets":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            _budgets={str(k): HourlyBudget.from_dict(v) for k, v in raw.items()}
        )
