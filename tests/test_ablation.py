"""The ablation switches do what they say — offline, no model."""

from __future__ import annotations

import pytest

from benchmarks.harness_eval import ABLATABLE, ENGINE_ABLATABLE


def test_every_engine_switch_name_is_read_by_the_engine():
    """A name in the list that no engine code reads would make an ablation variant
    identical to the baseline and report "this mechanism is worth nothing"."""
    from pathlib import Path

    source = "".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).resolve().parents[1] / "harness" / "loop").glob("*.py")
    )
    for name in ENGINE_ABLATABLE:
        assert f'"{name}" in self.ablated' in source or f'"{name}" not in self.ablated' in source, name
    assert set(ENGINE_ABLATABLE) <= set(ABLATABLE)


def _engine(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    return build_engine(agent=reeve_agent(), workspace=tmp_path, provider=object())


def _red_check(engine):
    engine.messages = [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "run_shell", "arguments": '{"command": "pytest -q"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "1 failed\n[exit 1; cwd D:\ws]"},
    ]


@pytest.mark.parametrize("ablated, nudged", [(frozenset(), True), (frozenset({"check_failed_nudge"}), False)])
def test_the_failed_check_nudge_switch(tmp_path, ablated, nudged):
    engine = _engine(tmp_path)
    _red_check(engine)
    engine.ablated = ablated
    assert engine._nudge_failed_check() is nudged


def test_the_check_loop_switch(tmp_path):
    engine = _engine(tmp_path)
    engine._check_streak = 99
    assert engine._check_loop_stopped() is True
    engine.ablated = frozenset({"check_loop"})
    assert engine._check_loop_stopped() is False and engine._nudge_check_loop() is False


def test_the_product_ablates_nothing(tmp_path):
    assert _engine(tmp_path).ablated == frozenset()


def test_fired_counts_each_mechanism_by_its_own_events(tmp_path):
    """The table must say whether a mechanism acted: a variant whose mechanism never
    acted in the baseline can only differ by noise (the first run: up to 26%)."""
    import json

    from benchmarks.ablation import fired

    log = tmp_path / "conversations" / "s.events.jsonl"
    log.parent.mkdir()
    rows = [
        {"type": "verify/nudge", "reason": "no_run"},
        {"type": "verify/nudge", "reason": "check_failed"},
        {"type": "todo/nudge"},
        {"type": "todo/nudge"},
        {"type": "context/budget"},
        {"type": "request/header", "tools": [{"function": {"name": "tool_search"}}]},
        {"type": "request/header", "tools": [{"function": {"name": "read_file"}}]},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert fired(tmp_path, "verify_nudge") == 1
    assert fired(tmp_path, "check_failed_nudge") == 1
    assert fired(tmp_path, "check_loop") == 0
    assert fired(tmp_path, "todo_nudge") == 2
    assert fired(tmp_path, "context_budget_line") == 1
    assert fired(tmp_path, "tool_discovery") == 1


def test_fresh_counts_the_input_priced_in_full(tmp_path):
    """`fresh` is the input the provider priced in full (cache misses and writes): a
    mechanism that rewrites the middle of the prompt can cut tokens and raise it."""
    import json

    from benchmarks.harness_eval import fresh_tokens

    log = tmp_path / "conversations" / "s.events.jsonl"
    log.parent.mkdir()
    rows = [
        {"type": "assistant/message", "usage": {"input": 1_000, "cache_read": 9_000, "cache_write": 500}},
        {"type": "assistant/message", "usage": {"input": 200, "cache_read": 10_000}},
        {"type": "compaction/end", "summarizer_tokens": 7_000},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert fresh_tokens(tmp_path) == 1_700


# -- opt-in mechanisms (`--enable`): the baseline is the product, the variant adds one -----


def _session_log(tmp_path, rows):
    import json

    log = tmp_path / "conversations" / "s.events.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def test_a_fold_is_counted_as_the_opt_in_mechanism_acting_and_a_hint_is_not_the_budget_line(tmp_path):
    from benchmarks.harness_eval import compaction_ends, fired, token_split

    _session_log(tmp_path, [
        {"type": "context/budget", "band": 1},
        {"type": "context/budget", "band": 2, "hint": "fold", "moment": "turn"},
        {"type": "compaction/end", "reason": "folded"},
        {"type": "compaction/end", "reason": "summarized"},
        {"type": "assistant/message", "usage": {"input": 1_000, "cache_read": 9_000, "output": 300}},
        {"type": "assistant/message", "usage": {"input": 200, "cache_read": 4_000, "cache_write": 50, "output": 70}},
    ])
    assert fired(tmp_path, "model_fold") == 1
    assert fired(tmp_path, "context_budget_line") == 1  # the fold hint is its own mechanism's row
    assert compaction_ends(tmp_path) == {"folded": 1, "summarized": 1}
    assert token_split(tmp_path) == {"fresh": 1_250, "cached": 13_000, "output": 370}


def _row(passed, fresh, cached, folds=0, variant="baseline"):
    return {
        "suite": "long", "variant": variant, "passed": passed, "steps": 20, "tokens": fresh + cached,
        "split": {"fresh": fresh, "cached": cached, "output": 1_000},
        "fired": {"model_fold": folds}, "compactions": {"folded": folds} if folds else {},
    }


def test_an_enabled_mechanism_that_never_acted_gives_no_reading():
    from benchmarks.ablation import enabled_reading

    off = [_row(True, 60_000, 1_400_000) for _ in range(3)]
    on = [_row(True, 50_000, 900_000, variant="+model_fold") for _ in range(3)]
    # Cheaper on every run, and still nothing can be said: the model never used it.
    assert enabled_reading(off, on, "model_fold").startswith("never acted when enabled")


def test_an_enabled_mechanism_is_read_only_where_the_ranges_do_not_overlap():
    from benchmarks.ablation import enabled_reading, summarize_enabled

    off = [_row(True, 60_000, cached) for cached in (1_400_000, 1_200_000, 1_600_000)]
    on = [_row(True, fresh, cached, folds=1, variant="+model_fold")
          for fresh, cached in ((70_000, 800_000), (55_000, 900_000), (65_000, 1_000_000))]
    reading = enabled_reading(off, on, "model_fold")
    assert "acted in 3/3" in reading and "cached: less with it" in reading
    assert "fresh" not in reading  # 55k-70k straddles 60k: within noise, so not claimed
    assert "quality" not in reading

    worse = [_row(False, 60_000, 800_000, folds=1, variant="+model_fold") for _ in range(3)]
    assert "quality: worse with it" in enabled_reading(off, worse, "model_fold")

    lines = summarize_enabled([*off, *on], ["model_fold"])
    assert lines[0].startswith("model_fold") and "off" in lines[1] and "on" in lines[2]
    assert "compactions {'folded': 3}" in lines[2]
