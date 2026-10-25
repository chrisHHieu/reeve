"""The Metrics page's trends (2026-10-13): each harness check per day, and the period before the
one on screen. The eval ledger left the page on 2026-10-14."""

from __future__ import annotations

import json

from harness.interface.server.manager.metrics_harness_report import usage_between
from harness.interface.server.manager.metrics_store import MetricsStore
from harness.observability.metrics_harness import harness_fold

DAY1 = 1_790_000_000.0  # 2026-09-21 (UTC)
DAY2 = DAY1 + 86_400


def _events() -> list[dict]:
    usage = {"input": 1_000, "output": 10, "cache_read": 60_000, "cache_write": 0}
    return [
        {"t": DAY1, "type": "turn/start", "turn": 1},
        {"t": DAY1, "type": "user/message", "turn": 1},
        {"t": DAY1 + 40, "type": "step/start", "turn": 1, "step": 1},  # an unexplained wait on DAY1
        {"t": DAY1 + 45, "type": "assistant/message", "turn": 1, "step": 1, "model": "p:m", "usage": usage},
        {"t": DAY1 + 45, "type": "step/end", "turn": 1, "step": 1},
        {"t": DAY1 + 50, "type": "turn/end", "turn": 1, "tokens": 61_010 + 1_000},  # 1,000 unattributed
        {"t": DAY2, "type": "turn/start", "turn": 2},
        {"t": DAY2 + 1, "type": "compaction/end", "turn": 2, "reason": "failed"},
        {"t": DAY2 + 2, "type": "notice", "turn": 2, "kind": "usage_partial", "model": "p:q"},
        {"t": DAY2 + 3, "type": "assistant/message", "turn": 2, "step": 1, "model": "p:m",
         "usage": {"input": 300, "output": 5}},
        {"t": DAY2 + 4, "type": "turn/end", "turn": 2, "tokens": 305},
    ]


def test_each_check_lands_on_the_day_it_happened():
    days = harness_fold(_events())["days"]
    assert days["2026-09-21"] == {
        "turn_total": 62_010, "unattributed": 1_000, "unexplained": 1, "compaction_failed": 0, "usage_partial": [],
    }
    assert days["2026-09-22"] == {
        "turn_total": 305, "unattributed": 0, "unexplained": 0, "compaction_failed": 1, "usage_partial": ["p:q"],
    }


def _row(day: str, harness: dict) -> dict:
    return {"day": day, "tools": {}, "harness": harness, "turns_started": 1, "turns_completed": 1}


def test_the_report_sums_the_days_across_sessions_inside_the_range(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.upsert("s1", row=_row("2026-09-22", harness_fold(_events())), turns=[], log_mtime=1.0)
        store.upsert("s2", row=_row("2026-09-22", harness_fold(_events())), turns=[], log_mtime=1.0)
        daily = store.report(None)["harness"]["daily"]
        assert [d["day"] for d in daily] == ["2026-09-21", "2026-09-22"]
        assert daily[0]["unexplained"] == 2 and daily[0]["unattributed"] == 2_000
        assert daily[0]["unattributed_pct"] == round(2_000 / 124_020, 4)
        # The same route twice is one route on that day.
        assert daily[1]["compaction_failed"] == 2 and daily[1]["usage_partial"] == 1
        assert [d["day"] for d in store.report("2026-09-22")["harness"]["daily"]] == ["2026-09-22"]
    finally:
        store.close()


def test_the_period_before_is_read_from_the_rollup(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        for day, tokens in (("2026-09-10", 100), ("2026-09-14", 200), ("2026-09-15", 999)):
            store.record_model_usage("__title__", model="p:m", day=day, calls=1, tokens_in=tokens, tokens_out=0,
                                     cache_read=tokens, cache_write=0)
        before = usage_between(store._conn, store._lock, "2026-09-10", "2026-09-15")
        assert before["calls"] == 2 and before["tokens"] == 600 and before["cache_hit"] == 0.5
    finally:
        store.close()


def test_every_live_log_folds_once_more_for_its_days(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    store.upsert("s1", row=_row("2026-09-22", {}), turns=[], log_mtime=5.0)
    store._conn.execute("DELETE FROM meta WHERE key = 'harness_days_v6'")
    store._conn.commit()
    store.close()
    again = MetricsStore(tmp_path / "metrics.db")
    try:
        assert again.watermark("s1") == 0.0
    finally:
        again.close()
