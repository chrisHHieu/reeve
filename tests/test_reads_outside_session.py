"""A file tool reaching another folder is a permission question, and once allowed the
folder joins the session's roots before the tool runs.

The file tools keep their own scoping; what grows is the roots list. These run the real engine
with the real tools over a shared roots list, the way a session is built.
"""

from __future__ import annotations

import asyncio

from harness.config.roots import RootDir
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import ApprovalOutcome, TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.secrets import state_dir
from harness.tools import ToolRegistry
from harness.tools.file_writers import file_writers
from harness.tools.files import file_tools
from harness.tools.search import search_tools


class _Scripted(ProviderClient):
    def __init__(self, call: ToolCall):
        self.turns = [
            AssistantTurn(tool_calls=[call], finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]

    def complete(self, *, model, messages, tools=None, **settings):
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _session(tmp_path, mode: Mode, call: ToolCall, outcome=ApprovalOutcome.ONCE):
    ws = tmp_path / "ws"
    ws.mkdir()
    roots = [RootDir(path=ws, writable=True)]
    registry = ToolRegistry()
    registry.register_all(file_tools(str(ws), roots=roots))
    registry.register_all(file_writers(str(ws), roots=roots))
    registry.register_all(search_tools(str(ws), roots=roots))
    asked: list = []

    async def approver(request):
        asked.append(request)
        return outcome

    engine = TurnEngine(
        provider=_Scripted(call),
        registry=registry,
        permissions=PermissionEngine(workspace_root=ws, mode=mode, roots=roots),
        model="m",
        approver=approver,
    )
    return engine, roots, asked


def _run(engine):
    async def go():
        return [e async for e in engine.run("go")]

    return asyncio.run(go())


def _tool_text(engine) -> str:
    return [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]


def test_a_read_outside_asks_and_once_allowed_the_folder_is_a_read_only_root(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.md").write_text("the sibling project's notes\n", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments={"path": str(other / "notes.md")})
    engine, roots, asked = _session(tmp_path, Mode.WORKSPACE_WRITE, call)

    _run(engine)

    assert len(asked) == 1
    assert asked[0].card["reach"] == [{"path": str(other.resolve()), "writable": False}]
    assert "the sibling project's notes" in _tool_text(engine)
    added = [r for r in roots if r.path == other.resolve()]
    assert len(added) == 1 and added[0].writable is False
    # The next read there is inside the session: nothing to ask.
    assert engine.permissions.evaluate("read_file", {"path": str(other / "notes.md")}).allowed


def test_a_declined_read_adds_nothing_and_reads_nothing(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.md").write_text("private\n", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments={"path": str(other / "notes.md")})
    engine, roots, asked = _session(tmp_path, Mode.WORKSPACE_WRITE, call, ApprovalOutcome.DENY)

    _run(engine)

    assert len(asked) == 1
    assert "private" not in _tool_text(engine)
    assert all(r.path != other.resolve() for r in roots)


def test_full_access_reads_and_greps_another_folder_without_asking(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.py").write_text("def needle():\n    pass\n", encoding="utf-8")
    call = ToolCall(id="c1", name="grep", arguments={"pattern": "needle", "path": str(other)})
    engine, roots, asked = _session(tmp_path, Mode.DANGER_FULL_ACCESS, call)

    _run(engine)

    assert asked == []
    assert "needle" in _tool_text(engine)
    assert any(r.path == other.resolve() and not r.writable for r in roots)


def test_full_access_writes_another_folder_and_that_folder_becomes_writable(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    target = other / "out.txt"
    call = ToolCall(id="c1", name="write_file", arguments={"path": str(target), "content": "x"})
    engine, roots, asked = _session(tmp_path, Mode.DANGER_FULL_ACCESS, call)

    _run(engine)

    assert asked == []
    assert target.read_text(encoding="utf-8") == "x"
    assert any(r.path == other.resolve() and r.writable for r in roots)


def test_reeves_credentials_file_is_refused_in_every_mode(tmp_path):
    secrets = state_dir() / "secrets.json"
    for mode in (Mode.DANGER_FULL_ACCESS, Mode.WORKSPACE_WRITE, Mode.READ_ONLY):
        engine = PermissionEngine(workspace_root=tmp_path, mode=mode)
        decision = engine.evaluate("read_file", {"path": str(secrets)})
        assert not decision.allowed and not decision.needs_user, mode
        assert "credentials" in decision.reason


# A folder the session took by reading is where its access starts, not a mark the person wrote.
# Taken as a mark, every write to a project the agent had first read was refused, in full access
# too, with no card to approve (owner, 2026-10-18).


class _Calls(ProviderClient):
    def __init__(self, calls: list[ToolCall]):
        self.turns = [AssistantTurn(tool_calls=[c], finish_reason="tool_calls") for c in calls]
        self.turns.append(AssistantTurn(text="done", finish_reason="stop"))

    def complete(self, *, model, messages, tools=None, **settings):
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _read_then_write(tmp_path, mode: Mode):
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.md").write_text("before\n", encoding="utf-8")
    engine, roots, asked = _session(tmp_path, mode, ToolCall(id="x", name="read_file", arguments={}))
    engine.provider = _Calls(
        [
            ToolCall(id="c1", name="read_file", arguments={"path": str(other / "notes.md")}),
            ToolCall(id="c2", name="write_file", arguments={"path": str(other / "notes.md"), "content": "after\n"}),
        ]
    )
    _run(engine)
    return other, roots, asked


def test_a_write_after_a_read_is_asked_not_refused_and_the_project_turns_writable(tmp_path):
    other, roots, asked = _read_then_write(tmp_path, Mode.WORKSPACE_WRITE)

    assert [request.card["reach"] for request in asked] == [
        [{"path": str(other.resolve()), "writable": False}],
        [{"path": str(other.resolve()), "writable": True}],
    ]
    assert (other / "notes.md").read_text(encoding="utf-8") == "after\n"
    taken = [r for r in roots if r.path == other.resolve()]
    assert len(taken) == 1 and taken[0].writable and taken[0].reached


def test_full_access_writes_a_project_it_read_first(tmp_path):
    other, roots, asked = _read_then_write(tmp_path, Mode.DANGER_FULL_ACCESS)

    assert asked == []
    assert (other / "notes.md").read_text(encoding="utf-8") == "after\n"


def test_a_folder_the_person_marked_read_only_still_refuses_a_write_in_full_access(tmp_path):
    kept = tmp_path / "kept"
    kept.mkdir()
    roots = [RootDir(path=tmp_path / "ws", writable=True), RootDir(path=kept, writable=False)]
    engine = PermissionEngine(workspace_root=tmp_path / "ws", mode=Mode.DANGER_FULL_ACCESS, roots=roots)

    decision = engine.evaluate("write_file", {"path": str(kept / "a.txt"), "content": "x"})

    assert not decision.allowed and not decision.needs_user
    assert "read-only folder" in decision.reason


def test_a_reach_is_granted_for_the_project_not_the_folder_a_file_sits_in(tmp_path):
    project = tmp_path / "wealthlog"
    for part in ("frontend", "backend"):
        (project / part).mkdir(parents=True)
        (project / part / "app.py").write_text(f"# {part}\n", encoding="utf-8")
    (project / ".git").mkdir()
    engine, roots, asked = _session(tmp_path, Mode.DANGER_FULL_ACCESS, ToolCall(id="x", name="read_file", arguments={}))
    engine.provider = _Calls(
        [
            ToolCall(id="c1", name="read_file", arguments={"path": str(project / "frontend" / "app.py")}),
            ToolCall(id="c2", name="read_file", arguments={"path": str(project / "backend" / "app.py")}),
        ]
    )

    _run(engine)

    assert [r.path for r in roots if r.reached] == [project.resolve()]
    assert "# backend" in _tool_text(engine)


def test_a_folder_outside_any_repository_is_granted_as_itself(tmp_path):
    loose = tmp_path / "downloads" / "batch"
    loose.mkdir(parents=True)
    (loose / "a.json").write_text("{}", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments={"path": str(loose / "a.json")})
    engine, roots, asked = _session(tmp_path, Mode.DANGER_FULL_ACCESS, call)

    _run(engine)

    assert [r.path for r in roots if r.reached] == [loose.resolve()]
