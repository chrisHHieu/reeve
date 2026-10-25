"""The check loop: a red finish is named, a repeated red check is stopped.

A failed last check got one note at finish; two gaps stayed open. A finish with
the last check FAILING and no edit since still ended as `completed` — indistinguishable
from a verified one; and the repeat guard only stops byte-identical calls, so equivalent
commands (`pytest -q` / `python -m pytest`) could re-run a failing check until the turn
ran out of budget. Pinned here:

- the streak arithmetic: a NEW failed check advances it; a pass, an unknown exit, or any
  edit resets it; the same result seen twice does not double-count;
- the notice at 3 (once, model-visible, logged `reason="check_loop"`);
- the stop at 5: checkpoint written, `no_progress_stopped`, `check_loop` payload;
- the red finish label (`completed_check_failed` + `check_failed: true`);
- the no-false-stop path: an edit between failing checks keeps the streak below 3.
"""

from __future__ import annotations

import asyncio
import json

from harness.loop import ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.session.turns import TurnEndReason


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


class ScriptedProvider(ProviderClient):
    def __init__(self, turns, *, tail="done"):
        self._turns = list(turns)
        self._tail = tail
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if not self._turns:
            return AssistantTurn(text=self._tail, finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


async def _approve(_req):
    return ApprovalOutcome.ONCE


def _engine(tmp_path, turns):
    provider = ScriptedProvider(turns)
    registry = ToolRegistry()

    def run_shell(command: str = "") -> dict:
        return {"exit_code": 1, "summary": "1 failed"}

    def write_file(path: str = "", content: str = "") -> dict:
        return {"ok": True}

    run_shell.__name__ = "run_shell"
    write_file.__name__ = "write_file"
    registry.register(run_shell)
    registry.register(write_file)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=_approve,
    )
    engine.log = SessionLog(tmp_path / "session.jsonl")
    return engine, provider


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _reason(engine):
    return [e["reason"] for e in engine.log.read() if e["type"] == "turn/end"]


def _notes(engine):
    return [m for m in engine.messages if m.get("_attached_by") == "check_loop_nudge"]


CHECK = "python -m pytest -q"
#: Equivalent-but-DIFFERENT commands: the repeat guard keys on (tool, arguments) and
#: stays quiet, which is exactly the loop this rail exists for (the identical-call case
#: is already covered by the repeat guard's own 3/5/8 ladder).
CHECKS = [f"{CHECK} -k case{i}" for i in range(5)]


def test_a_new_failed_check_advances_and_a_pass_or_edit_resets(tmp_path):
    engine, _ = _engine(tmp_path, [])
    _seed_check(engine, "== 1 failed in 0.51s ==\n[exit 1; cwd ws]", call_id="c1")
    assert engine._track_check_streak() == 1
    assert engine._track_check_streak() == 1  # same result seen again: not a new check
    # The same result again, however long it took this time.
    _seed_check(engine, "== 1 failed in 0.73s ==\n[exit 1; cwd ws]", call_id="c2")
    assert engine._track_check_streak() == 2
    _seed_check(engine, "1 passed in 0.2s\n[exit 0; cwd ws]", call_id="c3")
    assert engine._track_check_streak() == 0


def test_a_failed_check_that_says_something_new_restarts_the_count(tmp_path):
    """The history-eval run the rail stopped had run four failing checks with
    four different results, learning what was broken. A different result is a finding,
    not a repeat."""
    engine, _ = _engine(tmp_path, [])
    for i, said in enumerate(["2 errors", "6 failed, 46 passed", "2 failed", "4 failed, 67 passed"]):
        _seed_check(engine, f"{said} in 1.{i}s\n[exit 1; cwd ws]", call_id=f"c{i}")
        assert engine._track_check_streak() == 1, said
    assert engine._check_loop_stopped() is False


def test_an_unknown_exit_does_not_advance(tmp_path):
    engine, _ = _engine(tmp_path, [])
    _seed_check(engine, "1 failed\n[exit 1; cwd ws]", call_id="c1")
    assert engine._track_check_streak() == 1
    _seed_check(engine, "some output\n[exit unknown; cwd ws]", call_id="c2")
    assert engine._track_check_streak() == 0


