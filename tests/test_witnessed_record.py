"""A cut of the context carries what the harness itself saw (loop/witnessed.py).

Most compaction failures are omissions (Slipstream, arXiv 2605.08580), and an agent fails to follow a
summary that says the work is done while an error is unresolved (AutoCompact, arXiv 2610.02163). The
harness knows the last check, its verdict and failing names, and what was edited since; a summary or
fold note sits beside that record, which the model did not write.
"""

from __future__ import annotations

import json

from harness.context.compaction import CompactionState, compacted_block
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.loop import TurnEngine
from harness.loop.witnessed import HEADING, witnessed_record
from harness.safety.permissions import PermissionEngine
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

FAILING = (
    "FAILED tests/test_cart.py::test_total - assert 10 == 11\n"
    "FAILED tests/test_cart.py::test_discount - KeyError\n"
    "==== 2 failed, 40 passed in 1.2s ====\n[exit 1; cwd D:\\ws]"
)


def _call(cid, name, args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}


def _result(cid, text):
    return {"role": "tool", "tool_call_id": cid, "content": text}


def _session(*, check_output=FAILING, edit_after=True):
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "fix the cart"},
        _call("e1", "replace_in_file", {"path": "shop/cart.py", "old": "a", "new": "b"}),
        _result("e1", "ok"),
        _call("c1", "run_shell", {"command": "pytest -q"}),
        _result("c1", check_output),
    ]
    if edit_after:
        messages += [_call("e2", "write_file", {"path": "shop/rounding.py", "content": "x"}), _result("e2", "ok")]
    return messages


def test_a_failed_check_is_named_with_its_failures_and_what_was_edited_since():
    record = witnessed_record(_session())
    assert record.startswith(HEADING)
    assert "`pytest -q` FAILED" in record
    assert "test_total" in record and "test_discount" in record
    assert "Edited since that check: `shop/rounding.py`; it has not run on them." in record
    assert "shop/cart.py" not in record  # edited before the check: the check saw it


def test_a_green_check_and_no_check_are_said_too():
    green = witnessed_record(_session(check_output="42 passed in 1.0s\n[exit 0; cwd D:\\ws]", edit_after=False))
    assert "`pytest -q` passed" in green and "Nothing has been edited since that check." in green
    unchecked = [m for m in _session() if "c1" not in json.dumps(m)]
    assert "No check has run in this session. Edited: `shop/rounding.py`, `shop/cart.py`." in witnessed_record(unchecked)
    assert witnessed_record([{"role": "user", "content": "hi"}]) == ""


class _Silent(ProviderClient):
    def complete(self, model, messages, tools):
        raise AssertionError("no request expected")

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_summary_or_note_is_adopted_with_the_record_and_a_prune_without(tmp_path):
    engine = TurnEngine(
        provider=_Silent(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5", messages=_session() + [{"role": "user", "content": "go on"}],
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    state = CompactionState(boundary_index=6, summary_text="All tests pass; the work is done.", working_state="")
    engine._adopt_compaction(state, "folded", "folded", manual=True)
    block = compacted_block(engine.compaction_state)
    assert block.index("All tests pass") < block.index(HEADING) < block.index("Continue where you left off")
    assert "FAILED" in block  # the record contradicts the note, and says which wins
    assert CompactionState.from_dict(engine.compaction_state.as_dict()).harness_record == state.harness_record

    pruned = CompactionState(boundary_index=0, summary_text="", working_state="", pruned_upto=4)
    engine._adopt_compaction(pruned, "pruned", "pruned")
    assert engine.compaction_state.harness_record == ""


# -- a stage compaction (C) -------------------------------------------------------------------


def _stage_engine(tmp_path, check_output):
    padding = [{"role": "user", "content": "x" * 160_000}]  # ~40k tokens of earlier work
    engine = TurnEngine(
        provider=_Silent(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5", messages=[{"role": "system", "content": "s"}, *padding] + _session(check_output=check_output, edit_after=False),
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {"context_window": 64_000, "buffer_tokens": 0}
    engine._turn_wrote_code = True
    return engine


def test_a_stage_that_just_ended_verified_is_where_the_session_compacts(tmp_path):
    engine = _stage_engine(tmp_path, "42 passed in 1.0s\n[exit 0; cwd D:\\ws]")
    signal, trigger = engine._context_signal(), engine._history_trigger_tokens()
    assert signal < trigger <= 2 * signal  # past half the trigger, under the trigger
    assert engine._stage_due(signal, trigger) is True
    assert engine._compaction_due() is True
    engine._turn_wrote_code = False
    assert engine._stage_due(signal, trigger) is False  # no code written this turn: no stage
    engine._turn_wrote_code = True
    engine.compaction_settings = lambda: {"context_window": 64_000, "buffer_tokens": 0, "stage_compaction": False}
    assert engine._stage_due(signal, trigger) is False


def test_a_red_check_is_not_the_end_of_a_stage(tmp_path):
    engine = _stage_engine(tmp_path, FAILING)
    assert engine._stage_due(engine._context_signal(), engine._history_trigger_tokens()) is False


def test_a_finished_stage_too_small_to_pay_for_a_summary_is_not_cut(tmp_path):
    engine = _stage_engine(tmp_path, "42 passed in 1.0s\n[exit 0; cwd D:\\ws]")
    # A cut already took the earlier work into a long summary: the context is still past half the
    # trigger, but what is new since the cut is the stage's few steps.
    engine.compaction_state = CompactionState(boundary_index=3, summary_text="earlier " * 16_000, working_state="")
    signal, trigger = engine._context_signal(), engine._history_trigger_tokens()
    assert signal >= 0.5 * trigger
    assert engine._stage_due(signal, trigger) is False


class _Summarizer(_Silent):
    def complete(self, *, model, messages, tools=None, **settings):
        from harness.context.compaction.summarizer import SUMMARY_SECTIONS
        from harness.llm.providers.turns import AssistantTurn

        return AssistantTurn(text="\n".join(f"## {name}\nx" for name in SUMMARY_SECTIONS), finish_reason="stop")


def test_a_stage_cut_summarizes_the_stage_and_keeps_only_the_step_that_checked_it(tmp_path):
    import asyncio

    engine = _stage_engine(tmp_path, "42 passed in 1.0s\n[exit 0; cwd D:\\ws]")
    engine.provider = _Summarizer()
    asyncio.run(engine._compact_now())
    check_step = max(i for i, m in enumerate(engine.messages) if m.get("role") == "assistant")
    assert engine.compaction_state.boundary_index == check_step
    assert "`pytest -q` passed" in engine.compaction_state.harness_record
