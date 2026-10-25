"""The session event log — what HAPPENED, beside what was said.

`messages` is the model's view: rewritten by compaction, stripped of sidecars, and by the
time a message is appended the step that produced it is over. The question "where did the
four minutes go" by keeping the conversation AS an append-only event log and deriving the
message list from it; this log sits BESIDE the message list instead, which is the same
information without trading a working execution path for a diagnostic view.

Two properties run through every test here:

  · it is OBSERVATION, never inference. Nothing computes a duration; the reader does, and
    can therefore tell a missing stamp from a zero.
  · it must never cost a turn. A full disk, an unwritable path, a runaway loop — every one
    of those loses a row in a report, and none of them may raise into the agent loop.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import StreamChunk, TokenUsage
from harness.session.session_log import MAX_EVENTS, MAX_FIELD_CHARS, NULL_LOG, SessionLog
from harness.tools import ToolRegistry


# -- the file ------------------------------------------------------------------------


def test_events_are_appended_in_order_with_their_own_sequence(tmp_path):
    log = SessionLog(tmp_path / "s.events.jsonl")
    log.append("turn/start", turn=1)
    log.append("step/start", turn=1, step=1)

    events = log.read()
    assert [e["type"] for e in events] == ["turn/start", "step/start"]
    assert [e["seq"] for e in events] == [0, 1]
    assert all(isinstance(e["t"], float) for e in events)


def test_a_reopened_log_continues_the_sequence(tmp_path):
    """A restart must not restart the numbering: `seq` is how a reader addresses a
    row, and two rows with the same number are two rows nobody can link to."""
    path = tmp_path / "s.events.jsonl"
    first = SessionLog(path)
    first.append("turn/start", turn=1)
    first.append("turn/end", turn=1, reason="completed")

    second = SessionLog(path)
    second.append("turn/start", turn=2)
    assert [e["seq"] for e in second.read()] == [0, 1, 2]


def test_a_none_field_is_absent_rather_than_null(tmp_path):
    """Absent means "this did not apply"; `null` means "it applied and was empty", and a
    reader that has to tell them apart should not have to guess which one it got."""
    log = SessionLog(tmp_path / "s.events.jsonl")
    log.append("assistant/message", turn=1, reasoning=None, text="hi")
    event = log.read()[0]
    assert "reasoning" not in event
    assert event["text"] == "hi"


def test_the_trace_is_stamped_on_every_row_and_wins_over_a_passing_caller(tmp_path):
    """The chain id is the log's own identity (the trace id, one level above
    `sessionId`): a reader groups a child's rows with its parent's without joining on
    paths, and no caller can misattribute a row by passing its own `trace=`."""
    log = SessionLog(tmp_path / "s.events.jsonl", trace="root-1")
    log.append("turn/start", turn=1)
    log.append("tool/call", turn=1, step=1, name="read_file", trace="someone-else")

    assert [e["trace"] for e in log.read()] == ["root-1", "root-1"]


def test_a_log_without_a_chain_carries_no_trace_field(tmp_path):
    """An ad-hoc log (a test fixture, a replay) is not part of a chain. Absent, not a
    fabricated id — the same rule every optional field follows here."""
    log = SessionLog(tmp_path / "s.events.jsonl")
    log.append("turn/start", turn=1)
    assert "trace" not in log.read()[0]


def test_a_long_field_keeps_its_head_and_says_how_much_went(tmp_path):
    """A tool result can be 20k characters and the message list already holds it at full
    size. This log is read to find WHICH step was slow, not to re-read its output."""
    log = SessionLog(tmp_path / "s.events.jsonl")
    log.append("tool/result", content="x" * (MAX_FIELD_CHARS + 5_000))

    content = log.read()[0]["content"]
    assert len(content) < MAX_FIELD_CHARS + 100
    assert content.startswith("xxx")
    assert "5,000 more characters" in content


def test_clipping_reaches_inside_nested_values(tmp_path):
    log = SessionLog(tmp_path / "s.events.jsonl")
    log.append("request/header", tools=[{"description": "y" * (MAX_FIELD_CHARS + 10)}])
    assert len(log.read()[0]["tools"][0]["description"]) < MAX_FIELD_CHARS + 100


# -- it must never cost a turn --------------------------------------------------------


def test_an_unwritable_path_is_not_an_error(tmp_path):
    """The log is an observation stream. Losing it costs a row in a report."""
    log = SessionLog(tmp_path / "nope" / "\0bad" / "s.jsonl")
    log.append("turn/start", turn=1)  # must not raise
    assert log.read() == []


def test_a_value_json_cannot_encode_is_stringified_rather_than_raised(tmp_path):
    log = SessionLog(tmp_path / "s.events.jsonl")
    log.append("notice", value=object())
    assert len(log.read()) == 1


def test_a_torn_final_line_costs_that_line_only(tmp_path):
    """The file is appended to while it is read, so a half-written last line is ordinary
    rather than exceptional."""
    path = tmp_path / "s.events.jsonl"
    log = SessionLog(path)
    log.append("turn/start", turn=1)
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"seq": 1, "type": "half')

    assert [e["type"] for e in log.read()] == ["turn/start"]


def test_the_log_stops_growing_rather_than_dropping_its_beginning(tmp_path):
    """A log missing its first turn explains nothing, so the cap truncates the END.
    A runaway loop then costs a bounded file instead of a disk."""
    log = SessionLog(tmp_path / "s.events.jsonl")
    log._seq = MAX_EVENTS - 1
    log._counted = True
    log.append("notice", text="last one in")
    log.append("notice", text="over the line")

    events = log.read()
    assert len(events) == 1
    assert events[0]["text"] == "last one in"


def test_the_null_log_accepts_everything_and_keeps_nothing():
    """An engine without a session — a script, a scripted test — must not have to check
    for a log before recording."""
    NULL_LOG.append("turn/start", turn=1)
    assert NULL_LOG.read() == []


def test_reading_can_be_bounded(tmp_path):
    log = SessionLog(tmp_path / "s.events.jsonl")
    for i in range(10):
        log.append("notice", text=str(i))
    assert len(log.read(limit=3)) == 3


# -- what the engine actually records -------------------------------------------------


class _Provider(ProviderClient):
    def __init__(self) -> None:
        self.n = 0

    def complete(self, **kw):
        raise NotImplementedError

    def stream(self, *, model, messages, tools=None, **settings):
        self.n += 1
        yield StreamChunk(text_delta="thinking")
        if self.n == 1:
            yield StreamChunk(
                turn=AssistantTurn(
                    text="checking",
                    tool_calls=[ToolCall(id="c1", name="probe", arguments={"q": "x"})],
                    finish_reason="tool_calls",
                    usage=TokenUsage(input=100, output=8, cache_read=50),
                )
            )
        else:
            yield StreamChunk(
                turn=AssistantTurn(
                    text="done", finish_reason="stop", usage=TokenUsage(input=200, output=4)
                )
            )

    def capabilities(self, model):
        return ModelCapabilities()


def probe(q: str = "") -> str:
    """A tool that returns something."""
    return "two rows"


@pytest.fixture
def ran(tmp_path):
    registry = ToolRegistry()
    registry.register(probe)
    engine = TurnEngine(
        provider=_Provider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "s.events.jsonl")

    async def go():
        async for _ in engine.run("audit the repo"):
            pass

    asyncio.run(go())
    return engine


def test_a_turn_records_its_own_vocabulary(ran):
    """Turn, step, message, call, result — the units every timing is measured against.
    The final step CLOSES: the completion path used to return without a step/end, so
    every turn's last step dangled (session a5a5ce3d: 24 starts, 21 ends)."""
    kinds = [e["type"] for e in ran.log.read()]
    assert kinds == [
        "turn/start",
        "user/message",
        "context/budget",  # the turn-start context line, logged since 2026-09-25 (0001)
        "request/header",
        "step/start",
        "assistant/message",
        "tool/call",
        "tool/result",
        "step/end",
        "step/start",
        "assistant/message",
        "step/end",
        "turn/end",
    ]


