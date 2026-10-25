"""An answer cut at the output-token limit is extended, not abandoned.

Output-token continuation: when a provider stops a response at the output
ceiling, the engine asks for the REST of the same message (at most `TRUNCATION_CONTINUES`
times) instead of ending the turn on half an answer. What is pinned here:

- the fragment is folded into the message it continues, and history holds ONE assistant
  message whose text is whole — the instruction that asked for it is removed, because
  nothing synthetic outlives the turn;
- the instruction IS model-visible, so it is logged before it is sent (`steering/message`
  with source "truncation-continue"), paired with `turn/continuation` when folded;
- usage across the extension is summed, not replaced;
- the cap is real: three extensions, then the turn reports `truncated` honestly;
- a cut tool call is NOT this case — the normal path owns it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aisuite as ai

from harness.loop import TRUNCATION_CONTINUES, TurnEngine
from harness.loop.nudges import TRUNCATION_CONTINUE_TEXT
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.llm.providers.base import TokenUsage, ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry


class _Scripted(ProviderClient):
    """Streams one scripted turn per call, in order (the last one repeats)."""

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = turns
        self.calls = 0
        self.seen: list[list[dict[str, Any]]] = []

    def stream(self, *, model, messages, tools=None, **settings):
        self.seen.append([dict(m) for m in messages])
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        yield StreamChunk(turn=turn)

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def _finish(text: str, reason: str, **kw) -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason=reason, **kw)


def _engine(tmp_path, provider, registry=None) -> TurnEngine:
    engine = TurnEngine(
        provider=provider,
        registry=registry if registry is not None else ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.log = SessionLog(tmp_path / "events.jsonl", trace="trace-1")
    return engine


def _run(engine, text="write me an essay") -> list[Any]:
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def _assistants(engine) -> list[dict[str, Any]]:
    return [m for m in engine.messages if m.get("role") == "assistant"]


# -- the extension itself -------------------------------------------------------------


def test_a_cut_answer_is_extended_and_folded_into_one_message(tmp_path):
    provider = _Scripted(
        [
            _finish("The first half", "length", usage=TokenUsage(input=100, output=40)),
            _finish(" and the second half", "length", usage=TokenUsage(input=120, output=30)),
            _finish(". The end.", "stop", usage=TokenUsage(input=140, output=8)),
        ]
    )
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.calls == 3  # two extensions asked for, two fragments
    assistants = _assistants(engine)
    assert len(assistants) == 1, "history holds ONE message, not one per round-trip"
    message = assistants[0]
    assert message["content"] == "The first half and the second half. The end."
    assert message["usage"]["input"] == 360 and message["usage"]["output"] == 78

    # Nothing synthetic outlives the turn: no instruction message is left in history.
    assert TRUNCATION_CONTINUE_TEXT not in [m.get("content") for m in engine.messages]
    assert all(
        m.get("_attached_by") != "truncation-continue" for m in engine.messages
    )

    # The turn reports a normal completion, not a truncation.
    end = [e for e in events if e.type is EventType.TURN_END][-1]
    assert end.data["status"] == "completed" and "truncated" not in end.data


def test_the_instruction_is_logged_before_it_is_sent(tmp_path):
    provider = _Scripted([_finish("half", "length"), _finish(" done", "stop")])
    engine = _engine(tmp_path, provider)
    _run(engine)

    events = engine.log.read()
    instructions = [
        e for e in events
        if e["type"] == "steering/message" and e.get("source") == "truncation-continue"
    ]
    folds = [e for e in events if e["type"] == "turn/continuation"]
    assert len(instructions) == 1 and instructions[0]["text"] == TRUNCATION_CONTINUE_TEXT
    assert len(folds) == 1 and folds[0]["attempt"] == 1 and folds[0]["added"] == 5
    assert folds[0]["trace"] == "trace-1"

    # The provider saw the instruction, then the partial message it continues.
    second_request = provider.seen[1]
    assert second_request[-1]["content"] == TRUNCATION_CONTINUE_TEXT
    assert second_request[-2]["content"] == "half"


def test_the_extension_is_capped_and_a_cut_answer_still_reports_truncated(tmp_path):
    provider = _Scripted([_finish("more", "length")])  # never finishes
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.calls == 1 + TRUNCATION_CONTINUES
    assert len([e for e in engine.log.read() if e["type"] == "turn/continuation"]) == TRUNCATION_CONTINUES
    message = _assistants(engine)[0]
    assert message["content"] == "more" * (1 + TRUNCATION_CONTINUES)
    end = [e for e in events if e.type is EventType.TURN_END][-1]
    assert end.data["status"] == "completed" and end.data.get("truncated") is True


def test_a_continuation_that_calls_a_tool_still_runs_it(tmp_path):
    (tmp_path / "notes.txt").write_text("from the file\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(roots=[{"path": str(tmp_path), "writable": True}]))
    provider = _Scripted(
        [
            _finish("Let me look", "length"),
            AssistantTurn(
                text=": reading the file.",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "notes.txt"})],
            ),
        ]
    )
    engine = _engine(tmp_path, provider, registry)
    events = _run(engine)

    assert _assistants(engine)[0]["content"] == "Let me look: reading the file."
    results = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert results and "from the file" in str(results[0].data.get("result_preview"))
    # The call rides in the SAME assistant message as the text it was written with, or a
    # provider would see a tool result whose call it never received.
    assert [c["id"] for c in _assistants(engine)[0]["tool_calls"]] == ["c1"]


def test_a_cut_tool_call_is_not_extended(tmp_path):
    """A tool call cut mid-arguments never parsed — `_mangled_tool` answers it with the
    diagnosis, and the turn's truncation flag stays honest. Extending it would re-send a
    half-written JSON document."""
    provider = _Scripted(
        [
            AssistantTurn(
                text="",
                finish_reason="length",
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"_raw": '{"path": "x'})],
            ),
            _finish("understood", "stop"),
        ]
    )
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert not [e for e in engine.log.read() if e["type"] == "turn/continuation"]
    results = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert results and results[0].data.get("status") == "error"
    # The extension never happened; the loop went on because the tool answered.
    assert provider.calls == 2
    assert _assistants(engine)[-1]["content"] == "understood"
