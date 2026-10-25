"""The foreground wait: how one command's run is read to its end.

A command is one process whose script ends with a trailer line saying how the
command ended and where the shell is. The executor needs three things back: the lines the
command printed, that trailer when there is one, and why the wait stopped.

**What ends a command**, in the order checked:

1. `reported` — the trailer was read. The shell said how it ended; nothing after it matters.
2. `ended` — the pipe closed with no trailer: the script never reached its last line (a parse
   error, an `exit`, a kill). Nothing more can arrive.
3. `held` — the process is gone and the pipe is still open after `PIPE_HELD_SECONDS`: a child
   the command left running holds it, and the wait must not become that child's.

and three that are not the command's own: `stopped` (the person pressed Stop), `deadline`
(the budget ran out; the command is NOT stopped, the caller hands it to the background) and
`released` (the person typed into the turn and is waiting on this step: the command gets
`RELEASE_GRACE_SECONDS` to end, then is handed to the background like a deadline).

**The wait sleeps on the output stream, never on a clock.** It used to wake every 50 ms and
ask the process whether it was done, returning on yes. A process is done before its last
lines are read — the pipe is drained by a reader thread a moment later — so that wait returned
whatever had arrived, which for a short command was nothing. Real store, 2026-10-04: 93 of
2,446 run_shell results came back "(no output)", exit 0, "the command's shell exited before it
reported a status"; 87 of them on one busy day, 12% of that day's commands; `echo done2` came
back empty. What starves the reader is the server's OWN threads, not the machine: 150 bash
`echo`s with 24 other processes saturating the CPU lost nothing, and the same 150 with 12
Python threads spinning inside the process lost 67 (output and status both, exit reported 0).
This wait, under that same contention: 0 of 150.

The stream is the only thing that knows the output is over (`output_ended` turns true after
the last line is in the buffer), so it is the thing waited on, and a command returns when its
trailer is read instead of at the next tick.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

#: How long the wait stays on a pipe that is still open after its process has gone. Normally
#: the pipe closes with the process and this is never reached.
PIPE_HELD_SECONDS = 2.0
#: How often a wait that has heard nothing looks at the process itself. Output and the end of
#: the pipe wake it at once; only a process that exits with its pipe held is silent.
PROCESS_CHECK_SECONDS = 0.25
#: How long a wait still gives its command once the person is waiting on the step. A command that
#: was about to end ends normally, and is not turned into a background job the model is later told
#: about; a long one stops holding the person's message.
RELEASE_GRACE_SECONDS = 3.0


@dataclass
class Waited:
    """What a foreground wait saw. `outcome` is one of `reported`, `ended`, `held`, `stopped`,
    `deadline`, `released`; `trailer` is the trailer line when the outcome is `reported`."""

    outcome: str
    lines: list[str] = field(default_factory=list)
    trailer: Optional[str] = None


def wait_for_command(
    task: Any,
    *,
    marker: str,
    deadline: float,
    stopped: threading.Event,
    emit: Callable[[str], None],
    released: Optional[threading.Event] = None,
) -> Waited:
    """Read `task`'s output until one of the six outcomes above.

    `emit` is handed each output line as it is read (the live view of a running command).
    `released` is set while the person waits on the step (`TurnEngine.person_waiting`).
    Never kills anything: the caller decides what a `stopped`, `deadline` or `released` means.
    """
    seen = Waited(outcome="ended")
    gone_at: Optional[float] = None
    release_at: Optional[float] = None
    while True:
        # Read BEFORE draining: every line is in the buffer before `output_ended` turns true,
        # so a drain that follows a true reading has seen them all.
        ended = task.output_ended
        for line in task.drain_new():
            if marker in line:
                seen.trailer = line
                continue
            seen.lines.append(line)
            emit(line)
        if seen.trailer is not None:
            seen.outcome = "reported"
            return seen
        if ended:
            return seen
        if stopped.is_set():
            seen.outcome = "stopped"
            return seen
        now = time.monotonic()
        if now >= deadline:
            seen.outcome = "deadline"
            return seen
        if release_at is None and released is not None and released.is_set():
            release_at = now + RELEASE_GRACE_SECONDS
        if release_at is not None and now >= release_at:
            seen.outcome = "released"
            return seen
        pause = min(deadline, release_at if release_at is not None else deadline) - now
        if task.done:
            gone_at = now if gone_at is None else gone_at
            held = PIPE_HELD_SECONDS - (now - gone_at)
            if held <= 0:
                seen.outcome = "held"
                return seen
            pause = min(pause, held)
        task.wait_output(min(pause, PROCESS_CHECK_SECONDS))


__all__ = ["PIPE_HELD_SECONDS", "PROCESS_CHECK_SECONDS", "RELEASE_GRACE_SECONDS", "Waited", "wait_for_command"]
