"""How a batch of tool calls is grouped into ordered, overlapping-honest segments.

The engine used to split a batch in two: every parallel-safe call ran together first, then
everything else one at a time. That had two problems, and the second is the serious one.

**It reordered the model's request.** `concurrent` was dispatched to completion before
`serial` started, regardless of where the calls sat in the model's message. So a batch of
`[write_file(a), read_file(a)]` — write it, then read it back to check — executed as
read-then-write: the model was handed the file's OLD contents, and the honest reading of
that result is "my write did not land". A batch is a SEQUENCE the model wrote down, and the
runtime is not entitled to permute it.

**It guaranteed safety with a blanket rule.** Writes were serial whether or not they
touched related files, which is correct but only by accident: the guarantee held because
"mutating tools are never in the concurrent set", a property nobody enforces. A tool marked
`risk_level="low"` that happens to mutate — `shell_task_kill` is already marked low and
`git` tools are one edit away from it — would silently join a parallel run with a writer.

So this module segments instead. A batch becomes an ordered list of segments; a segment is
either one call that must run alone (a barrier) or a contiguous run of calls that may
overlap. Order is preserved exactly, parallel-safe runs still overlap, and a run is CLOSED
when a call reserves a path another call in the run touches — which is the usual rule
(`agent/tool_dispatch_helpers.py`): reads overlapping reads are harmless, and any overlap
involving a write starts a new segment.

The reservation comes from the caller, because "what does this call touch" is the path
geometry `harness.safety.boundary` already owns for root scoping — one source of truth, so a tool
that declares where it writes cannot disagree with itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from harness.safety.boundary import paths_overlap


@dataclass(frozen=True)
class Reservation:
    """What one call would touch in the filesystem.

    `located=False` means the reach could not be determined. That is not "touches nothing":
    it is "cannot be proven disjoint", and it conflicts with any write in the same run.
    """

    paths: tuple[str, ...] = ()
    write: bool = False
    located: bool = True

    def conflicts_with(self, other: "Reservation") -> bool:
        if not self.write and not other.write:
            # Two reads cannot disturb each other, however they overlap — the whole reason
            # a parallel run is worth forming.
            return False
        if not self.located or not other.located:
            # One side writes and the other's reach is unknown. Assume they meet.
            return True
        return any(
            paths_overlap(a, b) for a in self.paths for b in other.paths
        )


@dataclass
class Segment:
    """One ordered group. `parallel` is False for a segment that must run alone."""

    calls: list = field(default_factory=list)
    parallel: bool = True

    def __len__(self) -> int:
        return len(self.calls)


def plan_batch(
    calls: Sequence[Any],
    *,
    reserve: Callable[[Any], Optional[Reservation]],
) -> list[Segment]:
    """Segment `calls` in model order.

    `reserve` returns a call's reservation, or None to force a barrier — the caller's way of
    saying "this one is not parallel-safe at all". A barrier call is always alone in its own
    segment and always keeps its place in the order.
    """
    segments: list[Segment] = []
    run: list = []
    held: list[Reservation] = []

    def flush() -> None:
        if run:
            segments.append(Segment(calls=list(run), parallel=True))
            run.clear()
            held.clear()

    for call in calls:
        reservation = reserve(call)
        if reservation is None:
            # A barrier closes whatever run was forming, then takes its own place.
            flush()
            segments.append(Segment(calls=[call], parallel=False))
            continue
        if any(reservation.conflicts_with(other) for other in held):
            # Close the run here and start a new one containing this call, so the conflicting
            # call waits for the previous run instead of racing it — and the calls after it
            # still get to overlap with each other.
            flush()
        run.append(call)
        held.append(reservation)

    flush()
    return segments


class StreamGate:
    """`plan_batch`'s rule for calls that arrive one at a time: a `run_code` program's.

    A program's sub-calls are not a batch the engine can see whole. They arrive as the
    program makes them, and several can be in flight when it `await`s them together. The
    rule is the batch rule, applied as they come:
    - calls START in the order the program made them;
    - a barrier (`None`) starts only when nothing is running, and holds everything
      behind it until it is done;
    - a call whose reservation conflicts with a running one waits for it;
    - at most `cap` run at once (0 = no cap).

    Before this there was no rule. Programs could not overlap calls at all, and a
    workflow's fan-out overlapped whatever it was given.
    """

    def __init__(self, cap: int = 0) -> None:
        self._cap = cap
        self._changed = asyncio.Condition()
        self._next_ticket = 0
        self._turn = 0  # the oldest ticket that has not started (or given up) yet
        self._done: set[int] = set()
        self._running: dict[int, Optional[Reservation]] = {}

    def ticket(self) -> int:
        """Called in submission order: the ticket is the call's place in line."""
        self._next_ticket += 1
        return self._next_ticket

    def _may_start(self, ticket: int, reservation: Optional[Reservation]) -> bool:
        if ticket != self._turn + 1:
            return False
        if reservation is None:
            return not self._running
        if any(held is None for held in self._running.values()):
            return False
        if self._cap and len(self._running) >= self._cap:
            return False
        return not any(
            reservation.conflicts_with(held)
            for held in self._running.values()
            if held is not None
        )

    def _advance(self) -> None:
        while self._turn + 1 in self._done:
            self._done.discard(self._turn + 1)
            self._turn += 1

    async def acquire(self, ticket: int, reservation: Optional[Reservation]) -> None:
        async with self._changed:
            await self._changed.wait_for(lambda: self._may_start(ticket, reservation))
            self._running[ticket] = reservation
            self._done.add(ticket)
            self._advance()
            self._changed.notify_all()

    async def release(self, ticket: int) -> None:
        """The call finished, or never started (refused): either way it leaves the line."""
        async with self._changed:
            self._running.pop(ticket, None)
            if ticket > self._turn:
                self._done.add(ticket)
                self._advance()
            self._changed.notify_all()
