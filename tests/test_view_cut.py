"""What a cut of the context may not cost.

A compaction and a fold both replace older turns with a block. Measured on the store
(2026-08-01 to 2026-10-05, 35 cuts in 14 sessions): 16 edits of a file whose last read was
behind a cut, with no read since. Fifteen quoted the text they replaced, which cannot go
wrong silently. One was a whole-file `write_file`, written from memory, and it went through:
the read-before-edit store still said the file had been read.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio
import json

import pytest

from harness.context.compaction import CompactionState
from harness.context.compaction.fold import build_fold_state
from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.provenance import resolve
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.context_fold import FOLD_CONTEXT
from harness.tools.observed import RULE_NEVER_READ, RULE_OUT_OF_VIEW, ObservedFiles
from harness.tools.observed_seed import seed_from_messages
from harness.tools.observed_gate import observation_gate
from harness.tools.pipeline import BLOCKED, OK, ToolExecution, ToolPipeline, ToolResult

NOTE = (
    "Renamed fetch_record to load_record in old.py (line 14) and its two callers; pytest "
    "passes (7 passed). Left: the changelog entry, in recent.py."
)


# -- the store -----------------------------------------------------------------------------


@pytest.fixture
def observed(tmp_path):
    for name in ("old.py", "recent.py", "unread.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    return ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))


def test_a_file_read_before_the_cut_is_out_of_view_and_one_in_the_kept_tail_is_not(observed):
    observed.observe("old.py")
    observed.observe("recent.py")
    assert observed.leave_view(["recent.py"]) == 1

    rule, reason = observed.blame("old.py")
    assert rule == RULE_OUT_OF_VIEW
    assert "no longer in your context" in reason and "Read the whole file first" in reason
    assert observed.blame("recent.py") == ("", "")
    # Never read is still its own rule, and creating is still free.
    assert observed.blame("unread.py")[0] == RULE_NEVER_READ
    assert observed.blame("new.py") == ("", "")


def test_reading_or_writing_it_again_puts_it_back_in_view(observed):
    observed.observe("old.py")
    observed.leave_view([])
    assert observed.blame("old.py")[0] == RULE_OUT_OF_VIEW
    observed.observe("old.py", covered=(1, 1), total=1)
    assert observed.blame("old.py") == ("", "")


def test_no_cut_standing_puts_everything_back(observed):
    observed.observe("old.py")
    observed.leave_view([])
    assert observed.leave_view(None) == 0
    assert observed.blame("old.py") == ("", "")


def _locate(name, arguments):
    path = arguments.get("path")
    return ([str(path)], True) if name in ("write_file", "replace_in_file") and path else ([], False)


def _dispatch(pipeline, name, arguments):
    async def run():
        return ToolResult(status=OK, value="written")

    return asyncio.run(pipeline.dispatch(ToolExecution(call_id="c", name=name, arguments=arguments), run))


def test_the_gate_refuses_a_whole_file_write_and_lets_a_quoting_edit_through(observed):
    """A whole file written from memory replaces what is not remembered. An edit that quotes
    the text it replaces changes nothing unless the quote matches, so it needs no read."""
    observed.observe("old.py")
    observed.leave_view([])
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    blocked = _dispatch(pipeline, "write_file", {"path": "old.py", "content": "x = 2\n"})
    assert blocked.status == BLOCKED and RULE_OUT_OF_VIEW in str(blocked.value)
    assert "no longer in your context" in str(blocked.value)
    quoting = _dispatch(pipeline, "replace_in_file", {"path": "old.py", "old": "x = 1", "new": "x = 2"})
    assert quoting.status == OK


# -- the engine: every adoption of a state tells the store ----------------------------------


class Scripted(ProviderClient):
    def __init__(self, turns):
        self.turns = list(turns)

    def complete(self, model, messages, tools):
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def history(tmp_path, steps=12):
    """`steps` reads of old.py, then two of recent.py: what a short kept tail still holds."""
    for name in ("old.py", "recent.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    messages = [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "rename it", "ts": 1.0}]
    for i in range(steps + 2):
        path = "old.py" if i < steps else "recent.py"
        call = {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}
        messages.append({"role": "assistant", "content": f"reading {i}", "tool_calls": [call], "ts": 1.0})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "line of code\n" * 150, "ts": 1.0})
    return messages


def make_engine(tmp_path, turns, messages):
    engine = TurnEngine(
        provider=Scripted(turns),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {
        "context_window": 200_000, "buffer_tokens": 0, "keep_tokens": 1_500, "fold_keep_tokens": 1_500,
    }
    engine.enable_model_fold()
    engine.observed = ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))
    return engine


def collect(engine, text="continue"):
    async def _run():
        return [e async for e in engine.run(text)]

    return asyncio.run(_run())


def _fold(**extra):
    return AssistantTurn(
        text="folding", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="f1", name=FOLD_CONTEXT, arguments={"note": NOTE, **extra})],
    )


DONE = AssistantTurn(text="done", finish_reason="stop")


def test_a_fold_takes_the_files_it_folded_out_of_view(tmp_path):
    """The earlier turn's reads are folded; the read this turn made before folding is kept."""

    def read_file(path: str) -> str:
        """Read a file."""
        return "x = 1"

    read = AssistantTurn(
        text="", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="r1", name="read_file", arguments={"path": "recent.py"})],
    )
    engine = make_engine(tmp_path, [read, _fold(), DONE], history(tmp_path))
    engine.registry.register(read_file, metadata=ToolMetadata())
    engine.observed.observe("old.py")
    engine.observed.observe("recent.py")
    assert engine.observed.blame("old.py") == ("", "")

    collect(engine)

    assert engine.compaction_state is not None and engine.compaction_state.reason == "folded"
    assert engine.observed.blame("old.py")[0] == RULE_OUT_OF_VIEW
    assert engine.observed.blame("recent.py") == ("", "")


