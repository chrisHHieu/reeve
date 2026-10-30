"""On-demand compaction — the human `/compact`.

The automatic path fires on pressure; this one runs because a person asked, and that
difference is the whole contract: it may run below the threshold, it asks for the summary
rather than for the prune tier, and it reports what it bought (or why nothing could be
done) instead of ending a turn. The durable transaction stays balanced either way.
"""

from __future__ import annotations

import asyncio

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

from summary_fixtures import valid_summary
from harness.context.compaction import part as _part, render_parts as _render_parts

SUMMARY = valid_summary("finish the report")


def _is_summarizer_call(messages):
    return any(
        "Produce ALL of the following sections" in str(m.get("content", ""))
        for m in messages or []
    )


class Provider(ProviderClient):
    def __init__(self, *, summary=SUMMARY, fails=0):
        self.summary = summary
        self.fails = fails
        self.summary_calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        if _is_summarizer_call(messages):
            self.summary_calls += 1
            if self.fails:
                self.fails -= 1
                raise RuntimeError("summarizer down")
            return AssistantTurn(text=self.summary, finish_reason="stop")
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def long_history(turns=8, bulk=1500):
    msgs = [{"role": "system", "content": "be helpful"}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"request {i}", "ts": 1.0})
        msgs.append(
            {"role": "assistant", "content": f"answer {i} " + "x" * bulk, "ts": 1.0}
        )
    return msgs


def make_engine(tmp_path, provider, *, messages=None, window=1_000_000):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {
        "context_window": window,
        "buffer_tokens": 0,
        "keep_tokens": 0,
    }
    return engine


def _events(engine, kind):
    return [e for e in engine.log.read() if e.get("type") == kind]


def test_manual_compaction_runs_below_pressure_and_reports_the_numbers(tmp_path):
    provider = Provider()
    # A million-token window: the automatic trigger is nowhere near firing, and the call
    # still compacts because a person asked.
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000_000)
    result = asyncio.run(engine.compact_now())

    assert result["ok"] is True and result["reason"] == "summarized"
    assert result["saved_tokens"] > 0 and result["saved_pct"] > 0
    assert result["summarizer_tokens"] >= 0
    assert provider.summary_calls == 1
    assert engine.compaction_state is not None
    assert engine.compaction_state.boundary_index > 0

    # The durable transaction is balanced and names the manual cause.
    assert len(_events(engine, "compaction/start")) == 1
    assert len(_events(engine, "compaction/summary")) == 1
    assert len(_events(engine, "compaction/end")) == 1
    assert _events(engine, "compaction/start")[0].get("manual") is True
    assert engine.log.dangling_compactions() == 0

    # And the transcript marker every compaction must leave: without it a reload shows a
    # smaller context with nothing explaining why.
    notice = next(
        m
        for m in engine.messages
        if m.get("role") == "notice" and m.get("kind") == "compacted"
    )
    assert notice.get("saved_tokens") == result["saved_tokens"]
    assert notice.get("summary") == _render_parts([_part(SUMMARY)])


def test_manual_compaction_asks_for_the_summary_not_the_prune_tier(tmp_path, monkeypatch):
    """A prune-only outcome would answer a different request than the one the human made."""
    calls = []
    engine = make_engine(tmp_path, Provider(), messages=long_history())
    monkeypatch.setattr(engine, "_prune_now", lambda *a, **k: calls.append(a))
    result = asyncio.run(engine.compact_now())
    assert calls == []
    assert result["reason"] == "summarized"


def test_nothing_to_compact_is_reported_not_invented(tmp_path):
    provider = Provider()
    engine = make_engine(
        tmp_path,
        provider,
        messages=[
            {"role": "system", "content": "s"},
            {"role": "user", "content": "hi"},
        ],
    )
    result = asyncio.run(engine.compact_now())

    assert result["ok"] is False and "nothing to compact" in result["error"]
    # Said apart from a failure: a view shows "Nothing to compact yet", not an error.
    assert result["reason"] == "nothing"
    assert provider.summary_calls == 0
    assert _events(engine, "compaction/end")[-1]["reason"] == "no_change"
    assert engine.log.dangling_compactions() == 0


def test_a_failing_summarizer_fails_the_manual_compaction_honestly(tmp_path):
    """No trim fallback (study round 29): a manual `/compact` whose summarizer fails
    reports the failure and its reason, and the view is left exactly as it was."""
    engine = make_engine(tmp_path, Provider(fails=99), messages=long_history())
    result = asyncio.run(engine.compact_now())

    assert result["ok"] is False
    assert "could not condense" in result["error"]
    assert "RuntimeError" in (result.get("detail") or "")
    assert engine.compaction_state is None
    assert engine.log.dangling_compactions() == 0


