"""The Ops data plane: the summary store, the manager's fold, and the report payload.

`metrics.py` is pinned by its own fixture; these tests pin the seam — that a log folds
into one session row plus one row per turn, that the report aggregates them, that deleting
a session removes its rows, and that the route serves the whole payload.
"""

from __future__ import annotations

from harness.interface.server.manager import SessionManager
from harness.interface.server.manager.metrics_store import MetricsStore
from harness.session.session_log import SessionLog
from harness.session.sessions import SessionRecord


def _row(**over):
    base = {
        "day": "2026-09-19",
        "agent": "reeve",
        "model": "m1",
        "workspace": "ws",
        "turns_started": 10,
        "turns_completed": 9,
        "turns_failed": 1,
        "llm_calls": 20,
        "retries": 2,
        "model_errors": 1,
        "tokens_in": 1000,
        "tokens_out": 200,
        "cache_read": 3000,
        "cache_write": 0,
        "ttft_avg_ms": 1200.0,
        "ttft_p95_ms": 4800.0,
        "wall_ms": 60000.0,
        "tool_calls": 40,
        "tool_errors": 2,
        "tool_timeouts": 1,
        "blocked": 1,
        "steps": 25,
        "loop_flags": 1,
        "compactions": 0,
        "subagents": 2,
        "tools": {
            "run_shell": {"calls": 30, "errors": 2, "timeouts": 1, "blocked": 0, "p95_ms": 900.0}
        },
    }
    base.update(over)
    return base


def _turn(turn: int, **over):
    base = {
        "turn": turn,
        "day": "2026-09-19",
        "agent": "reeve",
        "model": "m1",
        "wall_ms": 5000.0,
        "steps": 3,
        "failed": False,
        "reason": "completed",
        "tokens_in": 100,
        "tokens_out": 20,
        "tool_calls": 4,
        "tool_errors": 0,
        "tool_timeouts": 0,
        "blocked": 0,
        "ttft_ms": 1100.0,
        "loop": False,
        "errors": [],
    }
    base.update(over)
    return base


