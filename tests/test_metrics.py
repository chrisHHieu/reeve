"""Session metrics folded from the event log (harness/observability/metrics.py).

The numbers a production harness is judged on — token spend with the cache split,
latency, retry/failure rates, tool efficiency — must come from what the engine already
records, so a fixture with the exact event shapes pins each one without a provider.
"""

from __future__ import annotations

from harness.observability.metrics import summarize, turns_report


def _events():
    return [
        {"seq": 0, "t": 100.0, "type": "turn/start", "turn": 1},
        {"seq": 1, "t": 100.1, "type": "step/start", "turn": 1, "step": 1},
        {
            "seq": 2,
            "t": 100.2,
            "type": "assistant/message",
            "turn": 1,
            "step": 1,
            "usage": {"input": 100, "output": 50, "cache_read": 900, "cache_write": 0},
            "timing": {"dispatched": 100.1, "first_token": 100.5, "completed": 101.1},
        },
        {"seq": 3, "t": 101.2, "type": "llm/retry", "attempt": 1, "of": 2,
         "reason": "transport", "delay": 0.5},
        {
            "seq": 4,
            "t": 101.3,
            "type": "tool/call",
            "turn": 1,
            "step": 1,
            "call_id": "c1",
            "name": "read_file",
        },
        {
            "seq": 5,
            "t": 101.4,
            "type": "tool/call",
            "turn": 1,
            "step": 1,
            "call_id": "c2",
            "name": "run_shell",
        },
        {
            "seq": 6,
            "t": 101.5,
            "type": "tool/result",
            "turn": 1,
            "step": 1,
            "call_id": "c1",
            "name": "read_file",
            "status": "ok",
            "timing": {"started": 101.3, "ended": 101.4},
        },
        {
            "seq": 7,
            "t": 101.6,
            "type": "tool/result",
            "turn": 1,
            "step": 1,
            "call_id": "c2",
            "name": "run_shell",
            "status": "timeout",
            "timing": {"started": 101.3, "ended": 103.3},
            "delegation": {"run_id": "r1"},
        },
        {"seq": 8, "t": 101.7, "type": "tool/result/budget", "call_id": "c2"},
        {"seq": 9, "t": 101.8, "type": "verify/nudge", "turn": 1, "step": 1},
        {"seq": 10, "t": 101.9, "type": "notice", "notice_kind": "compacted", "text": "x"},
        {"seq": 11, "t": 102.0, "type": "turn/checkpoint", "turn": 1, "step": 1},
        {"seq": 12, "t": 102.1, "type": "turn/end", "turn": 1, "reason": "completed",
         "tokens": 1050, "steps": 1},
        {"seq": 13, "t": 200.0, "type": "turn/start", "turn": 2},
        {
            "seq": 14,
            "t": 200.1,
            "type": "assistant/message",
            "turn": 2,
            "step": 1,
            "partial": True,
        },
        {"seq": 15, "t": 200.2, "type": "turn/end", "turn": 2, "reason": "error",
         "tokens": 10, "steps": 1},
    ]


def test_token_split_and_cache_hit_rate():
    report = summarize(_events())
    tokens = report["tokens"]
    assert tokens == {
        "input": 100,
        "output": 50,
        "cache_read": 900,
        "cache_write": 0,
        "total": 1050,
        "cache_read_share": 0.9,
        "per_llm_call": 525.0,
    }


def test_turn_reasons_and_costs():
    turns = summarize(_events())["turns"]
    assert turns["started"] == 2
    assert turns["ended"] == 2
    assert turns["by_reason"] == {"completed": 1, "error": 1}
    assert turns["tokens"] == [1050, 10]
    assert turns["tokens_avg"] == 530


def test_latency_from_assistant_timings():
    latency = summarize(_events())["latency"]
    assert latency["ttft_ms_avg"] == 400.0  # 100.5 - 100.1
    assert latency["ttft_samples"] == 1
    assert latency["step_ms_avg"] == 1000.0  # 101.1 - 100.1
    assert latency["step_ms_max"] == 1000.0


def test_retry_and_failure_rates():
    llm = summarize(_events())["llm"]
    assert llm["calls"] == 2
    assert llm["partial"] == 1
    assert llm["retries"] == 1
    assert llm["retry_rate"] == 0.5
    assert llm["retries_by_reason"] == {"transport": 1}
    assert llm["errors"] == 1
    assert llm["error_rate"] == 0.5