def test_every_event_carries_the_turn_and_step_it_belongs_to(ran):
    """Without these the ledger cannot group anything, and grouping is what turns a flat
    list into something a person can read."""
    events = {e["type"]: e for e in ran.log.read()}
    assert events["assistant/message"]["turn"] == 1
    assert events["tool/call"]["step"] == 1
    assert events["turn/end"]["steps"] == 2


def test_a_step_is_one_model_call_plus_its_tools(ran):
    """The definition, and the reason two steps appear here for one turn: the tool
    result sent the conversation back to the model."""
    steps = [e for e in ran.log.read() if e["type"] == "step/start"]
    assert [s["step"] for s in steps] == [1, 2]


def test_the_assistant_event_carries_timing_and_usage_the_message_alone_cannot(ran):
    step = next(e for e in ran.log.read() if e["type"] == "assistant/message")
    assert step["timing"]["dispatched"] <= step["timing"]["first_token"]
    assert step["timing"]["first_token"] <= step["timing"]["completed"]
    assert step["usage"] == {"input": 100, "output": 8, "cache_read": 50, "cache_write": 0}
    assert step["model"] == "m"


def test_a_call_records_the_arguments_as_the_model_produced_them(ran):
    """A log answering "why did it call that" needs what was SENT."""
    call = next(e for e in ran.log.read() if e["type"] == "tool/call")
    assert json.loads(call["arguments"]) == {"q": "x"}
    assert call["call_id"] == "c1"


