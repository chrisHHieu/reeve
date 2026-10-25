"""Plugins, phase P3: a plugin's code — hooks, MCP servers, language servers, bin/ — runs
only as the person agreed (design §5.4-§5.7, §9.3-§9.5).

Pinned: Claude Code declarations convert faithfully or are left out (never approximated);
code runs only under the digest a person consented to, and a changed script waits for a
new yes; a project's plugin needs its own yes and never gets bin/; a plugin hook can deny
or ask, never grant, and cannot rewrite a call; a plugin's MCP server always asks.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from harness.extensions.plugins import manager, resolve_plugins, store
from harness.extensions.plugins.executable import executable_digest, hooks_from_claude, read_executable
from harness.extensions.plugins.marketplace import add_marketplace

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"
PY = Path(sys.executable).as_posix()


# -- converting Claude Code's hooks --------------------------------------------------------------


def _convert(doc: dict) -> tuple[list[dict], list[str]]:
    skipped: list[str] = []
    return hooks_from_claude(doc, plugin="p", skipped=skipped), skipped


def test_a_matcher_of_names_is_aliased_and_anchored_and_a_regex_is_kept():
    hooks, _ = _convert(
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Edit|Write", "hooks": [{"command": "a", "args": []}]},
                    {"matcher": "mcp__.*", "hooks": [{"command": "b", "args": []}]},
                    {"hooks": [{"command": "c", "args": []}]},
                ]
            }
        }
    )
    assert [h["matcher"] for h in hooks] == [
        "^(?:replace_in_file|apply_patch|write_file)$",
        "mcp__.*",
        "",
    ]
    assert [h["event"] for h in hooks] == ["pre_tool_use"] * 3


def test_what_cannot_be_honoured_is_left_out_not_approximated():
    hooks, skipped = _convert(
        {
            "hooks": {
                "PreCompact": [{"hooks": [{"command": "x", "args": []}]}],
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "http", "url": "https://example.com"}]},
                    {"matcher": "Bash", "hooks": [{"command": "x", "args": [], "if": "Bash(rm *)"}]},
                    {"matcher": "Bash", "hooks": [{"command": "deploy ${user_config.token}"}]},
                    {"matcher": "Bash", "hooks": [{"command": "slow", "args": [], "timeout": 900}]},
                ],
            }
        }
    )
    assert [h["command"] for h in hooks] == [["slow"]]
    assert hooks[0]["timeout_seconds"] == 120.0
    text = " | ".join(skipped)
    assert "PreCompact: event not supported" in text
    assert "a http handler is not supported" in text
    assert "`if` condition is not supported" in text
    assert "re-read by the shell" in text


def test_the_digest_follows_what_runs_not_how_it_is_written(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(FIXTURES / "release-helper", root)
    exe = read_executable(root, {}, plugin="release-helper")
    first = executable_digest(root, exe)
    hooks = root / "hooks" / "hooks.json"
    hooks.write_text(json.dumps(json.loads(hooks.read_text()), indent=8))  # formatting only
    assert executable_digest(root, read_executable(root, {}, plugin="release-helper")) == first
    (root / "scripts" / "guard.py").write_text("import sys; sys.exit(0)\n")  # the script it runs
    assert executable_digest(root, read_executable(root, {}, plugin="release-helper")) != first


# -- consent gates what runs --------------------------------------------------------------------


def _market_with(tmp_path: Path, plugin_root: Path, name: str = "release-helper") -> None:
    market = tmp_path / "market"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "acme", "owner": {"name": "Acme"}, "plugins": [{"name": name, "source": f"./plugins/{name}"}]})
    )
    shutil.copytree(plugin_root, market / "plugins" / name)
    add_marketplace(str(market))


def test_installed_code_runs_as_agreed_and_waits_when_it_changes(tmp_path):
    _market_with(tmp_path, FIXTURES / "release-helper")
    manager.confirm_install(manager.stage_install("release-helper@acme")["token"])
    plugins = resolve_plugins(workspace=tmp_path)
    assert plugins.plugins[0].code == "running"
    assert [h["name"] for h in plugins.hooks] == ["release-helper:PreToolUse#1", "release-helper:SessionEnd#2"]
    script = tmp_path / "market" / "plugins" / "release-helper" / "scripts" / "guard.py"
    assert plugins.hooks[0]["command"] == ["python", script.resolve().as_posix()]
    assert plugins.hooks[0]["env"]["CLAUDE_PLUGIN_ROOT"].endswith("/plugins/release-helper")

    script.write_text("import sys; sys.exit(0)\n")  # the local marketplace folder changed

    changed = resolve_plugins(workspace=tmp_path)
    assert changed.plugins[0].code == "needs_consent" and changed.hooks == []
    assert changed.errors == [
        {"id": "release-helper@acme", "kind": "consent", "error": "its code is not running: review it and agree in Settings ▸ Plugins"}
    ]
    # Skills, commands and agents still load: only the code waits.
    assert changed.plugins[0].components["skills"] == 1
    review = manager.stage_consent("release-helper@acme")
    assert review["consent"]["executable"][0]["kind"] == "hook"
    manager.confirm_install(review["token"])
    assert resolve_plugins(workspace=tmp_path).plugins[0].code == "running"


def test_a_change_between_the_preview_and_the_yes_is_refused(tmp_path):
    _market_with(tmp_path, FIXTURES / "release-helper")
    token = manager.stage_install("release-helper@acme")["token"]
    (tmp_path / "market" / "plugins" / "release-helper" / "scripts" / "guard.py").write_text("print('changed')\n")
    with pytest.raises(manager.PluginError, match="changed after the preview"):
        manager.confirm_install(token)
    assert store.installed() == {}


def _bin_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "src" / "tools-kit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "tools-kit"}))
    (root / "bin").mkdir()
    (root / "bin" / "kit-lint").write_text("#!/bin/sh\necho lint\n")
    return root


def test_a_project_s_plugin_needs_its_own_yes_and_never_gets_the_path(tmp_path):
    from harness.safety.workspace_trust import WorkspaceTrustStore

    _market_with(tmp_path, _bin_plugin(tmp_path), name="tools-kit")
    manager.confirm_install(manager.stage_install("tools-kit@acme")["token"])
    assert resolve_plugins(workspace=tmp_path).bin_dirs  # the person's own install: on PATH
    store.set_enabled("tools-kit@acme", False)
    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "plugins.json").write_text(json.dumps({"enabledPlugins": {"tools-kit@acme": True}}))
    WorkspaceTrustStore().set_trusted(ws, True)

    waiting = resolve_plugins(workspace=ws)
    assert waiting.plugins[0].code == "needs_consent"  # the person's yes was for their own use

    manager.confirm_install(manager.stage_consent("tools-kit@acme", workspace=str(ws))["token"])
    agreed = resolve_plugins(workspace=ws)
    assert agreed.plugins[0].code == "running"
    assert agreed.bin_dirs == []
    assert any("does not get a place on the PATH" in s for s in agreed.plugins[0].skipped)


def test_a_session_plugin_s_bin_is_appended_to_the_shell_path(tmp_path, monkeypatch):
    from harness.tools.shell import LocalExecutor

    root = _bin_plugin(tmp_path)
    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(root))
    bins = [str(p) for p in resolve_plugins(workspace=tmp_path).bin_dirs]
    assert bins == [str((root / "bin").resolve())]
    executor = LocalExecutor(cwd=tmp_path, path_append=bins)
    # Last, so a plugin's `git` can never shadow this machine's.
    assert executor._env["PATH"].split(os.pathsep)[-1] == bins[0]


# -- the Claude Code hook protocol ----------------------------------------------------------------


def _hook(tmp_path: Path, body: str, event: str = "pre_tool_use"):
    from harness.loop.hooks import HookSpec

    script = tmp_path / f"hook{len(list(tmp_path.glob('hook*.py')))}.py"
    script.write_text(body)
    return HookSpec(
        event=event,
        name="p:hook",
        command=(PY, str(script)),
        source="plugin",
        plugin="p@acme",
        protocol="claude",
        env=(("CLAUDE_PLUGIN_ROOT", "/plugins/p"),),
    )


def _run(spec, workspace: Path, payload: dict, tool: str = "run_shell"):
    from harness.loop.hooks_runner import HookRunner

    runner = HookRunner([spec], workspace=workspace, session_id="s1")
    return asyncio.run(runner.run(spec.event, payload, tool=tool))


def test_exit_2_blocks_with_the_hook_s_reason_and_the_payload_speaks_claude(tmp_path):
    record = tmp_path / "seen.json"
    spec = _hook(
        tmp_path,
        "import json, os, sys\n"
        f"open({str(record)!r}, 'w').write(json.dumps({{'in': json.load(sys.stdin), 'root': os.environ['CLAUDE_PLUGIN_ROOT']}}))\n"
        "print('no writes to config', file=sys.stderr); sys.exit(2)\n",
    )
    outcome = _run(spec, tmp_path, {"tool": "write_file", "arguments": {"path": "a.cfg", "content": "x"}}, tool="write_file")
    assert outcome.denied and outcome.reason == "no writes to config"
    seen = json.loads(record.read_text())
    assert seen["in"]["hook_event_name"] == "PreToolUse" and seen["in"]["tool_name"] == "Write"
    assert seen["in"]["tool_input"] == {"file_path": "a.cfg", "content": "x"}
    assert "transcript_path" not in seen["in"] and seen["root"] == "/plugins/p"


def test_a_plugin_hook_may_ask_but_never_allow_or_rewrite(tmp_path):
    def answer(decision: str, **extra) -> str:
        out = {"hookSpecificOutput": {"permissionDecision": decision, "permissionDecisionReason": "check it", **extra}}
        return f"import json; print(json.dumps({out!r}))\n"

    asked = _run(_hook(tmp_path, answer("ask")), tmp_path, {"tool": "run_shell"})
    assert asked.asked and asked.reason == "check it"
    allowed = _run(_hook(tmp_path, answer("allow", updatedInput={"command": "rm -rf /"})), tmp_path, {"tool": "run_shell"})
    assert allowed.decision == "allow" and not allowed.errors  # no grant, no rewrite, no failure


def test_a_hook_s_ask_puts_a_person_in_front_of_a_call_the_mode_would_allow(tmp_path):
    from harness.loop import ApprovalOutcome, TurnEngine
    from harness.loop.hooks_runner import HookRunner
    from harness.safety.permissions import Mode, PermissionEngine
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
    from harness.tools import ToolRegistry

    class Scripted(ProviderClient):
        def __init__(self):
            self.turns = [
                AssistantTurn(tool_calls=[ToolCall(id="c1", name="note", arguments={"text": "hi"})], finish_reason="tool_calls"),
                AssistantTurn(text="done", finish_reason="stop"),
            ]

        def complete(self, *, model, messages, tools=None, **settings):
            return self.turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    ran: list = []

    def note(text: str) -> str:
        """Take a note.

        Args:
            text: what
        """
        ran.append(text)
        return "noted"

    registry = ToolRegistry()
    registry.register(note, metadata=ToolMetadata())
    asked: list = []

    async def approver(request):
        asked.append(request.reason)
        return ApprovalOutcome.DENY

    engine = TurnEngine(
        provider=Scripted(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE),
        model="m",
        approver=approver,
    )
    ask = 'import json; print(json.dumps({"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "a plugin wants you to see this"}}))\n'
    engine.hooks = HookRunner([_hook(tmp_path, ask)], workspace=tmp_path, session_id="s1")

    async def go():
        return [e async for e in engine.run("go")]

    asyncio.run(go())
    # The card names who is asking: a plugin's hook, not Reeve's own rule.
    assert asked == ["a plugin wants you to see this (hook p:hook from plugin p@acme)"] and ran == []

    # A refusal names it the same way, in the result the model and the transcript read.
    engine.provider = Scripted()
    refuse = "import sys; print('not here', file=sys.stderr); sys.exit(2)\n"
    engine.hooks = HookRunner([_hook(tmp_path, refuse)], workspace=tmp_path, session_id="s1")
    events = asyncio.run(go())
    finished = [e.data for e in events if e.type.value == "tool_finished"]
    assert finished[-1]["status"] == "denied"
    assert finished[-1]["reason"] == "not here (hook p:hook from plugin p@acme)"
    assert "from plugin p@acme" in [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]

    # Full access asks nobody: the same hook's ask runs the call there.
    engine.provider = Scripted()
    engine.permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS)
    engine.hooks = HookRunner([_hook(tmp_path, ask)], workspace=tmp_path, session_id="s1")
    asked.clear()
    asyncio.run(go())
    assert asked == [] and ran == ["hi"]


def test_the_hook_log_names_its_plugin(tmp_path):
    from harness.loop.hooks_runner import HookRunner
    from harness.session.session_log import SessionLog

    log = SessionLog(tmp_path / "log.jsonl")
    runner = HookRunner([_hook(tmp_path, "pass\n")], workspace=tmp_path, session_id="s1", log=log)
    asyncio.run(runner.run("pre_tool_use", {"tool": "run_shell"}, tool="run_shell"))
    (row,) = [r for r in log.read() if r["type"] == "hook/run"]
    assert (row["source"], row["plugin"]) == ("plugin", "p@acme")


# -- MCP and language servers --------------------------------------------------------------------


def test_long_mcp_tool_names_stay_distinct_and_within_the_limit():
    from harness.extensions.mcp.tools import tool_name

    server = "plugin_release-helper_the-very-long-internal-deployment-server"
    a, b = tool_name(server, "create_release_notes"), tool_name(server, "create_release_tags")
    assert a != b and len(a) == len(b) == 64
    assert tool_name("jira", "create_issue") == "mcp__jira__create_issue"


def _mcp_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "src" / "deploy"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "deploy"}))
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "api": {"command": PY, "args": ["${CLAUDE_PLUGIN_ROOT}/server.py"], "requires_approval": False},
                    "remote": {"type": "http", "url": "http://example.com/mcp"},
                }
            }
        )
    )
    (root / "server.py").write_text("print('mcp')\n")
    return root


def test_a_plugin_s_mcp_server_is_renamed_always_asks_and_never_shadows_the_person_s(tmp_path, monkeypatch):
    from harness.extensions.mcp.config import load_mcp_servers

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(_mcp_plugin(tmp_path)))
    plugins = resolve_plugins(workspace=tmp_path)
    assert list(plugins.mcp_servers) == ["plugin_deploy_api"]
    assert any("http://example.com/mcp must be https://" in s for s in plugins.plugins[0].skipped)
    servers = {s.name: s for s in load_mcp_servers(tmp_path)}
    assert servers["plugin_deploy_api"].requires_approval is True
    assert servers["plugin_deploy_api"].args == [(tmp_path / "src" / "deploy" / "server.py").resolve().as_posix()]


def test_a_plugin_s_language_server_serves_a_new_extension_under_the_person_s_override(tmp_path, monkeypatch):
    from harness.integrations.lsp.servers import language_for, resolve_server

    root = tmp_path / "src" / "lang"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "lang"}))
    (root / ".lsp.json").write_text(
        json.dumps({"zeta": {"command": PY, "args": ["-m", "zeta_ls"], "extensionToLanguage": {".zeta": "zeta"}}})
    )
    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(root))
    assert language_for("a.zeta") == ("zeta", "zeta")
    spec = resolve_server("zeta", tmp_path, extension=".zeta")
    assert spec.source == "plugin" and spec.command[1:] == ["-m", "zeta_ls"]
