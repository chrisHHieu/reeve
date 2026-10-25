"""The faithful tier of a cut: aged output of a call that can be made again leaves the view.

Long sessions were half re-runnable tool output older than their newest results (49% of 11
histories over 60k tokens, 2026-10-10), which the old prune never reached: it folded results over
5,000 characters to their ends, and the median result is 400. This tier replaces the whole output of
a re-runnable call with a line naming the call, saves what cannot simply be asked for again, and
keeps the rest; the read guard is told, and while the cache is warm it runs only in batches large
enough to repay the cache they cost.
"""

from __future__ import annotations

import json

from harness.context.compaction import (
    REDERIVE_MIN_CHARS,
    CompactionState,
    apply_to_outbound,
    prune_tool_results,
    result_shortened,
)
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.safety.provenance import resolve
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.observed import RULE_OUT_OF_VIEW, ObservedFiles

READ = "line of a file\n" * 200  # ~3,000 characters
OUTPUT = "PASSED test_x\n" * 200


def _turn(cid, name, args, result):
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ]},
        {"role": "tool", "tool_call_id": cid, "content": result},
    ]


def _thread():
    return (
        [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
        + _turn("r", "read_file", {"path": "src/app.py"}, READ)
        + _turn("s", "run_shell", {"command": "pytest -q"}, OUTPUT)
        + _turn("m", "mcp__docs__search", {"q": "x"}, READ)
        + _turn("t", "read_file", {"path": "tiny.py"}, "x = 1\n")
    )


def test_a_rerunnable_output_becomes_its_call_and_a_command_its_saved_file():
    messages = _thread()
    snapshot = json.dumps(messages)
    saved = []
    out = prune_tool_results(messages, upto=len(messages), rederive=True, spill=lambda text: saved.append(text) or "out/run-1.log")
    assert json.dumps(messages) == snapshot and len(out) == len(messages)
    assert "read_file(path=src/app.py)" in out[3]["content"] and "call it again" in out[3]["content"]
    assert len(out[3]["content"]) < 250
    assert "out/run-1.log" in out[5]["content"] and saved == [OUTPUT]  # saved, then named
    assert out[7]["content"] == READ  # another server's tool: not this tier's to drop
    assert out[9]["content"] == "x = 1\n"  # smaller than the line that would replace it


def test_without_the_tier_or_a_sink_the_old_prune_holds():
    messages = _thread()
    assert prune_tool_results(messages, upto=len(messages))[3]["content"] == READ
    assert prune_tool_results(messages, upto=len(messages), rederive=True)[5]["content"] == OUTPUT  # nowhere to save it


def test_the_state_decides_and_the_read_guard_is_told():
    messages = _thread()
    plain = CompactionState(boundary_index=0, summary_text="", working_state="", pruned_upto=len(messages))
    assert apply_to_outbound(messages, plain)[3]["content"] == READ
    dropping = CompactionState.from_dict({**plain.as_dict(), "prune_rederivable": True})
    assert "call it again" in apply_to_outbound(messages, dropping)[3]["content"]
    assert CompactionState.from_dict(dropping.as_dict()).prune_rederivable is True
    assert result_shortened(READ, "read_file", rederive=True) is True
    assert result_shortened(READ, "read_file", rederive=False) is False
    assert result_shortened("x" * REDERIVE_MIN_CHARS, "read_file", rederive=True) is False


class _Silent(ProviderClient):
    def complete(self, model, messages, tools):
        raise AssertionError("no request expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, messages):
    (tmp_path / "read.py").write_text("x = 1\n", encoding="utf-8")
    engine = TurnEngine(
        provider=_Silent(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {"context_window": 400_000, "buffer_tokens": 0}
    engine.observed = ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))
    engine.observed.observe("read.py")
    return engine


def _long(reads, size):
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    messages += _turn("first", "read_file", {"path": "read.py"}, "y = 2\n" * 400)
    for i in range(reads):
        messages += _turn(f"r{i}", "read_file", {"path": f"src/m{i}.py"}, "z" * size)
    return messages


def test_a_large_batch_leaves_while_the_cache_is_warm_and_the_guard_follows(tmp_path):
    engine = _engine(tmp_path, _long(120, 4_000))  # ~120k tokens of aged reads
    assert engine._working_trim() is True
    assert engine.compaction_state.prune_rederivable is True
    assert engine.observed.blame("read.py")[0] == RULE_OUT_OF_VIEW  # its read left the view
    row = next(r for r in engine.log.read() if r.get("type") == "compaction/prune")
    assert row["reason"] == "rederivable" and row["saved_tokens"] >= 20_000


def test_a_batch_too_small_to_repay_the_cache_waits(tmp_path):
    engine = _engine(tmp_path, _long(30, 1_000))  # a short context: under twice the bar
    assert engine._working_trim() is False
    assert engine.compaction_state is None


def test_the_setting_turns_the_tier_off(tmp_path):
    engine = _engine(tmp_path, _long(120, 4_000))
    engine.prune_rederivable = False
    assert engine._working_trim() is False
