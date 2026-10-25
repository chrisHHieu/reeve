"""GUI-managed subagents — PUT/DELETE `/v1/subagents`, file round-trip, presets.

Settings ▸ Subagents writes per-agent markdown (never config.toml). The tests here prove
the contract the GUI depends on:

- a file the composer writes round-trips through `load_config`/`resolve_agents` and
  resolves to the SAME ruleset the preset promised;
- a markdown file shadows a built-in, and deleting it restores the built-in;
- user scope lands in the state dir's `agents/`, project scope in
  `<workspace>/.harness/modes/agents/` (the loader's own directories);
- every bad input is a 400 with an error string, a missing delete is a 404, a malformed
  hand-written file never breaks the listing;
- `inject_agents: false` reaches `build_engine` (no AGENTS.md block).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness.compose import build_engine
from harness.modes.agents import Agent
from harness.config.config import load_config
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server import SessionManager, create_app
from harness.orchestration.subagent_files import SubagentError
from harness.tools.task_agents import ALLOW, DENY, READONLY_GROUPS, disabled_tools, evaluate, preset_rules, resolve_agents, rule_decider, summarize_rules


class _Provider(ProviderClient):
    def __init__(self, turns=()):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason="stop")


@pytest.fixture()
def state(tmp_path, monkeypatch):
    """A per-test state dir (`REEVE_STATE_DIR`), so user scope never touches real files."""
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setenv("REEVE_STATE_DIR", str(root))
    return root


@pytest.fixture()
def ws(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return path


BODY = {
    "scope": "user",
    "description": "Reviews a diff and reports blocking issues as path:line.",
    "color": "#2f9e5f",
    "model": "opencode-go/deepseek-v4.1-flash",
    "tools": {"preset": "custom", "allow": ["read", "grep", "list", "shell-read"]},
    "prompt": "You are a code reviewer. Report findings as path:line.",
    "inject_agents": False,
}


# -- one file: write, round-trip, ruleset -----------------------------------------------


def test_put_writes_a_file_that_round_trips_to_the_same_ruleset(state, ws):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    out = mgr.save_subagent("code-reviewer", BODY)

    path = state / "agents" / "code-reviewer.md"
    assert out == {
        "ok": True,
        "name": "code-reviewer",
        "scope": "user",
        "file": str(path),
    }
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "\t" not in text  # the parser rejects tabs outright

    agent = resolve_agents(load_config(ws))["code-reviewer"]
    assert agent.description == BODY["description"]
    assert agent.color == "#2f9e5f"
    assert agent.model == "opencode-go/deepseek-v4.1-flash"
    assert agent.mode == "subagent"
    assert agent.prompt == BODY["prompt"]
    assert agent.inject_agents is False

    # The written rules resolve to the preset's exact decisions (last match wins over
    # the new agent's DEFAULTS layer), in both directions.
    for tool in ("read_file", "read_image", "grep", "list_files", "shell_read"):
        assert evaluate(tool, "", agent.permission) == ALLOW, tool
    assert evaluate("run_shell", "git log -5", agent.permission) == ALLOW
    for tool in (
        "write_file",
        "apply_patch",
        "run_shell",
        "web_fetch",
        "load_skill",
        "remember",
        "task",
    ):
        assert evaluate(tool, "", agent.permission) == DENY, tool
    removed = disabled_tools(
        [
            "read_file",
            "write_file",
            "replace_in_file",
            "run_shell",
            "web_fetch",
            "load_skill",
            "remember",
            "task",
        ],
        agent.permission,
    )
    # The shell stays, for its reads (`shell-read`); a command that writes is refused at the call.
    assert removed == {
        "write_file",
        "replace_in_file",
        "web_fetch",
        "load_skill",
        "remember",
        "task",
    }


def test_preset_all_writes_no_permission_table(state, ws):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    mgr.save_subagent("plain", {**BODY, "tools": {"preset": "all"}})
    entry = resolve_agents(load_config(ws))["plain"]
    assert evaluate("write_file", "", entry.permission) == ALLOW  # DEFAULTS only
    assert evaluate("run_shell", "", entry.permission) == ALLOW
    text = (state / "agents" / "plain.md").read_text(encoding="utf-8")
    assert "permission:" not in text


def test_readonly_preset_denies_write_and_shell(state, ws):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    mgr.save_subagent("scout", {**BODY, "tools": {"preset": "readonly"}})
    entry = resolve_agents(load_config(ws))["scout"]
    for tool in ("read_file", "grep", "list_files", "web_search"):
        assert evaluate(tool, "", entry.permission) == ALLOW, tool
    for tool in ("write_file", "run_shell", "load_skill", "remember", "task"):
        assert evaluate(tool, "", entry.permission) == DENY, tool
    # Its shell runs what only reads: the ruleset hands that to the engine, which
    # holds it to the roots, and refuses everything else.
    decide = rule_decider(entry.permission)
    assert decide("run_shell", {"command": "git log --oneline -5"}) is None
    assert decide("run_shell", {"command": "git commit -m x"}) == DENY
    assert "run_shell" not in disabled_tools(["run_shell", "write_file"], entry.permission)


def test_nested_name_creates_parent_directories(state, ws):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    out = mgr.save_subagent("team/helper", BODY)
    assert out["file"] == str(state / "agents" / "team" / "helper.md")
    assert (state / "agents" / "team" / "helper.md").is_file()
    assert "team/helper" in resolve_agents(load_config(ws))


# -- override semantics -----------------------------------------------------------------


def test_put_overrides_a_builtin_and_delete_restores_it(state, ws):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    mgr.save_subagent(
        "explore",
        {
            **BODY,
            "scope": "project",
            "description": "My own explore",
            "tools": {"preset": "readonly"},
        },
    )

    row = {r["name"]: r for r in mgr.list_subagents()}["explore"]
    assert row["native"] is True
    assert row["source"] == "project"
    assert row["overrides"] is True
    assert row["file"] == str(ws / ".reeve" / "agents" / "explore.md")
    assert row["description"] == "My own explore"
    assert row["tools"] == {
        "preset": "readonly",
        "allow": list(READONLY_GROUPS),
        "deny": [],
    }
    # The built-ins stay listed: `general` is untouched beside the override.
    names = {r["name"] for r in mgr.list_subagents()}
    assert {"general", "explore"} <= names

    mgr.delete_subagent("explore", "project")
    restored = {r["name"]: r for r in mgr.list_subagents()}["explore"]
    assert restored["source"] == "builtin"
    assert restored["file"] is None
    assert restored["overrides"] is False
    assert restored["description"].startswith("Read-only search of a folder's files")


def test_delete_missing_file_is_a_404(state):
    mgr = SessionManager(workspace=None, provider=_Provider())
    with pytest.raises(SubagentError) as error:
        mgr.delete_subagent("ghost", "user")
    assert error.value.status == 404
    assert "ghost" in str(error.value)


def test_project_scope_requires_a_workspace(state):
    mgr = SessionManager(workspace=None, provider=_Provider())
    with pytest.raises(SubagentError) as error:
        mgr.save_subagent("x", {**BODY, "scope": "project"})
    assert "open a folder" in str(error.value)


def test_project_file_wins_over_the_workspace_root_agents_dir(state, ws):
    """`<ws>/.harness/modes/agents` merges after `<ws>/agents` in `load_config`, so the GUI's
    project scope is the effective one when both exist."""
    root_agents = ws / "agents"
    root_agents.mkdir()
    (root_agents / "shared.md").write_text(
        "---\ndescription: from the root\n---\nroot\n", encoding="utf-8"
    )
    mgr = SessionManager(workspace=ws, provider=_Provider())
    mgr.save_subagent("shared", {**BODY, "scope": "project", "description": "from .reeve"})
    assert resolve_agents(load_config(ws))["shared"].description == "from .reeve"
    # The listing reports the winning file, not the shadowed one.
    row = {r["name"]: r for r in mgr.list_subagents()}["shared"]
    assert row["source"] == "project"
    assert row["file"] == str(ws / ".reeve" / "agents" / "shared.md")


# -- validation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "Bad Name", "../escape", "team/../escape", "/absolute", "UPPER", "a b", "x/" + "y" * 96],
)
def test_invalid_names_are_rejected(state, ws, name):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    with pytest.raises(SubagentError):
        mgr.save_subagent(name, BODY)


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"description": "  "}, "description"),
        ({"prompt": ""}, "prompt"),
        ({"scope": "elsewhere"}, "scope"),
        ({"color": "green"}, "#rrggbb"),
        ({"model": "not a model"}, "provider/model"),
        ({"tools": {"preset": "sometimes"}}, "tools.preset"),
        ({"tools": {"preset": "custom", "allow": ["bogus"]}}, "unknown tool group"),
    ],
)
def test_invalid_bodies_are_rejected(state, ws, patch, message):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    with pytest.raises(SubagentError) as error:
        mgr.save_subagent("x", {**BODY, **patch})
    assert message in str(error.value)


# -- preset ↔ ruleset helpers -----------------------------------------------------------


def test_preset_rules_and_summary_round_trip():
    assert preset_rules("all") == []
    denied = preset_rules("all", None, ["shell"])
    assert summarize_rules(denied) == {"preset": "all", "allow": [], "deny": ["shell"]}

    readonly = preset_rules("readonly")
    assert summarize_rules(readonly) == {
        "preset": "readonly",
        "allow": list(READONLY_GROUPS),
        "deny": [],
    }

    custom = preset_rules("custom", ["read", "shell"])
    assert ("*", "*", DENY) in custom
    assert any(rule == ("run_shell", "*", ALLOW) for rule in custom)
    assert summarize_rules(custom) == {
        "preset": "custom",
        "allow": ["read", "shell"],
        "deny": [],
    }

    # A deny named after an allow is written last, so it wins.
    both = preset_rules("custom", ["shell"], ["shell"])
    assert evaluate("run_shell", "ls", both) == DENY


def test_every_group_is_canonical_vocabulary():
    """The group map must speak the same vocabulary `resolve_agents` consumes: each
    permission name survives `canonical_permission` unchanged."""
    from harness.tools.task_agents import GROUP_PERMISSIONS, canonical_permission

    for group, permissions in GROUP_PERMISSIONS.items():
        for permission in permissions:
            assert canonical_permission(permission) == permission, (group, permission)


# -- reflection, routes, malformed files ------------------------------------------------


def test_routes_put_delete_and_reflection(state, ws):
    mgr = SessionManager(workspace=ws, provider=_Provider())
    client = TestClient(create_app(mgr))

    put = client.put("/v1/subagents/code-reviewer", json=BODY)
    assert put.status_code == 200 and put.json()["ok"] is True

    # Reflection (item 7): a just-saved file is visible without a restart — the route
    # re-reads the config from disk on every call.
    rows = {r["name"]: r for r in client.get("/v1/subagents").json()["agents"]}
    assert rows["code-reviewer"]["source"] == "user"
    assert rows["code-reviewer"]["file"] == str(state / "agents" / "code-reviewer.md")

    nested = client.put("/v1/subagents/team/helper", json=BODY)
    assert nested.status_code == 200
    assert (state / "agents" / "team" / "helper.md").is_file()

    bad = client.put("/v1/subagents/ok-name", json={**BODY, "prompt": ""})
    assert bad.status_code == 400
    assert "system prompt" in bad.json()["error"]

    missing = client.delete("/v1/subagents/never-saved")
    assert missing.status_code == 404
    assert "never-saved" in missing.json()["error"]

    gone = client.delete("/v1/subagents/code-reviewer")
    assert gone.status_code == 200
    names = {r["name"] for r in client.get("/v1/subagents").json()["agents"]}
    assert "code-reviewer" not in names


def test_a_malformed_file_is_skipped_without_breaking_the_listing(state, ws):
    agents_dir = state / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "broken.md").write_text("---\nthis line has no colon\n---\n", encoding="utf-8")
    (agents_dir / "fine.md").write_text(
        "---\ndescription: Fine\n---\nok\n", encoding="utf-8"
    )
    mgr = SessionManager(workspace=ws, provider=_Provider())

    names = {r["name"] for r in mgr.list_subagents()}
    assert "fine" in names
    assert "broken" not in names


# -- inject_agents ----------------------------------------------------------------------


def test_inject_agents_false_reaches_build_engine(state, ws):
    (ws / "AGENTS.md").write_text("PROJECT RULE: prefer pathlib.", encoding="utf-8")

    off = build_engine(
        agent=Agent(
            name="plain",
            title="plain",
            system_prompt="SYS",
            inject_agents=False,
        ),
        workspace=ws,
        provider=_Provider(),
    )
    on = build_engine(
        agent=Agent(name="normal", title="normal", system_prompt="SYS"),
        workspace=ws,
        provider=_Provider(),
    )
    try:
        assert "prefer pathlib" not in off.messages[0]["content"]
        assert "prefer pathlib" in on.messages[0]["content"]
    finally:
        off.executor.close()
        on.executor.close()
