"""Stop during a tool call, and MCP deadlines that actually cancel.

Two problems, and the honest scope of each matters more than the fix:

**Stop.** The tools that thought about it already carried their own deadline — shell
`timeout_seconds` capped at `_MAX_TIMEOUT`, MCP 120s, connector httpx 30s, grep 30s,
git 15s — but the set was not closed: `tools/files.py` reads block with no deadline and
no contract, so a call was unbounded whenever a tool had simply not remembered. (The
runtime ceiling that closes that set landed later and lives in `tests/test_tool_deadline.py`;
this file stays about Stop and about MCP's cancellation.) What was missing here is that
the user could not
get out early: `await asyncio.to_thread(...)` was a bare await, so pressing Stop did
nothing until the tool's own deadline expired. Python cannot cancel a thread, so the
fix stops the TURN waiting, not the worker; these tests pin exactly that, including
the invariant that an interrupted call still answers the model.

**MCP.** Its 120s budget lived on the worker thread, where `future.result(timeout)`
can only stop the caller waiting — the abandoned request kept running on the server
loop and kept the session busy. The cancelling deadline now sits inside
`MCPManager.call`, where `wait_for` can actually stop the work, with the thread-side
budget demoted to a backstop that fires ten seconds later and cancels explicitly.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.extensions.mcp.client import CALL_TIMEOUT, MCPManager
from harness.extensions.mcp.config import MCPServerDef
from harness.extensions.mcp.tools import build_callables
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.pipeline import INTERRUPTED, OK


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


def _engine(tmp_path, tool):
    registry = ToolRegistry()
    registry.register(tool, metadata=ToolMetadata())
    return TurnEngine(
        provider=_OneCall(tool.__name__),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )


# -- Stop during a tool call ----------------------------------------------------


def _stop_when(engine, started, *, after=5.0):
    """Fire `request_interrupt` from the LOOP once `started` is set.

    `_cancel` is an `asyncio.Event`: setting it from a foreign thread never wakes the
    waiters, so a test that called `request_interrupt()` off-loop would silently prove
    nothing. Every real caller — the server's async handlers, the TUI action — already
    runs on the loop, which is the assumption `_astream` and `_interruptible` have made
    since before this change.
    """
    loop = asyncio.get_running_loop()

    def _wait_then_stop():
        started.wait(timeout=after)
        loop.call_soon_threadsafe(engine.request_interrupt)

    threading.Thread(target=_wait_then_stop, daemon=True).start()


def test_stop_resolves_the_turn_while_a_tool_is_still_running(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_tool() -> dict:
        """Slow."""
        started.set()
        release.wait(timeout=5)  # bounded so a regression fails instead of hanging
        return {"finished": True}

    engine = _engine(tmp_path, slow_tool)

    async def _go():
        _stop_when(engine, started)
        began = time.monotonic()
        events = [ev async for ev in engine.run("go")]
        return events, time.monotonic() - began

    events, elapsed = asyncio.run(_go())
    release.set()  # let the detached worker finish; it outlives the turn by design

    finished = next(ev for ev in events if ev.type is EventType.TOOL_FINISHED)
    assert finished.data["status"] == INTERRUPTED
    # The turn came back promptly rather than waiting out the tool's own deadline.
    assert elapsed < 3


def test_an_interrupted_call_still_answers_the_model(tmp_path):
    """The invariant the whole stop path is built around: a tool_call with no result
    orphans the history — hosted chat templates reject it and durable-resume re-prompts
    it."""
    started = threading.Event()
    release = threading.Event()

    def slow_tool() -> dict:
        """Slow."""
        started.set()
        release.wait(timeout=5)
        return {"finished": True}

    engine = _engine(tmp_path, slow_tool)

    async def _go():
        _stop_when(engine, started)
        return [ev async for ev in engine.run("go")]

    asyncio.run(_go())
    release.set()

    answers = [m for m in engine.messages if m.get("role") == "tool"]
    assert len(answers) == 1
    assert "interrupted" in answers[0]["content"]


def test_the_detached_worker_does_not_leak_an_unhandled_exception(tmp_path):
    """The abandoned future is drained on completion. Without that, a tool that raises
    after the turn moved on surfaces later as an unhandled-task warning with no owner
    and no way to trace it back."""
    started = threading.Event()
    release = threading.Event()

    def angry_tool() -> dict:
        """Raises after the turn has moved on."""
        started.set()
        release.wait(timeout=5)
        raise RuntimeError("too late")

    engine = _engine(tmp_path, angry_tool)
    seen: list = []

    async def _go():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: seen.append(ctx)
        )
        _stop_when(engine, started)
        events = [ev async for ev in engine.run("go")]
        release.set()
        await asyncio.sleep(0.2)  # let the detached worker settle
        return events

    asyncio.run(_go())
    assert seen == []


def test_a_tool_that_finishes_normally_is_untouched(tmp_path):
    def quick_tool() -> dict:
        """Quick."""
        return {"ok": True}

    engine = _engine(tmp_path, quick_tool)
    events = asyncio.run(_drain(engine))
    finished = next(ev for ev in events if ev.type is EventType.TOOL_FINISHED)
    assert finished.data["status"] == OK


async def _drain(engine):
    return [ev async for ev in engine.run("go")]


# -- MCP deadlines --------------------------------------------------------------


class _StuckSession:
    """A session whose call never answers, and that records being cancelled."""

    def __init__(self):
        self.cancelled = False

    async def call_tool(self, tool, arguments):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _Conn:
    def __init__(self, session):
        self.session = session
        self.tools: list = []


def test_mcp_call_cancels_the_request_when_its_deadline_expires():
    session = _StuckSession()
    manager = MCPManager()
    manager._conns[("", "srv")] = _Conn(session)

    async def _go():
        with pytest.raises(TimeoutError) as caught:
            await manager.call("srv", "slow", {}, timeout=0.05)
        # Give the cancellation a tick to be delivered into the coroutine.
        await asyncio.sleep(0.05)
        return caught.value

    error = asyncio.run(_go())
    assert session.cancelled, "the abandoned request must not keep running"
    assert "cancelled" in str(error)


def test_mcp_call_reports_which_tool_ran_out_of_time():
    manager = MCPManager()
    manager._conns[("", "srv")] = _Conn(_StuckSession())

    async def _go():
        with pytest.raises(TimeoutError) as caught:
            await manager.call("srv", "slow", {}, timeout=0.05)
        return str(caught.value)

    assert "srv/slow" in asyncio.run(_go())


def test_an_unknown_server_still_fails_fast():
    async def _go():
        with pytest.raises(RuntimeError, match="not connected"):
            await MCPManager().call("nope", "t", {})

    asyncio.run(_go())


def test_the_thread_backstop_sits_above_the_cancelling_deadline():
    """Ordering is the whole design: the deadline that can CANCEL must fire first, so
    the thread-side budget only ever trips when the server loop itself is wedged."""
    import inspect

    backstop = inspect.signature(build_callables).parameters["timeout"].default
    assert backstop > CALL_TIMEOUT


class _FakeTool:
    name = "slow"
    description = "slow"
    inputSchema = {"type": "object", "properties": {}}


class _FakeServer:
    name = "srv"
    include_tools = None
    exclude_tools = None
    requires_approval = True
    approval_by_hints = False


def test_the_cancelling_deadlines_message_survives_the_thread_bridge():
    """The issue is the bridge, not the server loop.

    On 3.11 `concurrent.futures.TimeoutError`, `asyncio.TimeoutError` and the builtin
    `TimeoutError` are ONE class, so the `except` guarding the thread-side backstop also
    caught the cancelling deadline's own `TimeoutError` and rewrote it as "MCP tool
    <tool> did not answer within <backstop>s" — a message that names the wrong tier and
    drops the server/tool prefix and the cancellation note (owner-read as a 120s backstop
    when the request had been cancelled at 110s). The tool's own failure must come
    through untouched; only an unfinished future is the backstop.
    """

    async def cancelled_at_its_own_deadline(tool, args):
        await asyncio.sleep(0.01)
        raise TimeoutError(
            f"MCP tool srv/{tool} did not answer within 0.3s (the request was cancelled)"
        )

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        [invoke] = build_callables(
            _FakeServer(),
            [_FakeTool()],
            cancelled_at_its_own_deadline,
            loop,
            timeout=0.4,
        )
        with pytest.raises(TimeoutError) as caught:
            invoke()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)

    message = str(caught.value)
    assert "srv/slow" in message
    assert "(the request was cancelled)" in message
    assert "backstop" not in message


def test_the_backstop_cancels_the_future_it_gave_up_on():
    """`future.result(timeout)` never stopped the coroutine on its own — that was the
    original leak, one rung up."""

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        [invoke] = build_callables(
            _FakeServer(),
            [_FakeTool()],
            lambda t, a: asyncio.sleep(3600),
            loop,
            timeout=0.05,
        )
        with pytest.raises(TimeoutError, match="slow"):
            invoke()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)


# -- a live server whose handlers block ----------------------------------------


_SLOW_SERVER = '''
import asyncio
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("slow-server")


@mcp.tool()
def blocking_slow(seconds: float = 2.0) -> str:
    """Blocks the server's own loop with a plain sleep."""
    time.sleep(seconds)
    return "blocked-done"


