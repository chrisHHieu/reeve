"""What compaction keeps, measured as what it LOSES rather than what it saves.

Token counts say a compaction worked; they say nothing about whether the session can still
finish its job. These tests ask the harder question, in the one way that can be answered
without a live model: plant a sentinel fact in a part of the history, compact, and check
whether that fact is still reachable from the outbound view.

Two properties, and they are not the same one.

**Region scoping** — what the summarizer is shown. The verbatim tail is sent AFTER the
block, so a summarizer that read the tail could fold it into the block and produce two
copies of the recent past that disagree. Reeve sends the whole conversation in replay mode
on purpose (it is the same prefix the provider just cached, which is what makes the call
cheap), so this property is asserted where it holds structurally — the non-replay path,
where the span is the only conversation content.

**Mechanical preservation** — what survives without being asked. The working state is
extracted from tool records by code, not by a model, so a file written inside the span is
recorded as a fact rather than as something the summarizer might have mentioned. This is
the half of recall that can be pinned exactly, and `tests/test_compaction_thrash.py` covers
the other half's arithmetic.
"""

from __future__ import annotations

import asyncio

from summary_fixtures import valid_summary

from harness.context import compaction as C
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.tools import ToolRegistry

HEAD = "SENTINEL-HEAD: the user's first instruction was to audit the repo"
MIDDLE = "SENTINEL-MIDDLE: the answer is in ledger.py:212, and the fix is a null check"
TAIL = "SENTINEL-TAIL: the newest instruction, which the block must not swallow"


class _Recording(ProviderClient):
    """Keeps every request it is given, so the test can inspect what the summarizer saw."""

    def __init__(self, *, summary=None):
        summary = summary if summary is not None else valid_summary("audit")
        self.summary = summary
        self.requests: list = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(list(messages))
        if _is_summarizer_call(messages):
            return AssistantTurn(text=self.summary, finish_reason="stop")
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()

    @property
    def summarizer_inputs(self) -> list:
        return [r for r in self.requests if _is_summarizer_call(r)]


def _is_summarizer_call(messages) -> bool:
    return any(
        "compaction engine" in str(m.get("content", ""))
        or "Produce ALL of the following sections" in str(m.get("content", ""))
        for m in messages or []
    )


def _engine(tmp_path, provider, *, messages=None, cap=4_000, window=1_000_000):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.compaction_settings = lambda: {
        # `cap` is the desired trigger; the new rule derives it from the window, so the
        # buffer is `window − cap` (max() then picks the buffer when it dwarfs output).
        # Keep a quarter of the trigger verbatim, mirroring the old engine fraction.
        "context_window": window,
        "buffer_tokens": max(0, window - cap),
        "keep_tokens": max(1, cap // 4),
    }
    return engine


def _sentinelled_history(*, middle_chars: int = 6_000) -> list[dict]:
    """A head (user intent), a long middle (tool work), and a recent tail to keep verbatim."""
    msgs: list[dict] = [{"role": "system", "content": "be helpful"}]
    msgs.append({"role": "user", "content": HEAD})
    for i in range(8):
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"command": "python -m pytest -q"}',
                        },
                    }
                ],
            }
        )
        msgs.append(
            {
                "role": "user",
                "content": f"step {i}: keep going " + ("filler " * 40),
            }
        )
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": (MIDDLE if i == 4 else "") + ("r" * middle_chars),
            }
        )
    msgs.append({"role": "user", "content": TAIL})
    msgs.append({"role": "assistant", "content": "waiting on the newest instruction"})
    return msgs


# -- region scoping ---------------------------------------------------------------


def test_the_summarizer_sees_the_span_and_not_the_verbatim_tail(tmp_path):
    """Non-replay path, where the property holds structurally. A summarizer that could read
    the tail could write it into the block — and then the block and the verbatim tail would
    both describe the recent past, which is how a session ends up with two disagreeing
    accounts of what it was doing."""
    provider = _Recording()
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())
    engine._summarizer_replay_pays = False  # the non-replay path

    notice = asyncio.run(engine._compact_now())
    assert notice is not None

    [request] = provider.summarizer_inputs
    # This path renders the span into one user message and carries the instruction as the
    # system prompt — so "the conversation below" is the correct deixis here, and the span
    # is the whole of what the model sees.
    assert "conversation below" in str(request[0]["content"])
    sent = str(request[-1]["content"])
    assert MIDDLE in sent, "the span's own content must be what is summarized"
    assert TAIL not in sent, "the verbatim tail is not the summarizer's to fold in"


