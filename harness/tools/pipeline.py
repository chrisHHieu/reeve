"""Tool execution pipeline — three points where a concern can attach to a tool call
without being written into the turn engine.

The engine used to dispatch a call with one line:

    result, status = await asyncio.to_thread(self._execute_sync, tool_call)

which left nowhere to put anything that wraps a call rather than performs one. Every
such concern therefore had to grow inside `engine.py`, and the file is 1970 lines with
six approval callbacks partly because of it. Timeouts, output bounding, spill, and a
repeat-call guard are all the same shape, and all four were queued behind this.

    check         before approval     may REFUSE the call; asked again at dispatch
    pre_execute   before dispatch     may BLOCK the call
    execute       around dispatch     wraps it (deadlines, retries, metering)
    post_execute  after dispatch      may replace the result (bounding, spill)

Listeners receive the shared `ToolExecution` and a zero-argument `next()`; calling it
delegates downstream and returns what downstream produced. The FIRST listener
registered is the outermost, so its transformation applies LAST — a generic bounding
policy registers first precisely so it bounds whatever the tool-specific listeners
produced.

**What this deliberately does NOT take over: approval.** `TurnEngine._authorize` keeps
the permission gate, the provenance floor, standing rules, and the
approval card. That is the most safety-critical code in the repo; moving it here would
be a large diff through subtle policy for no gain, since none of the four concerns
above need it moved. The pipeline runs only on calls approval already cleared — except
`check`, which the engine asks BEFORE approval: a call a check refuses is
refused whatever a person says, so asking them first only buys a click that changes
nothing. Checks are deny-only, synchronous and free of side effects; `dispatch` asks them
again, because what they read (a file's state) can change while a card waits.

**Failure policy is asymmetric, on purpose.** A `pre_execute` listener exists to say no;
one that raises therefore BLOCKS the call, because a policy that crashed cannot be
read as consent. `execute` and `post_execute` failures degrade instead: an `execute`
raise becomes an error result (identical to the tool itself raising) and a
`post_execute` raise keeps the untransformed result, because bounding and presentation
are not permission decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace as _replace
from typing import Any, Awaitable, Callable, Optional

from .contract import ToolContract

logger = logging.getLogger(__name__)

OK = "ok"
ERROR = "error"
BLOCKED = "blocked"
#: The user stopped the turn while this call was in flight. Distinct from ERROR: the
#: tool did not fail, it was never allowed to finish — and it may still be running.
INTERRUPTED = "interrupted"
#: The call outlived its wall-clock budget (`tools/deadline.py`). Distinct from
#: INTERRUPTED because the two mean opposite things to a reader: a stop is a person's
#: decision and the call may well have been about to succeed, while a timeout is the
#: tool failing to do its job. Every engine branch tests `status == "ok"`, so this
#: settles as a failure — which is what a timeout is.
TIMEOUT = "timeout"


@dataclass(frozen=True)
class ToolExecution:
    """One approved call moving through the pipeline.

    Frozen so nothing swaps the identity of a call mid-flight; `arguments` is a live
    dict a `pre_execute` listener may still rewrite, which is the one mutation the
    stages are expected to make.
    """

    name: str
    arguments: dict[str, Any]
    call_id: str
    contract: Optional[ToolContract] = None


@dataclass(frozen=True)
class ToolResult:
    """What a dispatch settled to. `status` is one of OK / ERROR / BLOCKED /
    INTERRUPTED / TIMEOUT."""

    value: Any
    status: str = OK

    @property
    def ok(self) -> bool:
        return self.status == OK

    def with_value(self, value: Any) -> "ToolResult":
        """The same outcome carrying a different value — what a `post_execute`
        listener returns after bounding or spilling. A method rather than a
        re-exported `dataclasses.replace` so the transform is discoverable from the
        object a listener already holds."""
        return _replace(self, value=value)


@dataclass(frozen=True)
class Block:
    """A `pre_execute` listener's refusal. The reason reaches the model verbatim.

    `rule` names the policy that refused, when the producer has a name for it
    (`read-before-edit`, `durability`, …). It exists because the reason alone was not
    enough: the borrowed write renderers printed only `value["error"]`, so a refusal
    the gate had explained in full reached the model as a bare `tool call blocked` —
    session 435e0be4 retried one edit five ways, and gave up on a patch one read would
    have unlocked.
    """

    reason: str
    rule: str = ""
    #: The files the reason itself shows whole, as (path, digest of the version shown): a
    #: refusal that carries the file is a read of it once the model has the text.
    shown: tuple[tuple[str, str], ...] = ()


def _blocked_text(block: Block) -> str:
    """The model-facing sentence for a refusal. Never bare: with no producer-supplied
    detail at all it says that much rather than posing as a complete answer."""
    if block.rule and block.reason:
        return f"tool call blocked by {block.rule}: {block.reason}"
    if block.rule:
        return f"tool call blocked by {block.rule}"
    if block.reason:
        return f"tool call blocked: {block.reason}"
    return "tool call blocked: the refusing policy left no reason"


#: `None` means "no objection" — the Pythonic shape for a veto point, and it keeps a
#: listener that only observes down to one line.
PreDecision = Optional[Block]

#: A deny-only question about a call's arguments and the world as it is: no `next()`,
#: nothing to wrap, and nothing it may change.
Check = Callable[[ToolExecution], PreDecision]

PreListener = Callable[[ToolExecution, Callable[[], Awaitable[PreDecision]]], Awaitable[PreDecision]]
ExecListener = Callable[[ToolExecution, Callable[[], Awaitable[ToolResult]]], Awaitable[ToolResult]]
PostListener = Callable[
    [ToolExecution, ToolResult, Callable[[], Awaitable[ToolResult]]], Awaitable[ToolResult]
]


@dataclass
class ToolPipeline:
    """The three registries plus the dispatcher that runs them."""

    _checks: list[Check] = field(default_factory=list)
    _pre: list[PreListener] = field(default_factory=list)
    _exec: list[ExecListener] = field(default_factory=list)
    _post: list[PostListener] = field(default_factory=list)

    # -- registration ----------------------------------------------------------
    # Each returns a disposer. A registration that cannot be undone is a leak the
    # moment anything is scoped to one session or toggled from settings, and every
    # policy this pipeline exists for is one of those.

    def on_check(self, check: Check) -> Callable[[], None]:
        return _register(self._checks, check)

    def on_pre_execute(self, listener: PreListener) -> Callable[[], None]:
        return _register(self._pre, listener)

    def on_execute(self, listener: ExecListener) -> Callable[[], None]:
        return _register(self._exec, listener)

    def on_post_execute(self, listener: PostListener) -> Callable[[], None]:
        return _register(self._post, listener)

    # -- dispatch --------------------------------------------------------------

    async def dispatch(
        self,
        execution: ToolExecution,
        run: Callable[[], Awaitable[ToolResult]],
    ) -> ToolResult:
        """Run one approved call through all three stages.

        `run` performs the actual call and must not raise — the caller owns turning a
        tool exception into an ERROR result, exactly as it did before this pipeline
        existed.
        """
        blocked = self._run_checks(execution, fail_closed=True)
        if blocked is None:
            blocked = await self._run_pre(execution)
        if blocked is not None:
            return _blocked_result(blocked)
        result = await self._run_exec(execution, run)
        return await self._run_post(execution, result)

    def refusal(self, execution: ToolExecution) -> Optional[ToolResult]:
        """The BLOCKED result a check gives this call now, before anyone approves it —
        the same result `dispatch` would give it — or None.

        Fails OPEN, unlike `dispatch`: a check that raised here is asked again at dispatch,
        where a raise does block. Refusing early on a crash would only move that answer
        in front of the approval, at the cost of a check that cannot be told apart from a
        policy.
        """
        blocked = self._run_checks(execution, fail_closed=False)
        return None if blocked is None else _blocked_result(blocked)

    def _run_checks(self, execution: ToolExecution, *, fail_closed: bool) -> PreDecision:
        for check in list(self._checks):
            try:
                blocked = check(execution)
            except Exception as exc:
                logger.warning(
                    "tool %s: a check raised%s",
                    execution.name,
                    "; blocking the call" if fail_closed else "; asking again at dispatch",
                    exc_info=True,
                )
                if fail_closed:
                    return Block(f"a pre-execute policy failed: {exc}", rule="pre-execute")
                continue
            if blocked is not None:
                return blocked
        return None

    async def _run_pre(self, execution: ToolExecution) -> PreDecision:
        async def terminal() -> PreDecision:
            return None

        try:
            return await _waterfall(self._pre, terminal, execution)
        except Exception as exc:
            # Fail CLOSED: a gate that crashed cannot be read as consent.
            logger.warning(
                "tool %s: a pre_execute listener raised; blocking the call",
                execution.name,
                exc_info=True,
            )
            return Block(f"a pre-execute policy failed: {exc}", rule="pre-execute")

    async def _run_exec(
        self, execution: ToolExecution, run: Callable[[], Awaitable[ToolResult]]
    ) -> ToolResult:
        try:
            return await _waterfall(self._exec, run, execution)
        except Exception as exc:
            # Same shape the engine already gives a tool that raised.
            logger.warning(
                "tool %s: an execute listener raised", execution.name, exc_info=True
            )
            return ToolResult(
                value={"error": str(exc), "error_type": type(exc).__name__},
                status=ERROR,
            )

    async def _run_post(
        self, execution: ToolExecution, result: ToolResult
    ) -> ToolResult:
        async def terminal() -> ToolResult:
            return result

        try:
            transformed = await _waterfall(self._post, terminal, execution, result)
        except Exception:
            # Fail OPEN: bounding and presentation are not permission decisions, and a
            # broken formatter must not lose a result the tool actually produced.
            logger.warning(
                "tool %s: a post_execute listener raised; keeping the untransformed "
                "result",
                execution.name,
                exc_info=True,
            )
            return result
        if not isinstance(transformed, ToolResult):
            logger.warning(
                "tool %s: a post_execute listener returned %s, expected ToolResult",
                execution.name,
                type(transformed).__name__,
            )
            return result
        return transformed


def _blocked_result(blocked: Block) -> ToolResult:
    return ToolResult(
        value={
            "error": _blocked_text(blocked),
            "reason": blocked.reason,
            "rule": blocked.rule,
            # A runtime sidecar (`loop/result_recording.py` lifts it): never model-visible.
            **({"_shown": [list(entry) for entry in blocked.shown]} if blocked.shown else {}),
        },
        status=BLOCKED,
    )


def _register(bucket: list, listener: Any) -> Callable[[], None]:
    bucket.append(listener)
    disposed = False

    def dispose() -> None:
        nonlocal disposed
        if disposed:
            return
        disposed = True
        try:
            bucket.remove(listener)
        except ValueError:  # already gone
            pass

    return dispose


async def _waterfall(listeners: list, terminal: Callable[[], Awaitable[Any]], *args: Any):
    """Around-middleware over `listeners`, innermost call being `terminal`.

    Index 0 is OUTERMOST: it runs first and its transformation of the returned value
    applies last. A listener delegates with `next()` and short-circuits by returning
    without calling it.
    """
    # Snapshot: a listener that registers or disposes another mid-dispatch must not
    # change the chain this call is already walking.
    chain = list(listeners)

    async def step(index: int) -> Any:
        if index >= len(chain):
            return await terminal()

        async def nxt() -> Any:
            return await step(index + 1)

        return await chain[index](*args, nxt)

    return await step(0)


__all__ = [
    "BLOCKED",
    "INTERRUPTED",
    "TIMEOUT",
    "Block",
    "Check",
    "ERROR",
    "OK",
    "PreDecision",
    "ToolExecution",
    "ToolPipeline",
    "ToolResult",
]
