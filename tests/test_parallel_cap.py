"""The bounded parallel pool (`max_parallel_tools`).

Low-risk reads may overlap, but one model response asking for thirty of them must not
start thirty at once. The cap chunks a parallel segment; it changes how many run
together, never the order results are recorded in.
"""

from __future__ import annotations

import asyncio
import threading

import aisuite as ai

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class _Tracker:
    """Shared concurrency counter for the fake reads."""

    def __init__(self, hold: float = 0.02):
        self.active = 0
        self.peak = 0
        self.order: list[str] = []
        self.hold = hold
        self._lock = threading.Lock()

    def enter(self, label: str) -> None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.order.append(label)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


def _read_tool(tracker: _Tracker, label: str):
    def _fn() -> dict:
        tracker.enter(label)
        try:
            # A real read has latency; without it the pool is invisible to the test.
            end = threading.Event()
            end.wait(tracker.hold)
            return {"label": label}
        finally:
            tracker.leave()

    _fn.__name__ = label  # the registry keys on the function name
    return _fn


def _engine(tmp_path, provider, *, parallel_calls: int, cap: int):
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)
    tracker = _Tracker()
    registry = ToolRegistry()
    for i in range(parallel_calls):
        registry.register(_read_tool(tracker, f"read_{i}"), metadata=low)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        max_parallel_tools=cap,
    )
    return engine, tracker


def _run(engine, text="go"):
    async def go():
        return [event async for event in engine.run(text)]

    return asyncio.run(go())


def test_a_batch_over_the_cap_runs_in_chunks(tmp_path):
    calls = [ToolCall(id=f"c{i}", name=f"read_{i}", arguments={}) for i in range(6)]
    provider = _Scripted(
        [
            AssistantTurn(tool_calls=calls, finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]
    )
    engine, tracker = _engine(tmp_path, provider, parallel_calls=6, cap=2)
    _run(engine)

    assert tracker.peak <= 2  # never more than two in flight
    # The pool did not serialize everything: the first two did overlap.
    assert tracker.peak == 2
    # Order is the model's order, whatever the chunking.
    assert tracker.order[:4] == ["read_0", "read_1", "read_2", "read_3"]
    results = [m for m in engine.messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in results] == [f"c{i}" for i in range(6)]


def test_the_cap_of_one_is_serial(tmp_path):
    calls = [ToolCall(id=f"c{i}", name=f"read_{i}", arguments={}) for i in range(4)]
    provider = _Scripted(
        [
            AssistantTurn(tool_calls=calls, finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]
    )
    engine, tracker = _engine(tmp_path, provider, parallel_calls=4, cap=1)
    _run(engine)
    assert tracker.peak == 1
    assert tracker.order == ["read_0", "read_1", "read_2", "read_3"]


def test_zero_is_the_old_uncapped_run(tmp_path):
    calls = [ToolCall(id=f"c{i}", name=f"read_{i}", arguments={}) for i in range(5)]
    provider = _Scripted(
        [
            AssistantTurn(tool_calls=calls, finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]
    )
    engine, tracker = _engine(tmp_path, provider, parallel_calls=5, cap=0)
    _run(engine)
    assert tracker.peak == 5  # everything at once, exactly as before


def test_a_batch_within_the_cap_is_untouched(tmp_path):
    calls = [ToolCall(id=f"c{i}", name=f"read_{i}", arguments={}) for i in range(3)]
    provider = _Scripted(
        [
            AssistantTurn(tool_calls=calls, finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]
    )
    engine, tracker = _engine(tmp_path, provider, parallel_calls=3, cap=10)
    _run(engine)
    assert tracker.peak == 3


def test_the_config_default_reaches_the_engine(tmp_path):
    """`build_engine` without an explicit cap reads `Config.max_parallel_tools`."""
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    engine = build_engine(
        agent=reeve_agent(),
        workspace=tmp_path,
        provider=_Scripted([]),
    )
    assert engine.max_parallel_tools == 10  # the shipping default


def test_chunks_are_a_pure_function_of_the_cap(tmp_path):
    engine, _ = _engine(tmp_path, _Scripted([]), parallel_calls=0, cap=3)
    calls = [ToolCall(id=f"c{i}", name="x", arguments={}) for i in range(7)]
    chunks = engine._parallel_chunks(calls)
    assert [len(c) for c in chunks] == [3, 3, 1]
    assert [c.id for chunk in chunks for c in chunk] == [f"c{i}" for i in range(7)]

    engine.max_parallel_tools = 0  # uncapped
    assert [len(c) for c in engine._parallel_chunks(calls)] == [7]