def test_a_result_records_its_own_wall_time_and_outcome(ran):
    result = next(e for e in ran.log.read() if e["type"] == "tool/result")
    assert result["is_error"] is False
    assert result["status"] == "ok"
    assert result["timing"]["started"] <= result["timing"]["ended"]
    assert "two rows" in result["content"]


def test_the_header_records_the_catalog_the_model_was_given(ran):
    """The tool a call was made against, at the moment it was made. Recorded on CHANGE
    only: the same bytes every turn would be the largest thing in the log while saying
    nothing new."""
    header = next(e for e in ran.log.read() if e["type"] == "request/header")
    assert header["reason"] == "initial"
    assert any(t["function"]["name"] == "probe" for t in header["tools"])
    assert len([e for e in ran.log.read() if e["type"] == "request/header"]) == 1


def test_an_engine_with_no_session_records_nothing_and_still_runs(tmp_path):
    """The null log is the default, so a script or a scripted test never has to own a
    file to run a turn."""
    registry = ToolRegistry()
    registry.register(probe)
    engine = TurnEngine(
        provider=_Provider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS),
        model="m",
    )
    assert engine.log is NULL_LOG

    async def go():
        async for _ in engine.run("hello"):
            pass

    asyncio.run(go())  # must not raise
    assert engine.messages


# -- the standing plan is its own record ----------------------------------------------
#
# `todo/write` stays off the model-visible surface entirely and folds the UI's plan out
# of the log (packages/todo/tool-todo). Ported here for the same reason: the plan a person
# reads gets its own record instead of being re-derived from the message list.


class _PlanProvider(ProviderClient):
    """One `todo_write`, then stop."""

    def __init__(self, todos) -> None:
        self.todos = todos
        self.n = 0

    def complete(self, **kw):
        raise NotImplementedError

    def stream(self, *, model, messages, tools=None, **settings):
        self.n += 1
        if self.n == 1:
            yield StreamChunk(
                turn=AssistantTurn(
                    tool_calls=[
                        ToolCall(id="t1", name="todo_write", arguments={"todos": self.todos})
                    ],
                    finish_reason="tool_calls",
                )
            )
        else:
            yield StreamChunk(turn=AssistantTurn(text="done", finish_reason="stop"))

    def capabilities(self, model):
        return ModelCapabilities()


def _plan_engine(tmp_path, todos):
    from harness.tools.todo import TodoList, todo_tools

    todo = TodoList()
    registry = ToolRegistry()
    registry.register_all(todo_tools(todo))
    engine = TurnEngine(
        provider=_PlanProvider(todos),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS
        ),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "s.events.jsonl")
    engine.todo = todo

    async def go():
        async for _ in engine.run("plan the work"):
            pass

    asyncio.run(go())
    return engine


def test_a_landed_todo_write_is_recorded_as_its_own_event(tmp_path):
    todos = [
        {"content": "Load the CSV", "status": "done"},
        {"content": "Chart revenue", "status": "in_progress"},
    ]
    engine = _plan_engine(tmp_path, todos)

    written = [e for e in engine.log.read() if e["type"] == "todo/write"]
    assert len(written) == 1
    assert written[0]["todos"] == todos
    assert written[0]["call_id"] == "t1"
    assert written[0]["turn"] == 1 and written[0]["step"] == 1


