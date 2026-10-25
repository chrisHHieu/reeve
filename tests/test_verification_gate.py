"""The verification gate: what arms it, and what a finish is called.

A hard gate would block a turn from finishing after a code change until new test evidence exists.
Reeve made that a nudge — one note, then the model
decides — because a gate that can loop is worse than one that can be ignored once. What the
port left undone was the other half of the bargain: nothing NAMED the unproven finish, so
"I changed app.py" and "I changed app.py and ran the tests" both ended the turn as
`completed`, and only one of them is evidence.

Two holes are pinned here.

**The gate watched one door of four.** `_CODE_WRITE_TOOLS` was `{"write_file"}`, so
`replace_in_file` — the tool `write_file`'s own description tells the model to prefer for a
fragment edit — and both patch tools set nothing at all. The most common edit path in the
product could change a `.py` and finish with the gate silent.

**An ignored nudge left no trace.** The finish now carries
`TurnEndReason.COMPLETED_UNVERIFIED`, plus `unverified: true` on the turn-end payload for
the surfaces and automation that already read `truncated` the same way.

The coverage matrix is asserted against `_note_tool_outcome` rather than through a live
turn: the other three write tools bring their own approval and read-before-edit
requirements into a turn, so an end-to-end test of them would be testing those instead of
the gate. Their real argument shapes are the ones asserted here — path for the two that name
a file, patch/diff envelopes for the two that do not.
"""

from __future__ import annotations

import asyncio
import json

import aisuite as ai

from harness.loop import ApprovalOutcome, TurnEngine
from harness.loop.code_changes import code_paths as _code_paths, is_code_write as _is_code_write
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.session.turns import TurnEndReason


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
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


async def _approve(_req):
    return ApprovalOutcome.ONCE


def _engine(tmp_path, turns, *, approver=_approve):
    provider = ScriptedProvider(turns)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approver,
    )
    engine.log = SessionLog(tmp_path / "session.jsonl")
    return engine, provider


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _reason(engine):
    return [e["reason"] for e in engine.log.read() if e["type"] == "turn/end"]


def _nudged(engine):
    return any(m.get("_attached_by") == "verify_nudge" for m in engine.messages)


# -- the coverage hole -----------------------------------------------------------


def test_all_four_write_tools_arm_the_gate(tmp_path):
    """The bug this file exists for: three of the four write tools used to arm nothing."""
    engine, _ = _engine(tmp_path, [])
    for name, arguments in [
        ("write_file", {"path": "src/app.py", "content": "x = 1"}),
        ("replace_in_file", {"path": "src/app.py", "old": "a", "new": "b"}),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"},
        ),
        (
            "apply_unified_diff",
            {"diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b"},
        ),
    ]:
        engine._turn_wrote_code = False
        engine._turn_ran_after = True  # a stale True must be cleared by a write
        engine._note_tool_outcome(name, arguments, "ok")
        assert engine._turn_wrote_code, name
        assert not engine._turn_ran_after, name


def test_prose_and_failed_writes_do_not_arm_the_gate(tmp_path):
    engine, _ = _engine(tmp_path, [])
    for name, arguments in [
        ("write_file", {"path": "notes.md", "content": "hi"}),
        ("replace_in_file", {"path": "README.md", "old": "a", "new": "b"}),
        ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: docs/x.md\n*** End Patch"}),
        ("run_shell", {"command": "ls"}),  # not a write at all
        ("write_file", {}),  # nothing named
    ]:
        engine._turn_wrote_code = False
        engine._note_tool_outcome(name, arguments, "ok")
        assert not engine._turn_wrote_code, name

    # A write that FAILED left nothing on disk to run: it is not a code change.
    engine._turn_wrote_code = False
    engine._note_tool_outcome("write_file", {"path": "src/app.py"}, "error")
    assert not engine._turn_wrote_code


def test_deleting_and_renaming_a_code_file_arm_the_gate():
    """A deletion's only surviving name is in the diff's `---` header (its `+++` side is
    /dev/null), so scanning one side of a unified diff would call a deleted module
    untouched."""
    assert _code_paths("apply_unified_diff", {"diff": "--- a/src/dead.py\n+++ /dev/null\n@@\n-x"}) == [
        "src/dead.py"
    ]
    assert _is_code_write(
        "apply_unified_diff", {"diff": "--- a/src/dead.py\n+++ /dev/null\n@@\n-x"}
    )
    assert _is_code_write(
        "apply_unified_diff", {"diff": "--- a/old.py\n+++ b/new.py\n@@\n-a\n+b"}
    )
    assert _is_code_write("apply_patch", {"patch": "*** Delete File: src/dead.ts\n"})


