"""The wall-clock ceiling on a tool call, and the belief that made it necessary.

This file exists because a documented invariant was false. `engine._dispatch` said so:

    the honest contract is: the TURN stops waiting, the call keeps running to its own
    deadline. That is why every tool with blocking work carries one of its own

and `tests/test_tool_deadlines.py` repeated it. It held for shell, terminal, MCP, grep,
git and the connectors — and not for `tools/files.py`, the largest family after shell:
`read_file` iterates a blocking `open()` (and `read_bytes()` for a picture), and neither
declared a contract, so neither had a budget. A read against a stalled network mount was
unbounded and the runtime had no way to say so.

The fix is a ceiling resolved in two layers (`tools/deadline.py`):

    effective = contract.timeout_ms   if declared   (a real budget, deliberately set)
              = DEADLINE_CEILING_MS   otherwise     (a backstop, applies to everything)

so the guarantee stops depending on every tool remembering. These tests pin the
mechanism, the ORDERING that keeps it from pre-empting a tool's own deadline, and the
absences — no leak, no orphan, no budget on the wire.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio
import time

import pytest

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.extensions.mcp.client import CALL_TIMEOUT
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.code_mode import DEFAULT_TIMEOUT as CODE_MODE_TIMEOUT
from harness.tools.contract import ToolContract, attach_contract
from harness.tools.deadline import DEADLINE_CEILING_MS, budget_for, deadline_listener
from harness.tools.pipeline import ERROR, INTERRUPTED, OK, TIMEOUT, ToolExecution, ToolPipeline, ToolResult
from harness.tools.shell import _MAX_TIMEOUT as SHELL_MAX_TIMEOUT
from harness.tools.terminal import MAX_SEND_TIMEOUT


# -- the mechanism, at the pipeline ---------------------------------------------


def _dispatch(budget, run, *, ceiling_ms=DEADLINE_CEILING_MS):
    """Run one call through a pipeline armed with a ceiling, and time it."""
    contract = None if budget is None else ToolContract(timeout_ms=budget)

    async def _go():
        pipeline = ToolPipeline()
        pipeline.on_execute(deadline_listener(ceiling_ms))
        began = time.monotonic()
        result = await pipeline.dispatch(
            ToolExecution(name="probe", arguments={}, call_id="c1", contract=contract),
            run,
        )
        return result, time.monotonic() - began

    return asyncio.run(_go())


async def _sleep_then(value, seconds):
    await asyncio.sleep(seconds)
    return ToolResult(value)


def test_a_call_that_declares_no_budget_is_bounded_by_the_ceiling():
    """The case that was unbounded: a tool with no contract at all."""
    result, elapsed = _dispatch(None, lambda: _sleep_then("never", 30), ceiling_ms=120)
    assert result.status == TIMEOUT
    assert result.value["reason"] == "deadline"
    assert result.value["timeout_seconds"] == pytest.approx(0.12)
    assert elapsed < 2, "the turn must stop waiting promptly, not at 30s"


def test_the_ceiling_is_a_fallback_so_a_tighter_declared_budget_wins():
    result, elapsed = _dispatch(
        100, lambda: _sleep_then("never", 30), ceiling_ms=60_000
    )
    assert result.status == TIMEOUT
    assert elapsed < 2, "must fire at 100ms, not at the 60s ceiling"


def test_a_declared_budget_above_the_ceiling_is_honoured():
    """The contract is authoritative, not a cap.

    A tool author saying "this call legitimately takes three seconds" outranks any
    default — otherwise a generous ceiling would quietly become a limit, and a
    slow-but-working call would be reported as a failure while the work went on to
    happen anyway.
    """
    result, elapsed = _dispatch(
        3_000, lambda: _sleep_then("finished", 0.3), ceiling_ms=100
    )
    assert result.status == OK
    assert result.value == "finished"
    assert elapsed >= 0.25


def test_a_call_that_finishes_inside_its_budget_is_untouched():
    result, _ = _dispatch(60_000, lambda: _sleep_then({"ok": True}, 0.01))
    assert result.status == OK
    assert result.value == {"ok": True}


def test_a_raising_call_still_settles_as_an_error():
    """Unchanged behaviour: the deadline wraps dispatch, it does not reinterpret it."""

    async def _boom():
        raise RuntimeError("tool blew up")

    result, _ = _dispatch(60_000, _boom)
    assert result.status == ERROR
    assert "tool blew up" in result.value["error"]


def test_the_abandoned_call_does_not_leak_an_unhandled_exception():
    """A tool that raises AFTER the turn stopped waiting must not surface later as an
    unhandled-task warning with no owner — the same leak `_dispatch` drains for."""
    seen: list = []

    async def _late_boom():
        await asyncio.sleep(0.3)
        raise RuntimeError("too late")

    async def _go():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: seen.append(ctx)
        )
        pipeline = ToolPipeline()
        pipeline.on_execute(deadline_listener(50))
        result = await pipeline.dispatch(
            ToolExecution(name="probe", arguments={}, call_id="c1"), _late_boom
        )
        assert result.status == TIMEOUT
        await asyncio.sleep(0.5)  # let the abandoned call settle
        return result

    asyncio.run(_go())
    assert seen == []


# -- the vocabulary ---------------------------------------------------------------


def test_an_expired_call_is_a_failure_and_not_a_stop():
    """`TIMEOUT` and `INTERRUPTED` mean opposite things to a reader: a stop is a person's
    decision and the call may have been about to succeed; a timeout is the tool failing
    to do its job."""
    expired = ToolResult({"error": "nope"}, TIMEOUT)
    assert not expired.ok
    # Every engine branch tests `status == "ok"`, so a status that is neither OK nor a
    # stop settles as a failure — which is what a timeout is. Spelled out so renaming
    # TIMEOUT onto one of those two cannot silently turn timeouts into something else.
    assert TIMEOUT not in (OK, INTERRUPTED)


def test_budget_for_prefers_the_contract_and_falls_back_to_the_ceiling():
    ceiling = 5_000
    assert budget_for(ToolExecution("t", {}, "c1"), ceiling) == ceiling
    assert (
        budget_for(ToolExecution("t", {}, "c1", ToolContract(timeout_ms=1234)), ceiling)
        == 1234
    )


# -- the ordering that makes the layering work -----------------------------------
#
# The ceiling must sit ABOVE every budget a tool enforces for itself. Below it, a
# slow-but-working call would be reported as a timeout while the work went on to
# happen — worse than no backstop, because the model is told it failed.


def test_the_ceiling_sits_above_every_self_enforced_deadline():
    self_enforced = {
        "run_shell (_MAX_TIMEOUT)": SHELL_MAX_TIMEOUT,
        "terminal_send (MAX_SEND_TIMEOUT)": MAX_SEND_TIMEOUT,
        "mcp (CALL_TIMEOUT)": CALL_TIMEOUT,
        "run_code (DEFAULT_TIMEOUT)": CODE_MODE_TIMEOUT,
    }
    for label, seconds in self_enforced.items():
        assert DEADLINE_CEILING_MS > seconds * 1000, (
            f"the ceiling pre-empts {label}, which bounds itself at {seconds}s"
        )


def test_every_declared_budget_clears_the_deadline_it_sits_on_top_of():
    """Where a tool bounds its own work, the declared budget must not fire first.

    A table rather than a loop over every contract, because "what does this tool bound
    itself at" is knowledge no registry holds — the pairing IS the assertion.
    """
    from harness.extensions.mcp.tools import _contract as mcp_contract
    from harness.tools.search import GREP_CONTRACT
    from harness.integrations.web.fetch import WEB_FETCH_CONTRACT
    from harness.integrations.web.tool import WEB_SEARCH_CONTRACT

    pairs = [
        # 30 s subprocess timeout on the ripgrep branch; 20 s httpx in the web providers.
        ("grep vs its 30s subprocess timeout", GREP_CONTRACT.timeout_ms, 30_000),
        # The model may pass timeout=120s; the declared budget must clear that maximum.
        ("web_fetch vs its 120s httpx client", WEB_FETCH_CONTRACT.timeout_ms, 120_000),
        ("web_search vs its 20s httpx provider", WEB_SEARCH_CONTRACT.timeout_ms, 20_000),
        (
            "mcp vs the cancelling deadline in MCPManager.call",
            mcp_contract("d", "remote", "srv").timeout_ms,
            int(CALL_TIMEOUT * 1000),
        ),
    ]
    for label, declared, inner in pairs:
        assert declared is not None, f"{label}: no budget declared"
        assert declared > inner, f"{label}: {declared}ms would pre-empt a {inner}ms limit"


def test_a_declared_budget_never_outlives_the_ceiling():
    """A tool MAY exceed the ceiling deliberately — the contract is authoritative — but
    the shipped budgets must not: one longer than the backstop is a number nobody chose,
    and it would read as the ceiling being ignored."""
    from harness.tools.files import READ_FILE_CONTRACT
    from harness.tools.files.contracts import BORROWED_CONTRACTS as _BORROWED_CONTRACTS
    from harness.tools.history import SEARCH_HISTORY_CONTRACT
    from harness.tools.notebook_tool import CONTRACT as EDIT_NOTEBOOK_CONTRACT
    from harness.tools.search import GREP_CONTRACT
    from harness.integrations.web.fetch import WEB_FETCH_CONTRACT
    from harness.integrations.web.tool import WEB_SEARCH_CONTRACT

    declared = {
        "read_file": READ_FILE_CONTRACT.timeout_ms,
        "edit_notebook": EDIT_NOTEBOOK_CONTRACT.timeout_ms,
        "grep": GREP_CONTRACT.timeout_ms,
        "web_fetch": WEB_FETCH_CONTRACT.timeout_ms,
        "web_search": WEB_SEARCH_CONTRACT.timeout_ms,
        "search_history": SEARCH_HISTORY_CONTRACT.timeout_ms,
        **{name: c.timeout_ms for name, c in _BORROWED_CONTRACTS.items()},
    }
    for name, budget in declared.items():
        assert budget is not None, f"{name} declared no budget"
        assert budget <= DEADLINE_CEILING_MS, (
            f"{name} declares {budget}ms, past the {DEADLINE_CEILING_MS}ms ceiling"
        )


def test_the_families_that_had_no_deadline_at_all_now_have_one():
    """The specific gap, named. `tools/files.py` reads block with no internal deadline,
    so a contract budget is the only thing that can bound them."""
    from harness.tools.files import READ_FILE_CONTRACT
    from harness.tools.files.contracts import BORROWED_CONTRACTS as _BORROWED_CONTRACTS
    from harness.tools.notebook_tool import CONTRACT as EDIT_NOTEBOOK_CONTRACT

    for label, budget in {
        "read_file": READ_FILE_CONTRACT.timeout_ms,
        "edit_notebook": EDIT_NOTEBOOK_CONTRACT.timeout_ms,
        **{n: c.timeout_ms for n, c in _BORROWED_CONTRACTS.items()},
    }.items():
        assert budget and budget > 0, f"{label} blocks with no deadline"


def test_the_budget_never_reaches_a_provider_request():
    """`timeout_ms` is a runtime concern. `registry.schemas()` is an explicit allowlist
    (`name`, `description`, `parameters`) and must stay one."""
    from harness.tools.files import READ_FILE_CONTRACT

    def read_file(path: str) -> dict:
        """Read a file.

        :param path: where
        """
        return {"path": path}

    attach_contract(read_file, READ_FILE_CONTRACT)
    registry = ToolRegistry()
    registry.register(read_file, metadata=ToolMetadata())

    [schema] = registry.schemas()
    assert set(schema) == {"type", "function"}
    assert set(schema["function"]) == {"name", "description", "parameters"}
    assert "timeout" not in str(schema).lower()


# -- the engine wiring -----------------------------------------------------------


class _OneCall(ProviderClient):
    def __init__(self, name):
        self._turns = [
            AssistantTurn(
                tool_calls=[ToolCall(id="c1", name=name, arguments={})],
                finish_reason="tool_calls",
            ),
            AssistantTurn(text="done", finish_reason="stop"),
        ]

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def test_build_engine_arms_the_ceiling_exactly_once(tmp_path):
    """The registration IS the feature: a listener module nothing registers is the state
    `on_execute` was already in — defined, documented, and empty."""
    from harness.compose import build_engine
    from harness.modes.agents import chat_agent

    engine = build_engine(agent=chat_agent(), workspace=tmp_path)
    # `_waterfall` walks every listener on every call and index 0 is outermost, so a
    # second listener here would silently change which budget applies.
    assert len(engine.pipeline._exec) == 1


def test_an_expired_call_still_answers_the_model(tmp_path):
    """The invariant the whole stop path is built around, now for timeouts too: a
    tool_call with no result orphans the history — hosted chat templates reject it and
    durable-resume re-prompts it."""

    def slow_tool() -> dict:
        """Slow."""
        time.sleep(1.5)
        return {"finished": True}

    registry = ToolRegistry()
    registry.register(slow_tool, metadata=ToolMetadata())
    engine = TurnEngine(
        provider=_OneCall("slow_tool"),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.pipeline.on_execute(deadline_listener(100))

    async def _go():
        events = [ev async for ev in engine.run("go")]
        await asyncio.sleep(1.8)  # let the abandoned call finish before the loop closes
        return events

    events = asyncio.run(_go())

    finished = next(ev for ev in events if ev.type is EventType.TOOL_FINISHED)
    assert finished.data["status"] == TIMEOUT
    answers = [m for m in engine.messages if m.get("role") == "tool"]
    assert len(answers) == 1, "an unanswered tool_call orphans the history"
    assert "did not finish within" in answers[0]["content"]
