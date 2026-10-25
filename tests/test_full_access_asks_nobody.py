"""Full access asks nobody.

The engine's floors are covered by the decision matrix and `test_permission_guards.py`. These
are the asks that live outside the permission engine: a file the agent downloaded, a call that
was under way when the session stopped, and the broker that a child's ask and a spawn ask go
through.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.loop.approvals import UNCERTAIN_RETRY_MESSAGE
from harness.safety.permissions import Mode, PermissionEngine
from harness.tools import ToolRegistry


class _Silent(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, mode: Mode) -> TurnEngine:
    return TurnEngine(
        provider=_Silent(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="m",
    )


def test_a_downloaded_file_is_run_in_full_access_and_asked_about_below_it(tmp_path):
    call = ToolCall(id="c1", name="run_shell", arguments={"command": "python fetched.py"})
    full = _engine(tmp_path, Mode.DANGER_FULL_ACCESS)
    full._downloaded_target = lambda tool_call: object()
    decision, _note = full._decide(call, None)
    assert decision.allowed and not decision.needs_user

    asking = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    asking._downloaded_target = lambda tool_call: object()
    decision, _note = asking._decide(call, None)
    assert not decision.allowed and decision.needs_user and decision.human_only


def test_a_call_under_way_at_a_stop_is_handed_back_to_the_model_in_full_access(tmp_path):
    """Not run (a second send would be a real harm) and not asked about: the model is told it
    may have happened, and a call it makes again is a new call."""
    call = ToolCall(id="c1", name="write_file", arguments={"path": "a.txt", "content": "x"})
    full = _engine(tmp_path, Mode.DANGER_FULL_ACCESS)
    full._uncertain_calls = {"c1"}
    decision, _note = full._decide(call, None)
    assert not decision.allowed and not decision.needs_user
    assert decision.reason == UNCERTAIN_RETRY_MESSAGE

    again = ToolCall(id="c2", name="write_file", arguments={"path": "a.txt", "content": "x"})
    decision, _note = full._decide(again, None)
    assert decision.allowed

    asking = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    asking._uncertain_calls = {"c1"}
    decision, _note = asking._decide(call, None)
    assert decision.needs_user and decision.human_only


def _with_engine(mgr: SessionManager, session_id: str, mode: Mode, tmp_path) -> None:
    mgr._engines[session_id] = SimpleNamespace(
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode)
    )


def test_the_broker_answers_a_child_of_a_full_access_session_without_a_frame(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=_Silent())
    _with_engine(mgr, "root-1", Mode.DANGER_FULL_ACCESS, tmp_path)
    _with_engine(mgr, "root-2", Mode.WORKSPACE_WRITE, tmp_path)
    frames: list = []
    mgr.register_event_client(lambda frame: frames.append(frame))

    async def main():
        spawn = mgr.request_permission(
            session_id="root-1", permission="task", patterns=["explore"], always=["*"]
        )
        child = mgr.request_permission(
            session_id="child-a", parent_id="root-1", permission="run_shell", patterns=["echo hi"]
        )
        assert (await spawn).allowed and (await child).allowed
        parked = mgr.request_permission(
            session_id="child-b", parent_id="root-2", permission="run_shell", patterns=["echo hi"]
        )
        await asyncio.sleep(0)
        assert not parked.done()
        mgr.reply_permission(mgr.pending_permissions()[0]["request_id"], "reject")

    asyncio.run(main())
    asked = [f for f in frames if isinstance(f, dict) and f.get("type") == "permission_asked"]
    assert [f["data"]["session_id"] for f in asked] == ["child-b"]