def test_every_evidence_tool_disarms_the_gate(tmp_path):
    engine, _ = _engine(tmp_path, [])
    for name, args in (
        ("run_shell", {"command": "pytest -q"}),
        ("run_code", {}),
        ("terminal_send", {}),
        ("shell_task_output", {}),
    ):
        engine._note_tool_outcome("write_file", {"path": "app.py"}, "ok")
        assert not engine._turn_ran_after
        engine._note_tool_outcome(name, args, "ok")
        assert engine._turn_ran_after, name


def test_a_shell_command_counts_only_when_it_exercises_the_change(tmp_path):
    # 2026-09-25 audit, W13: any successful `run_shell` counted, so `cat app.py` or
    # `git diff` after an edit silenced the gate. A check or a run of the changed file
    # counts; looking at the change does not.
    engine, _ = _engine(tmp_path, [])
    engine._note_tool_outcome("replace_in_file", {"path": "src/calc.py"}, "ok")
    for looking in (
        "cat src/calc.py",
        "git diff -- src/calc.py",
        "Get-Content src\\calc.py | Select-Object -First 20",
        "grep -n def src/calc.py",
    ):
        engine._note_tool_outcome("run_shell", {"command": looking}, "ok")
        assert not engine._turn_ran_after, looking
    engine._note_tool_outcome("run_shell", {"command": "ls -la"}, "ok")
    assert not engine._turn_ran_after
    engine._note_tool_outcome("run_shell", {"command": "python -m pytest tests -q"}, "ok")
    assert engine._turn_ran_after

    engine._note_tool_outcome("write_file", {"path": "tool.py"}, "ok")
    assert not engine._turn_ran_after
    engine._note_tool_outcome("run_shell", {"command": "python tool.py --demo"}, "ok")
    assert engine._turn_ran_after


# -- end to end: what a finish is called ----------------------------------------


def test_replace_in_file_now_triggers_the_gate(tmp_path):
    """The end-to-end shape of the bug: editing a `.py` through the tool the product
    recommends for a fragment edit used to finish with no note at all."""
    (tmp_path / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    turns = [
        _tool_turn(
            "replace_in_file",
            {"path": "app.py", "old": "return 41", "new": "return 42"},
            "w1",
        ),
        _text_turn("Bumped it."),
        _text_turn("Nothing to run here."),
    ]
    engine, provider = _engine(tmp_path, turns)
    _collect(engine, "bump the answer")

    assert "return 42" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert _nudged(engine)
    assert provider.calls == 3  # the write, the answer, and the answer after the nudge
    assert [e["reason"] for e in engine.log.read() if e["type"] == "verify/nudge"] == ["no_run"]


def test_an_ignored_nudge_labels_the_finish_unverified(tmp_path):
    turns = [
        _tool_turn("write_file", {"path": "app.py", "content": "x = 1"}, "w1"),
        _text_turn("Wrote the module."),
        _text_turn("Still done."),
    ]
    engine, _ = _engine(tmp_path, turns)
    events = _collect(engine, "write the module")

    end = events[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "completed"  # downstream matching is unchanged
    assert end.data["unverified"] is True  # ...and the new fact is available
    assert _reason(engine) == [TurnEndReason.COMPLETED_UNVERIFIED]


def test_a_prose_write_is_not_gated_and_finishes_completed(tmp_path):
    """The fix must not turn every file edit into a gate: a notes.md write is prose."""
    turns = [
        _tool_turn("write_file", {"path": "notes.md", "content": "hi"}, "w1"),
        _text_turn("Saved the note."),
    ]
    engine, provider = _engine(tmp_path, turns)
    events = _collect(engine, "save this")

    assert not _nudged(engine)
    assert provider.calls == 2
    assert "unverified" not in events[-1].data
    assert _reason(engine) == [TurnEndReason.COMPLETED]


def test_a_turn_that_changed_nothing_is_still_a_plain_completed(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("an answer")])
    events = _collect(engine, "hi")

    assert "unverified" not in events[-1].data
    assert _reason(engine) == [TurnEndReason.COMPLETED]


def test_a_cut_answer_outranks_the_unverified_label(tmp_path):
    """Both facts can be true at once; the cut is a fact about the text the person
    actually received, and it is the more urgent of the two."""
    engine, _ = _engine(tmp_path, [])
    engine._turn_wrote_code = True
    engine._turn_ran_after = False
    engine._turn_truncated = True

    assert engine._completion_reason() is TurnEndReason.COMPLETED_TRUNCATED


def test_the_reason_is_a_bare_string_for_every_existing_consumer(tmp_path):
    """The enum compares equal to the string the surfaces already match on, and the new
    value degrades to an unknown reason rather than a crash for anything that never
    learned it."""
    assert TurnEndReason.COMPLETED_UNVERIFIED == "completed_unverified"
    assert isinstance(TurnEndReason.COMPLETED_UNVERIFIED, str)


# -- the red-check gate ------------------------------------------------


def _seed_check(engine, content: str, command: str = "pytest -q") -> None:
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
                    {"id": "c1", "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": content},
        ]
    )
    engine.log.append("assistant/message", turn=1, step=1, text="", tool_calls=[{"call_id": "c1", "name": "run_shell"}])
    engine.log.append("tool/call", turn=1, step=1, call_id="c1", name="run_shell", arguments={"command": command})
    engine.log.append("tool/result", turn=1, step=1, call_id="c1", content=content, is_error="exit 1" in content)