def test_a_resumed_session_does_not_believe_in_reads_behind_its_block(tmp_path):
    """Seeding takes every read in the transcript as seen, "because the model still carries
    them". Behind a compaction block it does not."""
    messages = history(tmp_path)
    state = build_fold_state(messages, note=NOTE, keep_tokens=1_500)
    engine = make_engine(tmp_path, [], messages)
    seed_from_messages(engine.observed, messages)
    assert engine.observed.blame("old.py") == ("", "")

    engine.compaction_state = CompactionState.from_dict(state.as_dict())  # what a resume does

    assert engine.observed.blame("old.py")[0] == RULE_OUT_OF_VIEW
    assert engine.observed.blame("recent.py") == ("", "")
    # A state with no block in the view (reset) puts everything back.
    engine.compaction_state = None
    assert engine.observed.blame("old.py") == ("", "")


def test_a_session_with_no_store_adopts_a_state_as_before(tmp_path):
    engine = make_engine(tmp_path, [_fold(), DONE], history(tmp_path))
    engine.observed = None
    collect(engine)
    assert engine.compaction_state is not None


# -- the harness does not ask for a fold mid-edit --------------------------------------------


def _step(i, name="noop", arguments=None):
    return AssistantTurn(
        text="", finish_reason="tool_calls",
        tool_calls=[ToolCall(id=f"r{i}", name=name, arguments=arguments or {})],
    )


def _todos(*statuses):
    return {"todos": [{"content": f"item {i}", "status": s} for i, s in enumerate(statuses)]}


def _with_tools(engine):
    """`edit` and `check` stand for a code write and a run that exercises it: they set the
    two facts `_note_tool_outcome` sets (pinned by tests/test_verification_gate.py)."""
    from harness.tools.todo import TodoList, todo_tools

    def noop() -> str:
        """Do nothing."""
        return "ok"

    def edit() -> str:
        """Change code."""
        engine._turn_wrote_code, engine._turn_ran_after = True, False
        return "ok"

    def check() -> str:
        """Run the change."""
        engine._turn_ran_after = True
        return "ok"

    for tool in (noop, edit, check):
        engine.registry.register(tool, metadata=ToolMetadata())
    engine.todo = TodoList()
    engine.registry.register_all(todo_tools(engine.todo))
    engine.compaction_settings = lambda: {
        "context_window": 12_000, "buffer_tokens": 0, "keep_tokens": 1_500, "fold_keep_tokens": 1_500,
    }
    engine._stamp_fold_hint = lambda message: None  # the turn-start stamp is its own test


