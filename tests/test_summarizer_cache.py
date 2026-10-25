"""Making the summarizer call a prefix of the conversation it summarizes.

Compaction fires when the context is at its FULLEST — the one moment the conversation is
most expensive to send. The summarizer then built a fresh two-message prompt (its own
system prompt, the span rendered into one user message) that shared no bytes with the
request that had just run, so it paid full price for every token while the provider held
a warm cache of nearly the same content.

Measured on a 160-message transcript: replaying the conversation costs 0.18x on a cache
hit and 1.80x on a miss. Both halves of that matter, so both gates below exist:

- a summarizer on a DIFFERENT model has no cache of this conversation to read, and the
  replay would be strictly worse. Cheap and certain, so it is checked first;
- whether the cache actually answers cannot be known in advance — the prefix must match
  byte for byte and the provider decides. So the call asks once and believes the answer,
  the same shape as the Bedrock cache point: turn what cannot be verified here into
  something that verifies itself in production.

The same design is reached from the other end: the
directive is "delivered as the FINAL user message after the replayed conversation rather
than as a distinct summarizer system prompt", for exactly this reason.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from harness.context.compaction import COMPACTION_INSTRUCTION, summarize_span, summarizer_messages


class _Recorder:
    """Captures the request and answers with a usage the test dictates."""

    def __init__(self, cache_read=0, text="the summary"):
        self.calls = []
        self._cache_read = cache_read
        self._text = text

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=self._text,
            usage=SimpleNamespace(cache_read=self._cache_read, cache_write=0),
        )


CONVERSATION = [
    {"role": "system", "content": "you are a coding agent"},
    {"role": "user", "content": "look at the parser"},
    {"role": "assistant", "content": "reading it now"},
]


# -- the two shapes ---------------------------------------------------------------


def test_without_a_replay_the_call_is_exactly_what_it_always_was():
    provider = _Recorder()
    summarize_span(provider, "gpt-5.5", CONVERSATION[1:])
    sent = provider.calls[0]
    assert sent["messages"] == summarizer_messages(CONVERSATION[1:])
    assert sent["tools"] is None


def test_a_replay_puts_the_instruction_after_the_conversation():
    """Not before it. The whole point is that everything ahead of the instruction is
    byte-identical to the request that just ran."""
    provider = _Recorder(cache_read=900)
    summarize_span(provider, "gpt-5.5", [], replay=CONVERSATION, tools=[{"name": "read_file"}])

    sent = provider.calls[0]["messages"]
    assert sent[: len(CONVERSATION)] == CONVERSATION
    assert sent[-1] == {"role": "user", "content": COMPACTION_INSTRUCTION}


def test_the_instruction_points_at_the_conversation_that_is_actually_there():
    """The original prompt says "the conversation below" — which, replayed, is above it.
    A summarizer told to read the wrong direction summarizes its own instruction."""
    assert "conversation ABOVE" in COMPACTION_INSTRUCTION
    assert "conversation below" not in COMPACTION_INSTRUCTION
    # Every section survives the rewording; the deixis is the only change.
    for section in ("Primary request and intent", "All user messages", "Next step"):
        assert section in COMPACTION_INSTRUCTION


def test_a_replay_with_a_prior_summary_says_the_earlier_parts_stay():
    """The replayed conversation carries the earlier parts only as its `<compacted-history>`
    block; without the rules on the instruction the summarizer reads them as part of what to
    summarize, and replay is the default path (they are kept, not rewritten)."""
    provider = _Recorder(cache_read=900)
    summarize_span(
        provider,
        "gpt-5.5",
        [],
        prior_summary="the old summary",
        replay=CONVERSATION,
    )

    instruction = provider.calls[0]["messages"][-1]["content"]
    assert instruction.startswith(COMPACTION_INSTRUCTION)
    assert "you are writing ONLY the part for the conversation after it" in instruction

    # No prior summary → the instruction stays byte-identical to the base prompt (the
    # replay's prefix contract, pinned above).
    provider = _Recorder(cache_read=900)
    summarize_span(provider, "gpt-5.5", [], replay=CONVERSATION)
    assert provider.calls[0]["messages"][-1] == {
        "role": "user",
        "content": COMPACTION_INSTRUCTION,
    }


def test_tools_ride_along_with_the_replay():
    """Providers cache tools together with the system prompt, so dropping them moves the
    boundary and loses the prefix the replay exists to reuse."""
    provider = _Recorder(cache_read=900)
    tools = [{"name": "read_file"}]
    summarize_span(provider, "gpt-5.5", [], replay=CONVERSATION, tools=tools)
    assert provider.calls[0]["tools"] == tools


def test_a_tool_call_in_the_reply_is_harmless():
    """This is a bare provider round-trip, not the agent loop — nothing dispatches a call
    it returns, and only `.text` is read."""

    class _CallsATool(_Recorder):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                text="the summary",
                tool_calls=[SimpleNamespace(name="rm_rf", arguments={})],
                usage=SimpleNamespace(cache_read=900, cache_write=0),
            )

    assert summarize_span(_CallsATool(), "gpt-5.5", [], replay=CONVERSATION) == "the summary"


def test_an_empty_summary_still_raises_either_way():
    for replay in (None, CONVERSATION):
        with pytest.raises(RuntimeError):
            summarize_span(_Recorder(text="   "), "gpt-5.5", CONVERSATION[1:], replay=replay)


# -- the gates --------------------------------------------------------------------


def _engine(tmp_path, provider, *, model="gpt-5.5", summarizer_model=None):
    from harness.safety.permissions import PermissionEngine
    from harness.loop import TurnEngine
    from harness.tools import ToolRegistry

    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model=model,
    )
    engine.messages = list(CONVERSATION)
    # A small window so a boundary actually exists: the default 8k keep no longer scales
    # with the window, but the transcript is still far smaller than a real one.
    settings = {"context_window": 2000, "buffer_tokens": 0, "keep_tokens": 500}
    if summarizer_model:
        settings["model"] = summarizer_model
    engine.compaction_settings = lambda: settings
    return engine


def _compact(engine):
    async def go():
        return await engine._compact_now(force=True)

    return asyncio.run(go())


def test_a_summarizer_on_another_model_is_not_replayed(tmp_path):
    """It holds no cache of this conversation, so the replay would be strictly more
    expensive — more tokens, all of them billed at full price."""
    provider = _Recorder(cache_read=0)
    engine = _engine(tmp_path, provider, summarizer_model="claude-fable-5")
    engine.messages = list(CONVERSATION) * 60
    _compact(engine)

    assert provider.calls, "the summarizer never ran, so this test proved nothing"
    sent = provider.calls[0]
    assert sent["model"] == "claude-fable-5"
    assert sent["tools"] is None
    # The old shape: its own system prompt and the span rendered into one user message.
    assert len(sent["messages"]) == 2
    assert sent["messages"][0]["role"] == "system"


def test_a_cache_that_never_answers_is_asked_only_once(tmp_path):
    """Once is a measurement. Twice would be a habit — and each miss is 1.8x."""
    engine = _engine(tmp_path, _Recorder(cache_read=0))
    assert engine._summarizer_replay_pays is True

    engine.messages = list(CONVERSATION) * 60
    _compact(engine)
    assert engine._summarizer_replay_pays is False


def test_a_cache_that_answers_keeps_the_replay(tmp_path):
    engine = _engine(tmp_path, _Recorder(cache_read=4096))
    engine.messages = list(CONVERSATION) * 60
    _compact(engine)
    assert engine._summarizer_replay_pays is True
