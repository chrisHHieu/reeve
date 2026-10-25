"""A tool call whose arguments could not be parsed (kept as `{"_raw": …}`) is answered with what went
wrong, and never run.

As it happened (2026-08-15, writing a report page): a large write_file call ran past the output
token limit, the cut JSON became `{"_raw": …}`, and the tool's plain parameter error sent the model
round a "wrong parameter" loop. Worse, every stored `_raw` call read as an example to follow: after a
few the model began to write `_raw` as if it were a parameter, and every request sent the thousands
of junk tokens again.
"""

from __future__ import annotations

import json

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.loop.message_shapes import MANGLED_PREVIEW_CHARS
from harness.safety.permissions import Mode, PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry


class _Unparsed(ProviderClient):
    """First a write_file call whose arguments did not parse, ended as `finish_reason`; then an answer."""

    def __init__(self, finish_reason: str, raw: str = "x" * 5000):
        self.finish_reason, self.raw, self.calls = finish_reason, raw, 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls > 1:
            return AssistantTurn(text="done", tool_calls=[])
        return AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name="write_file", arguments={"_raw": self.raw})], finish_reason=self.finish_reason)

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


async def _ran(tmp_path, finish_reason, **raw):
    engine = TurnEngine(provider=_Unparsed(finish_reason, **raw), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE), model="m")
    return engine, [event async for event in engine.run("build the report")]


def _result(engine) -> str:
    return str([m for m in engine.messages if m.get("role") == "tool"][-1]["content"])


def _stored_arguments(engine) -> dict:
    call = next(m for m in engine.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    return json.loads(call["tool_calls"][0]["function"]["arguments"])


async def test_a_call_cut_off_at_the_token_limit_is_told_to_come_in_smaller_pieces(tmp_path):
    # finish "length" and arguments that do not parse mean the call was cut off; what cures that
    # is smaller pieces, not a complaint about parameters
    engine, events = await _ran(tmp_path, "length")
    assert next(e for e in events if e.type is EventType.TOOL_FINISHED).data["status"] == "error"
    said = _result(engine)
    assert "output-token limit" in said and "smaller pieces" in said
    assert [e for e in events if e.type is EventType.TURN_END]  # and the turn goes on to its answer


async def test_bad_json_that_was_not_cut_off_is_told_raw_is_no_parameter(tmp_path):
    # not cut off: the model wrote bad JSON, or copied `_raw`
    engine, _ = await _ran(tmp_path, "stop")
    said = _result(engine)
    assert "_raw" in said and "not a parameter" in said and "declared parameters" in said


async def test_the_unparsed_text_is_cut_short_before_it_is_kept(tmp_path):
    # kept whole it costs tokens every turn and shows the model `_raw` as a shape to copy
    engine, _ = await _ran(tmp_path, "length", raw="y" * 5000)
    kept = _stored_arguments(engine)["_raw"]
    assert len(kept) < MANGLED_PREVIEW_CHARS + 100 and "truncated in history" in kept


async def test_unparsed_text_under_the_preview_size_is_kept_as_it_was(tmp_path):
    engine, _ = await _ran(tmp_path, "stop", raw='{"path": "repo')
    assert _stored_arguments(engine) == {"_raw": '{"path": "repo'}
