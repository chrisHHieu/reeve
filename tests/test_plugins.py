"""Plugins, phase P1: a folder-loaded plugin's skills, agents and commands.

The fixtures under tests/fixtures/plugins are invented plugins in the Claude Code plugin layout.
What is pinned is behaviour a person or the model sees: the names components get, what a
plugin may not do (grant a permission, run code unconsented), and that a broken plugin
costs only itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.extensions.plugins import resolve_plugins
from harness.extensions.plugins.compat import TOOL_ALIASES, claude_tool, reeve_tool
from harness.extensions.plugins.manifest import read_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"
RELEASE = FIXTURES / "release-helper"
SINGLE = FIXTURES / "single-skill"


def _plugin(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


# -- the manifest -------------------------------------------------------------------------


def test_a_claude_code_plugin_reads_with_its_components_and_says_what_it_skipped():
    manifest = read_manifest(RELEASE)
    assert manifest.errors == []
    assert (manifest.name, manifest.version) == ("release-helper", "0.3.0")
    assert manifest.manifest_path == ".claude-plugin/plugin.json"
    assert [p.name for p in manifest.skill_dirs] == ["skills"]
    assert sorted(p.name for p in manifest.command_files) == ["draft.md", "ship.md"]
    assert sorted(f.name for f, _base in manifest.agent_files) == ["release-reviewer.md", "security.md"]
    joined = " | ".join(manifest.skipped)
    # Code-running parts are not the manifest's question (they are `plugins.executable`'s).
    assert "hooks" not in joined
    assert "outputStyles: not supported" in joined and "output-styles/: not supported" in joined


def test_a_folder_with_no_manifest_is_named_by_the_folder_and_a_root_skill_is_one_skill():
    manifest = read_manifest(SINGLE)
    assert (manifest.name, manifest.root_skill, manifest.errors) == ("single-skill", True, [])


@pytest.mark.parametrize("bad", ["Release Helper", "release_helper", "a:b", "tools@acme", "../x", ""])
def test_a_name_that_is_not_kebab_case_fails_the_plugin(tmp_path, bad):
    root = _plugin(tmp_path, "p", {".claude-plugin/plugin.json": json.dumps({"name": bad})})
    assert read_manifest(root).errors


def test_a_bad_homepage_fails_the_plugin_and_an_unknown_field_is_only_noted(tmp_path):
    bad = _plugin(tmp_path, "bad", {".claude-plugin/plugin.json": json.dumps({"name": "bad", "homepage": "not a url"})})
    odd = _plugin(tmp_path, "odd", {".claude-plugin/plugin.json": json.dumps({"name": "odd", "flavour": 1})})
    assert "is not a URL" in read_manifest(bad).errors[0]
    manifest = read_manifest(odd)
    assert manifest.errors == [] and "flavour: unknown field, ignored" in manifest.skipped


def test_component_paths_must_stay_inside_the_plugin(tmp_path):
    (tmp_path / "outside" / "stolen").mkdir(parents=True)
    (tmp_path / "outside" / "stolen" / "SKILL.md").write_text("---\nname: stolen\n---\nx\n")
    root = _plugin(
        tmp_path,
        "leaky",
        {
            ".claude-plugin/plugin.json": json.dumps(
                {"name": "leaky", "skills": ["./../outside", "extra", "./missing"]}
            )
        },
    )
    manifest = read_manifest(root)
    assert manifest.skill_dirs == []
    joined = " | ".join(manifest.skipped)
    assert "./../outside leaves the plugin folder" in joined
    assert "extra must start with ./" in joined
    assert "./missing does not exist" in joined


def test_the_reeve_manifest_wins_over_the_claude_one(tmp_path):
    root = _plugin(
        tmp_path,
        "both",
        {
            ".reeve-plugin/plugin.json": json.dumps({"name": "ours", "version": "2"}),
            ".claude-plugin/plugin.json": json.dumps({"name": "theirs"}),
        },
    )
    manifest = read_manifest(root)
    assert (manifest.name, manifest.version) == ("ours", "2")
    assert any("ignored" in s and ".claude-plugin" in s for s in manifest.skipped)


# -- the resolved set ---------------------------------------------------------------------


def test_a_broken_plugin_costs_only_itself(tmp_path):
    broken = _plugin(tmp_path, "broken", {".claude-plugin/plugin.json": "{not json"})
    plugins = resolve_plugins([broken, RELEASE, SINGLE])
    assert [p.id for p in plugins.plugins] == ["release-helper@inline", "single-skill@inline"]
    assert plugins.errors and plugins.errors[0]["id"] == "broken@inline"
    info = plugins.plugins[0]
    assert info.components == {"skills": 1, "commands": 2, "agents": 2}


def test_two_plugins_with_one_name_load_once(tmp_path):
    copy = _plugin(tmp_path, "copy", {".claude-plugin/plugin.json": json.dumps({"name": "release-helper"})})
    plugins = resolve_plugins([RELEASE, copy])
    assert [p.id for p in plugins.plugins] == ["release-helper@inline"]
    assert "already loaded" in plugins.errors[0]["error"]


def test_commands_are_named_under_the_plugin():
    names = sorted(c.name for c in resolve_plugins([RELEASE]).commands)
    assert names == ["release-helper:notes:draft", "release-helper:ship"]


# -- skills --------------------------------------------------------------------------------


def test_plugin_skills_load_prefixed_under_the_person_s_own(tmp_path, monkeypatch):
    from harness.safety.secrets import state_dir
    from harness.extensions.skills.base import SkillLoader
    from harness.extensions.skills.store import loader_roots

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", os.pathsep.join([str(RELEASE), str(SINGLE)]))
    own = state_dir() / "skills" / "changelog"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("---\nname: changelog\ndescription: mine\n---\nmine\n")

    loader = SkillLoader(loader_roots(tmp_path))

    assert {"changelog", "release-helper:changelog", "single-skill:tidy"} <= set(loader.names())
    assert loader.get("changelog").description == "mine"
    body = loader.get("release-helper:changelog").instructions
    assert "${CLAUDE_PLUGIN_ROOT}" not in body
    assert f"{RELEASE.resolve().as_posix()}/templates/entry.md" in body


def test_a_plugin_skill_is_on_the_session_menu(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    manager = SessionManager(workspace=tmp_path)
    assert "release-helper:changelog" in manager.effective_skill_names("s1", tmp_path)


# -- agents ---------------------------------------------------------------------------------


def _agents(tmp_path, monkeypatch, *, config: str = ""):
    from harness.config.config import load_config
    from harness.safety.secrets import state_dir
    from harness.tools.task_agents import resolve_agents

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    if config:
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / "config.toml").write_text(config, encoding="utf-8")
    return resolve_agents(load_config(tmp_path))


def test_a_plugin_agent_joins_the_roster_under_its_plugin(tmp_path, monkeypatch):
    agents = _agents(tmp_path, monkeypatch)
    reviewer = agents["release-helper:release-reviewer"]
    assert reviewer.description == "Reviews a release branch before it ships."
    assert reviewer.mode == "subagent"
    assert reviewer.prompt.startswith("You review release branches")
    # `model: sonnet` means "the session's model", not a vendor id this person lacks.
    assert reviewer.model == ""
    # `agents/review/security.md` with `name: audit`: the name replaces the last part.
    assert "release-helper:review:audit" in agents


def test_a_plugin_agent_s_tool_list_only_narrows(tmp_path, monkeypatch):
    """A Claude Code `tools` allowlist denies the rest, and a listed tool keeps
    the verdict the session already had — here the person asks before every shell."""
    from harness.tools.task_agents import evaluate

    agents = _agents(tmp_path, monkeypatch, config='[permission]\nrun_shell = "ask"\n')
    reviewer = agents["release-helper:release-reviewer"].permission
    assert evaluate("read_file", "a.py", reviewer) == "allow"
    assert evaluate("write_file", "a.py", reviewer) == "deny"
    audit = agents["release-helper:review:audit"].permission
    assert evaluate("run_shell", "ls", audit) == "deny"
    assert evaluate("read_file", "a.py", audit) == "allow"


def test_a_plugin_agent_may_not_raise_what_the_session_asks(tmp_path, monkeypatch):
    from harness.tools.task_agents import evaluate

    grep_ask = _agents(tmp_path, monkeypatch, config='[permission]\ngrep = "ask"\n')
    assert evaluate("grep", "x", grep_ask["release-helper:release-reviewer"].permission) == "ask"


def test_what_a_plugin_agent_may_not_set_is_reported(tmp_path, monkeypatch):
    plugins = resolve_plugins([RELEASE])
    joined = " | ".join(plugins.plugins[0].skipped)
    assert "permissionMode: a plugin agent may not set it" in joined
    assert "tool NotebookEdit: no Reeve counterpart" in joined
    # Its `maxTurns: 5` is the agent's step cap now, not a loss.
    assert "maxTurns" not in joined
    assert _agents(tmp_path, monkeypatch)["release-helper:release-reviewer"].steps == 5


def test_the_tool_alias_table_reads_both_ways():
    for claude, reeve in TOOL_ALIASES.items():
        assert reeve_tool(claude) == reeve
        if reeve is not None and claude not in ("Task",):
            assert claude_tool(reeve) == claude
    assert reeve_tool("mcp__plugin_a_b__c") == "mcp__plugin_a_b__c"


# -- slash commands -------------------------------------------------------------------------


def _command(folder: Path, name: str, body: str, **front: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    header = "".join(f"{k}: {v}\n" for k, v in front.items())
    (folder / f"{name}.md").write_text(f"---\n{header}---\n{body}\n", encoding="utf-8")


def test_commands_come_from_plugins_the_person_and_the_project_highest_wins(tmp_path, monkeypatch):
    from harness.extensions.commands import load_commands
    from harness.safety.secrets import state_dir

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    _command(state_dir() / "commands", "standup", "mine", description="My standup")
    _command(ws / ".reeve" / "commands", "standup", "the project's", description="Project standup")
    _command(ws / ".claude" / "commands", "review", "compat review")
    _command(ws / ".reeve" / "commands" / "db", "migrate", "run $1")

    found = load_commands(ws)

    assert found["standup"].body == "the project's" and found["standup"].source == "project"
    assert found["review"].source == "compat"
    assert found["db:migrate"].body == "run $1"
    ship = found["release-helper:ship"]
    assert (ship.source, ship.plugin, ship.argument_hint) == ("plugin", "release-helper@inline", "<version>")
    # Shown, never applied: a plugin may not grant a tool.
    assert ship.allowed_tools == ("Bash(git tag:*)",)


def test_the_command_list_carries_no_bodies_and_one_command_does(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    client = TestClient(create_app(SessionManager(workspace=tmp_path)))

    listing = client.get("/v1/commands").json()["commands"]
    ship = next(c for c in listing if c["name"] == "release-helper:ship")
    assert "body" not in ship and ship["argument_hint"] == "<version>"
    one = client.get("/v1/commands/release-helper:ship").json()
    assert one["body"].startswith("Prepare release $1")
    assert client.get("/v1/commands/nope").status_code == 404


def test_the_plugins_route_lists_what_loaded_and_what_failed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app
    from harness.interface.server.manager import SessionManager

    broken = _plugin(tmp_path, "broken", {".claude-plugin/plugin.json": "{"})
    monkeypatch.setenv("REEVE_PLUGIN_DIRS", os.pathsep.join([str(RELEASE), str(broken)]))
    body = TestClient(create_app(SessionManager(workspace=tmp_path))).get("/v1/plugins").json()
    assert [p["id"] for p in body["plugins"]] == ["release-helper@inline"]
    assert body["plugins"][0]["components"] == {"skills": 1, "commands": 2, "agents": 2}
    assert body["errors"][0]["id"] == "broken@inline"


# -- where a person sees them -----------------------------------------------------------------


def test_plugin_skills_are_listed_read_only_in_settings(tmp_path, monkeypatch):
    from harness.extensions.skills.store import SkillStore

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    rows = {r["name"]: r for r in SkillStore().rows(tmp_path)}
    row = rows["release-helper:changelog"]
    assert (row["scope"], row["compat"], row["plugin"]) == ("plugin", True, "release-helper@inline")


def test_a_session_logs_the_plugins_it_runs_with(tmp_path, monkeypatch):
    """A plugin's skills and agents reach the model, so the session log names
    the plugins behind them."""
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    manager = SessionManager(workspace=tmp_path)
    manager.get_engine("with-plugins", workspace=str(tmp_path))
    rows = [r for r in manager.session_log("with-plugins").read() if r.get("type") == "plugins/active"]
    assert len(rows) == 1
    assert rows[0]["plugins"][0]["id"] == "release-helper@inline"
    assert rows[0]["plugins"][0]["components"]["agents"] == 2


def test_no_plugins_no_row(tmp_path):
    from harness.interface.server.manager import SessionManager

    manager = SessionManager(workspace=tmp_path)
    manager.get_engine("plain", workspace=str(tmp_path))
    assert not [r for r in manager.session_log("plain").read() if r.get("type") == "plugins/active"]


@pytest.mark.parametrize(
    "text, fields, body, problem",
    [
        ("---\n---\nrun it", {}, "run it", ""),
        ("---\r\ndescription: x\r\n---\r\nbody", {"description": "x"}, "body", ""),
        ("---\ndescription: x\nno close", {}, "---\ndescription: x\nno close", "unterminated frontmatter"),
        ("---\n- a list\n---\nbody", {}, "body", "frontmatter is not a mapping"),
        ("no frontmatter", {}, "no frontmatter", ""),
    ],
)
def test_frontmatter_splits_the_way_a_component_loads(text, fields, body, problem):
    from harness.extensions.plugins import split_frontmatter

    assert split_frontmatter(text) == (fields, body, problem)


# -- the command line -------------------------------------------------------------------------


def test_validate_reports_components_and_what_was_left_out(capsys):
    from harness.interface.cli import _plugin_cli

    assert _plugin_cli(["validate", str(RELEASE)]) == 0
    out = capsys.readouterr().out
    assert "plugin: release-helper 0.3.0" in out
    assert "manifest: .claude-plugin/plugin.json" in out
    assert "agents: 2" in out and "warning: outputStyles: not supported" in out
    # Its SessionEnd hook is honoured now (the session was deleted or the app is closing).
    assert "SessionEnd" not in out
    # --strict fails a plugin that has anything left out (a plugin's own CI).
    assert _plugin_cli(["validate", str(RELEASE), "--strict"]) == 1
    assert _plugin_cli(["validate", str(SINGLE), "--strict"]) == 0


def test_validate_fails_a_broken_plugin(tmp_path, capsys):
    from harness.interface.cli import _plugin_cli

    bad = _plugin(tmp_path, "bad", {".claude-plugin/plugin.json": json.dumps({"name": "Bad Name"})})
    assert _plugin_cli(["validate", str(bad)]) == 2
    assert "must be kebab-case" in capsys.readouterr().err


def test_plugin_dir_adds_folders_ahead_of_the_environment(tmp_path, monkeypatch):
    from harness.interface.cli import add_plugin_dirs
    from harness.extensions.plugins import plugin_dirs_from_env

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(SINGLE))
    add_plugin_dirs([str(RELEASE)])
    assert plugin_dirs_from_env() == [RELEASE.resolve(), SINGLE]


def test_the_skill_catalog_says_which_plugin_a_skill_came_from(tmp_path, monkeypatch):
    """Design §9.7: a plugin's text is the person's choice, but the model and a reader of
    the log should see where it came from."""
    from harness.extensions.skills.base import SkillLoader, skill_catalog_text
    from harness.extensions.skills.store import loader_roots

    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    text = skill_catalog_text(SkillLoader(loader_roots(tmp_path)))
    line = next(line for line in text.splitlines() if "release-helper:changelog" in line)
    assert line.endswith("(plugin: release-helper@inline)")
