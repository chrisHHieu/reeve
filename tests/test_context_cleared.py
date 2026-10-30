"""The price of a context is the request as sent, and the provider's count is believed on evidence.

Session 90e83048-f57, 2026-10-30: Claude's server-side context editing cleared about 34k tokens
from every request, the reply said so, and the engine never read it. A count that went down after
a pass read as a provider under-reporting, the engine stopped believing it for good, and priced the
context itself, at twice what the model read (signatures priced as prose did the rest). A stage
cut then ran at 161k of a 480k trigger.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from harness.context import compaction as C
from harness.llm.providers import AnthropicProvider, AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.claude import context_editing
from harness.llm.providers.claude.stream import read_stream
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

APPLIED = [
    SimpleNamespace(type="clear_thinking_20251015", cleared_thinking_turns=2, cleared_input_tokens=4_000),
    SimpleNamespace(type="clear_tool_uses_20250919", cleared_tool_uses=183, cleared_input_tokens=33_776),
]


# -- the reply says what was cleared ---------------------------------------------------------


def test_a_streamed_reply_carries_what_context_editing_cleared():
    message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=2, cache_read_input_tokens=160_000, cache_creation_input_tokens=0),
        context_management=SimpleNamespace(applied_edits=APPLIED),
    )
    events = [
        SimpleNamespace(type="message_start", message=message),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"), usage=SimpleNamespace(output_tokens=5)),
    ]
    turn = [chunk for chunk in read_stream(events)][-1].turn
    assert turn.context_cleared == {"input_tokens": 37_776, "tool_uses": 183, "thinking_turns": 2}


def test_a_plain_reply_carries_it_too_and_a_reply_without_edits_carries_nothing():
    replies = [
        {"context_management": {"applied_edits": [{"cleared_input_tokens": 500, "cleared_tool_uses": 3}]}},
        {},
    ]

    def create(**request):
        extra = replies.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi")], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=1),
            **extra,
        )

    api = SimpleNamespace(messages=SimpleNamespace(create=create), beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    provider = AnthropicProvider(client=api)
    history = [{"role": "user", "content": "hello"}]
    assert provider.complete(model="claude-opus-5-5", messages=history).context_cleared == {"input_tokens": 500, "tool_uses": 3}
    assert provider.complete(model="claude-opus-5-5", messages=history).context_cleared is None
    assert context_editing.cleared(SimpleNamespace(context_management=None)) is None


# -- the engine prices what was sent and believes what follows it ----------------------------


class _Quiet(ProviderClient):
    def complete(self, **_):
        raise AssertionError("no requests here")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path):
    engine = TurnEngine(
        provider=_Quiet(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model="claude-opus-5-5",
        messages=[{"role": "system", "content": "be helpful"}, {"role": "user", "content": "go"}],
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {"context_window": 10_000_000, "buffer_tokens": 0}
    return engine


def _view(engine):
    return C.estimate_tokens(engine._outbound_messages()) + engine._tool_schema_tokens()


def _append(engine, tokens):
    engine.messages.append({"role": "assistant", "content": "a" * (tokens * 4)})
    engine.messages.append({"role": "user", "content": "next"})


def test_a_count_that_fell_because_the_provider_cleared_old_results_is_believed(tmp_path):
    engine = _engine(tmp_path)
    engine._record_context_measure(_view(engine))
    _append(engine, 30_000)
    sent = _view(engine)

    engine._record_context_measure(sent - 35_000, cleared=35_000)

    assert engine._usage_unreliable is False
    assert engine._last_context_tokens == sent - 35_000  # the window holds what the model read
    assert engine._context_signal() < sent - 30_000
    assert engine._estimate_bias is not None and 0.95 <= engine._estimate_bias <= 1.05  # sent / its estimate


def test_the_same_fall_with_nothing_said_about_it_is_not_believed(tmp_path):
    engine = _engine(tmp_path)
    engine._record_context_measure(_view(engine))
    _append(engine, 30_000)
    engine._record_context_measure(_view(engine) - 35_000)
    assert engine._usage_unreliable is True and engine._last_context_tokens is None
    notices = [row for row in engine.log.read() if row.get("kind") == "usage_partial"]
    assert len(notices) == 1


def test_one_short_step_is_not_evidence_and_a_count_that_catches_up_is_believed_again(tmp_path):
    engine = _engine(tmp_path)
    start = _view(engine)
    engine._record_context_measure(start)
    _append(engine, 6_000)
    engine._record_context_measure(start + 500)  # grew less than a quarter, over one short step
    assert engine._usage_unreliable is False

    _append(engine, 20_000)
    engine._record_context_measure(start + 500)  # 26k appended and the count has not moved
    assert engine._usage_unreliable is True

    _append(engine, 4_000)
    engine._record_context_measure(_view(engine))  # the count follows again
    assert engine._usage_unreliable is False and engine._last_context_tokens == _view(engine)
    kinds = [row.get("kind") for row in engine.log.read() if row.get("type") == "notice"]
    assert kinds == ["usage_partial", "usage_trusted"]


def test_a_cut_starts_the_evidence_again_and_another_model_is_believed_until_it_shows_otherwise(tmp_path):
    engine = _engine(tmp_path)
    engine._record_context_measure(_view(engine))
    _append(engine, 30_000)
    engine._record_context_measure(1_000)
    assert engine._usage_unreliable is True

    engine._forget_context_measure()  # a compaction: the old count describes nothing now
    assert engine._usage_anchor is None and engine._usage_unreliable is True

    engine.switch_model("gpt-5.5")
    assert engine._usage_unreliable is False


# -- the estimate --------------------------------------------------------------------------


def test_a_thinking_blocks_signature_and_digest_are_not_priced_its_thinking_is():
    def answer(thinking, signature):
        return {
            "role": "assistant", "content": "done",
            "_anthropic": {"blocks": [{"type": "thinking", "thinking": thinking, "signature": signature}], "prefix": "f" * 64},
        }

    bare = C.estimate_tokens([{"role": "assistant", "content": "done"}])
    signed = C.estimate_tokens([answer("", "s" * 20_000)])
    thought = C.estimate_tokens([answer("t" * 4_000, "s" * 20_000)])
    assert signed - bare < 50  # 20k characters of signature are not 5k tokens
    assert 900 <= thought - signed <= 1_100  # the thinking is sent back and read


# -- the surface hears why ------------------------------------------------------------------


def test_the_compacting_event_says_why_it_runs(tmp_path):
    engine = _engine(tmp_path)
    engine.compaction_settings = lambda: {"context_window": 2_000, "buffer_tokens": 0}
    _append(engine, 5_000)
    assert engine._compaction_why() == "full"

    async def first():
        events = engine._compaction_checkpoint()
        try:
            return await events.__anext__()
        finally:
            await events.aclose()

    event = asyncio.run(first())
    assert event.type == EventType.COMPACTING and event.data["why"] == "full" and event.data["tokens"] > 2_000
    assert isinstance(AssistantTurn().context_cleared, type(None))
