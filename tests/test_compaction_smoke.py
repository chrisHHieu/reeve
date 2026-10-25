"""A long conversation through the real SessionManager, compacted again and again (smoke
4 of 4). The provider has to be sent the compacted view (the summary block and the tail as it was),
what the person asked for has to outlive every compaction, and the state has to survive a save and
a rebuild halfway. It stands in, scripted, for the smoke on a live model, which needs a key.
"""

import asyncio
import json

from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import TokenUsage

# About 19k characters (~4.7k tokens) an answer: two of them outgrow the 8k tail a compaction
# keeps, so from the second turn on there is something to summarize. It was 400 repeats while the
# tail was measured on the stored rows (usage, timings, stamps); measured as sent an 8k
# tail holds 8k, and four such turns fitted in it.
BULK = "analysis paragraph " * 1000
STANDING_RULE = "never email it without approval"
TURNS = 8


# A summarizer round trip, whichever shape it has. Its instruction used to be `messages[0]`, a
# system prompt of its own; in the shape that reuses the cache it is the last message, after the
# conversation played again. Matching the list of sections, not a position, holds for both.
def _is_summarizer_call(messages):
    return any("Produce ALL of the following sections" in str(m.get("content", "")) for m in messages or [])


def _is_extraction_call(messages):
    """The checkpoint before a compaction (memory v2) asks the same provider for memories in the
    background; a scripted double answers it on the side, or it takes a turn's place."""
    return any("nominate durable memories" in str(m.get("content", "")) for m in messages or [])


def _summarizer_body(messages):
    """All the summarizer was given. Not `messages[1]`: in the shape that reuses the cache the
    material is the conversation spread over many messages, and an earlier summary is inside the
    compacted block, not under a heading of its own."""
    return chr(10).join(str(m.get("content", "")) for m in messages or [])


def _summary(number: int) -> str:
    sections = [
        ("Primary request and intent", f"Build the Q3 report; {STANDING_RULE}."),
        ("Key concepts and decisions", "Q3 closes at the end of September."),
        ("Artifacts and files", "(none yet)"),
        ("Errors and fixes", "(none)"),
        ("Problem solving", "(none)"),
        ("All user messages", "- Build the Q3 report"),
        ("Pending tasks", "- Finish the report"),
        ("Current work", f"Drafting section {number}."),
        ("Next step", "Continue drafting."),
    ]
    return "\n\n".join(f"## {heading}\n{body}" for heading, body in sections)


class LongSessionProvider(ProviderClient):
    """Answers each turn at length and reports usage as large as what it was sent; answers a summary
    call with every section; answers the memory pass with nothing."""

    def __init__(self):
        self.main_messages_seen: list[list[dict]] = []
        self.summary_prompts: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        if _is_extraction_call(messages):
            return AssistantTurn(text="[]", finish_reason="stop")
        if _is_summarizer_call(messages):
            self.summary_prompts.append(_summarizer_body(messages))
            return AssistantTurn(text=_summary(len(self.summary_prompts)), finish_reason="stop")
        self.main_messages_seen.append([dict(m) for m in messages])
        sent = sum(len(json.dumps(m, default=str)) for m in messages) // 4  # billed as a provider would
        return AssistantTurn(text=f"turn {len(self.main_messages_seen)}: {BULK}", finish_reason="stop", usage=TokenUsage(input=sent, output=500))

    def capabilities(self, model):
        return ModelCapabilities()


def _talk(manager, sid, workspace):
    """Eight turns on one event loop, as the server runs them: the engine's asyncio primitives bind
    to the loop they first run on, and an asyncio.run() a turn drops every stream after the first
    (found in the live smoke). The engine is rebuilt after the fifth. Gives back the engine and each
    new boundary in turn."""
    boundaries: list[int] = []

    async def conversation():
        engine = manager.get_engine(sid, agent="reeve", workspace=workspace)
        for n in range(TURNS):
            async for _ in engine.run(f"user step {n}: keep drafting the Q3 report"):
                pass
            answer = next(m for m in reversed(engine.messages) if m.get("role") == "assistant")
            assert f"turn {n + 1}:" in str(answer.get("content", ""))  # empty would mean a dropped stream
            state = engine.compaction_state
            if state is not None and (not boundaries or state.boundary_index != boundaries[-1]):
                boundaries.append(state.boundary_index)
            manager.save(sid, engine)
            if n == 4:
                manager._engines.pop(sid)
                engine = manager.get_engine(sid, agent="reeve", workspace=workspace)
                assert engine.compaction_state is not None  # back after the rebuild
        return engine

    return asyncio.run(conversation()), boundaries


def test_a_long_conversation_compacts_again_and_again_and_keeps_what_matters(tmp_path):
    provider = LongSessionProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    # Set off through the real Settings: a buffer larger than any window takes
    # `window - max(output, buffer)` to its floor, so every turn compacts.
    manager._prefs["compaction_buffer_tokens"] = 10_000_000
    engine, boundaries = _talk(manager, "smoke-long", str(tmp_path))

    assert len(boundaries) >= 2 and boundaries == sorted(boundaries)  # again and again, always forward
    # A later summary is given the earlier one. What has to outlive it is a rule the person stated
    # once, many compactions back, not any heading around it.
    assert any(STANDING_RULE in prompt for prompt in provider.summary_prompts[1:])

    # What the model was sent last: the block and the tail, not the whole growing history.
    last = provider.main_messages_seen[-1]
    assert last[0]["role"] == "system"
    block = last[1]["content"]
    assert "<compacted-history>" in block and "Q3 report" in block  # the intent
    assert "user step 0" in block  # the person's own messages, from the very first
    assert "do not recap" in block  # how to go on from it
    assert len(last) < len(engine.messages)

    # The stored transcript still has every turn, and a divider for each compaction.
    said = [str(m.get("content", "")) for m in engine.messages]
    assert all(any(f"user step {n}" in text for text in said) for n in range(TURNS))
    assert sum(1 for m in engine.messages if m.get("kind") == "compacted") >= 2
    assert manager.session_store.load("smoke-long").compaction["boundary_index"] == engine.compaction_state.boundary_index