def test_a_stopping_session_refuses(tmp_path):
    engine = make_engine(tmp_path, Provider(), messages=long_history())
    engine._cancel.set()
    result = asyncio.run(engine.compact_now())
    assert result["ok"] is False and "stopping" in result["error"]


def test_the_manager_runs_it_durably_and_refuses_mid_turn(tmp_path):
    from harness.interface.server.manager import SessionManager

    mgr = SessionManager(workspace=tmp_path, provider=Provider())
    sid = "compact-now"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    engine.messages += long_history()[1:]
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
        "buffer_tokens": 0,
        "keep_tokens": 0,
    }

    broadcasts: list[dict] = []

    async def record(session_id: str, message: dict) -> None:
        broadcasts.append({"session_id": session_id, "message": message})

    saved_broadcast = mgr.broadcast_session
    mgr.broadcast_session = record  # type: ignore[assignment]
    try:
        out = asyncio.run(mgr.session_compact_now(sid))
    finally:
        mgr.broadcast_session = saved_broadcast  # type: ignore[assignment]
    assert out["ok"] is True and out["reason"] == "summarized"
    assert engine.compaction_state is not None
    # Every view hears it start, so a message typed meanwhile is queued instead of being
    # refused by the running mark (owner, 2026-10-13). Then the marker, WITH the numbers:
    # the GUI moves the context meter on `now_tokens` instead of waiting for the next turn,
    # and the marker discloses `summary` without a reload.
    assert [b["message"].get("type") for b in broadcasts] == ["compacting", "compacted"]
    assert broadcasts[0]["message"]["data"] == {"manual": True}
    data = broadcasts[1]["message"]["data"]
    assert data["now_tokens"] == out["now_tokens"] > 0
    assert data["saved_tokens"] == out["saved_tokens"] > 0
    assert data["summary"] == _render_parts([_part(SUMMARY)])
    assert isinstance(data["seconds"], float) and data["seconds"] >= 0

    # Durable: a rebuild keeps the condensed view instead of resurrecting the raw thread.
    state = engine.compaction_state
    mgr._engines.pop(sid)
    rebuilt = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    assert rebuilt.compaction_state is not None
    assert rebuilt.compaction_state.boundary_index == state.boundary_index

    # Mid-turn is refused for the same reason restore is: the outbound view is moving.
    mgr.mark_running(sid)
    try:
        busy = asyncio.run(mgr.session_compact_now(sid))
    finally:
        mgr._running_sessions.discard(sid)
    assert busy["ok"] is False and "mid-turn" in busy["error"]


def test_a_view_hears_when_a_manual_compaction_ends_without_a_marker(tmp_path):
    """`compacting` with `manual` ends only on `compacted` or `compact_failed`; without the
    second a view would say it is compacting for good, and queue every message behind it."""
    from harness.interface.server.manager import SessionManager

    mgr = SessionManager(workspace=tmp_path, provider=Provider())
    sid = "nothing-yet"
    mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    broadcasts: list[dict] = []

    async def record(session_id: str, message: dict) -> None:
        broadcasts.append(message)

    mgr.broadcast_session = record  # type: ignore[assignment]
    out = asyncio.run(mgr.session_compact_now(sid))
    assert out["ok"] is False
    assert [b["type"] for b in broadcasts] == ["compacting", "compact_failed"]
    assert broadcasts[1]["data"]["nothing"] is True
    assert "nothing to compact" in broadcasts[1]["data"]["error"]
    assert not mgr.is_running(sid)


def test_the_manager_reports_an_unopened_session(tmp_path):
    from harness.interface.server.manager import SessionManager

    mgr = SessionManager(workspace=tmp_path, provider=Provider())
    out = asyncio.run(mgr.session_compact_now("never-opened"))
    assert out["ok"] is False and "not open" in out["error"]


def test_the_route_is_wired(tmp_path):
    from fastapi.testclient import TestClient

    from harness.interface.server import SessionManager, create_app

    manager = SessionManager(workspace=tmp_path, provider=Provider())
    sid = "route-compact"
    engine = manager.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    engine.messages += long_history()[1:]
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
        "buffer_tokens": 0,
        "keep_tokens": 0,
    }
    client = TestClient(create_app(manager))

    resp = client.post(f"/v1/sessions/{sid}/compact")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["reason"] == "summarized"
    assert body["saved_tokens"] > 0
