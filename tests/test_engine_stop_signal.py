"""A TurnEngine used across two event loops must not invent a user Stop.

`asyncio.Event` binds to the first loop that awaits it and raises RuntimeError on every
other one. A TurnEngine outlives the loop of any single turn wherever turns are driven by
`asyncio.run`, and the engine races `self._cancel.wait()` against real work in three
places — the stream reader, an approval await, and a running tool. In every one of them
the race is decided by *which future finished*, so a raise inside `wait()` reads exactly
like the user pressing Stop.

That made the symptom silent rather than loud: from the second turn onward the stream
truncated mid-answer, approval prompts resolved themselves, and tool calls came back
"interrupted by user" with nobody having pressed anything.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio

import aisuite as ai

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry


class _Scripted(ProviderClient):
    """One tool call, then a plain answer — replayed for every turn."""

    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls % 2 == 1:
            return AssistantTurn(
                tool_calls=[
                    ToolCall(id=f"c{self.calls}", name=self.name, arguments=self.args)
                ],
                finish_reason="tool_calls",
            )
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(
        provider=_Scripted("read_file", {"path": "a.txt"}),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )


def _turn(engine, text):
    """Each turn under its OWN loop — what `asyncio.run` per turn actually does."""

    async def run():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(run())


def test_the_second_turn_on_a_new_loop_runs_its_tools_for_real(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path)

    first = _turn(engine, "read it")
    second = _turn(engine, "read it again")

    for label, events in (("first", first), ("second", second)):
        finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
        assert finished, f"{label} turn ran no tool"
        assert finished[0].data["status"] == "ok", f"{label} turn: {finished[0].data}"
        assert events[-1].data["status"] == "completed"

    contents = [m["content"] for m in engine.messages if m.get("role") == "tool"]
    assert len(contents) == 2
    assert all("interrupted" not in c for c in contents)
    assert all("hello" in c for c in contents)


def test_a_new_turn_does_not_inherit_the_last_turns_stop(tmp_path):
    """The reset replaces the event, which also clears it — and clearing is what all
    three entry points already did by hand. A turn that inherited the previous Stop
    would refuse to run at all."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path)
    _turn(engine, "read it")

    engine.request_interrupt()
    assert engine._cancel.is_set()

    events = _turn(engine, "read it again")
    assert not engine._cancel.is_set()
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[0].data["status"] == "ok"


def test_a_resumed_turn_runs_its_pending_tools_on_the_new_loop(tmp_path):
    """`resume()` replays the suspended turn's unanswered tool calls BEFORE it enters
    the model loop. Binding the Stop flag any later than the reset `resume()` already
    does would leave exactly that path racing an event from a dead loop — and its tool
    calls are the ones a durable restart depends on."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path)
    _turn(engine, "read it")  # binds the flag to a first, now-closed loop

    # A turn suspended mid-call: an assistant message whose tool call has no answer.
    engine.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "pending-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
                }
            ],
        }
    )

    async def _resume():
        return [ev async for ev in engine.resume()]

    events = asyncio.run(_resume())
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished, "resume ran no tool"
    assert finished[0].data["status"] == "ok", finished[0].data
    answer = next(m for m in engine.messages if m.get("tool_call_id") == "pending-1")
    assert "hello" in answer["content"]


def test_the_flag_is_stable_for_the_whole_turn(tmp_path):
    """Replacing the event is safe only at a turn boundary. Within a turn every waiter
    — the stream reader, an approval await, a running tool — must be racing the SAME
    object that `request_interrupt` sets, or Stop would set a flag nobody is watching.
    """
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path)
    seen = []

    def watcher(path: str) -> str:
        seen.append(engine._cancel)
        return "watched"

    watcher.__name__ = "watcher"
    engine.registry.register(watcher, metadata=ToolMetadata())

    class _TwoCalls(ProviderClient):
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            if self.calls <= 2:
                return AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id=f"w{self.calls}",
                            name="watcher",
                            arguments={"path": f"a{self.calls}.txt"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            return AssistantTurn(text="done", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    engine.provider = _TwoCalls()

    _turn(engine, "watch it")
    assert len(seen) >= 2
    assert all(ev is seen[0] for ev in seen), "the flag changed mid-turn"
    assert engine._cancel is seen[0], "request_interrupt would set a different object"