@mcp.tool()
async def async_slow(seconds: float = 2.0) -> str:
    """Waits without blocking the server's loop."""
    await asyncio.sleep(seconds)
    return "async-done"


@mcp.tool()
def fast() -> str:
    """Answers immediately."""
    return "fast"


if __name__ == "__main__":
    mcp.run()
'''


async def test_a_blocking_handler_cannot_hide_the_cancelling_deadline(tmp_path: Path):
    """The report's hypothesis, tested against a real server.

    A handler in `time.sleep` cannot make `asyncio.wait_for` miss its deadline: stdio
    and HTTP servers live in another process, so their sleep never blocks OUR loop, and
    the cancelling deadline fires on time (budgets patched to fractions of a second, so
    the 2s handlers prove the cancellation, not the wait). The bridge is the only place
    the truth was lost — see
    `test_the_cancelling_deadlines_message_survives_the_thread_bridge`. A fast tool must
    be untouched by any of it.
    """
    script = tmp_path / "slow_mcp_server.py"
    script.write_text(_SLOW_SERVER, encoding="utf-8")

    manager = MCPManager()
    server = MCPServerDef(
        name="slow-srv",
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
    )
    conn = await manager.ensure(server)
    loop = asyncio.get_running_loop()

    async def call_async(tool, args):
        return await manager.call("slow-srv", tool, args, timeout=0.3)

    callables = {
        fn.__name__: fn
        for fn in build_callables(server, conn.tools, call_async, loop, timeout=0.4)
    }
    try:
        assert await asyncio.to_thread(callables["mcp__slow-srv__fast"]) == "fast"

        for name in (
            "mcp__slow-srv__blocking_slow",
            "mcp__slow-srv__async_slow",
        ):
            began = time.monotonic()
            with pytest.raises(TimeoutError) as caught:
                await asyncio.to_thread(callables[name])
            elapsed = time.monotonic() - began
            message = str(caught.value)
            assert "slow-srv/" in message, message
            assert "(the request was cancelled)" in message, message
            assert "backstop" not in message, message
            # The 2s handler must not hold the caller past the 0.3s cancelling budget.
            assert elapsed < 2.0, f"{name} waited {elapsed:.2f}s"
    finally:
        await manager.aclose()
