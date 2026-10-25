"""Aged write payloads fold in the outbound view.

The calls that wrote files carry the whole file, or both sides of an edit, in their
arguments, and every later request sent them again. Past the prune point a large payload
becomes one line; the file on disk is the recovery path, and the read guard is told the
write no longer shows the file.
"""

from __future__ import annotations

import json

from harness.safety.boundary import write_paths
from harness.context.compaction import ARGUMENT_FOLD_CHARS, CompactionState, apply_to_outbound, count_folded_arguments, fold_arguments, fold_write_arguments
from harness.safety.permissions import PermissionEngine
from harness.safety.provenance import resolve
from harness.loop import TurnEngine
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.observed import RULE_OUT_OF_VIEW, ObservedFiles

BIG = "def f():\n    return 1\n" * 200  # ~4,400 characters


def _call(cid, name, args, *, as_text=True):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args) if as_text else args},
    }


def _write_turn(cid, name, args, result="ok", *, as_text=True):
    return [
        {"role": "assistant", "content": "", "tool_calls": [_call(cid, name, args, as_text=as_text)]},
        {"role": "tool", "tool_call_id": cid, "content": result},
    ]


# -- what folds ------------------------------------------------------------------------------


def test_a_large_payload_folds_and_says_where_it_went():
    folded = fold_arguments("write_file", {"path": "app/main.py", "content": BIG, "overwrite": True})
    assert folded is not None
    assert folded["path"] == "app/main.py" and folded["overwrite"] is True
    assert len(folded["content"]) < 300
    assert "of app/main.py" in folded["content"] and "read_file it" in folded["content"]
    assert f"{BIG.count(chr(10)) + 1:,} lines" in folded["content"]


def test_a_small_payload_and_other_tools_are_left_alone():
    small = "x" * ARGUMENT_FOLD_CHARS
    assert fold_arguments("write_file", {"path": "a.py", "content": small}) is None
    assert fold_arguments("run_shell", {"command": BIG}) is None
    assert fold_arguments("read_file", {"path": BIG}) is None


def test_an_edit_folds_each_side_on_its_own():
    folded = fold_arguments("replace_in_file", {"path": "a.py", "old": BIG, "new": "y = 2"})
    assert folded["new"] == "y = 2" and folded["old"] != BIG


def test_a_patch_keeps_the_lines_that_name_its_files():
    patch = (
        "*** Begin Patch\n*** Update File: src/a.py\n@@\n" + "-old\n+new\n" * 300
        + "*** Add File: src/b.py\n" + "+line\n" * 10 + "*** End Patch"
    )
    folded = fold_arguments("apply_patch", {"patch": patch})["patch"]
    assert "*** Update File: src/a.py" in folded and "*** Add File: src/b.py" in folded
    assert "folded to free context" in folded and len(folded) < 400
    # What the call touched is still readable from it.
    assert write_paths("apply_patch", {"patch": folded}) == (["src/a.py", "src/b.py"], True)


# -- the outbound view ---------------------------------------------------------------------