def test_five_failed_checks_with_no_edit_stop_the_turn(tmp_path):
    turns = [
        _tool_turn("run_shell", {"command": command}, f"t{i}")
        for i, command in enumerate(CHECKS)
    ]
    turns.append(_text_turn("Handoff: the check keeps failing unchanged."))
    engine, provider = _engine(tmp_path, turns)

    events = _collect(engine, "make the tests pass")

    assert provider.calls == 6  # five checks plus the checkpoint call
    assert _reason(engine) == [TurnEndReason.NO_PROGRESS]
    end = events[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "no_progress_stopped"
    assert end.data["check_loop"]["count"] == 5
    assert end.data["check_loop"]["command"] == CHECKS[-1]
    # The notice spoke once, at 3, before the stop.
    rows = [e for e in engine.log.read() if e["type"] == "verify/nudge"]
    assert [(r["reason"], r.get("count")) for r in rows] == [("check_loop", 3)]
    assert len(_notes(engine)) == 1
    assert "3 in a row" in _notes(engine)[0]["content"]
    # The graceful path: a checkpoint handoff was written, not a bare abort.
    assert any(
        m.get("_attached_by") == "checkpoint" for m in engine.messages
    )


def test_an_edit_between_failing_checks_prevents_the_stop(tmp_path):
    turns = [
        _tool_turn("run_shell", {"command": CHECKS[0]}, "t1"),
        _tool_turn("write_file", {"path": "app.py", "content": "x = 1"}, "t2"),
        _tool_turn("run_shell", {"command": CHECKS[1]}, "t3"),
        _tool_turn("run_shell", {"command": CHECKS[2]}, "t4"),
        _text_turn("Done, the edit did not fix it."),
        _text_turn("Still done."),
    ]
    engine, provider = _engine(tmp_path, turns)
    events = _collect(engine, "fix it")

    assert len(_notes(engine)) == 0  # never reached 3 consecutive
    assert _reason(engine) == [TurnEndReason.COMPLETED_CHECK_FAILED]
    assert events[-1].data["check_failed"] is True
    assert provider.calls == 6


def test_a_red_finish_is_labeled_check_failed(tmp_path):
    engine, provider = _engine(tmp_path, [_text_turn("All done."), _text_turn("Still done.")])
    _seed_check(engine, "1 failed\n[exit 1; cwd ws]", call_id="c1")

    events = _collect(engine, "fix the tests")

    assert provider.calls == 2  # the finish, the nudge, the finish that ends the turn
    end = events[-1]
    assert end.data["status"] == "completed"  # downstream matching is unchanged
    assert end.data["check_failed"] is True
    assert "unverified" not in end.data
    assert _reason(engine) == [TurnEndReason.COMPLETED_CHECK_FAILED]


def test_a_turn_without_a_check_is_still_plain_completed(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("an answer")])
    events = _collect(engine, "hi")

    assert "check_failed" not in events[-1].data
    assert _reason(engine) == [TurnEndReason.COMPLETED]


def test_both_verify_notes_are_known_to_the_visibility_audit():
    """The red-check note shipped without a `_TAG_EVENTS` entry, so the audit read it as a
    call-id attachment and logged a violation for a history that was fully recorded.
    Both tags are pinned here."""
    from harness.session.invariants import audit_messages

    index = {
        "header": 1,
        "user": 0,
        "steering": 0,
        "assistant": 0,
        "verify": 1,
        "todo": 0,
        "checkpoint": 0,
        "summary": 0,
        "tool_calls": set(),
        "tool_results": set(),
    }
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "note", "_attached_by": "check_failed_nudge"},
        {"role": "user", "content": "note", "_attached_by": "check_loop_nudge"},
    ]
    assert audit_messages(messages, index) == []


def _seed_check(engine, content: str, *, call_id: str, command: str = "pytest -q") -> None:
    """A settled shell call in the transcript, as the loop would have left it.

    The matching log rows are written too, so the "model-visible means logged" audit
    sees a history it can vouch for.
    """
    engine.messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps({"command": command}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call_id, "content": content},
        ]
    )
    engine.log.append(
        "assistant/message",
        turn=1,
        step=1,
        text="",
        tool_calls=[{"call_id": call_id, "name": "run_shell"}],
    )
    engine.log.append(
        "tool/call", turn=1, step=1, call_id=call_id, name="run_shell",
        arguments={"command": command},
    )
    engine.log.append(
        "tool/result", turn=1, step=1, call_id=call_id, content=content,
        is_error="exit 1" in content,
    )