def _check_notes(engine):
    return [m for m in engine.messages if m.get("_attached_by") == "check_failed_nudge"]


def test_a_failed_last_check_earns_one_nudge(tmp_path):
    """Running the check and ignoring its result is its own failure, distinct from never
    running one: the finish gets one note naming the failing command and summary."""
    engine, provider = _engine(tmp_path, [_text_turn("All done."), _text_turn("Still done.")])
    _seed_check(engine, "1 failed, 13 passed\n[exit 1; cwd D:\\ws]")

    _collect(engine, "fix the tests")

    assert provider.calls == 2  # the finish, the nudge, and the second finish
    notes = _check_notes(engine)
    assert len(notes) == 1
    assert "pytest -q" in notes[0]["content"] and "FAILED" in notes[0]["content"]
    rows = [e["reason"] for e in engine.log.read() if e["type"] == "verify/nudge"]
    assert rows == ["check_failed"]


def test_an_edit_after_the_failed_check_disarms_the_nudge(tmp_path):
    """`edits_since == 0` is the point: a file changed after the red check may already be
    the fix, and the ledger refuses to guess."""
    engine, provider = _engine(tmp_path, [_text_turn("Done.")])
    _seed_check(engine, "1 failed\n[exit 1; cwd D:\\ws]")
    engine.messages.extend(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "function": {"name": "apply_patch", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "applied"},
        ]
    )

    _collect(engine, "fix the tests")

    assert provider.calls == 1
    assert _check_notes(engine) == []


def test_a_passed_or_unreadable_check_earns_no_nudge(tmp_path):
    passed, provider = _engine(tmp_path, [_text_turn("Done.")])
    _seed_check(passed, "1 passed in 0.2s\n[exit 0; cwd D:\\ws]")
    _collect(passed, "run the tests")
    assert provider.calls == 1 and _check_notes(passed) == []

    unknown, provider2 = _engine(tmp_path, [_text_turn("Done.")])
    _seed_check(unknown, "some output\n[exit unknown; cwd D:\\ws]")
    _collect(unknown, "run the tests")
    assert provider2.calls == 1 and _check_notes(unknown) == []


def test_the_red_check_nudge_speaks_once_per_turn(tmp_path):
    """One note per turn, and a later turn earns its own: the check is still red, so the
    next finish is nudged again rather than the gate going quiet forever."""
    engine, provider = _engine(
        tmp_path,
        [_text_turn("All done."), _text_turn("Done again."), _text_turn("Third."), _text_turn("Fourth.")],
    )
    _seed_check(engine, "1 failed\n[exit 1; cwd D:\\ws]")

    _collect(engine, "fix the tests")
    assert len(_check_notes(engine)) == 1
    assert provider.calls == 2  # the finish, the nudge, and the finish that ends the turn

    _collect(engine, "try again")
    assert len(_check_notes(engine)) == 2
    assert provider.calls == 4
    assert [e["reason"] for e in engine.log.read() if e["type"] == "verify/nudge"] == [
        "check_failed",
        "check_failed",
    ]
