"""A tool call whose result was never recorded does not make the provider refuse the request.

Found from a `/compact` that failed in session b3a8491e-9db: the summarizer call came back 400 from DeepSeek,
"An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'".
The history held a `run_shell` call with no result (the process died while it ran, then the model was switched), and
every request carries the whole history, a compaction replay included. What has to hold:

  - every call the provider is shown is answered right after its assistant message, by a stub when the history
    has no result for it;
  - a history with no orphan comes back unchanged (the bytes a provider cached stay the same);
  - pairing is by position: an id that repeats across the session (Gemini's `call_0`) is judged per assistant message;
  - the stub is projection only: canonical history keeps what happened;
  - a compaction's summarizer call is sent a history a strict provider accepts.
"""

from __future__ import annotations

import asyncio
import json

from harness.loop import TurnEngine
from harness.loop.message_shapes import ORPHANED_CALL_RESULT, answer_orphaned_calls as _answer_orphaned_calls
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

from summary_fixtures import valid_summary


def _asks(*ids):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": i, "function": {"name": "run_shell", "arguments": "{}"}} for i in ids],
    }


def _answer(call_id, content="done"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _unanswered(messages):
    """The strict-provider rule, as DeepSeek states it: each call is answered by the run right after it."""
    bad = []
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            got = set()
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                got.add(messages[j].get("tool_call_id"))
                j += 1
            bad += [c["id"] for c in m["tool_calls"] if c["id"] not in got]
    return bad


# -- the projection -----------------------------------------------------------------------


def test_an_unanswered_call_gets_a_stub_at_the_end_of_its_run():
    history = [{"role": "user", "content": "go"}, _asks("a"), {"role": "user", "content": "still there?"}]
    out = _answer_orphaned_calls(history)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user"]
    assert out[2]["tool_call_id"] == "a" and out[2]["content"] == ORPHANED_CALL_RESULT
    assert "no result was recorded" in json.loads(out[2]["content"])["error"]
    assert _unanswered(out) == []


def test_a_partly_answered_parallel_run_is_completed_after_its_real_results():
    out = _answer_orphaned_calls([_asks("a", "b", "c"), _answer("b"), {"role": "user", "content": "next"}])
    assert [(m["role"], m.get("tool_call_id")) for m in out] == [
        ("assistant", None),
        ("tool", "b"),  # the real result stays where it was
        ("tool", "a"),
        ("tool", "c"),
        ("user", None),
    ]
    assert out[1]["content"] == "done" and out[2]["content"] == ORPHANED_CALL_RESULT


def test_a_history_with_no_orphan_comes_back_unchanged():
    history = [{"role": "user", "content": "go"}, _asks("a", "b"), _answer("a"), _answer("b"), {"role": "assistant", "content": "ok"}]
    assert _answer_orphaned_calls(history) == history


def test_a_repeated_id_is_judged_by_its_own_assistant_message():
    # Gemini named every step's first call `call_0`: the first is orphaned, the second answered.
    history = [_asks("call_0"), {"role": "user", "content": "x"}, _asks("call_0"), _answer("call_0", "real")]
    out = _answer_orphaned_calls(history)
    assert [(m["role"], m.get("content") if m["role"] == "tool" else None) for m in out] == [
        ("assistant", None),
        ("tool", ORPHANED_CALL_RESULT),
        ("user", None),
        ("assistant", None),
        ("tool", "real"),
    ]


def test_the_projection_is_idempotent_and_does_not_touch_its_input():
    history = [_asks("a"), {"role": "user", "content": "x"}]
    snapshot = json.dumps(history)
    once = _answer_orphaned_calls(history)
    assert json.dumps(history) == snapshot
    assert _answer_orphaned_calls(once) == once


# -- through the engine -------------------------------------------------------------------


class _Strict(ProviderClient):
    """Refuses, as DeepSeek does, any request that holds a call with no result."""

    def __init__(self):
        self.seen_orphans: list[list[str]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        orphans = _unanswered(messages)
        self.seen_orphans.append(orphans)
        if orphans:
            raise RuntimeError(
                "Error code: 400 - An assistant message with 'tool_calls' must be followed by tool messages "
                "responding to each 'tool_call_id'. (insufficient tool messages following tool_calls message)"
            )
        if any("Produce ALL of the following sections" in str(m.get("content", "")) for m in messages):
            return AssistantTurn(text=valid_summary("finish the report"), finish_reason="stop")
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _history_with_an_orphan():
    msgs = [{"role": "system", "content": "be helpful"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"request {i}", "ts": 1.0})
        if i == 2:
            msgs.append(_asks("call_0"))  # the call that never got its result
            msgs.append({"role": "notice", "kind": "model_switch", "text": "switched"})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "x" * 1500, "ts": 1.0})
    return msgs


def _engine(tmp_path, provider, messages):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {"context_window": 1_000_000, "buffer_tokens": 0, "keep_tokens": 0}
    return engine


def test_a_compaction_summarizer_call_is_accepted_by_a_strict_provider(tmp_path):
    provider = _Strict()
    engine = _engine(tmp_path, provider, _history_with_an_orphan())
    result = asyncio.run(engine.compact_now())
    assert result["ok"] is True and result["reason"] == "summarized"  # it was a 400 and a "Retry / Skip" prompt
    assert provider.seen_orphans and all(not o for o in provider.seen_orphans)


def test_an_ordinary_turn_after_an_orphan_is_accepted_and_history_keeps_what_happened(tmp_path):
    provider = _Strict()
    messages = _history_with_an_orphan()
    engine = _engine(tmp_path, provider, messages)

    async def run():
        return [event async for event in engine.run("and now?")]

    asyncio.run(run())
    assert provider.seen_orphans == [[]]
    # Canonical history is untouched: the call is still without a result there, and no stub was written.
    assert _unanswered([m for m in engine.messages if m.get("role") != "notice"]) == ["call_0"]
    assert not any(m.get("content") == ORPHANED_CALL_RESULT for m in engine.messages)