def _hints(engine):
    return [
        m for m in engine.messages
        if m.get("role") == "user" and "System note" in str(m.get("content")) and "fold_context" in str(m.get("content"))
    ]


def test_a_finished_item_is_asked_about_once_the_edit_has_run(tmp_path):
    turns = [
        _step(1, "todo_write", _todos("in_progress", "pending")),
        _step(2, "edit"),
        _step(3, "todo_write", _todos("done", "pending")),  # finished, by the list; unverified, by the gate
        _step(4, "noop"),
        _step(5, "check"),
        DONE,
    ]
    engine = make_engine(tmp_path, turns, history(tmp_path, steps=14))
    _with_tools(engine)
    collect(engine)

    (hint,) = _hints(engine)
    assert "you just finished an item" in hint["content"]
    # Not after the step that marked it done, nor the one after: after the one that ran it.
    assert engine.messages[engine.messages.index(hint) - 1].get("tool_call_id") == "r5"


def test_the_same_turn_with_no_edit_is_asked_at_once(tmp_path):
    turns = [
        _step(1, "todo_write", _todos("in_progress", "pending")),
        _step(3, "todo_write", _todos("done", "pending")),
        _step(4, "noop"),
        DONE,
    ]
    engine = make_engine(tmp_path, turns, history(tmp_path, steps=14))
    _with_tools(engine)
    collect(engine)
    (hint,) = _hints(engine)
    assert engine.messages[engine.messages.index(hint) - 1].get("tool_call_id") == "r3"


def test_a_fold_made_mid_edit_is_logged_with_the_state_it_was_made_in(tmp_path):
    engine = make_engine(tmp_path, [_step(1, "edit"), _fold(), DONE], history(tmp_path))
    _with_tools(engine)
    engine.compaction_settings = lambda: {
        "context_window": 200_000, "buffer_tokens": 0, "keep_tokens": 1_500, "fold_keep_tokens": 1_500,
    }
    collect(engine)
    (start,) = [e for e in engine.log.read() if e.get("type") == "compaction/start"]
    assert start["author"] == "model" and start["unsettled"] == "unverified"

    settled = make_engine(tmp_path / "b", [_fold(), DONE], history(tmp_path))
    collect(settled)
    (start,) = [e for e in settled.log.read() if e.get("type") == "compaction/start"]
    assert start.get("unsettled") is None


def _check(index, code, ts=1.0):
    call = {"id": f"k{index}", "type": "function", "function": {"name": "run_shell", "arguments": '{"command": "pytest -q"}'}}
    return [
        {"role": "assistant", "content": "", "tool_calls": [call], "ts": ts},
        {"role": "tool", "tool_call_id": f"k{index}", "content": json.dumps({"exit_code": code, "output": "1 failed" if code else "7 passed"}), "ts": ts},
    ]


def test_a_failed_check_holds_the_hint_only_in_the_turn_it_failed_in(tmp_path):
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": "fix it", "ts": 1.0}]
    engine = make_engine(tmp_path, [], base + _check(1, 1))
    assert engine._work_unsettled() == "check_failed"

    passed = make_engine(tmp_path / "p", [], base + _check(1, 1) + _check(2, 0))
    assert passed._work_unsettled() == ""

    # The person moved on: a check that failed in an earlier turn is not this turn's edit.
    later = make_engine(tmp_path / "l", [], base + _check(1, 1) + [{"role": "user", "content": "now the docs", "ts": 2.0}])
    assert later._work_unsettled() == ""