def test_tool_efficiency():
    tools = summarize(_events())["tools"]
    assert tools["calls"] == 2
    assert tools["results"] == 2
    assert tools["by_status"] == {"ok": 1, "timeout": 1}
    assert tools["error_rate"] == 0.5
    assert tools["timeout_or_error"] == 1
    assert tools["duration_ms_max"] == 2000.0
    assert tools["budgeted_results"] == 1
    assert tools["top"] == {"read_file": 1, "run_shell": 1}
    assert tools["delegated"] == 1


def test_compactions_checkpoints_nudges_and_wall_time():
    report = summarize(_events())
    assert report["compactions"] == 1
    assert report["checkpoints"] == 1
    assert report["nudges"] == {"verify": 1}
    assert report["wall_seconds"] == 100.2
    assert report["events"] == 16


def test_compaction_savings_and_summarizer_spend():
    """Two compactions: one summarized (a block, a cost), one prune-only (savings, no
    summarizer). The net number is what the policy is judged on."""
    report = summarize(
        [
            {"seq": 0, "t": 1.0, "type": "turn/start", "turn": 1},
            {
                "seq": 1,
                "t": 2.0,
                "type": "compaction/start",
                "turn": 1,
                "step": 2,
                "before_tokens": 90_000,
            },
            {
                "seq": 2,
                "t": 3.0,
                "type": "compaction/summary",
                "turn": 1,
                "step": 2,
                "reason": "summarized",
                "boundary": 12,
                "before_tokens": 90_000,
                "after_tokens": 30_000,
                "saved_tokens": 60_000,
                "summarizer_tokens": 8_000,
            },
            {"seq": 3, "t": 4.0, "type": "compaction/end", "turn": 1, "step": 2,
             "reason": "summarized"},
            {"seq": 4, "t": 5.0, "type": "compaction/start", "turn": 1, "step": 4},
            {"seq": 5, "t": 6.0, "type": "compaction/end", "turn": 1, "step": 4,
             "reason": "pruned", "saved_tokens": 5_000},
        ]
    )
    assert report["compaction"] == {
        "blocks": 1,
        "saved_tokens": 60_000,
        "summarizer_tokens": 8_000,
        "net_tokens": 52_000,
    }
    assert report["events"] == 6


def test_an_empty_log_is_all_zeroes_not_an_error():
    report = summarize([])
    assert report["events"] == 0
    assert report["turns"]["started"] == 0
    assert report["tokens"]["total"] == 0
    assert report["tokens"]["cache_read_share"] is None
    assert report["latency"]["ttft_ms_avg"] is None
    assert report["tools"]["error_rate"] is None


def test_unknown_event_types_are_ignored():
    """A metric fold must never be the reason an old log fails to read: a new event type
    from a future engine is simply not counted here until this module learns it."""
    report = summarize(
        [
            {"seq": 0, "t": 1.0, "type": "some/future/event", "x": 1},
            {"seq": 1, "t": 2.0, "type": "turn/start", "turn": 1},
        ]
    )
    assert report["turns"]["started"] == 1
    assert report["events"] == 2


def test_cost_usd_is_priced_from_the_sheet(monkeypatch):
    """The same token split the report already carries, priced at the model's published
    per-class rates (cache writes fall back to the input rate when the sheet has none)."""

    class Stub:
        def get(self, model):
            return {"cost": {"input": 1.0, "output": 2.0, "cache_read": 0.1}}

    monkeypatch.setattr("harness.llm.providers.catalog.models_dev", lambda: Stub())
    report = summarize(_events(), model="p:m")
    assert report["model"] == "p:m"
    assert report["cost_usd"] == round((100 * 1.0 + 50 * 2.0 + 900 * 0.1) / 1e6, 6)


def test_cost_usd_is_none_when_the_model_is_not_priced(monkeypatch):
    class Stub:
        def get(self, model):
            return None

    monkeypatch.setattr("harness.llm.providers.catalog.models_dev", lambda: Stub())
    # Unknown is not free, and a report with no model cannot be priced at all.
    assert summarize(_events(), model="unknown:model")["cost_usd"] is None
    assert summarize(_events())["cost_usd"] is None

# -- the ops surface's per-turn fold --------------------------------------------------


