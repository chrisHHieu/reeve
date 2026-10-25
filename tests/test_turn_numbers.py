"""A turn number names one turn for the life of a session log.

The engine used to count turns from zero each time it was built, so a session whose engine
was rebuilt (a restart, a reopened conversation) logged `turn 1` twice. These tests pin both
halves of the repair: a new engine continues the log's numbering, and a reader renumbers a
log that already holds repeats.
"""

from __future__ import annotations

import asyncio

from harness.loop import TurnEngine
from harness.observability.metrics import turns_report
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.session.turn_numbers import last_turn, monotonic_turns


def _turn(number: int, *, start: float, steps: int = 1) -> list[dict]:
    rows = [{"type": "turn/start", "turn": number, "t": start}]
    for step in range(1, steps + 1):
        rows.append({"type": "step/start", "turn": number, "step": step, "t": start + step})
    rows.append({"type": "turn/end", "turn": number, "reason": "completed", "t": start + 50})
    return rows


def _numbers(events) -> list[int]:
    return [e["turn"] for e in monotonic_turns(events) if e["type"] == "turn/start"]


# -- the reader's half -------------------------------------------------------------------


def test_a_log_that_never_went_back_is_left_exactly_as_it_is():
    events = _turn(1, start=0) + _turn(2, start=100) + _turn(3, start=200)
    assert [id(e) for e in monotonic_turns(events)] == [id(e) for e in events]


def test_a_rebuilt_engines_turns_are_numbered_after_the_ones_before_it():
    # The shape in the real store: turns 1..3, then an engine rebuild starts at 1 again.
    events = _turn(1, start=0) + _turn(2, start=100) + _turn(3, start=200)
    events += _turn(1, start=1000) + _turn(2, start=1100)
    assert _numbers(events) == [1, 2, 3, 4, 5]
    # Every row of a turn moves with it, so `(turn, step)` names one step.
    keyed = {(e["turn"], e.get("step")) for e in monotonic_turns(events) if e["type"] == "step/start"}
    assert len(keyed) == 5


def test_two_rebuilds_then_an_engine_that_continues_the_count():
    events = _turn(1, start=0) + _turn(2, start=100)
    events += _turn(1, start=1000)  # first rebuild: counts again
    events += _turn(1, start=2000) + _turn(2, start=2100)  # second
    assert last_turn(events) == 5
    # An engine that seeds from `last_turn` writes 6; a reader must not shift it further.
    events += _turn(6, start=3000)
    assert _numbers(events) == [1, 2, 3, 4, 5, 6]


def test_a_synthetic_close_lands_on_the_turn_it_closes():
    """A new engine closes a turn a dead process left open BEFORE its own first start; that
    row carries the old turn's number and must stay on the old turn."""
    events = _turn(1, start=0)
    events += [{"type": "turn/start", "turn": 2, "t": 100}]  # the process died here
    events += [{"type": "turn/end", "turn": 2, "reason": "interrupted", "synthetic": True, "t": 900}]
    events += _turn(1, start=1000)
    renumbered = list(monotonic_turns(events))
    assert renumbered[-4]["turn"] == 2 and renumbered[-4].get("synthetic")
    assert _numbers(events) == [1, 2, 3]


def test_rows_without_a_turn_pass_through():
    events = [{"type": "mode/changed"}, {"type": "notice", "turn": None}] + _turn(1, start=0)
    assert list(monotonic_turns(events)) == events
    assert last_turn([]) == 0


def test_the_per_turn_fold_keeps_both_turns_that_shared_a_number():
    """`turn_metrics` is keyed (session, turn): with the raw numbers the second `turn 1`
    replaced the first one in the store."""
    events = _turn(1, start=0, steps=2) + _turn(1, start=1000, steps=3)
    rows = turns_report(events)
    assert [row["turn"] for row in rows] == [1, 2]
    assert [row["steps"] for row in rows] == [2, 3]


# -- the engine's half -------------------------------------------------------------------


class _Says(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, log: SessionLog) -> TurnEngine:
    """What a restarted app builds for a session: a fresh engine over the session's log."""
    engine = TurnEngine(
        provider=_Says(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.log = log
    return engine


def _run(engine: TurnEngine, text: str) -> None:
    async def drain() -> None:
        async for _ in engine.run(text):
            pass

    asyncio.run(drain())


def _logged(path, kind: str) -> list[int]:
    return [e["turn"] for e in SessionLog(path).read() if e["type"] == kind]


def test_a_rebuilt_engine_continues_the_logs_numbering(tmp_path):
    path = tmp_path / "s.events.jsonl"

    first = _engine(tmp_path, SessionLog(path))
    _run(first, "one")
    _run(first, "two")

    rebuilt = _engine(tmp_path, SessionLog(path))  # the app restarted: same session, same log
    _run(rebuilt, "three")

    assert _logged(path, "turn/start") == [1, 2, 3]
    assert _logged(path, "turn/end") == [1, 2, 3]


def test_an_engine_adopting_a_log_with_repeats_writes_a_number_no_reader_has_used(tmp_path):
    path = tmp_path / "old.events.jsonl"
    old = SessionLog(path)
    for row in _turn(1, start=0) + _turn(2, start=100) + _turn(1, start=1000):
        old.append(row["type"], **{k: v for k, v in row.items() if k not in ("type", "t")})

    _run(_engine(tmp_path, SessionLog(path)), "next")

    # Raw, the new turn is 4: after the numbers a reader assigns the three before it.
    assert _logged(path, "turn/start") == [1, 2, 1, 4]
    assert _numbers(SessionLog(path).read()) == [1, 2, 3, 4]
