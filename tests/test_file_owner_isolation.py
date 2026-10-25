"""Owner isolation for the file and search tools (owner-hit 2026-09-18).

A subagent's registry is a copy of its session's, so its `write_file` /
`read_file` / `grep` closures still resolved against the PARENT's root list — while the
child's plan file, spill store, shell variables and permission boundary all used the
CHILD's roots (`spawn_subagent` passes `roots=self.get_roots(...)` into `build_engine`).
The child's own scratch, the folder its plan already lived in and the one
`REEVE_SCRATCH` names, was then refused with "Path escapes allowed roots", and a write
to the parent's scratch passed the tool only to be denied by the boundary: no scratch path
worked for a deliverable (session 1bb41bb8f9c8, a child of a real-workspace session).

`build_engine` keeps the `AgentContext` it composed on the engine, and
`catalog.replace_path_tools` rebuilds the inherited tools from it. These tests pin both
halves: the copy alone still carries the parent's roots, and the rebind restores the
child's own scratch without widening the parent's.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.compose import build_engine
from harness.modes.agents import reeve_agent
from harness.modes.agents.base import AgentContext
from harness.modes.catalog import replace_path_tools
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.config.roots import RootDir
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.tools import ToolRegistry


class _Scripted(ProviderClient):
    """The child's two turns: one `write_file`, then the final report text."""

    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(text="nothing further", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()



def _roots(workspace, scratch):
    return [
        RootDir(path=workspace, writable=True, label="workspace"),
        RootDir(path=scratch, writable=True, label="scratch"),
    ]


def _engines(tmp_path):
    """A parent and a child with the SAME workspace and DIFFERENT scratch dirs — the
    exact shape `spawn_subagent` builds (the child inherits `parent.registry.filtered`)."""
    workspace = tmp_path / "shared_ws"
    parent_scratch = tmp_path / "parent_scratch"
    child_scratch = tmp_path / "child_scratch"
    for directory in (workspace, parent_scratch, child_scratch):
        directory.mkdir()
    parent = build_engine(
        agent=reeve_agent(),
        workspace=workspace,
        provider=object(),
        session_id="parent1",
        roots=_roots(workspace, parent_scratch),
    )
    child = build_engine(
        agent=reeve_agent(),
        workspace=workspace,
        provider=object(),
        session_id="child1",
        roots=_roots(workspace, child_scratch),
        base_registry=parent.registry.filtered(exclude=()),
    )
    return parent, child, workspace, parent_scratch, child_scratch


def test_the_inherited_copy_alone_still_carries_the_parents_roots(tmp_path):
    """The bug itself, pinned: without the rebind the child's own scratch is refused by
    the tool (and a read names the parent's directories).

    The refusal text is the reader's own (`path escapes the session's directories: …`) —
    the writers are ours and share that wording, which names the roots that
    WOULD have worked instead of a bare "escapes allowed roots".
    """
    parent, child, workspace, _parent_scratch, child_scratch = _engines(tmp_path)
    try:
        write = child.registry.get("write_file").func
        assert write(path=str(workspace / "ok.md"), content="hello")
        with pytest.raises(PermissionError, match="outside the session's folders"):
            write(path=str(child_scratch / "report.md"), content="hello")
        read = child.registry.get("read_file").func
        refused = read(path=str(child_scratch / "report.md"))
        assert "outside the session's folders" in refused["error"]
        assert str(child_scratch) not in refused["error"]
    finally:
        child.executor.close()
        parent.executor.close()


def test_replace_path_tools_rebinds_to_the_childs_own_roots(tmp_path):
    """After the swap the child works in its OWN scratch (the folder its plan lives in)
    and the parent's scratch is the boundary's 'no' — not the tool's."""
    parent, child, _workspace, parent_scratch, child_scratch = _engines(tmp_path)
    try:
        assert child.plan_path is not None
        assert str(child.plan_path).startswith(str(child_scratch))

        assert replace_path_tools(child.registry, child.context) is True

        write = child.registry.get("write_file").func
        assert write(path=str(child_scratch / "report.md"), content="hello")
        read = child.registry.get("read_file").func
        assert "hello" in read(path=str(child_scratch / "report.md"))["content"]
        # The parent's scratch is no longer a tool-side accident: the tool rebuilds
        # against the child's roots, and the boundary answers for it.
        boundary = child.permissions.boundary
        assert boundary.under_writable_root(str(parent_scratch)) is False
        assert boundary.under_writable_root(str(child_scratch)) is True
    finally:
        child.executor.close()
        parent.executor.close()


def test_replace_path_tools_is_a_noop_when_the_registry_has_none(tmp_path):
    """A surface that never held file tools (a chat registry) is left alone."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = AgentContext(workspace=workspace)
    assert replace_path_tools(ToolRegistry(), context) is False


def test_a_spawned_child_writes_its_own_scratch_end_to_end(tmp_path):
    """The spawn wiring itself: `spawn_subagent` rebinds the inherited file tools before
    the turn runs, so a child's `write_file` into its OWN scratch reaches disk — the
    deliverable path that failed for session 1bb41bb8f9c8."""
    provider = _Scripted([AssistantTurn(text="placeholder", finish_reason="stop")])
    # The workspace must NOT contain the scratch base: in production they are different
    # trees (a repo under the project root, scratch under `~/Reeve`), which is exactly
    # why the parent's inherited tools could not reach the child's scratch.
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(workspace=project, provider=provider)
    parent = SessionRecord(
        session_id="parent-1",
        workspace=str(manager.default_workspace or "."),
        model="gpt-5.5",
        mode="auto",  # full access: the write must not park on an approval
        messages=[{"role": "user", "content": "delegate the report"}],
        title="parent",
        agent="reeve",
    )
    manager.session_store.save(parent)
    # A LIVE parent engine is what makes the child inherit its registry;
    # without it `spawn_subagent` falls back to the fixed set, which is a different path.
    manager.get_engine(parent.session_id)
    child_id = manager.create_child_session(
        parent.session_id, "Write the report", "general", "gpt-5.5"
    )
    report = manager.scratch_base() / child_id / "report.md"
    provider._turns[0] = AssistantTurn(
        tool_calls=[
            ToolCall(
                id="call_1",
                name="write_file",
                arguments={"path": str(report), "content": "hello"},
            )
        ],
        finish_reason="tool_calls",
    )

    out = asyncio.run(
        manager.spawn_subagent(
            parent_id=parent.session_id,
            description="Write the report",
            prompt=f"write the report to {report}",
            agent_name="general",
            task_id=child_id,
        )
    )

    assert out["task_id"] == child_id
    assert out["status"] == "completed"
    assert report.read_text(encoding="utf-8") == "hello"