def _two_turn_log():
    """Two turns: turn 1 on model A with a failing tool, turn 2 on model B after a
    mid-session switch — the case a per-session price/fold cannot see."""
    events = [
        {"t": 10.0, "type": "turn/start", "turn": 1},
        {"t": 10.1, "type": "request/header", "turn": 1, "step": 1, "model": "model-a"},
        {"t": 10.2, "type": "step/start", "turn": 1, "step": 1},
        {
            "t": 10.3,
            "type": "assistant/message",
            "turn": 1,
            "step": 1,
            "usage": {"input": 100, "output": 10, "cache_read": 0, "cache_write": 0},
            "timing": {"dispatched": 10.2, "first_token": 10.5, "completed": 11.0},
        },
        {"t": 10.4, "type": "tool/call", "turn": 1, "step": 1, "call_id": "a", "name": "run_shell", "arguments": {"command": "ls"}},
        {"t": 10.5, "type": "tool/result", "turn": 1, "step": 1, "call_id": "a", "name": "run_shell", "status": "error", "timing": {"started": 10.4, "ended": 10.6}},
        {"t": 10.6, "type": "tool/call", "turn": 1, "step": 1, "call_id": "b", "name": "run_shell", "arguments": {"command": "ls"}},
        {"t": 10.7, "type": "tool/result", "turn": 1, "step": 1, "call_id": "b", "name": "run_shell", "status": "timeout", "timing": {"started": 10.6, "ended": 12.6}},
        {"t": 10.8, "type": "tool/call", "turn": 1, "step": 1, "call_id": "c", "name": "apply_patch", "arguments": {"path": "x"}},
        {"t": 10.9, "type": "tool/result", "turn": 1, "step": 1, "call_id": "c", "name": "apply_patch", "status": "blocked", "timing": {"started": 10.8, "ended": 10.85}},
        {"t": 11.5, "type": "turn/end", "turn": 1, "reason": "completed", "tokens": 110, "steps": 1},
        {"t": 20.0, "type": "turn/start", "turn": 2},
        {"t": 20.1, "type": "request/header", "turn": 2, "step": 1, "model": "model-b"},
        {"t": 20.2, "type": "step/start", "turn": 2, "step": 1},
        {"t": 20.3, "type": "assistant/message", "turn": 2, "step": 1, "usage": {"input": 50, "output": 5, "cache_read": 0, "cache_write": 0}, "timing": {"dispatched": 20.2, "first_token": 20.9, "completed": 21.0}},
        {"t": 21.0, "type": "turn/end", "turn": 2, "reason": "error", "tokens": 55, "steps": 1},
    ]
    return events


def test_turns_report_builds_one_row_per_turn_with_its_own_model():
    rows = turns_report(_two_turn_log())
    assert [row["turn"] for row in rows] == [1, 2]
    first, second = rows
    # The model is the turn's OWN header — a mid-session switch is visible per row.
    assert first["model"] == "model-a"
    assert second["model"] == "model-b"
    assert first["wall_ms"] == 1500.0
    assert first["steps"] == 1
    assert first["tool_calls"] == 3
    assert first["tool_errors"] == 1
    assert first["tool_timeouts"] == 1
    assert first["blocked"] == 1
    assert first["ttft_ms_avg"] == 300.0
    assert first["failed"] is False and first["reason"] == "completed"
    assert second["failed"] is True and second["reason"] == "error"


def test_turns_report_marks_thrashing_but_not_re_reading():
    def turn_with(repeats: int):
        events = [
            {"t": 1.0, "type": "turn/start", "turn": 1},
            {"t": 1.1, "type": "request/header", "turn": 1, "step": 1, "model": "m"},
        ]
        for i in range(repeats):
            events.append({"t": 1.2, "type": "tool/call", "turn": 1, "step": 1, "call_id": f"c{i}", "name": "read_file", "arguments": {"path": "a.py"}})
        events.append({"t": 1.3, "type": "turn/end", "turn": 1, "reason": "completed", "tokens": 1, "steps": 1})
        return events

    calm = turns_report(turn_with(4))[0]
    assert calm["repeats_max"] == 4 and calm["loop"] is False
    looping = turns_report(turn_with(5))[0]
    assert looping["repeats_max"] == 5 and looping["loop"] is True


def test_summarize_exposes_process_signals_and_per_tool_health():
    report = summarize(_two_turn_log())
    process = report["process"]
    assert process["steps"] == 2
    assert process["steps_per_turn"] == 1.0
    assert process["loop_flags"] == 0
    assert process["model_errors"] == 1  # turn 2 died with reason=error

    tools = report["tools"]["by_name"]
    assert tools["run_shell"]["calls"] == 2
    assert tools["run_shell"]["errors"] == 1
    assert tools["run_shell"]["timeouts"] == 1
    assert tools["run_shell"]["p95_ms"] is not None
    assert tools["apply_patch"]["blocked"] == 1
    assert report["latency"]["ttft_ms_p95"] is not None


def test_summarize_counts_abandoned_retries_as_model_errors():
    events = _two_turn_log() + [
        {"t": 30.0, "type": "llm/retry", "attempt": 3, "of": 3, "reason": "overloaded", "abandoned": True},
    ]
    report = summarize(events)
    assert report["llm"]["retries"] == 1
    assert report["process"]["model_errors"] == 2  # turn error + abandoned retry

