"""The repeat guard reached through a real turn, not just called directly.

The unit tests pin what the guard decides. These pin that it is actually WIRED — that
the reminder lands in the message the model reads, that denials reach it at all (they
never produce a result, so they bypass `_record_result` entirely), and that the turn cap
now says what the turn was stuck on instead of naming the counter that stopped it.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio

import aisuite as ai

from harness.loop import ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


class _Repeater(ProviderClient):
    """A model that asks for the same call forever — the shape of a stuck turn."""

    def __init__(self, turn):
        self._turn = turn
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        # A fresh id each time: real providers never reuse one, and the guard must not
        # secretly be keying on it.
        return AssistantTurn(
            tool_calls=[
                ToolCall(
                    id=f"call_{self.calls}",
                    name=self._turn.tool_calls[0].name,
                    arguments=self._turn.tool_calls[0].arguments,
                )
            ],
            finish_reason="tool_calls",
        )

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turn, *, approver=None, mode=None, max_iterations=12):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path)
    if mode is not None:
        permissions.mode = mode
    return TurnEngine(
        provider=_Repeater(turn),
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approver,
        max_iterations=max_iterations,
    )


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _tool_messages(engine):
    return [m for m in engine.messages if m.get("role") == "tool"]


# -- the reminder reaches the model ----------------------------------------------


def test_the_reminder_lands_in_the_message_the_model_reads(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path, _tool_turn("read_file", {"path": "a.txt"}))
    _collect(engine, "read it")

    contents = [m["content"] for m in _tool_messages(engine)]
    # Eight identical calls exhaust the reminders and the turn stops — the guard has
    # teeth now; the iteration cap is only the backstop behind it.
    assert len(contents) == 8
    assert "Note:" not in contents[0] and "Note:" not in contents[1]
    assert "3 calls to `read_file`" in contents[2]
    # …and the result itself is still there, ahead of the note.
    assert "hello" in contents[2]
    assert "5 times" in contents[4]


def test_a_tool_whose_answer_changes_is_never_nagged(tmp_path):
    """The signal is same-call-same-answer, not call count. A tool that returns
    something new each time is making progress, whatever the arguments look like."""
    registry = ToolRegistry()
    counter = {"n": 0}

    def poll(task_id: str) -> str:
        counter["n"] += 1
        return f"line {counter['n']}"

    poll.__name__ = "poll"
    registry.register(poll, metadata=ToolMetadata())
    engine = TurnEngine(
        provider=_Repeater(_tool_turn("poll", {"task_id": "bg-1"})),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        max_iterations=12,
    )
    _collect(engine, "poll it")
    assert all("Note:" not in m["content"] for m in _tool_messages(engine))


# -- denials are counted, interrupts are not -------------------------------------


def test_a_call_the_user_keeps_denying_is_counted(tmp_path):
    """Denials never reach `_record_result` — there is no result — so this is the path
    a guard hung off tool results alone would be blind to. It is also the loop a user is
    most likely to sit and watch."""

    async def refuse(request):
        return ApprovalOutcome.DENY

    engine = _engine(
        tmp_path,
        _tool_turn("write_file", {"path": "x.txt", "content": "hi"}),
        approver=refuse,
        mode=Mode.WORKSPACE_WRITE,
    )
    _collect(engine, "write it")

    contents = [m["content"] for m in _tool_messages(engine)]
    assert contents, "the denial should still be recorded as a tool message"
    assert any("3 calls to `write_file`" in c for c in contents)
    assert all("tool call not executed" in c for c in contents[:3])


def test_an_unknown_tool_asked_for_repeatedly_is_counted(tmp_path):
    engine = _engine(tmp_path, _tool_turn("no_such_tool", {"x": 1}))
    _collect(engine, "go")
    contents = [m["content"] for m in _tool_messages(engine)]
    assert any("3 calls to `no_such_tool`" in c for c in contents)


# -- the turn cap now explains itself --------------------------------------------


def test_the_cap_names_what_the_turn_was_stuck_on(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path, _tool_turn("read_file", {"path": "a.txt"}))
    events = _collect(engine, "read it")

    end = events[-1]
    assert end.type is EventType.TURN_END
    # The repeat guard, not the cap, stops this one — the loop was named and ignored
    # until the last threshold, and that is the story the ending tells.
    assert end.data["status"] == "repetition_stopped"
    repeated = end.data["repeated"]
    assert repeated["tool"] == "read_file"
    assert repeated["count"] == 8
    assert repeated["summary"]  # something sayable, not a bare payload


def test_a_turn_that_never_repeated_reports_nothing_to_blame(tmp_path):
    """`repeated` is absent, not zeroed: a turn that did twelve different things ran out
    of room, which is a different story from one that went in circles."""
    registry = ToolRegistry()
    seen = {"n": 0}

    def step(path: str) -> str:
        seen["n"] += 1
        return f"did {seen['n']}"

    step.__name__ = "step"
    registry.register(step, metadata=ToolMetadata())

    class _Varying(ProviderClient):
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            return AssistantTurn(
                tool_calls=[
                    ToolCall(id=f"c{self.calls}", name="step", arguments={"path": f"p{self.calls}"})
                ],
                finish_reason="tool_calls",
            )

        def capabilities(self, model):
            return ModelCapabilities()

    engine = TurnEngine(
        provider=_Varying(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        max_iterations=12,
    )
    events = _collect(engine, "go")
    assert events[-1].data["status"] == "max_iterations_exceeded"
    assert "repeated" not in events[-1].data


def test_repetition_does_not_carry_across_turns(tmp_path):
    """The user asking the same thing twice is not the agent going in circles."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(tmp_path, _tool_turn("read_file", {"path": "a.txt"}))
    _collect(engine, "read it")
    # Eight identical calls and the guard stops the turn — the counter peaks there.
    assert engine.repeats.worst.count == 8

    before = len(engine.messages)
    _collect(engine, "read it again")
    fresh = [m for m in engine.messages[before:] if m.get("role") == "tool"]
    assert "Note:" not in fresh[0]["content"]
    assert "Note:" not in fresh[1]["content"]
    assert "3 calls" in fresh[2]["content"]
