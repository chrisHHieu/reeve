"""`scripts/harness_kpis.py` counts what the audit counted, from invented event logs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "harness_kpis", Path(__file__).resolve().parents[1] / "scripts" / "harness_kpis.py"
)
kpis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(kpis)


def _write_log(root: Path, name: str, events: list[dict]) -> None:
    folder = root / "conversations"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{name}.events.jsonl", "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def _read(call_id: str, path: str, *, t: float = 100.0, turn: int = 1, step: int = 1) -> dict:
    return {
        "t": t,
        "type": "tool/call",
        "turn": turn,
        "step": step,
        "call_id": call_id,
        "name": "read_file",
        "arguments": json.dumps({"path": path}),
    }


def test_a_read_shrunk_by_the_budget_and_read_again_is_counted(tmp_path):
    _write_log(
        tmp_path,
        "s1",
        [
            {"t": 100.0, "type": "turn/start", "turn": 1},
            _read("a", "big.py"),
            {"type": "tool/result/budget", "turn": 1, "step": 1, "call_id": "a",
             "name": "read_file", "limit": 4000},
            _read("b", "big.py", step=2),
            {"type": "tool/result/budget", "turn": 1, "step": 2, "call_id": "b",
             "name": "read_file", "limit": 4000},
            # A different path in the same turn is not a re-read.
            _read("c", "other.py", step=3),
            {"type": "turn/end", "turn": 1, "steps": 3, "tokens": 300_000},
        ],
    )
    report = kpis.compute(tmp_path)
    assert report["reads"] == {
        "calls": 3,
        "duplicate_windows": 1,
        "shrunk_by_budget": 2,
        "rereads_after_shrink": 1,
        "reshrunk": 1,
    }
    assert report["budget"]["turns_past_aggregate"] == 1
    # 100k tokens per step: inside the >100k band only when strictly above it.
    assert report["context_bands"][">100k"] == {"steps_pct": 0.0, "tokens_pct": 0.0}


def test_errors_bands_mcp_share_and_since(tmp_path):
    _write_log(
        tmp_path,
        "old",
        [
            {"t": 10.0, "type": "turn/start", "turn": 1},
            {"type": "turn/end", "turn": 1, "steps": 1, "tokens": 900_000},
        ],
    )
    tools = [
        {"type": "function", "function": {"name": "read_file", "description": "x" * 50}},
        {"type": "function", "function": {"name": "mcp__srv__q", "description": "y" * 50}},
    ]
    _write_log(
        tmp_path,
        "new",
        [
            {"t": 1000.0, "type": "request/header", "tools": tools},
            {"type": "tool/call", "turn": 1, "step": 1, "call_id": "e",
             "name": "replace_in_file", "arguments": json.dumps({"path": "a.py"})},
            {"type": "tool/call", "turn": 1, "step": 1, "call_id": "f",
             "name": "grep", "arguments": "{}"},
            {"type": "tool/result", "name": "replace_in_file", "content": "Error: nope"},
            {"type": "tool/result", "name": "grep", "content": "Found 1 match"},
            {"type": "turn/end", "turn": 1, "steps": 2, "tokens": 500_000},
        ],
    )
    everything = kpis.compute(tmp_path)
    assert everything["sessions"] == 2
    assert everything["context_bands"][">400k"]["steps_pct"] == round(100 / 3, 1)

    recent = kpis.compute(tmp_path, since=500.0)
    assert recent["sessions"] == 1
    assert recent["tools"]["replace_in_file"] == {"calls": 1, "error_pct": 100.0}
    assert recent["tools"]["grep"] == {"calls": 1, "error_pct": 0.0}
    assert recent["edits"] == {"calls": 1, "error_pct": 100.0}
    assert recent["request"]["mcp_share_median"] == 0.5
    assert recent["parallelism"]["calls_per_step_pct"] == {"2": 100.0}
    assert recent["context_bands"][">200k"] == {"steps_pct": 100.0, "tokens_pct": 100.0}
    assert recent["cache"] == {}  # no metrics.db in this store


def test_since_keeps_the_new_turns_of_a_resumed_session(tmp_path):
    """Sessions are resumed for days; filtering them by their FIRST event dropped 2 of the
    3 sessions used on the first after-change afternoon (2026-09-24)."""
    tools = [{"type": "function", "function": {"name": "read_file", "description": "x"}}]
    _write_log(
        tmp_path,
        "resumed",
        [
            {"t": 10.0, "type": "request/header", "tools": tools},
            {"t": 10.0, "type": "turn/start", "turn": 1},
            {"t": 11.0, "type": "tool/call", "turn": 1, "step": 1, "call_id": "a",
             "name": "grep", "arguments": "{}"},
            {"t": 12.0, "type": "turn/end", "turn": 1, "steps": 1, "tokens": 900_000},
            {"t": 1000.0, "type": "turn/start", "turn": 2},
            _read("b", "a.py", t=1001.0, turn=2),
            {"t": 1002.0, "type": "turn/end", "turn": 2, "steps": 1, "tokens": 50_000},
        ],
    )
    recent = kpis.compute(tmp_path, since=500.0)
    assert recent["sessions"] == 1 and recent["turns"] == 1
    assert recent["tools"] == {"read_file": {"calls": 1, "error_pct": 0.0}}
    assert recent["tokens"] == 50_000
    assert recent["request"]["tools_median"] == 1  # the header those turns ran under


def test_spend_names_every_logged_call_and_the_residue(tmp_path):
    """`turn/end` tokens split by the logged call that paid them; what no event explains
    is `unattributed` — 17% of one session on 2026-09-24, an unlogged compaction prepare."""
    usage = {"input": 100, "cache_read": 800, "cache_write": 0, "output": 100}
    _write_log(
        tmp_path,
        "s",
        [
            # A reloaded session reuses turn 1: the abandoned attempt's call must not fold
            # into the turn that later closes under the same number.
            {"t": 1.0, "type": "turn/start", "turn": 1},
            {"t": 1.0, "type": "assistant/message", "turn": 1, "usage": usage},
            {"t": 2.0, "type": "turn/start", "turn": 1},
            {"t": 2.0, "type": "assistant/message", "turn": 1, "usage": usage},
            {"t": 2.0, "type": "compaction/prepare", "turn": 1, "tokens": 500},
            {"t": 2.0, "type": "compaction/end", "turn": 1, "summarizer_tokens": 300},
            {"t": 2.0, "type": "tool/result", "turn": 1, "name": "task", "tokens": 200},
            {"t": 2.0, "type": "turn/end", "turn": 1, "steps": 1, "tokens": 3_000},
        ],
    )
    spend = kpis.compute(tmp_path)["spend"]
    assert spend["turn_total"] == 3_000
    assert (spend["main_pct"], spend["prepare_pct"], spend["summarizer_pct"], spend["delegated_pct"]) == (
        33.3, 16.7, 10.0, 6.7,
    )
    assert spend["unattributed"] == 1_000  # 3000 − (1000 + 500 + 300 + 200)


def test_latency_bands_and_stalls_are_classified(tmp_path):
    """The regression's pre-step stalls had no event of their own; the KPI names every
    wait over 10s by what explains it, and `unexplained` must stay at zero."""
    usage = {"input": 1_000, "cache_read": 60_000, "cache_write": 0, "output": 10}
    _write_log(
        tmp_path,
        "s",
        [
            {"t": 0.0, "type": "turn/start", "turn": 1},
            {"t": 0.0, "type": "user/message", "turn": 1},
            {"t": 40.0, "type": "step/start", "turn": 1, "step": 1},  # unexplained 40s
            {"t": 45.0, "type": "assistant/message", "turn": 1, "usage": usage,
             "timing": {"dispatched": 40.0, "first_token": 43.5}},
            {"t": 45.0, "type": "step/end", "turn": 1, "step": 1},
            {"t": 46.0, "type": "compaction/start", "turn": 1},
            {"t": 70.0, "type": "step/start", "turn": 1, "step": 2},  # compaction
            {"t": 71.0, "type": "step/end", "turn": 1, "step": 2},
            {"t": 72.0, "type": "llm/retry", "turn": 1},
            {"t": 90.0, "type": "step/start", "turn": 1, "step": 3},  # provider retry
            {"t": 91.0, "type": "step/end", "turn": 1, "step": 3},
            {"t": 91.0, "type": "turn/end", "turn": 1, "steps": 3, "tokens": 1},
            {"t": 900.0, "type": "turn/start", "turn": 2},
            {"t": 900.5, "type": "user/message", "turn": 2},
            {"t": 901.0, "type": "step/start", "turn": 2, "step": 1},  # 0.5s: no stall
        ],
    )
    latency = kpis.compute(tmp_path)["latency"]
    assert latency["stalls"] == {"unexplained": 1, "compaction": 1, "provider retry": 1}
    assert latency["ttft_p50"] == {"50-100k": 3.5}


def test_outcomes_count_how_turns_end_and_what_failed_calls_and_list_only_steps_cost(tmp_path):
    """The harness is judged by finishing correctly at a reasonable cost: the engine's own
    turn-end reasons, the next step's context after a failed call, and steps spent only on
    the task list."""

    def message(turn, step, tokens):
        return {"type": "assistant/message", "turn": turn, "step": step,
                "usage": {"input": tokens, "cache_read": 0}}

    def call(turn, step, cid, name):
        return {"type": "tool/call", "turn": turn, "step": step, "call_id": cid, "name": name,
                "arguments": "{}"}

    _write_log(
        tmp_path,
        "s1",
        [
            {"t": 100.0, "type": "turn/start", "turn": 1},
            message(1, 1, 1_000), call(1, 1, "a", "todo_write"),
            message(1, 2, 2_000), call(1, 2, "b", "replace_in_file"),
            {"type": "tool/result", "turn": 1, "step": 2, "call_id": "b",
             "name": "replace_in_file", "content": "Error: Expected 1 replacement(s), found 0"},
            message(1, 3, 3_000), call(1, 3, "c", "replace_in_file"), call(1, 3, "d", "todo_write"),
            {"type": "turn/end", "turn": 1, "reason": "completed", "steps": 3, "tokens": 6_000},
            {"t": 200.0, "type": "turn/start", "turn": 2},
            {"type": "turn/end", "turn": 2, "reason": "completed_check_failed", "steps": 1, "tokens": 1},
            {"t": 300.0, "type": "turn/start", "turn": 3},
            {"type": "turn/end", "turn": 3, "reason": "error", "steps": 1, "tokens": 1},
        ],
    )
    out = kpis.compute(tmp_path)["outcomes"]
    assert out["turns_ended"] == 3
    assert out["end_reasons"] == {"completed": 1, "completed_check_failed": 1, "error": 1}
    assert out["not_evidence_pct"] == 33.3 and out["stopped_pct"] == 33.3
    assert out["recovery_tokens"] == 3_000  # the step after the failed call
    assert out["todo_only_steps"] == 1  # step 3 carried the list beside work: free
