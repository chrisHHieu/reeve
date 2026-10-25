"""Stop, failure and retry: a Stop takes effect in whatever the turn is doing (streaming, thinking,
waiting for an approval, running tools), a failed stream keeps what had arrived, and Retry runs the
failed turn again without a second message.

Through all of it the history stays valid: every tool call gets a result, since hosted chat
templates refuse an orphan and a resumed turn would ask for it again. (v0.1.4 stopped only between
steps; ledgered 2026-07-21.)
"""

from __future__ import annotations

import asyncio
import time

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, ToolCall
from harness.loop import USER_STOP_MESSAGE, ApprovalOutcome, TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry


class _Streams(ProviderClient):
    """Streams what `pieces` yields; `complete` is never used."""

    def __init__(self, pieces):
        self._pieces = pieces
        self.calls = 0

    def complete(self, **request):  # pragma: no cover - it only streams
        raise AssertionError("complete() is never called on a streaming client")

    def capabilities(self, model):
        return ModelCapabilities()

    def stream(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        yield from self._pieces(self.calls)


class _Answers(ProviderClient):
    def __init__(self, turn):
        self._turn = turn

    def complete(self, **kwargs):
        return self._turn

    def capabilities(self, model):
        return ModelCapabilities()


def _endless(kind):
    """Two hundred pieces 10 ms apart, then an answer: long enough to stop, bounded so a
    regression fails instead of hanging."""
    produced = {"n": 0}

    def pieces(call):
        for i in range(200):
            produced["n"] += 1
            yield StreamChunk(**{kind: f"{'w' if kind == 'text_delta' else 't'}{i} "})
            time.sleep(0.01)
        yield StreamChunk(turn=AssistantTurn(text="full", finish_reason="stop"))

    return pieces, produced


def _outage_first(failures=1):
    def pieces(call):
        if call <= failures:
            raise RuntimeError("outage")
        yield StreamChunk(turn=AssistantTurn(text="recovered", finish_reason="stop"))

    return pieces


def _calls(*calls):
    return AssistantTurn(tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)], finish_reason="tool_calls")


def _engine(tmp_path, provider, *, registry=None, model="gpt-5.5", **fields):
    return TurnEngine(provider=provider, registry=registry or ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model=model, **fields)


def _run(engine, text="go", stop_on=None, after=0):
    """The turn's events; with `stop_on`, Stop is pressed at that event once `after` have passed."""

    async def go():
        events = []
        async for event in engine.run(text):
            events.append(event)
            if stop_on is not None and event.type == stop_on and len(events) > after:
                engine.request_interrupt()
        return events

    return asyncio.run(go())


def _retry(engine):
    async def go():
        return [event async for event in engine.retry()]

    return asyncio.run(go())


def _results(engine):
    return [m for m in engine.messages if m.get("role") == "tool"]


# -- Stop ----------------------------------------------------------------------------------------


def test_a_stop_mid_answer_keeps_what_was_shown_and_marks_the_turn(tmp_path):
    pieces, produced = _endless("text_delta")
    engine = _engine(tmp_path, _Streams(pieces))
    assert _run(engine, stop_on=EventType.ASSISTANT_DELTA, after=3)[-1].type == EventType.INTERRUPTED
    assert produced["n"] < 100  # the stream was left, not read to its end
    assert engine.messages[-1] == {"role": "notice", "kind": "interrupted", "ts": engine.messages[-1]["ts"]}
    partial = engine.messages[-2]
    assert partial["role"] == "assistant" and partial["content"].startswith("w0 ") and "tool_calls" not in partial
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())  # the mark is for the person


def test_a_stop_mid_thinking_keeps_the_thinking_so_far(tmp_path):
    pieces, _ = _endless("reasoning_delta")
    engine = _engine(tmp_path, _Streams(pieces))
    assert _run(engine, stop_on=EventType.REASONING_DELTA, after=3)[-1].type == EventType.INTERRUPTED
    partial = engine.messages[-2]  # [-1] is the interrupted mark
    assert partial["role"] == "assistant" and partial["reasoning"].startswith("t0 ")


def test_a_stop_at_an_approval_declines_that_action_and_says_so(tmp_path):
    """"Interrupted by user" is true here and useless: it cannot tell a person who walked away
    from one who said no to this action, and the next turn asked for the same thing again."""

    async def never_answered(request):
        await asyncio.Event().wait()

    registry = ToolRegistry()

    def write_file(path: str, content: str):  # pragma: no cover - the approval never comes
        raise AssertionError("the write ran with its approval still pending")

    registry.register(write_file)
    engine = _engine(tmp_path, _Answers(_calls(("write_file", {"path": "x", "content": "y"}))), registry=registry, approver=never_answered)
    assert _run(engine, stop_on=EventType.PERMISSION_REQUIRED)[-1].type == EventType.INTERRUPTED
    [result] = _results(engine)
    assert USER_STOP_MESSAGE in result["content"] and "which declines it" in result["content"]


