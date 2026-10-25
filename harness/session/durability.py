"""Record the request before acting on it.

The session log is best-effort by design — `SessionLog.append` never raises and latches an
internal `_broken` flag instead — and the conversation store is written by the manager at
checkpoints. Between those two facts sits a window: the turn has decided to run a tool that
changes the world, and nothing has yet put that decision on disk. If the process dies in
that window, the transcript comes back with no record of a write that already happened, and
the operator's next session cannot tell what was already done.

That matters most for the paths nobody is watching. A durable resume reads the saved thread
and re-drives unanswered tool calls (`engine.resume`), which is only trustworthy if what it
reads reflects what was actually done; a self-wake or a scheduled run has no user to notice
that a step is missing. The rule this listens to: *do not run side-effecting
tools from state that exists only in this process*.

So: before a tool the risk classifier calls consequential, ask the host to flush. If the
flush cannot be done — a full disk, a read-only store — the call is BLOCKED rather than
run, and the refusal reaches the model in words it can act on. A `pre_execute` listener is
the right stage for it and already fails closed for exactly this reason
(`tools/pipeline.py`: "a gate that crashed cannot be read as consent").

Two deliberate limits, both stated where they will be read:

* It bounds the FIRST effect after a decision, not ordering between effects. Two writes in
  one batch each flush before their own turn at the pipeline; nothing here sequences them.
* A host that installs no flush is unaffected — the listener is a no-op. That keeps the
  CLI, the bench and every test that builds a bare engine byte-identical to before, and it
  is why the guarantee is worth having rather than worth assuming: the callers that need it
  are the ones that install it.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from harness.tools.pipeline import Block, PreListener

logger = logging.getLogger(__name__)

#: What the model is told when the record could not be written. It names the consequence
#: rather than the mechanism, because "the flush failed" is not something a model can use.
REFUSAL = (
    "the session's history could not be written to disk, so this action was not run: there "
    "would have been no record of it if the app stopped. Check free disk space and try again."
)


def durable_before_effect(
    flush_of: Callable[[], Optional[Callable[[], Awaitable[bool]]]],
    *,
    consequential: Callable[[str, dict], bool],
) -> PreListener:
    """Build the listener. `flush_of` is read per call so a host that attaches its flush
    after this is registered still reaches it."""

    async def listener(execution, next_):
        flush = flush_of()
        if flush is None:
            return await next_()
        if not consequential(execution.name, execution.arguments):
            # Reads change nothing, so there is nothing a crash could make untrue. Flushing
            # for them would pay the cost on the common path to protect nothing.
            return await next_()
        try:
            landed = await flush()
        except Exception:
            # A flush that raises is a flush that did not land. Same answer as `False`
            # rather than an exception the pipeline would turn into an ERROR result — the
            # model should be told the action was refused, not that the tool broke.
            logger.warning(
                "tool %s: the durability flush raised; blocking the call",
                execution.name,
                exc_info=True,
            )
            landed = False
        if not landed:
            logger.warning(
                "tool %s: refusing a consequential call whose record could not be written",
                execution.name,
            )
            return Block(REFUSAL, rule="durability")
        return await next_()

    return listener
