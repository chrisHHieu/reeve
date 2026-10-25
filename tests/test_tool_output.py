"""Live tool output: a running command's lines reach the surface before it ends.

The feature exists for one moment: a build or a test run that prints for a minute. Before
this, everything the command said arrived at once when it finished, so the screen showed a
spinner and then a wall of text — the difference between watching work and waiting for it.

Two contracts, and both are tested here rather than in the GUI because both are the engine's:

  · a tool's `emit()` reaches the turn's event stream, tagged with its call id, WHILE the
    call is still running (worker thread → contextvar → drain);
  · the call's RESULT is unchanged. Streaming is a display convenience; the model still reads
    the whole output once, and a surface that missed the chunks has missed nothing.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry, output
from harness.tools.shell import LocalExecutor

# Per-OS snippets, like tests/test_shell.py: the executor drives the native shell, so `printf`
# is not a command on Windows (it echoed PowerShell's "not recognized" error instead).
_WIN = sys.platform == "win32"
TWO_LINES = "Write-Output alpha; Write-Output beta" if _WIN else "echo alpha; echo beta"
THREE_LINES = (
    "Write-Output one; Write-Output two; Write-Output three"
    if _WIN
    else "echo one; echo two; echo three"
)


@dataclass
class _Meta:
    category: str = ""
    risk_level: str = "low"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


# -- the channel itself --------------------------------------------------------------


def test_emit_reaches_the_sink_with_the_call_id():
    seen: list[tuple[str, str]] = []
    with output.capture("call-1", lambda call_id, text: seen.append((call_id, text))):
        output.emit("line one\n")
        output.emit("line two\n")
    assert seen == [("call-1", "line one\n"), ("call-1", "line two\n")]


def test_emit_outside_a_capture_is_a_no_op():
    # A tool that streams must also be usable where nobody is listening: a unit test, a
    # subagent, the TUI. A raise here would make `emit()` a liability for every caller.
    output.emit("nobody is listening\n")


def test_a_capture_does_not_leak_into_the_next_call():
    after: list[tuple[str, str]] = []
    with output.capture("call-1", lambda c, t: after.append((c, t))):
        pass
    output.emit("after the block\n")
    assert after == []


def test_the_capture_survives_the_thread_hop(tmp_path: Path):
    # The real shape: the collection runs `run()` on an executor thread, and the contextvar
    # has to travel WITH it (that is why this is a contextvar and not a hand-passed object).
    seen: list[tuple[str, str]] = []
    executor = LocalExecutor(cwd=tmp_path, default_timeout=20)
    try:
        with output.capture("call-9", lambda c, t: seen.append((c, t))):
            result = asyncio.run(asyncio.to_thread(executor.run, TWO_LINES))
    finally:
        executor.close()
    assert result["exit_code"] == 0
    assert all(call_id == "call-9" for call_id, _ in seen)
    # The invariant that matters: what the reader saw while it ran is EXACTLY the text the
    # model is handed afterwards, in the same order. A stream that differs is a second
    # account of one command, and the two would drift.
    assert "".join(text for _, text in seen) == result["output"]
    assert "alpha" in result["output"] and "beta" in result["output"]


def test_the_stream_keeps_the_command_s_order(tmp_path: Path):
    seen: list[str] = []
    executor = LocalExecutor(cwd=tmp_path, default_timeout=20)
    try:
        with output.capture("c", lambda _c, text: seen.append(text)):
            executor.run(THREE_LINES)
    finally:
        executor.close()
    streamed = "".join(seen).split()
    assert streamed == ["one", "two", "three"]


# -- through the engine --------------------------------------------------------------


def _engine(tmp_path: Path, turns: list[AssistantTurn], tool):
    registry = ToolRegistry()
    registry.register(tool, metadata=_Meta(risk_level="high"))
    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS),
        model="m",
    )
    return engine


def _run(engine: TurnEngine, text: str = "go") -> list[Any]:
    async def go():
        return [event async for event in engine.run(text)]

    return asyncio.run(go())


def test_a_running_call_publishes_deltas_before_its_result(tmp_path: Path):
    def slow_stream(seconds: float = 0.35) -> str:
        """Run something that prints while it works.

        Args:
            seconds: how long to keep printing
        """
        import time

        for i in range(3):
            output.emit(f"tick {i}\n")
            time.sleep(seconds / 3)
        return "finished"

    turns = [
        AssistantTurn(
            tool_calls=[ToolCall(id="c1", name="slow_stream", arguments={})],
            finish_reason="tool_calls",
        ),
        AssistantTurn(text="done", finish_reason="stop"),
    ]
    engine = _engine(tmp_path, turns, slow_stream)
    events = _run(engine)

    deltas = [e for e in events if e.type is EventType.TOOL_OUTPUT_DELTA]
    assert [d.data["text"] for d in deltas] == ["tick 0\n", "tick 1\n", "tick 2\n"]
    assert {d.data["call_id"] for d in deltas} == {"c1"}

    # Ordering is the whole point: at least one chunk landed BEFORE the call finished.
    kinds = [e.type for e in events]
    assert kinds.index(EventType.TOOL_OUTPUT_DELTA) < kinds.index(EventType.TOOL_FINISHED)

    # And the result still carries the tool's answer.
    finished = next(e for e in events if e.type is EventType.TOOL_FINISHED)
    assert finished.data["status"] == "ok"


def test_a_call_that_publishes_nothing_emits_no_deltas(tmp_path: Path):
    def quiet() -> str:
        """Say nothing."""
        return "ok"

    turns = [
        AssistantTurn(
            tool_calls=[ToolCall(id="c1", name="quiet", arguments={})],
            finish_reason="tool_calls",
        ),
        AssistantTurn(text="done", finish_reason="stop"),
    ]
    events = _run(_engine(tmp_path, turns, quiet))
    assert not [e for e in events if e.type is EventType.TOOL_OUTPUT_DELTA]