def test_the_recorded_plan_is_the_normalized_list_the_tool_returned(tmp_path):
    """What the tool WROTE, not what the model typed: the panel must show the same list
    the agent is working from, statuses coerced the same way."""
    engine = _plan_engine(
        tmp_path, [{"content": "Ship it", "status": "completed"}]  # model alias for done
    )

    written = [e for e in engine.log.read() if e["type"] == "todo/write"]
    assert written[0]["todos"] == [{"content": "Ship it", "status": "done"}]
    assert engine.todo.items == written[0]["todos"]


def test_the_plan_event_follows_the_result_that_proved_it_landed(tmp_path):
    """Observation, never inference: the write is recorded once the call returned."""
    kinds = [e["type"] for e in _plan_engine(tmp_path, []).log.read()]
    assert kinds.index("tool/result") < kinds.index("todo/write")


def test_a_call_that_raised_leaves_no_plan_behind(tmp_path):
    """The other half of the same rule. A surface folding this log must never show a list
    that no write ever wrote — which is why the live path waits for the result too
    (surfaces/gui/src/todoPlan.ts)."""

    def todo_write(todos: list = None) -> dict:
        """Replace the task list."""
        raise RuntimeError("the list did not stick")

    registry = ToolRegistry()
    registry.register(todo_write)
    engine = TurnEngine(
        provider=_PlanProvider([{"content": "Ship it", "status": "pending"}]),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS
        ),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "s.events.jsonl")

    async def go():
        async for _ in engine.run("plan the work"):
            pass

    asyncio.run(go())

    events = engine.log.read()
    assert [e["type"] for e in events].count("tool/result") == 1  # the call did happen
    assert not [e for e in events if e["type"] == "todo/write"]


def test_a_mode_switch_is_logged_once_per_change(tmp_path):
    """Mode switches are logged; `engine.set_mode` is our one live write path. The row is
    a CHANGE, not a declaration: re-selecting the current mode writes nothing, so a log
    with two rows means the session moved exactly twice."""
    engine = TurnEngine(
        provider=_Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=Mode.WORKSPACE_WRITE
        ),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "s.events.jsonl")

    assert engine.set_mode(Mode.READ_ONLY) is True
    assert engine.set_mode(Mode.READ_ONLY) is False
    assert engine.set_mode("auto") is True  # the legacy spelling lands on full access

    events = [e for e in engine.log.read() if e["type"] == "mode/changed"]
    assert [(e["from"], e["to"]) for e in events] == [
        ("workspace-write", "read-only"),
        ("read-only", "danger-full-access"),
    ]
    assert all(e["reason"] == "user" for e in events)


def test_a_plan_toggle_is_logged_once_per_change(tmp_path):
    """The overlay has its own one live write path (`engine.set_plan`) and its own log
    event (`plan/mode`), for the same reason modes do: the row records a CHANGE."""
    engine = TurnEngine(
        provider=_Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=Mode.WORKSPACE_WRITE
        ),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "s.plan.jsonl")

    assert engine.set_plan(True) is True
    assert engine.set_plan(True) is False
    assert engine.set_plan(False, reason="plan_approval") is True

    events = [e for e in engine.log.read() if e["type"] == "plan/mode"]
    assert [e["active"] for e in events] == [True, False]
    assert [e["reason"] for e in events] == ["user", "plan_approval"]


def test_the_request_header_keeps_the_whole_prompt_and_schemas(tmp_path):
    # 2026-09-25 review: `system` and long tool schemas were cut at MAX_FIELD_CHARS, so a
    # request could not be rebuilt from its log. Other fields stay bounded.
    from harness.session.session_log import MAX_FIELD_CHARS, SessionLog

    log = SessionLog(tmp_path / "s.events.jsonl")
    system = "S" * (MAX_FIELD_CHARS * 5)
    tools = [{"type": "function", "function": {"name": "task", "description": "D" * (MAX_FIELD_CHARS * 2)}}]
    log.append("request/header", reason="initial", model="m", system=system, tools=tools)
    log.append("user/message", content="U" * (MAX_FIELD_CHARS * 2))
    header, user = log.read()
    assert header["system"] == system and header["tools"] == tools
    assert len(user["content"]) < MAX_FIELD_CHARS + 100
