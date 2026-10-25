"""A wall-clock ceiling on every tool call — the backstop under each tool's own deadline.

**The problem this closes was a stated belief that was false.** `_dispatch` documents the
invariant that keeps a turn from hanging (`engine.py`: *"the honest contract is: the TURN
stops waiting, the call keeps running to its own deadline. That is why every tool with
blocking work carries one of its own"*), and `tests/test_tool_deadlines.py` repeats it in
its module docstring. The belief held for shell, terminal, MCP, grep, git and the
connectors — and not for `tools/files.py`, the largest tool family after shell:
`read_file` iterates a blocking `open()` and `read_image` calls `read_bytes()`, neither
with a timeout, because neither declared a contract. A read against a stalled network
mount, a FIFO, or a device node was unbounded, and nothing in the runtime could say so.

**Why a ceiling rather than a value per tool.** The registry holds ~100 builtin tools,
~40 connectors and MCP tools discovered at runtime. Declaring a budget on each one is a
migration that is never finished: the first tool added afterwards silently rejoins the
unbounded set, which is exactly how this gap appeared. So the budget is resolved in two
layers instead:

    effective = contract.timeout_ms   if declared     (a real budget, set deliberately)
              = DEADLINE_CEILING_MS   otherwise       (a backstop, applies to everything)

Every call therefore has a budget from the moment this listener is registered — the ones
that thought about it and the ones that did not — and a new tool cannot opt out by
forgetting. This is a prepended execute step:
wrapper applying to every call, with per-tool `timeoutMs` on top), adapted to a pipeline
that already had the stage and no tenant for it.

**The ceiling sits ABOVE every self-enforced deadline, on purpose.** Shell and
`terminal_send` cap themselves at `_MAX_TIMEOUT` (600 s); the connectors' `_run_git` allows
600 s. A backstop below those would convert a slow-but-working command into a phantom
timeout, which is worse than no backstop — the model would be told a call failed while it
went on to do the work anyway. `tests/test_tool_deadline.py` pins the ordering.

**What this cannot do, stated plainly.** Python cannot cancel a thread, so this stops the
turn WAITING; it does not stop the worker. A tool that declared a tighter budget of its own
still cancels its real work (shell kills the process group, MCP cancels the request, the
subagent sets its stop rail) — and because every such budget is below the ceiling, the
tool's own mechanism fires first and this one is what catches the tools that have none.
The abandoned task is drained rather than cancelled, for the reason `_dispatch` does the
same: an un-retrieved exception from an abandoned task surfaces later as an unhandled-task
warning with no owner.

Distinct from a user's Stop: that is `INTERRUPTED` (a person's decision, and the call may
have been about to succeed), this is `TIMEOUT` (the tool failing to do its job).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .pipeline import TIMEOUT, ExecListener, ToolExecution, ToolResult

logger = logging.getLogger(__name__)

#: The budget a call gets when its contract declares none. Above every deadline a tool
#: enforces for itself (600 s — shell, `terminal_send`, the connectors' `_run_git`) and
#: above the largest DECLARED contract budget (the subagent's rail: a 600 s child plus
#: its 60 s schema repair plus a 60 s margin = 720 s), so the backstop never pre-empts a
#: rail a tool set deliberately. 15 minutes is still low enough that a wedged call cannot
#: hold a turn for the rest of a working day. It is a ceiling, not a policy: put a real
#: budget on the contract.
DEADLINE_CEILING_MS = 900_000


def budget_for(execution: ToolExecution, ceiling_ms: int = DEADLINE_CEILING_MS) -> int:
    """The wall-clock budget for one call, in milliseconds.

    The contract wins when it declares one — that is a tool author saying "this call has
    a known shape and I know how long it may take", which is strictly better information
    than any default.
    """
    declared = execution.contract.timeout_ms if execution.contract is not None else None
    return declared if declared is not None else ceiling_ms


def deadline_listener(ceiling_ms: int = DEADLINE_CEILING_MS) -> ExecListener:
    """Build the `on_execute` listener.

    The ceiling is a parameter rather than a module constant read at call time so a test
    can inject a small one and prove the mechanism in milliseconds instead of asserting
    against a 12-minute default it would never wait out.
    """

    async def listener(
        execution: ToolExecution, next_: Any
    ) -> ToolResult:
        budget_ms = budget_for(execution, ceiling_ms)
        work = asyncio.ensure_future(next_())
        done, _ = await asyncio.wait({work}, timeout=budget_ms / 1000)

        if work in done:
            # `result()` not a bare return: a raise travels to `_run_exec`, which already
            # turns it into an ERROR result exactly as it did before this listener existed.
            return work.result()

        # Expired. The call keeps running — see the module docstring — so the outcome is
        # detached and drained rather than cancelled, and the model is told what happened
        # in words it can act on.
        work.add_done_callback(_drain)
        logger.warning(
            "tool %s: exceeded its %d ms budget; the turn stops waiting, the call does not",
            execution.name,
            budget_ms,
        )
        return ToolResult(
            value={
                "error": (
                    f"{execution.name} did not finish within {budget_ms / 1000:g}s and was "
                    "abandoned. It may still be running."
                ),
                "reason": "deadline",
                "timeout_seconds": budget_ms / 1000,
            },
            status=TIMEOUT,
        )

    return listener


def _drain(task: "asyncio.Future[Any]") -> None:
    """Retrieve and discard the outcome of a call the turn stopped waiting on.

    Without this, a tool that raises after the deadline is reported surfaces later as an
    unhandled-task warning with no owner and no way to trace it back to the call that
    produced it — the same leak `engine._dispatch` attaches the same callback to avoid.
    """
    if task.cancelled():
        return
    task.exception()