def test_report_aggregates_sessions_turns_tools_and_lists(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.upsert(
            "s1",
            row=_row(),
            turns=[
                _turn(1, wall_ms=12000.0),
                _turn(
                    2,
                    failed=True,
                    reason="error",
                    tool_errors=1,
                    errors=[{"tool": "run_shell", "status": "error", "reason": "exit 1"}],
                ),
            ],
            log_mtime=1.0,
        )
        store.upsert("s2", row=_row(day="2026-09-20", agent="ops", model="m2"), turns=[_turn(1)], log_mtime=2.0)

        report = store.report(None)
        headline = report["headline"]
        assert headline["sessions"] == 2
        assert headline["turns_started"] == 20
        assert headline["turns_failed"] == 2
        assert headline["tool_calls"] == 80
        assert headline["cache_hit"] == 0.75  # 6000 / (2000 + 6000)
        assert headline["failed_spend"] is not None

        assert [row["name"] for row in report["by_model"]] == ["m1", "m2"]
        assert report["by_model"][0]["sessions"] == 1

        tools = {row["name"]: row for row in report["tools"]}
        assert tools["run_shell"]["calls"] == 60
        assert tools["run_shell"]["errors"] == 4

        assert report["failures"][0]["tool"] == "run_shell"
        assert report["failures"][0]["reason"] == "exit 1"

        # A range filter drops the older day.
        only_late = store.report("2026-09-20")
        assert only_late["headline"]["sessions"] == 1
    finally:
        store.close()


def test_a_deleted_session_keeps_its_numbers_and_loses_its_identity(tmp_path):
    """Owner call (2026-09-25): the Metrics tab tracks the harness and the providers over
    time, and a conversation cleaned up yesterday still ran and still spent. Its counts
    stay in every aggregate; its workspace and error texts go, and it leaves every
    per-session list."""
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.upsert("s1", row=_row(), turns=[_turn(1)], log_mtime=1.0)
        before = store.report(None)["headline"]
        store.remove_session("s1")
        after = store.report(None)
        assert store.has_rows() is True
        assert after["headline"] == before
        assert after["failures"] == []
        row = store._conn.execute("SELECT workspace, deleted FROM session_metrics").fetchone()
        assert (row["workspace"], row["deleted"]) == ("", 1)
        errors = store._conn.execute("SELECT errors_json FROM turn_metrics").fetchone()[0]
        assert errors == "[]"
    finally:
        store.close()


def test_notable_needs_history_and_fires_on_a_spike():
    calm = [
        {"day": f"2026-09-{day:02d}", "tool_error_rate": 0.02, "tokens": 1_000_000, "failed": 0, "loops": 0}
        for day in range(10, 17)
    ]
    spike = {"day": "2026-09-17", "tool_error_rate": 0.09, "tokens": 1_100_000, "failed": 0, "loops": 0}
    entries = MetricsStore._notable(calm + [spike])
    assert any(item["kind"] == "tool_error_rate" and item["level"] == "warn" for item in entries)

    # Seven days is the floor: a young install gets an empty strip, not noise.
    assert MetricsStore._notable(calm[:5] + [spike]) == []


def _write_log(manager: SessionManager, session_id: str) -> None:
    log = SessionLog(manager.session_store.conv_dir / f"{session_id}.events.jsonl")
    log.append("turn/start", turn=1)
    log.append("request/header", turn=1, step=1, model="m1", reason="initial")
    log.append("step/start", turn=1, step=1)
    log.append(
        "assistant/message",
        turn=1,
        step=1,
        text="hi",
        usage={"input": 100, "output": 20, "cache_read": 0, "cache_write": 0},
        timing={"dispatched": 1.0, "first_token": 1.4, "completed": 2.0},
    )
    log.append("tool/call", turn=1, step=1, call_id="c1", name="run_shell", arguments={"command": "x"})
    log.append(
        "tool/result",
        turn=1,
        step=1,
        call_id="c1",
        name="run_shell",
        status="error",
        content="exit 1: boom",
        timing={"started": 2.0, "ended": 2.5},
    )
    log.append("turn/end", turn=1, reason="completed", tokens=120, steps=1)


def test_manager_fold_and_ops_metrics_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(data_dir=tmp_path / "data")
    try:
        mgr.session_store.save(
            SessionRecord(
                session_id="s1",
                workspace=str(tmp_path),
                model="m1",
                mode="workspace-write",
                title="Fold me",
                agent="reeve",
            )
        )
        _write_log(mgr, "s1")

        mgr._record_session_metrics("s1", force=True)
        payload = mgr.ops_metrics("7d")
        headline = payload["headline"]
        assert headline["turns_started"] == 1
        assert headline["turns_completed"] == 1
        assert headline["tool_errors"] == 1
        assert headline["tokens_in"] == 100
        assert payload["failures"][0]["tool"] == "run_shell"
        assert payload["range"] == "7d"

        # The debounce skips a second non-forced fold; forcing still refreshes.
        before = mgr.metrics_store.watermark("s1")
        mgr._record_session_metrics("s1")
        assert mgr.metrics_store.watermark("s1") == before

        # Deleting the session keeps its numbers and drops it from the per-session lists.
        mgr.delete_session("s1")
        after = mgr.ops_metrics("7d")
        assert after["headline"]["sessions"] == 1 and after["headline"]["tool_errors"] == 1
        assert after["failures"] == []
    finally:
        mgr.metrics_store.close()


def test_opening_metrics_reads_no_session_record(tmp_path, monkeypatch):
    """The page took 4.5 s to open on the owner's store (2026-10-13): the report itself was
    6-9 ms, and 3.2-3.8 s went to loading 39 session records for titles the page never drew.
    The payload comes from the metrics tables alone, and carries no per-session lists."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(data_dir=tmp_path / "data")
    try:
        mgr.session_store.save(
            SessionRecord(session_id="s1", workspace=str(tmp_path), model="m1", mode="workspace-write", title="Fold me", agent="reeve")
        )
        _write_log(mgr, "s1")
        mgr._record_session_metrics("s1", force=True)

        def no_load(*_args, **_kwargs):
            raise AssertionError("the Metrics payload loaded a session record")

        monkeypatch.setattr(mgr.session_store, "load", no_load)
        for range_name in ("today", "7d", "30d", "all"):
            payload = mgr.ops_metrics(range_name)
            assert payload["failures"][0]["tool"] == "run_shell"
            for gone in ("by_session", "slowest", "by_agent", "by_workspace"):
                assert gone not in payload
            assert "title" not in payload["failures"][0]
            # The period before, for each number's change; all time has none.
            assert (payload["previous"] is None) == (range_name == "all")
            # Evals left the page (2026-10-14); the ship days and the self-review took their place.
            assert "versions" not in payload and "evals" not in payload
            assert isinstance(payload["ships"], list)
        from datetime import date, datetime, timedelta, timezone

        today = datetime.now(timezone.utc).date()
        before = mgr.ops_metrics("7d")["previous"]
        # The seven days that end where the seven on screen begin.
        assert before["until"] == (today - timedelta(days=6)).isoformat()
        assert date.fromisoformat(before["until"]) - date.fromisoformat(before["since"]) == timedelta(days=7)
    finally:
        mgr.metrics_store.close()


def test_metrics_route_serves_the_payload(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    body = client.get("/v1/metrics", params={"range": "7d"}).json()
    assert body["range"] == "7d"
    for key in ("headline", "daily", "by_model", "tools", "failures", "notable"):
        assert key in body


def test_model_usage_rollup_splits_a_session_by_model_and_survives_delete(tmp_path):
    from harness.observability.metrics import summarize

    events = [
        {"type": "assistant/message", "t": 1.0, "turn": 1, "step": 1, "model": "openai:gpt-6-sol",
         "usage": {"input": 1000, "output": 100, "cache_read": 200, "cache_write": 0}},
        {"type": "assistant/message", "t": 2.0, "turn": 1, "step": 2, "model": "openrouter:glm-5.2",
         "usage": {"input": 500, "output": 50, "cache_read": 0, "cache_write": 25}},
        {"type": "assistant/message", "t": 3.0, "turn": 2, "step": 1, "model": "openai:gpt-6-sol",
         "usage": {"input": 2000, "output": 200, "cache_read": 0, "cache_write": 0}},
    ]
    row = {**summarize(events), "model": "gpt-6-sol", "day": "2026-09-24", "tools": {}}
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.upsert("s1", row=row, turns=[], log_mtime=1.0)
        by_model = {r["name"]: r for r in store.report(None)["by_model"]}
        assert set(by_model) == {"openai:gpt-6-sol", "openrouter:glm-5.2"}
        assert by_model["openai:gpt-6-sol"]["provider"] == "openai"
        assert by_model["openai:gpt-6-sol"]["tokens_in"] == 3000
        assert by_model["openai:gpt-6-sol"]["llm_calls"] == 2
        assert by_model["openrouter:glm-5.2"]["tokens"] == 575

        # Deleting the session keeps the spend: the rollup is not the session's to lose.
        store.remove_session("s1")
        survivors = {r["name"]: r for r in store.report(None)["by_model"]}
        assert survivors["openai:gpt-6-sol"]["tokens_in"] == 3000
        assert survivors["openrouter:glm-5.2"]["tokens"] == 575
        assert store.report(None)["headline"]["sessions"] == 1  # the numbers stay
    finally:
        store.close()


def test_off_session_usage_and_evals_are_recorded_outside_sessions(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.record_model_usage("__title__", provider="acme", model="small-1", tokens_in=700)
        store.record_model_usage("__title__", provider="acme", model="small-1", tokens_in=300)
        rows = {r["name"]: r for r in store.report(None)["by_model"]}
        assert rows["small-1"]["provider"] == "acme"
        assert rows["small-1"]["tokens_in"] == 1000  # additive, not replace
        assert rows["small-1"]["llm_calls"] == 2
    finally:
        store.close()