def test_a_read_that_failed_in_the_kept_tail_shows_nothing(tmp_path):
    messages = history(tmp_path)
    state = build_fold_state(messages, note=NOTE, keep_tokens=1_500)
    for message in messages[state.boundary_index:]:
        if message.get("role") == "tool":
            message["content"] = "Error: file not found: recent.py"
    engine = make_engine(tmp_path, [], messages)
    seed_from_messages(engine.observed, messages)
    engine.compaction_state = state
    assert engine.observed.blame("recent.py")[0] == RULE_OUT_OF_VIEW


# -- a write sent with the fold that hid its file --------------------------------------


def test_a_messages_calls_are_judged_against_the_view_it_was_written_from(observed):
    observed.observe("old.py")
    observed.observe("recent.py")
    observed.leave_view(["recent.py"])                 # a cut between messages
    assert observed.blame("old.py")[0] == RULE_OUT_OF_VIEW

    observed.observe("old.py")
    observed.observe("unread.py")
    observed.calls_written()                           # a message arrives; its calls start
    observed.leave_view(["recent.py"])                 # one of them cuts the view
    assert observed.blame("old.py") == ("", "") and observed.blame("unread.py") == ("", "")
    assert observed.in_view_keys() == [observed.resolve("recent.py")]  # out of view all the same

    observed.settle_view()                             # the next message is written after the cut
    assert observed.blame("old.py")[0] == RULE_OUT_OF_VIEW


def test_a_file_already_out_of_view_when_the_message_was_written_stays_out(observed):
    observed.observe("old.py")
    observed.leave_view([])                            # gone before the message was written
    observed.observe("recent.py")
    observed.calls_written()
    observed.leave_view([])
    assert observed.blame("old.py")[0] == RULE_OUT_OF_VIEW
    assert observed.blame("recent.py") == ("", "")
    # Read again by one of the message's own calls: back in view, as between messages.
    observed.observe("old.py", covered=(1, 1), total=1)
    assert observed.blame("old.py") == ("", "")


def test_a_write_sent_in_one_message_with_the_fold_that_hid_its_file_goes_through(tmp_path):
    """Both `out-of-view` refusals in the store by 2026-10-06 had this shape: the model sent
    `fold_context` and a whole-file `write_file` together. The file's text was in its view
    when it wrote the call; the fold ran first and the write was refused as written from
    memory, so a whole file of output was thrown away and written again."""
    written = []

    def write_file(path: str, content: str) -> str:
        """Write a file."""
        written.append(path)
        return "written"

    together = AssistantTurn(
        text="folding, then the rewrite", finish_reason="tool_calls",
        tool_calls=[
            ToolCall(id="f1", name=FOLD_CONTEXT, arguments={"note": NOTE}),
            ToolCall(id="w1", name="write_file", arguments={"path": "old.py", "content": "x = 2\n"}),
        ],
    )
    later = AssistantTurn(
        text="", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="w2", name="write_file", arguments={"path": "other.py", "content": "y = 2\n"})],
    )
    (tmp_path / "other.py").write_text("y = 1\n", encoding="utf-8")
    engine = make_engine(tmp_path, [together, later, DONE], history(tmp_path))
    engine.permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS)
    engine.registry.register(write_file, metadata=ToolMetadata())
    engine.pipeline.on_check(observation_gate(engine.observed, _locate))
    engine.observed.observe("old.py")
    engine.observed.observe("other.py")

    collect(engine)

    results = {m["tool_call_id"]: str(m.get("content")) for m in engine.messages if m.get("role") == "tool"}
    assert engine.compaction_state is not None and engine.compaction_state.reason == "folded"
    assert "written" in results["w1"] and RULE_OUT_OF_VIEW not in results["w1"]
    # A step later the model writes from the folded view: the guard holds as before.
    assert RULE_OUT_OF_VIEW in results["w2"] and written == ["old.py"]

    # A cut the engine makes on its own, between steps, spares nothing: what follows it is
    # written from the cut view.
    (tmp_path / "third.py").write_text("z = 1\n", encoding="utf-8")
    engine.observed.observe("third.py")
    engine.compaction_state = engine.compaction_state
    assert engine.observed.blame("third.py")[0] == RULE_OUT_OF_VIEW