def test_a_stop_while_a_tool_runs_skips_the_rest_and_answers_every_call(tmp_path):
    registry = ToolRegistry()
    holder = {}

    def first_tool():
        """Runs, then the user hits Stop while it holds the turn."""
        holder["engine"].request_interrupt()
        return {"ok": True}

    def second_tool():  # pragma: no cover - after the stop nothing more runs
        raise AssertionError("a call ran after the stop")

    registry.register(first_tool)
    registry.register(second_tool)

    async def approve(request):
        return ApprovalOutcome.ONCE

    engine = _engine(tmp_path, _Answers(_calls(("first_tool", {}), ("second_tool", {}))), registry=registry, approver=approve)
    holder["engine"] = engine
    assert _run(engine)[-1].type == EventType.INTERRUPTED
    results = _results(engine)
    assert len(results) == 2 and "interrupted by user" in results[1]["content"]


def test_stop_runs_the_hooks_a_world_registered(tmp_path):
    fired = []
    engine = _engine(tmp_path, _Answers(AssistantTurn(text="hi", finish_reason="stop")), interrupt_hooks=[lambda: fired.append(True)])
    engine.request_interrupt()
    assert fired == [True]


# -- a failed stream and Retry -------------------------------------------------------------------


def test_a_stream_that_dies_mid_answer_keeps_the_text_and_leaves_an_error_notice(tmp_path):
    def pieces(call):
        yield StreamChunk(text_delta="partial ")
        yield StreamChunk(text_delta="answer")
        raise RuntimeError("provider went away")

    engine = _engine(tmp_path, _Streams(pieces))
    assert _run(engine)[-1].type == EventType.ERROR
    notice, partial = engine.messages[-1], engine.messages[-2]
    assert (notice["role"], notice["kind"]) == ("notice", "error") and "provider went away" in notice["text"]
    assert partial["role"] == "assistant" and partial["content"] == "partial answer" and "tool_calls" not in partial


def test_retry_runs_the_failed_turn_again_without_a_new_message(tmp_path):
    engine = _engine(tmp_path, _Streams(_outage_first()))
    assert _run(engine, "hello")[-1].type == EventType.ERROR
    assert _retry(engine)[-1].type == EventType.TURN_END
    assert sum(1 for m in engine.messages if m.get("role") == "user") == 1
    assert engine.messages[-1]["content"] == "recovered"


def test_retry_does_nothing_unless_the_last_thing_is_an_error(tmp_path):
    """A stray Retry frame must not give a finished conversation a second answer."""
    engine = _engine(tmp_path, _Answers(AssistantTurn(text="done", finish_reason="stop")))
    _run(engine, "hello")
    assert _retry(engine) == [] and engine.messages[-1]["content"] == "done"


def test_retry_survives_model_switches(tmp_path):
    """A failed turn, then one switch of model or more, then Retry: the turn runs again, on the
    model switched to (the owner met it 2026-07-23, when the switch notices used up the retry)."""
    engine = _engine(tmp_path, _Streams(_outage_first()), model="gemini:gemini-3.6-flash")
    assert _run(engine, "hello")[-1].type == EventType.ERROR
    assert engine.switch_model("gemini:gemini-3.1-pro-preview") is not None
    assert engine.switch_model("gpt-5.6-sol") is not None
    assert _retry(engine)[-1].type == EventType.TURN_END
    assert engine.model == "gpt-5.6-sol" and engine.messages[-1]["content"] == "recovered"
    # the person's message is there once, and a turn that ended well has nothing to retry
    assert sum(1 for m in engine.messages if m.get("role") == "user") == 1
    assert _retry(engine) == []


# -- thinking ------------------------------------------------------------------------------------


def test_thinking_is_streamed_and_kept_but_never_sent_to_a_model(tmp_path):
    def pieces(call):
        yield StreamChunk(reasoning_delta="hmm, ")
        yield StreamChunk(reasoning_delta="let me think")
        yield StreamChunk(text_delta="the answer")
        yield StreamChunk(turn=AssistantTurn(text="the answer", finish_reason="stop", reasoning="hmm, let me think"))

    engine = _engine(tmp_path, _Streams(pieces), model="deepseek:deepseek-v4-pro")
    events = _run(engine)
    assert [e.data["text"] for e in events if e.type == EventType.REASONING_DELTA] == ["hmm, ", "let me think"]
    assert next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE).data["reasoning"] == "hmm, let me think"
    assert engine.messages[-1]["reasoning"] == "hmm, let me think"
    assert all("reasoning" not in m for m in engine._outbound_messages())