def test_the_instruction_matches_where_the_conversation_sits(tmp_path):
    """The deixis is load-bearing and differs by path: a model told to summarize what is
    BELOW it when the history is above it summarizes the instruction instead. Two paths,
    two layouts, and the wording has to follow the layout."""
    # Non-replay: instruction is the system prompt, the span is below it.
    assert "conversation below" in C.SUMMARY_SYSTEM_PROMPT
    assert "conversation ABOVE" in C.COMPACTION_INSTRUCTION
    assert "compaction engine" in C.COMPACTION_INSTRUCTION

    # Replay: the conversation is the messages in front, and the instruction is last.
    provider = _Recording()
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())
    asyncio.run(engine._compact_now())
    [request] = provider.summarizer_inputs
    assert "conversation ABOVE" in str(request[-1]["content"])


def test_the_replay_path_is_a_real_prefix_of_the_request_that_just_ran(tmp_path):
    """The cache trick, asserted as the property that makes it work: the summarizer's
    messages must be the outbound view byte for byte, with only the instruction appended.
    Anything else and the provider has no prefix to reuse, and the replay costs full price
    for the whole conversation — which is exactly what the gate on this path exists to
    avoid paying twice."""
    provider = _Recording()
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())
    # Captured BEFORE compacting: the point is that the replay is the view that was on the
    # wire a moment ago, and after the call that view is the compacted one.
    before = engine._outbound_messages()

    asyncio.run(engine._compact_now())
    [request] = provider.summarizer_inputs
    assert request[:-1] == before, "the replay must be the outbound view, unmodified"
    assert "compaction engine" in str(request[-1]["content"])


# -- mechanical preservation ------------------------------------------------------


def test_a_file_written_in_the_span_survives_without_the_model(tmp_path):
    """Extracted by code from the tool records, so it does not depend on the summarizer
    remembering. This is the difference between "the block usually mentions the file" and
    "the file is in the block"."""
    messages = _sentinelled_history()
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            msg["tool_calls"][0]["function"] = {
                "name": "write_file",
                "arguments": '{"path": "src/ledger.py", "content": "x"}',
            }
            break

    state = C.CompactionState(
        boundary_index=1, summary_text="", working_state=C.extract_working_state(messages)
    )
    block = C.compacted_block(state)
    assert "src/ledger.py" in block


def test_the_shell_commands_in_the_span_survive_without_the_model():
    state = C.CompactionState(
        boundary_index=1,
        summary_text="",
        working_state=C.extract_working_state(_sentinelled_history()),
    )
    block = C.compacted_block(state)
    assert "pytest" in block
    assert "Working state" in block


def test_the_span_summarized_matches_what_the_block_replaces(tmp_path):
    """A tripwire: the block must not be able to claim anything the
    span did not contain. `boundary_index` is the contract — the block represents exactly
    `messages[:boundary]`, and the tail from there on is verbatim."""
    provider = _Recording()
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())

    asyncio.run(engine._compact_now())
    state = engine.compaction_state
    assert state is not None
    outbound = C.apply_to_outbound(engine.messages, state)

    # The block is one user message directly after the system prompt.
    assert outbound[0]["role"] == "system"
    assert outbound[1]["role"] == "user" and "compacted-history" in outbound[1]["content"]
    # And the verbatim tail begins exactly where the state says.
    assert outbound[2:] == engine.messages[state.boundary_index :]


def test_the_tail_stays_verbatim_so_the_newest_instruction_is_intact(tmp_path):
    """The fact a session most needs after compacting is what it was just asked to do. It is
    kept as text rather than summarized, so it is asserted by identity rather than by
    substring: the message the user typed is the message the model reads."""
    provider = _Recording()
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())

    asyncio.run(engine._compact_now())
    outbound = engine._outbound_messages()
    assert any(
        m.get("role") == "user" and m.get("content") == TAIL for m in outbound
    ), "the newest user message must survive exactly"


