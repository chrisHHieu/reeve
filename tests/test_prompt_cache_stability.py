"""Prompt-cache stability of the outbound view.

A provider matches a cached prefix byte for byte, so the request Reeve sends on the
second step of a turn — and on the first step of the NEXT turn — has to be an
append-extension of the one before it. Anything else and every token behind the first
difference is re-priced at the write rate instead of read at the cache rate.

The `<system-context>` block is the one piece of the prompt rebuilt from live state — it
carries a clock, the mode reminder, the skills menu. It is frozen into the user message
at turn start (`_stamp_live_context`), once, and never rewritten; the tests below pin
both halves: intra-turn (the block cannot drift between steps) and cross-turn (a new turn
appends a fresh reading without touching the previous one).
"""

from __future__ import annotations

import asyncio
import json
from itertools import count

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry


class _Recording(ProviderClient):
    """Answers from a script, keeping every request the engine handed it."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(list(messages))
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, clock):
    """A real engine with compaction pressure parked far away — this file is about the
    wire, not about compaction."""
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        context_provider=clock,
    )
    engine.compaction_settings = lambda: {
        "buffer_tokens": 0,
        "context_window": 10_000_000,
    }
    return engine


def _first_difference(earlier, later) -> int:
    """Index of the first message that differs, or -1 when `earlier` is a byte-prefix."""
    for i, msg in enumerate(earlier):
        if i >= len(later):
            return i
        if json.dumps(msg, sort_keys=True) != json.dumps(later[i], sort_keys=True):
            return i
    return -1


def _ticking_clock():
    """A context provider whose text differs on EVERY call, the way a live clock does."""
    ticks = count()
    return lambda: f"Now: minute {next(ticks)}"


def _two_step_turn():
    """A turn that forces a second model call, so two requests reach the provider."""
    return [
        AssistantTurn(
            tool_calls=[ToolCall(id="c1", name="nonexistent_tool", arguments={})],
            finish_reason="tool_calls",
        ),
        AssistantTurn(text="done", finish_reason="stop"),
    ]


def test_both_steps_of_a_turn_are_two_requests_and_the_second_extends_the_first(tmp_path):
    """The property the cache depends on, asserted on the wire rather than on the view:

    step 1's request must be a byte-prefix of step 2's. The clock ticks between the two
    calls, so this fails the moment the block is recomputed per call."""
    provider = _Recording(_two_step_turn())
    engine = _engine(tmp_path, provider, _ticking_clock())

    asyncio.run(_collect(engine))

    assert len(provider.seen) == 2, "the scripted tool call must force a second request"
    at = _first_difference(provider.seen[0], provider.seen[1])
    assert at == -1, (
        f"step 2 diverged from step 1 at message {at}; a provider caches by exact prefix, "
        "so everything from there was re-priced"
    )


def test_one_block_per_turn_and_a_fresh_reading_for_the_next(tmp_path):
    """Both halves of the cross-turn contract, asserted on the wire:

    the second turn's request must be a byte-append of the first turn's (nothing the
    provider cached is rewritten), and it must carry a NEW reading (the clock advanced,
    the skills menu is current). The old reading stays in history untouched — that is
    what makes the append possible.
    """
    clock = _ticking_clock()
    provider = _Recording(
        [
            AssistantTurn(text="done one", finish_reason="stop"),
            AssistantTurn(text="done two", finish_reason="stop"),
        ]
    )
    engine = _engine(tmp_path, provider, clock)

    asyncio.run(_collect(engine, "one"))
    asyncio.run(_collect(engine, "two"))

    first, second = provider.seen[0], provider.seen[1]
    at = _first_difference(first, second)
    assert at == -1, (
        f"turn 2 diverged from turn 1 at message {at}; a provider caches by exact prefix, "
        "so everything from there was re-priced"
    )
    assert "minute 0" in first[0]["content"]
    assert "minute 1" in second[-1]["content"]
    # The old reading is still exactly the one that was sent — not deleted, not rewritten.
    assert "Now: minute 0" in second[0]["content"]


def test_a_restarted_engine_resumes_the_context_baseline(tmp_path):
    """A reload gets a fresh engine over the saved transcript. The stable context has not
    changed, so the next turn must append only the clock line — restating the full block
    would break the prefix for no reason the content demanded. The digest rides the
    persisted message as a sidecar and never reaches the wire."""
    from harness.llm.wire import CONTEXT_EPOCH as _CONTEXT_EPOCH

    stable = "SKILL MENU (stable)"
    ticks = count()

    def make_engine(messages):
        provider = _Recording([AssistantTurn(text="ok", finish_reason="stop")])
        parts: dict[str, str] = {}

        def ctx():
            value = f"Now: minute {next(ticks)}"
            parts.clear()
            parts.update({"clock": value, "skills": stable})
            return value + "\n\n" + stable

        engine = TurnEngine(
            provider=provider,
            registry=ToolRegistry(),
            permissions=PermissionEngine(workspace_root=tmp_path),
            model="gpt-5.5",
            context_provider=ctx,
            context_parts=parts,
            messages=messages,
        )
        engine.compaction_settings = lambda: {
            "buffer_tokens": 0,
            "context_window": 10_000_000,
        }
        return engine, provider

    first, _ = make_engine(None)
    asyncio.run(_collect(first, "one"))
    saved = first.messages
    stamped = [m for m in saved if isinstance(m.get(_CONTEXT_EPOCH), dict)]
    assert stamped, "the frozen block must record its baseline on the user message"

    second, provider = make_engine(saved)
    asyncio.run(_collect(second, "two"))

    sent = provider.seen[0]
    assert all(_CONTEXT_EPOCH not in message for message in sent)
    assert "minute 1" in sent[-1]["content"]
    assert stable not in sent[-1]["content"], (
        "the unchanged stable context must not be restated after a restart"
    )
    assert saved[0]["content"] == "one"  # persisted user text is untouched


def test_a_render_with_no_context_provider_adds_no_block(tmp_path):
    """The stamp must not invent a block where the session has no provider."""
    engine = _engine(tmp_path, _Recording([]), None)
    engine.messages.append({"role": "user", "content": "go"})
    engine._turn_no = 1

    assert "<system-context>" not in engine._outbound_messages()[-1]["content"]


async def _collect(engine, text="continue"):
    return [event async for event in engine.run(text)]


def test_the_background_line_moves_on_its_own_and_its_end_is_said_once(tmp_path):
    """Session b3a8491e-9db: the model could not tell a still-running background child from
    a lost one and spawned the same explorer twice. The live context now lists the children
    in flight. It is volatile like the clock: when only it changes, only it is appended (the
    skills menu is not restated), and when the last child finishes that is said once."""
    stable = "SKILL MENU (stable)"
    running = {"line": "Background subagents still running:\n- ab0bcd3f58f4 — Explore frontend"}
    ticks = count()
    parts: dict[str, str] = {}

    def ctx():
        parts.clear()
        parts["clock"] = f"Now: minute {next(ticks)}"
        if running["line"]:
            parts["background"] = running["line"]
        parts["skills"] = stable
        return "\n\n".join(parts.values())

    provider = _Recording([AssistantTurn(text=f"ok {i}", finish_reason="stop") for i in range(3)])
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        context_provider=ctx,
        context_parts=parts,
    )
    engine.compaction_settings = lambda: {"buffer_tokens": 0, "context_window": 10_000_000}

    asyncio.run(_collect(engine, "one"))
    first = provider.seen[0][-1]["content"]
    assert "ab0bcd3f58f4" in first and stable in first

    asyncio.run(_collect(engine, "did both finish?"))
    second = provider.seen[1][-1]["content"]
    assert "ab0bcd3f58f4" not in second  # unchanged: not restated
    assert stable not in second

    running["line"] = ""
    asyncio.run(_collect(engine, "and now?"))
    third = provider.seen[2][-1]["content"]
    assert "No background subagents are running now." in third
    assert stable not in third  # the end is news; the menu is not
    assert _first_difference(provider.seen[1], provider.seen[2]) == -1  # still an append


def test_background_context_lists_only_this_sessions_children():
    import threading
    from types import SimpleNamespace

    from harness.interface.server.manager.background_status import background_context

    manager = SimpleNamespace(
        _subagent_lock=threading.Lock(),
        _background_parents={"c1": "parent", "c2": "other", "c3": "parent"},
        _background_labels={"c1": "Explore frontend gaps", "c3": "Explore backend gaps"},
        # The names `task` calls gave their children.
        _child_names={"parent": {"frontend": "c1"}},
    )
    line = background_context(manager, "parent")
    assert "c1 (frontend) — Explore frontend gaps" in line and "c3 — Explore backend gaps" in line
    assert "c2" not in line
    # How to tell one something without starting it again.
    assert "message_task with its name or task_id" in line
    assert background_context(manager, "nobody") == ""


def test_a_manager_session_sees_its_own_background_children(tmp_path, monkeypatch):
    """The wiring, end to end: the manager's engine carries the live line, from the same
    registry the completion writer clears."""
    from harness.interface.server import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    engine = manager.get_engine("parent", workspace=str(tmp_path))
    assert "Background subagents" not in engine.context_provider()

    with manager._subagent_lock:
        manager._background_parents["kid-1"] = "parent"
        manager._background_labels["kid-1"] = "Explore frontend gaps"
    assert "kid-1 — Explore frontend gaps" in engine.context_provider()

    with manager._subagent_lock:  # what the completion writer does
        manager._background_parents.pop("kid-1")
        manager._background_labels.pop("kid-1")
    assert "Background subagents" not in engine.context_provider()
