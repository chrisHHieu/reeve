"""Session fork — branch a conversation into a new session (an agent create call
with a seed).

What must hold: the child starts with the parent's history up to a provider-safe
boundary; the parent is untouched; lineage is recorded and survives the child's first
save; consent (session-scoped "Always allow") does NOT fork with the transcript; and the
compaction projection does not either, because its boundary indexes belong to the
parent's message list.
"""

from __future__ import annotations

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=Provider())


def _parent(mgr: SessionManager, sid: str = "parent-1") -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(mgr.default_workspace or "."),
        model="gpt-5.5",
        mode="interactive",
        messages=[
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer"},
        ],
        title="a long conversation",
        agent="reeve",
        thinking="high",
        extra_roots=[{"path": "/extra", "writable": True, "label": "extra"}],
        grants={"tools": ["run_shell"]},
        bindings={"memory": "proj"},
        compaction={"boundary_index": 3, "summary_text": "old"},
    )
    mgr.session_store.save(record)
    return record


def test_a_fork_carries_the_history_and_records_lineage(tmp_path, monkeypatch):
    from harness.interface.server.manager.session_roots import _fork_safe_prefix

    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr)

    out = mgr.fork_session(parent.session_id)

    assert out["ok"] is True and out["parent_session"] == parent.session_id
    assert out["messages"] == len(parent.messages) == len(_fork_safe_prefix(parent.messages))
    child = mgr.session_store.load(out["session_id"])
    assert child is not None
    assert child.parent_session == parent.session_id
    assert child.seed_length == len(parent.messages)
    assert child.messages == parent.messages
    assert child.title == "a long conversation · fork"
    # Inherited context: route, mode, thinking, workspace, project bindings.
    assert (child.model, child.mode, child.agent) == ("gpt-5.5", "interactive", "reeve")
    assert child.thinking == "high" and child.workspace == parent.workspace
    assert child.bindings == {"memory": "proj"}
    assert child.extra_roots == parent.extra_roots
    # NOT inherited: consent and the parent's compaction projection.
    assert child.grants == {}
    assert child.compaction == {}
    # The parent is untouched.
    assert mgr.session_store.load(parent.session_id).messages == parent.messages


def test_lineage_survives_the_childs_first_turn_save(tmp_path, monkeypatch):
    """The per-turn save rebuilds the record without lineage; the upsert must leave the
    columns alone (the same rule `team` follows)."""
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-2")
    child_id = mgr.fork_session(parent.session_id)["session_id"]

    child = mgr.session_store.load(child_id)
    child.messages = child.messages + [{"role": "user", "content": "and now?"}]
    mgr.session_store.save(child)  # what manager.save does after every turn

    again = mgr.session_store.load(child_id)
    assert again.parent_session == parent.session_id
    assert again.seed_length == len(parent.messages)


def test_a_mid_step_tail_is_cut_to_a_provider_safe_prefix(tmp_path, monkeypatch):
    """A transcript persisted mid-step (crash/stop) ends with unanswered tool calls; a
    fork must not carry an orphaned call into a fresh session's first request."""
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-3")
    parent.messages += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}
            ],
        },
        {"role": "notice", "kind": "interrupted"},
    ]
    mgr.session_store.save(parent)

    out = mgr.fork_session(parent.session_id)

    child = mgr.session_store.load(out["session_id"])
    assert out["messages"] == len(parent.messages) - 2  # the call and the notice are cut
    assert child.messages[-1] == {"role": "assistant", "content": "second answer"}
    # Answered tool results are an ordinary mid-loop state and are kept.
    assert all(m.get("role") != "tool" for m in child.messages)


def test_refusals(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    assert "not known" in mgr.fork_session("nobody")["error"]

    parent = _parent(mgr, "parent-4")
    mgr.mark_running(parent.session_id)
    try:
        busy = mgr.fork_session(parent.session_id)
    finally:
        mgr._running_sessions.discard(parent.session_id)
    assert busy["ok"] is False and "mid-turn" in busy["error"]

    empty = SessionRecord(
        session_id="empty", workspace=str(tmp_path), model="gpt-5.5", mode="interactive", messages=[]
    )
    mgr.session_store.save(empty)
    nothing = mgr.fork_session("empty")
    assert nothing["ok"] is False and "nothing to carry over" in nothing["error"]


def test_the_route_is_wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-route")
    client = TestClient(create_app(mgr))

    resp = client.post(f"/v1/sessions/{parent.session_id}/fork")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["parent_session"] == parent.session_id
    assert mgr.session_store.load(body["session_id"]) is not None