def test_the_instruction_asks_for_the_sections_a_resumed_session_needs(tmp_path):
    """The deterministic half of "does the summary carry what matters": we cannot pin an
    LLM's prose, but we can pin that it was asked for the right things. A section dropped
    from this list is a class of fact that silently stops surviving compaction."""
    instruction = C.COMPACTION_INSTRUCTION
    for section in (
        "Primary request and intent",
        "Key concepts and decisions",
        "Artifacts and files",
        "Errors and fixes",
        "Problem solving",
        "All user messages",
        "Pending tasks",
        "Current work",
        "Next step",
    ):
        assert section in instruction, section
    # And the rules that keep a stale summary from being worse than none.
    assert "Do NOT carry full file contents as truth" in instruction
    assert "[REDACTED]" in instruction


# -- the section contract is enforced, not just requested ---------------------------


def test_a_reply_missing_sections_gets_one_corrective_re_ask(tmp_path):
    class _Scripted(_Recording):
        def __init__(self):
            super().__init__(summary="## Primary request and intent\nonly one section")
            self.replies = [self.summary, valid_summary("complete now")]

        def complete(self, *, model, messages, tools=None, **settings):
            self.requests.append(list(messages))
            if _is_summarizer_call(messages):
                return AssistantTurn(text=self.replies.pop(0), finish_reason="stop")
            return AssistantTurn(text="done", finish_reason="stop")

    provider = _Scripted()
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())
    asyncio.run(engine._compact_now())

    assert engine.compaction_state is not None
    assert engine.compaction_state.summary_text == valid_summary("complete now")
    calls = provider.summarizer_inputs
    assert len(calls) == 2
    correction = calls[1][-1]["content"]
    assert "missing" in correction.lower()
    assert "All user messages" in correction  # names what the first reply lacked


def test_a_second_invalid_reply_fails_the_attempt(tmp_path, monkeypatch):
    """One corrective re-ask; a second miss is the failure policy's
    problem, not a third call. Prune is disabled so the invalid reply is the one under
    test (it would otherwise resolve the pressure first)."""
    provider = _Recording(summary="## Primary request and intent\nstill one section")
    engine = _engine(tmp_path, provider, messages=_sentinelled_history())
    monkeypatch.setattr(C, "prune_boundary", lambda *a, **k: 0)

    assert asyncio.run(engine._compact_now()) is None

    assert engine.compaction_state is None
    assert engine._compaction_failed_at > 0
    # Two build attempts (the engine's one retry) × (reply + one corrective re-ask).
    assert len(provider.summarizer_inputs) == 4


def test_summary_sections_forgive_formatting_not_gaps():
    good = valid_summary("x")
    assert C.summary_missing_sections(good) == []
    assert C.summary_missing_sections(good.replace("## ", "### 1. ")) == []
    assert C.summary_missing_sections("## Summary\nall good") == list(C.SUMMARY_SECTIONS)


def test_the_block_tells_the_model_to_carry_on_without_recapping(tmp_path):
    """A resumed session that announces it was compacted, or re-asks something already
    answered, is a worse outcome than a slightly lossier summary."""
    state = C.CompactionState(boundary_index=1, summary_text="s", working_state="")
    block = C.compacted_block(state)
    assert C.CONTINUATION_CONTRACT in block
    assert "Do not re-ask answered questions" in block
    assert "do not mention that the context was compacted" in block


def test_the_prompt_names_every_required_section_in_order():
    """The contract has two halves that drift apart silently: the prompt that asks and the
    tuple the code checks. A section only in one of them is either never produced or never
    enforced."""
    prompt = C.SUMMARY_SYSTEM_PROMPT.lower()
    positions = [prompt.index(name.lower()) for name in C.SUMMARY_SECTIONS]
    assert positions == sorted(positions)
    assert "Problem solving" in C.SUMMARY_SECTIONS


def test_every_summarizer_instruction_opens_with_the_no_tools_rule():
    """The replay call leaves the conversation's tools in front of the model to keep its cache
    prefix; a reply that is a tool call has no text and loses the compaction. The rule has to
    lead the default prompt, the replay instruction, and a custom instruction alike."""
    assert C.SUMMARY_SYSTEM_PROMPT.startswith(C.NO_TOOLS_NOTICE)
    assert C.COMPACTION_INSTRUCTION.startswith(C.NO_TOOLS_NOTICE)
    assert C.compaction_instruction("Keep only ticket ids.").startswith(C.NO_TOOLS_NOTICE)
