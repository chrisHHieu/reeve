"""Record the request before acting on it.

The session log is best-effort — `SessionLog.append` never raises and latches a `_broken`
flag — and the conversation store is written at checkpoints. Between those two facts sits a
window where a turn has decided to run a consequential tool and nothing has put that
decision on disk. It matters most for the paths nobody is watching: `engine.resume` re-drives
unanswered tool calls from the saved thread, so what it reads has to reflect what was done,
and a self-wake or a scheduled run has no user to notice a missing step.

These tests pin the gate, the fail-closed direction, and the cost — a read must not pay for
a flush, and a host with no store must behave exactly as it did before.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.session.durability import REFUSAL, durable_before_effect
from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.safety.risk import RiskClass, classify
from harness.tools import ToolRegistry
from harness.tools.pipeline import BLOCKED, OK, ToolExecution, ToolPipeline, ToolResult


# -- the gate, on its own ---------------------------------------------------------


def _probe(*, name="writer", flush_of, predicate=None):
    """Dispatch one call and report (result, whether the body ran)."""
    ran: list[str] = []

    async def run():
        ran.append("ran")
        # The pipeline's terminal must produce a ToolResult — `run` is the thing the engine
        # wraps, and the engine's own `run` returns one.
        return ToolResult({"ok": True})

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(
        durable_before_effect(
            flush_of, consequential=predicate or (lambda n, a: n == "writer")
        )
    )

    async def _go():
        return await pipeline.dispatch(
            ToolExecution(name=name, arguments={}, call_id="c1"), run
        )

    return asyncio.run(_go()), ran


def test_a_consequential_call_runs_when_the_record_landed():
    result, ran = _probe(flush_of=lambda: _returns(True))
    assert result.status == OK
    assert ran == ["ran"]


def test_a_consequential_call_is_blocked_when_the_record_did_not_land():
    """Not an ERROR: the tool did not break, the runtime refused to run it."""
    result, ran = _probe(flush_of=lambda: _returns(False))
    assert result.status == BLOCKED
    assert ran == [], "the whole point is that the effect did not happen"
    assert result.value["reason"] == REFUSAL


def test_a_flush_that_raises_is_also_a_refusal():
    """Fail closed. A durability check that crashed cannot be read as "the record is fine",
    which is the same rule `_run_pre` already applies to a gate that raises."""
    result, ran = _probe(flush_of=lambda: _raises())
    assert result.status == BLOCKED
    assert ran == []


def test_a_read_does_not_pay_for_a_flush():
    """Reads change nothing, so no crash could make their record untrue. Flushing for them
    would tax the common path to protect nothing."""
    flushes: list[int] = []

    async def flush():
        flushes.append(1)
        return True

    result, ran = _probe(name="reader", flush_of=lambda: flush)
    assert result.status == OK
    assert ran == ["ran"]
    assert flushes == []


def test_a_host_with_no_flush_behaves_exactly_as_before():
    """The CLI, the bench and every bare engine in the test suite. This is what makes the
    guarantee optional rather than a new way for turns to fail."""
    result, ran = _probe(flush_of=lambda: None)
    assert result.status == OK
    assert ran == ["ran"]


def test_the_flush_is_read_per_call():
    """A host that attaches its store after `build_engine` still reaches the gate — which is
    every host, since the manager installs `engine.flush` post-construction."""
    holder: dict = {}

    async def flush():
        holder["called"] = True
        return True

    looked_up = lambda: holder.get("flush")  # noqa: E731 — the point is the lookup

    result, ran = _probe(flush_of=looked_up)
    assert result.status == OK and ran == ["ran"], "nothing attached yet: ungated"

    holder["flush"] = flush
    result, ran = _probe(flush_of=looked_up)
    assert result.status == OK and holder.get("called") is True


def _returns(value):
    async def flush():
        return value

    return flush


def _raises():
    async def flush():
        raise OSError("no space left on device")

    return flush


def test_the_flush_happens_before_the_effect():
    """The property, not the mechanism: the record must already be durable when the body
    runs. Asserted from inside the body, which is the only place that can tell."""
    order: list[str] = []

    async def flush():
        order.append("flush")
        return True

    async def run():
        order.append("effect")
        return ToolResult({"ok": True})

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(
        durable_before_effect(lambda: flush, consequential=lambda n, a: True)
    )

    async def _go():
        return await pipeline.dispatch(
            ToolExecution(name="writer", arguments={}, call_id="c1"), run
        )

    assert asyncio.run(_go()).status == OK
    assert order == ["flush", "effect"]


# -- through a real engine --------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool_turn(name, args):
    return AssistantTurn(
        tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _engine_with(tmp_path, name, tool, *, metadata, flush):
    registry = ToolRegistry()
    registry.register(tool, metadata=metadata)
    engine = TurnEngine(
        provider=_Scripted([_tool_turn(name, {}), AssistantTurn(text="done", finish_reason="stop")]),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS),
        model="gpt-5.5",
    )
    engine.flush = flush
    engine.pipeline.on_pre_execute(
        durable_before_effect(
            lambda: engine.flush,
            # The engine's own rule, inlined: consequential means "not a READ".
            consequential=lambda n, a: classify(n, metadata, None) is not RiskClass.READ,
        )
    )
    return engine


def test_a_write_is_gated_and_the_tool_does_not_run(tmp_path):
    ran: list[str] = []
    # `requires_approval` is what `classify` reads for a tool it does not know by name, and
    # an MCP or connector tool that declares approval is exactly this shape.
    metadata = ai.ToolMetadata(
        category="filesystem", risk_level="low", requires_approval=True
    )
    # If this stops being consequential the test would pass without exercising the gate.
    assert classify("probe_writer", metadata, None) is not RiskClass.READ

    def probe_writer() -> dict:
        """Write."""
        ran.append("ran")
        return {"ok": True}

    engine = _engine_with(
        tmp_path, "probe_writer", probe_writer, metadata=metadata, flush=_returns(False)
    )
    events = asyncio.run(_drain(engine))

    assert ran == [], "a write whose record could not be persisted must not happen"
    finished = [e for e in events if e.type.value == "tool_finished"]
    assert finished[0].data["status"] == BLOCKED
    answers = [m for m in engine.messages if m.get("role") == "tool"]
    assert REFUSAL in answers[0]["content"]


def test_a_write_runs_once_the_record_landed(tmp_path):
    ran: list[str] = []
    metadata = ai.ToolMetadata(
        category="filesystem", risk_level="low", requires_approval=True
    )

    def probe_writer() -> dict:
        """Write."""
        ran.append("ran")
        return {"ok": True}

    engine = _engine_with(
        tmp_path, "probe_writer", probe_writer, metadata=metadata, flush=_returns(True)
    )
    asyncio.run(_drain(engine))
    assert ran == ["ran"]


async def _drain(engine):
    return [event async for event in engine.run("go")]


# -- the host's side --------------------------------------------------------------


class _BrokenStore:
    def save(self, record):
        raise OSError("Read-only file system")


def test_the_manager_reports_a_failed_flush_rather_than_raising():
    """`save` raising is the real disk-full shape. The gate needs a bool, and the session
    must not die because the disk did."""
    from harness.interface.server.manager import SessionManager

    manager = _bare_manager(SessionManager)
    manager.session_store = _BrokenStore()
    engine = _bare_engine()
    assert asyncio.run(manager.flush_session("s1", engine)) is False


def test_the_manager_reports_a_landed_flush():
    from harness.interface.server.manager import SessionManager

    class _Store:
        def __init__(self):
            self.saved: list = []

        def save(self, record):
            self.saved.append(record)

    manager = _bare_manager(SessionManager)
    manager.session_store = _Store()
    engine = _bare_engine()
    assert asyncio.run(manager.flush_session("s1", engine)) is True
    # Saved, not merely reported: the assertion above is about the bool, this one is about
    # the record. A stub that stored nothing would pass the first and mean nothing.
    assert len(manager.session_store.saved) == 1


def _bare_manager(cls):
    """A manager with only what `save` reads. Standing up the real one would drag in
    sockets, stores and a scheduler to answer a question about a bool."""
    manager = object.__new__(cls)
    manager._prefs = {}
    return manager


def _bare_engine():
    return TurnEngine(
        provider=_Scripted([]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root="."),
        model="gpt-5.5",
    )