def _thread():
    return (
        [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
        + _write_turn("a", "write_file", {"path": "old.py", "content": BIG})
        + _write_turn("b", "replace_in_file", {"path": "old.py", "old": BIG, "new": BIG}, as_text=False)
        + _write_turn("c", "write_file", {"path": "new.py", "content": BIG})
    )


def test_only_calls_before_the_point_fold_and_the_thread_is_untouched():
    messages = _thread()
    snapshot = json.dumps(messages)
    out = fold_write_arguments(messages, upto=6)

    assert json.dumps(messages) == snapshot  # canonical thread untouched
    assert len(out) == len(messages)  # every index into the list still holds
    first = json.loads(out[2]["tool_calls"][0]["function"]["arguments"])
    assert first["content"] != BIG  # a JSON string stays a valid JSON string
    second = out[4]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(second, dict) and second["old"] != BIG  # a dict stays a dict
    assert out[6] is messages[6]  # past the point: the same object, untouched
    assert count_folded_arguments(messages, 0, 6) == 2
    assert count_folded_arguments(messages, 3, 6) == 1


def test_the_state_decides_and_survives_a_resume():
    messages = _thread()
    plain = CompactionState(boundary_index=0, summary_text="", working_state="", pruned_upto=6)
    assert apply_to_outbound(messages, plain)[2] is messages[2]

    folding = CompactionState.from_dict({**plain.as_dict(), "prune_arguments": True})
    assert folding.prune_arguments is True
    assert apply_to_outbound(messages, folding)[2] is not messages[2]
    assert CompactionState.from_dict(folding.as_dict()).prune_arguments is True  # what a resume reads
    assert CompactionState.from_dict(plain.as_dict()).prune_arguments is False
    older = {key: value for key, value in folding.as_dict().items() if key != "prune_arguments"}
    assert CompactionState.from_dict(older).prune_arguments is False  # a state saved before the field


# -- the engine and the read guard ---------------------------------------------------------


class _Silent(ProviderClient):
    def complete(self, model, messages, tools):
        raise AssertionError("no request expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, messages, *, fold_arguments_too):
    for name in ("old.py", "new.py", "read.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    engine = TurnEngine(
        provider=_Silent(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {"context_window": 200_000, "buffer_tokens": 0}
    engine.prune_write_arguments = fold_arguments_too
    engine.observed = ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))
    for name in ("old.py", "new.py", "read.py"):
        engine.observed.observe(name)
    return engine


def _long_thread():
    """Two aged writes and an aged long read, then enough recent work to age them."""
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    messages += _write_turn("w1", "write_file", {"path": "old.py", "content": BIG})
    call = _call("r1", "read_file", {"path": "read.py"})
    messages += [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "r1", "content": "line\n" * 4_000},
    ]
    messages += _write_turn("w2", "write_file", {"path": "new.py", "content": "small = True\n"})
    for i in range(40):
        messages += _write_turn(f"x{i}", "read_file", {"path": "elsewhere.txt"}, result="z" * 4_500)
    return messages


def test_a_file_seen_only_through_a_folded_write_or_a_shortened_read_leaves_the_view(tmp_path):
    engine = _engine(tmp_path, _long_thread(), fold_arguments_too=True)
    assert engine._opportunistic_prune() is False  # nothing proves the cache cold yet
    engine._prefix_dirty = True
    assert engine._opportunistic_prune() is True

    state = engine.compaction_state
    assert state.prune_arguments is True and state.pruned_upto > 8
    assert engine.observed.blame("old.py")[0] == RULE_OUT_OF_VIEW  # its write was folded
    assert engine.observed.blame("read.py")[0] == RULE_OUT_OF_VIEW  # its read was shortened
    assert engine.observed.blame("new.py") == ("", "")  # a small write still shows the file
    row = next(r for r in engine.log.read() if r.get("type") == "compaction/prune")
    assert row["arguments_folded"] == 1
    sent = engine._outbound_messages() if hasattr(engine, "_outbound_messages") else None
    if sent is not None:
        assert BIG not in json.dumps(sent, default=str)


def test_without_the_switch_a_write_still_shows_its_file(tmp_path):
    engine = _engine(tmp_path, _long_thread(), fold_arguments_too=False)
    engine._prefix_dirty = True
    assert engine._opportunistic_prune() is True
    assert engine.compaction_state.prune_arguments is False
    assert engine.observed.blame("old.py") == ("", "")
    assert engine.observed.blame("read.py")[0] == RULE_OUT_OF_VIEW
    row = next(r for r in engine.log.read() if r.get("type") == "compaction/prune")
    assert "arguments_folded" not in row


def test_a_state_that_folds_keeps_folding(tmp_path):
    engine = _engine(tmp_path, _long_thread(), fold_arguments_too=True)
    engine._prefix_dirty = True
    engine._opportunistic_prune()
    engine.prune_write_arguments = False
    for i in range(40):  # enough newer work to age the next stretch, with results to shorten
        engine.messages += _write_turn(f"late{i}", "read_file", {"path": "elsewhere.txt"}, result="z" * 6_000)
    candidate = engine._prune_candidate(engine._history_trigger_tokens())
    assert candidate is not None and candidate[0].prune_arguments is True


def test_it_is_on_unless_the_setting_or_the_engine_says_otherwise(tmp_path):
    engine = _engine(tmp_path, _long_thread(), fold_arguments_too=None)
    assert engine._prunes_arguments() is True  # the default
    engine.compaction_settings = lambda: {"context_window": 200_000, "buffer_tokens": 0, "prune_write_arguments": False}
    assert engine._prunes_arguments() is False
    engine.prune_write_arguments = True  # an engine override wins over the setting
    assert engine._prunes_arguments() is True
