"""The summarizer streams, so a slow summary is not a failed one; and a failure says why.

Found from a `/compact` on zai-coding-plan:glm-5.3-flash that ended in "Context compaction failed ... How should I
proceed?". The summarizer called `complete()`, a request with no byte on the wire until the model has finished, and
the transport's read timeout (180 s, meant to bound SILENCE) became a cap on the whole summary. In the real logs that
model took 356 s for one summary that landed and failed with `APITimeoutError: Request timed out.` after 578 s on
another. The prompt itself said nothing about why: the reason reached the log only after the person answered.
What has to hold:

  - the summarizer is asked through `stream()` and its final turn is the summary (a client with no `stream` still
    works through `complete()`);
  - a stream that ends without a final turn fails like an empty summary, not with an empty one adopted;
  - the failure prompt names the error.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.context.compaction.summarizer import summarize_span
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

from summary_fixtures import valid_summary

SPAN = [{"role": "user", "content": "plan the report"}, {"role": "assistant", "content": "drafted it"}]


class _Streaming(ProviderClient):
    """Answers through stream() only; a complete() call is the old, timeout-capped path."""

    def __init__(self, chunks=None):
        self.chunks = chunks
        self.streamed = 0

    def stream(self, *, model, messages, tools=None, **settings):
        self.streamed += 1
        for chunk in self.chunks if self.chunks is not None else [
            StreamChunk(reasoning_delta="thinking "),
            StreamChunk(text_delta="partial "),
            StreamChunk(turn=AssistantTurn(text=valid_summary("finish the report"), finish_reason="stop")),
        ]:
            yield chunk

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the summarizer must stream: a non-streamed call is capped by the read timeout")

    def capabilities(self, model):
        return ModelCapabilities()


def test_the_summarizer_is_asked_through_the_stream():
    provider = _Streaming()
    summary = summarize_span(provider, "m", SPAN)
    assert provider.streamed == 1
    assert "finish the report" in summary


def test_a_stream_with_no_final_turn_is_a_failure_not_an_empty_summary():
    provider = _Streaming(chunks=[StreamChunk(text_delta="half a sum")])
    with pytest.raises(RuntimeError, match="without a final turn"):
        summarize_span(provider, "m", SPAN)


def test_a_client_with_no_stream_still_works_through_complete():
    class _Bare:
        def complete(self, **kwargs):
            return AssistantTurn(text=valid_summary("finish the report"), finish_reason="stop")

    assert "finish the report" in summarize_span(_Bare(), "m", SPAN)


class _TimesOut(ProviderClient):
    def stream(self, *, model, messages, tools=None, **settings):
        if any("Produce ALL of the following sections" in str(m.get("content", "")) for m in messages):
            raise TimeoutError("Request timed out.")
        yield StreamChunk(turn=AssistantTurn(text="ok", finish_reason="stop"))

    def complete(self, **kwargs):
        raise AssertionError("unused")

    def capabilities(self, model):
        return ModelCapabilities()


def test_the_failure_prompt_names_the_error(tmp_path):
    history = [{"role": "system", "content": "be helpful"}]
    for i in range(8):
        history += [
            {"role": "user", "content": f"request {i}", "ts": 1.0},
            {"role": "assistant", "content": f"answer {i} " + "x" * 1500, "ts": 1.0},
        ]
    engine = TurnEngine(
        provider=_TimesOut(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
        messages=history,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {"context_window": 1_000_000, "buffer_tokens": 0, "keep_tokens": 0}
    engine.is_attended = lambda: True
    asked = []

    async def asker(args, tool_call_id=None):
        asked.append(args["question"])
        return {"answer": "Skip for now"}

    engine.question_asker = asker
    asyncio.run(engine.compact_now())
    assert asked and "TimeoutError: Request timed out." in asked[0]
