"""The auto-memory directory is writable with the ordinary file tools, without an ask
— and nothing else is. Memory is kept with Write/Edit; the dedicated
`auto_memory` tool this replaced was low risk, so the allowance covers the read-only
modes too. The floors still judge every such write."""

from __future__ import annotations

from harness.safety.permissions import Mode, PermissionEngine


def _engine(tmp_path, mode=Mode.WORKSPACE_WRITE, **kw):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    memory = tmp_path / "state" / "projects" / "ws" / "memory"
    memory.mkdir(parents=True)
    engine = PermissionEngine(workspace_root=workspace, mode=mode, memory_dirs=[memory], **kw)
    return engine, workspace, memory


def test_a_memory_write_runs_without_an_ask(tmp_path):
    engine, _ws, memory = _engine(tmp_path)
    decision = engine.evaluate("write_file", {"path": str(memory / "no-db-mocks.md"), "content": "x"})
    assert decision.allowed and decision.reason == "auto memory"
    edit = engine.evaluate(
        "replace_in_file", {"path": str(memory / "MEMORY.md"), "old": "a", "new": "b"}
    )
    assert edit.allowed


def test_a_workspace_write_still_asks(tmp_path):
    engine, ws, _memory = _engine(tmp_path)
    decision = engine.evaluate("write_file", {"path": str(ws / "a.py"), "content": "x"})
    assert not decision.allowed and decision.needs_user


def test_one_target_outside_the_memory_dir_loses_the_allowance(tmp_path):
    engine, ws, memory = _engine(tmp_path)
    patch = (
        "*** Begin Patch\n"
        f"*** Delete File: {memory / 'stale.md'}\n"
        f"*** Delete File: {ws / 'a.py'}\n"
        "*** End Patch"
    )
    assert not engine.evaluate("apply_patch", {"patch": patch}).allowed
    only_memory = f"*** Begin Patch\n*** Delete File: {memory / 'stale.md'}\n*** End Patch"
    assert engine.evaluate("apply_patch", {"patch": only_memory}).allowed


def test_a_traversal_out_of_the_memory_dir_is_not_memory(tmp_path):
    engine, _ws, memory = _engine(tmp_path)
    escaped = str(memory / ".." / ".." / ".." / "config.toml")
    assert not engine.evaluate("write_file", {"path": escaped, "content": "x"}).allowed


def test_read_only_modes_still_save_memory_but_nothing_else(tmp_path):
    engine, ws, memory = _engine(tmp_path, mode=Mode.READ_ONLY)
    assert engine.evaluate("write_file", {"path": str(memory / "m.md"), "content": "x"}).allowed
    refused = engine.evaluate("write_file", {"path": str(ws / "a.py"), "content": "x"})
    assert not refused.allowed and not refused.needs_user
    planning, _ws2, memory2 = _engine(tmp_path / "p", plan=True)
    assert planning.evaluate("write_file", {"path": str(memory2 / "m.md"), "content": "x"}).allowed


def test_a_ruleset_deny_still_wins(tmp_path):
    engine, _ws, memory = _engine(tmp_path, rule_decider=lambda tool, args: "deny")
    assert not engine.evaluate("write_file", {"path": str(memory / "m.md"), "content": "x"}).allowed


def test_no_memory_dir_means_no_allowance(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = PermissionEngine(workspace_root=workspace, mode=Mode.WORKSPACE_WRITE)
    elsewhere = tmp_path / "state" / "m.md"
    assert not engine.evaluate("write_file", {"path": str(elsewhere), "content": "x"}).allowed


def test_scratch_conversations_share_one_memory(tmp_path, monkeypatch):
    """Each no-project conversation runs in its own scratch folder; keyed by that path,
    every one had a private memory no later one could read (13 dead directories on the
    audited install). They share the `general` key; a real project keeps its own."""
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from harness.interface.server.manager import SessionManager

    class _Quiet(ProviderClient):
        def complete(self, **kwargs):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    scratch = tmp_path / "scratch"
    monkeypatch.setenv("REEVE_SCRATCH_BASE", str(scratch))
    project = tmp_path / "project"
    project.mkdir()
    mgr = SessionManager(workspace=project, provider=_Quiet())
    first = scratch / "s1"
    second = scratch / "s2"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert mgr._memory_key_for(None, str(first)) == "general"
    assert mgr._memory_key_for(None, str(second)) == "general"
    assert mgr._memory_key_for(None, str(project)) not in (None, "general")
    assert mgr._auto_memory_for_workspace(str(first)).root == mgr._auto_memory_for_workspace(
        str(second)
    ).root
