"""The Metrics tab's harness and usage blocks (harness/observability/metrics_harness.py + the store)."""

from __future__ import annotations

import sqlite3

from harness.observability.metrics_harness import harness_fold, usage_by_day
from harness.interface.server.manager.metrics_store import MetricsStore

DAY1 = 1_790_000_000.0  # 2026-09-21 (UTC)
DAY2 = DAY1 + 86_400


def _events() -> list[dict]:
    usage = {"input": 1_000, "output": 10, "cache_read": 60_000, "cache_write": 0}
    return [
        {"t": DAY1, "type": "turn/start", "turn": 1},
        {"t": DAY1, "type": "user/message", "turn": 1},
        {"t": DAY1 + 40, "type": "step/start", "turn": 1, "step": 1},  # unexplained stall
        {"t": DAY1 + 45, "type": "assistant/message", "turn": 1, "step": 1,
         "model": "opencode-go:deepseek-v4.1-flash", "usage": usage,
         "timing": {"dispatched": DAY1 + 40, "first_token": DAY1 + 43}},
        {"t": DAY1 + 45, "type": "step/end", "turn": 1, "step": 1},
        {"t": DAY1 + 46, "type": "compaction/prepare", "turn": 1, "outcome": "parked", "tokens": 500},
        {"t": DAY1 + 47, "type": "compaction/end", "turn": 1, "reason": "prepared"},
        {"t": DAY1 + 48, "type": "notice", "turn": 1, "kind": "usage_partial",
         "model": "opencode-go:qwen3.8-flash", "text": "partial"},
        {"t": DAY1 + 50, "type": "turn/end", "turn": 1, "steps": 1, "tokens": 61_010 + 500 + 1_000},
        # The session is resumed the next day: its tokens belong to DAY2, not DAY1.
        {"t": DAY2, "type": "turn/start", "turn": 2},
        {"t": DAY2 + 1, "type": "assistant/message", "turn": 2, "step": 1,
         "model": "zai-coding-plan:glm-5.3-flash", "usage": {"input": 300, "output": 5}},
        {"t": DAY2 + 2, "type": "turn/end", "turn": 2, "steps": 1, "tokens": 305},
    ]


def test_tokens_land_on_the_day_each_call_ran():
    rows = usage_by_day(_events())
    assert [(r["day"], r["provider"], r["tokens_in"]) for r in rows] == [
        ("2026-09-21", "opencode-go", 1_000),
        ("2026-09-22", "zai-coding-plan", 300),
    ]


def test_the_harness_fold_names_every_signal():
    fold = harness_fold(_events())
    assert fold["spend"]["unattributed"] == 1_000  # charged, logged by nothing
    assert fold["stalls"] == {"unexplained": 1}
    assert fold["compaction"] == {"prepared": 1} and fold["prepares"] == {"parked": 1}
    assert fold["usage_partial"] == ["opencode-go:qwen3.8-flash"]
    assert list(fold["ttft"]) == ["50-100k"] and fold["ttft"]["50-100k"] == [3.0]


def test_the_report_carries_harness_usage_and_background_blocks(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        events = _events()
        row = {
            "day": "2026-09-22",
            "tools": {},
            "usage_by_day": usage_by_day(events),
            "harness": harness_fold(events),
        }
        store.upsert("s1", row=row, turns=[], log_mtime=1.0)
        store.record_model_usage("__memory__", provider="opencode-go", model="m", tokens_in=700)
        store.remove_session("s1")  # deleted: every number below must survive it
        report = store.report(None)
        assert report["harness"]["spend"]["unattributed"] == 1_000
        assert report["harness"]["stalls"]["unexplained"] == 1
        assert report["harness"]["ttft"][0] == {"band": "50-100k", "p50_ms": 3000, "p90_ms": 3000, "samples": 1}
        assert report["harness"]["usage_partial"] == [{"model": "opencode-go:qwen3.8-flash", "sessions": 1}]
        days = {(r["day"], r["provider"]) for r in report["usage_daily"]}
        assert ("2026-09-21", "opencode-go") in days and ("2026-09-22", "zai-coding-plan") in days
        assert report["background"] == [{"purpose": "memory", "calls": 1, "tokens": 700}]
    finally:
        store.close()


def test_an_old_store_migrates_and_refolds(tmp_path):
    """A metrics.db written before v3 has neither column; opening it adds both and resets
    the watermarks so every live log re-folds with tokens split by day."""
    path = tmp_path / "metrics.db"
    MetricsStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE session_metrics DROP COLUMN harness_json")
    conn.execute("ALTER TABLE session_metrics DROP COLUMN deleted")
    conn.execute("DELETE FROM meta WHERE key = 'metrics_v3'")
    conn.execute("INSERT INTO session_metrics (session_id, day, log_mtime) VALUES ('old', '2026-09-01', 5)")
    conn.commit()
    conn.close()

    store = MetricsStore(path)
    try:
        columns = {r["name"] for r in store._conn.execute("PRAGMA table_info(session_metrics)")}
        assert {"harness_json", "deleted"} <= columns
        assert store.watermark("old") == 0.0  # re-folds once
        assert store.report(None)["harness"]["spend"]["turn_total"] == 0
    finally:
        store.close()
