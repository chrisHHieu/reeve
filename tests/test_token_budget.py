"""The per-turn token budget: the spend rail beside the iteration cap.

`max_iterations` bounds how MANY steps a turn takes and says nothing about how large they
were. A step is not a fixed size — a hundred steps at a full context is two orders of
magnitude more provider traffic than a hundred small ones — and the runaway loop is exactly
the shape that keeps a large context alive, re-sending it every step. Nothing bounded that:
the count of steps was bounded, the size of them was not.

What is pinned here:

  · the budget trips on CACHE READS, not just fresh input — the shape it exists to stop is
    cheap per token and would hide from a fresh-input-only count;
  · a tripped turn ends the way the iteration cap ends it: a checkpoint is written (so
    "continue" resumes), the reason is typed, and the payload carries the spend;
  · what a subagent spent counts against the parent's turn, because a fan-out is the
    expense the parent's own traffic does not show;
  · under the budget nothing changes, and 0 means no ceiling at all.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import TokenUsage
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.session.turns import TurnEndReason
from harness.tools.task import task_tools


def _text_turn(text, usage=None):
    return AssistantTurn(text=text, finish_reason="stop", usage=usage)


def _tool_turn(name, args, call_id="call_1", usage=None):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
        usage=usage,
    )


def _usage(input=0, output=0, cache_read=0, cache_write=0):
    return TokenUsage(
        input=input, output=output, cache_read=cache_read, cache_write=cache_write
    )


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turns, *, token_budget=0, max_iterations=12):
    provider = ScriptedProvider(turns)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        max_iterations=max_iterations,
        token_budget=token_budget,
    )
    engine.log = SessionLog(tmp_path / "session.jsonl")
    return engine, provider


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _reasons(engine):
    return [e["reason"] for e in engine.log.read() if e["type"] == "turn/end"]


def _ends(engine):
    return [e for e in engine.log.read() if e["type"] == "turn/end"]


# -- the rail --------------------------------------------------------------------


def test_a_turn_over_budget_stops_on_the_graceful_path(tmp_path):
    """Same ending as the iteration cap: a checkpoint is written and the reason is typed,
    so "continue" resumes from the handoff instead of the turn dying as a bare error."""
    turns = [
        _tool_turn("list_files", {}, "c1", usage=_usage(input=60_000)),
        _tool_turn("list_files", {}, "c2", usage=_usage(input=60_000)),
        # The grace call that writes the checkpoint — it must not need a tool.
        _text_turn("DONE: listed the files; nothing remains."),
    ]
    engine, provider = _engine(tmp_path, turns, token_budget=100_000)
    events = _collect(engine, "look around")

    assert _reasons(engine) == [TurnEndReason.TOKEN_BUDGET]
    end = events[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "token_budget_exceeded"
    assert end.data["tokens"] >= 120_000
    assert end.data["token_budget"] == 100_000
    # The grace call: one final tool-less turn wrote the handoff.
    assert provider.calls == 3
    assert any(
        m.get("role") == "assistant" and "DONE:" in str(m.get("content", ""))
        for m in engine.messages
    )


def test_cached_traffic_counts_against_the_budget(tmp_path):
    """The runaway this exists to stop is the CHEAP-looking one: a long turn re-sends a
    large context every step, so its spend lives in `cache_read` and its fresh-input line
    reads close to zero. A fresh-input-only count would let the most expensive loop in the
    product never trip the ceiling — so this turn has NO fresh input at all and must still
    stop."""
    turns = [
        _tool_turn("list_files", {}, "c1", usage=_usage(cache_read=60_000)),
        _tool_turn("list_files", {}, "c2", usage=_usage(cache_read=60_000)),
        _text_turn("DONE: nothing left."),
    ]
    engine, _ = _engine(tmp_path, turns, token_budget=100_000)
    events = _collect(engine, "go")

    assert events[-1].data["status"] == "token_budget_exceeded"
    assert engine._turn_tokens == 120_000  # every token of it a cache read
    assert engine._turn_tokens >= 100_000


def test_under_budget_nothing_changes(tmp_path):
    turns = [
        _tool_turn("list_files", {}, "c1", usage=_usage(input=1_000, output=200)),
        _text_turn("Listed them."),
    ]
    engine, provider = _engine(tmp_path, turns, token_budget=1_000_000)
    events = _collect(engine, "look")

    assert events[-1].data["status"] == "completed"
    assert "token_budget_exceeded" not in _reasons(engine)
    assert provider.calls == 2


def test_zero_means_no_ceiling(tmp_path):
    """Off by default on a bare engine: the check is a `>` against 0, which would stop
    every turn at its first reported token if 0 were taken literally."""
    turns = [
        _tool_turn("list_files", {}, "c1", usage=_usage(input=10_000_000)),
        _text_turn("Done."),
    ]
    engine, _ = _engine(tmp_path, turns, token_budget=0)
    events = _collect(engine, "go")

    assert events[-1].data["status"] == "completed"


def test_the_iteration_cap_is_named_first_when_both_are_spent(tmp_path):
    """They are not exclusive — a runaway trips both — and only one can be reported. The
    order is the order of the rail checks, and it is stated rather than incidental."""
    turns = [
        _tool_turn("list_files", {}, "c1", usage=_usage(input=50_000)),
        _tool_turn("list_files", {}, "c2", usage=_usage(input=50_000)),
        _text_turn("DONE: stopping."),
    ]
    engine, _ = _engine(tmp_path, turns, token_budget=10_000, max_iterations=1)
    _collect(engine, "go")

    assert _reasons(engine) == [TurnEndReason.MAX_ITERATIONS]


# -- the ledger ------------------------------------------------------------------


def test_the_turn_end_records_what_the_turn_cost(tmp_path):
    """`what did this turn cost` is a question the log should answer without the reader
    re-summing every assistant/message above it."""
    turns = [
        _tool_turn("list_files", {}, "c1", usage=_usage(input=1_000, output=100)),
        _text_turn("Done.", usage=_usage(input=2_000, output=50)),
    ]
    engine, _ = _engine(tmp_path, turns, token_budget=1_000_000)
    _collect(engine, "go")

    ends = _ends(engine)
    assert len(ends) == 1
    assert ends[0]["tokens"] == 3_150  # 1000+100 + 2000+50
    assert ends[0]["reason"] == "completed"


def test_delegated_work_counts_against_the_parents_turn(tmp_path):
    """A fan-out is the expense the parent's own traffic does not show: while a child
    reads its way through a codebase the parent's counter barely moves. The child's total
    rides the tool result and is folded in."""
    child = ScriptedProvider(
        [_text_turn("Found it in a.py:1.", usage=_usage(input=80_000, output=500))]
    )
    parent_turns = [
        _tool_turn(
            "task",
            {
                "description": "find it",
                "prompt": "where is answer?",
                "subagent_type": "explore",
            },
            "s1",
        ),
        _text_turn("Done."),
    ]
    provider = ScriptedProvider(parent_turns)
    registry = ToolRegistry()
    registry.register_all(task_tools(workspace=tmp_path, provider=child, model="m"))
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        token_budget=1_000_000,
    )
    engine.log = SessionLog(tmp_path / "session.jsonl")
    _collect(engine, "delegate it")

    # 80,500 from the child, none of it the parent's own traffic.
    assert engine._turn_tokens >= 80_500
    assert _ends(engine)[0]["tokens"] >= 80_500
    # ...and the log says where it came from: the delegating call's own result carries
    # the child's total, so `turn/end` is explained by logged calls
    # (scripts/harness_kpis.py reports any residue as unattributed spend).
    results = [
        e for e in engine.log.read() if e.get("type") == "tool/result" and e.get("name") == "task"
    ]
    assert [e.get("tokens") for e in results] == [80_500]


def test_a_provider_that_reports_no_usage_still_trips_the_budget(tmp_path):
    """Otherwise the ceiling would silently not exist for that provider, which is the one
    thing a rail may not do. The counter falls back to the same chars/4 estimator the
    window signal uses when nothing was measured."""
    text = "x" * 40_000  # ≈10k estimated tokens of tool output per step
    turns = [
        _tool_turn("write_file", {"path": "a.txt", "content": text}, "w1"),
        _tool_turn("write_file", {"path": "b.txt", "content": text}, "w2"),
        _text_turn("DONE: both written."),
    ]
    engine, _ = _engine(tmp_path, turns, token_budget=10_000)
    events = _collect(engine, "write it")

    assert events[-1].data["status"] == "token_budget_exceeded"
    assert engine._turn_tokens > 0  # nothing was reported, and the rail still counted


def test_only_a_delegating_result_contributes_delegated_tokens(tmp_path):
    """The fold is a no-op for every other tool rather than an invented number, and an
    absent or zero count adds nothing."""
    from harness.loop.message_shapes import delegated_tokens as _delegated_tokens

    assert _delegated_tokens({"path": "a.txt"}) == 0
    assert _delegated_tokens({"tokens": 0}) == 0
    assert _delegated_tokens({"tokens": None}) == 0
    assert _delegated_tokens({"tokens": "800"}) == 0
    assert _delegated_tokens("done") == 0
    assert _delegated_tokens({"tokens": 80_500}) == 80_500

    # And in a real turn with no reported usage, the counter is the estimator's number for
    # the prompt — small — never a delegation-sized one.
    turns = [
        _tool_turn("write_file", {"path": "a.txt", "content": "x"}, "w1"),
        _text_turn("Done."),
    ]
    engine, _ = _engine(tmp_path, turns, token_budget=1_000_000)
    _collect(engine, "write it")

    assert 0 < engine._turn_tokens < 10_000


def test_carried_background_spend_is_charged_and_logged(tmp_path):
    """Orchestration audit O7: a background child's tokens, handed to the parent engine
    when its result is injected, count toward the turn that receives it — with a
    `delegation/background` row so the turn's total stays attributable."""
    from harness.session.session_log import SessionLog

    engine = TurnEngine(
        provider=ScriptedProvider([AssistantTurn(text="done", finish_reason="stop")]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=str(tmp_path)),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "log.jsonl")
    engine.carry_delegated("general-7", 1200)

    async def go():
        return [e async for e in engine.run("go")]

    asyncio.run(go())
    rows = engine.log.read()
    carried = [r for r in rows if r.get("type") == "delegation/background"]
    assert [(r["run_id"], r["tokens"]) for r in carried] == [("general-7", 1200)]
    end = [r for r in rows if r.get("type") == "turn/end"][-1]
    assert end["tokens"] >= 1200
