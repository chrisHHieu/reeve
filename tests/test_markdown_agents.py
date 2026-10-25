"""F3 — markdown agents, `{file:}`/`{env:}` substitution, and the global `tools` map.

Markdown agents (`{agent,agents}/**/*.md` per config
directory, name from the relative path, body → prompt, markdown merged after the JSON of
the same directory) (nested folders become `team/helper`)
and (`{file:…}`/`{env:…}` in config strings).
folds the top-level `tools` map into the global permission every agent inherits.

One deliberate difference, recorded in the ledger (2.13): a malformed frontmatter is
REPORTED and that file skipped, where a strict loader aborts the whole config load
 — one broken file must not cost the person every other agent.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from harness.config.config import load_config
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.tools.task_agents import resolve_agents


class _Provider(ProviderClient):
    def __init__(self, turns=()):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason="stop")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load(tmp_path: Path, *, workspace: str | Path | None = None, global_text: str = ""):
    g = tmp_path / "config.toml"
    g.write_text(global_text, encoding="utf-8")
    return load_config(workspace, global_path=g)


# -- markdown agents: name, frontmatter, body -----------------------------------------


def test_a_markdown_agent_loads_with_its_frontmatter_and_body(tmp_path):
    """: the frontmatter keys are the agent definition, the trimmed
    markdown BODY is the prompt that replaces the provider base prompt."""
    _write(
        tmp_path / "agents" / "reviewer.md",
        "---\n"
        "description: Reviews a diff\n"
        "mode: subagent\n"
        "model: deepseek:chat\n"
        "variant: high\n"
        "temperature: 0.3\n"
        "top_p: 0.9\n"
        "steps: 7\n"
        "hidden: true\n"
        'color: "#ff8800"\n'
        "permission:\n"
        "  edit: deny\n"
        "  read:\n"
        "    \"*.env\": ask\n"
        "---\n"
        "\n"
        "You review diffs carefully.\n",
    )

    agent = resolve_agents(_load(tmp_path))["reviewer"]

    assert agent.description == "Reviews a diff"
    assert agent.mode == "subagent"
    assert agent.model == "deepseek:chat"
    assert agent.variant == "high"
    assert agent.temperature == pytest.approx(0.3)
    assert agent.top_p == pytest.approx(0.9)
    assert agent.steps == 7
    assert agent.hidden is True
    assert agent.color == "#ff8800"
    assert ("edit", "*", "deny") in agent.permission
    assert ("read", "*.env", "ask") in agent.permission
    assert agent.prompt == "You review diffs carefully."


def test_a_nested_markdown_path_becomes_a_slash_name(tmp_path):
    """: the name is the path relative to the scanned
    directory, prefix and `.md` stripped — nested folders keep the slash."""
    _write(
        tmp_path / "agent" / "team" / "helper.md",
        "---\ndescription: via agent/\n---\nbody\n",
    )
    _write(
        tmp_path / "agents" / "team" / "coach.md",
        "---\ndescription: via agents/\n---\nbody\n",
    )

    agents = resolve_agents(_load(tmp_path))

    assert agents["team/helper"].description == "via agent/"
    assert agents["team/coach"].description == "via agents/"


def test_the_markdown_body_wins_over_a_frontmatter_prompt(tmp_path):
    _write(
        tmp_path / "agents" / "x.md",
        "---\nprompt: the declared one\n---\nthe body\n",
    )
    assert resolve_agents(_load(tmp_path))["x"].prompt == "the body"


def test_disable_removes_a_builtin_and_other_markdown_agents_still_load(tmp_path):
    """`disable: true` removes a built-in too; the file's other
    neighbors are unaffected."""
    _write(tmp_path / "agents" / "explore.md", "---\ndisable: true\n---\n")
    _write(
        tmp_path / "agents" / "keeper.md",
        "---\ndescription: Keeper\n---\nKeep.\n",
    )

    agents = resolve_agents(_load(tmp_path))

    assert "explore" not in agents
    assert agents["keeper"].description == "Keeper"


def test_a_malformed_frontmatter_is_reported_and_skipped(tmp_path, caplog):
    """Our stance, recorded deliberately: a strict loader aborts the whole load on
    a bad file; we log and skip it, so every other agent still exists."""
    _write(
        tmp_path / "agents" / "broken.md",
        "---\nthis line has no colon\n---\nbody\n",
    )
    _write(
        tmp_path / "agents" / "fine.md",
        "---\ndescription: Fine\n---\nok\n",
    )

    with caplog.at_level(logging.WARNING, logger="harness.config.config"):
        agents = resolve_agents(_load(tmp_path))

    assert "broken" not in agents
    assert agents["fine"].description == "Fine"
    assert any("broken.md" in record.getMessage() for record in caplog.records)


def test_markdown_merges_over_the_toml_table_per_key(tmp_path):
    """Markdown merges AFTER the TOML of the same directory and wins per key; a key only
    the TOML wrote survives (`mergeDeep`)."""
    _write(
        tmp_path / "agents" / "reviewer.md",
        "---\ndescription: from markdown\n---\nprompt body\n",
    )

    agent = resolve_agents(
        _load(
            tmp_path,
            global_text=(
                '[agents.reviewer]\n'
                'description = "from TOML"\n'
                'model = "deepseek:chat"\n'
            ),
        )
    )["reviewer"]

    assert agent.description == "from markdown"
    assert agent.model == "deepseek:chat"
    assert agent.prompt == "prompt body"


def test_the_workspace_root_and_the_config_agents_directories_both_load(tmp_path):
    """Both scan roots are relative to the workspace, and the config file's own directory
    merges last — a same-name file there beats the workspace root's."""
    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "config.toml").write_text("")
    _write(ws / "agents" / "rootling.md", "---\ndescription: from the root\n---\nroot\n")
    _write(
        ws / ".reeve" / "agents" / "nested.md",
        "---\ndescription: from .reeve\n---\nnested\n",
    )
    _write(ws / "agents" / "both.md", "---\ndescription: root copy\n---\nroot\n")
    _write(
        ws / ".reeve" / "agents" / "both.md",
        "---\ndescription: config-dir copy\n---\nws\n",
    )

    agents = resolve_agents(load_config(ws, global_path=tmp_path / "nope.toml"))

    assert agents["rootling"].description == "from the root"
    assert agents["nested"].description == "from .reeve"
    assert agents["both"].description == "config-dir copy"
    assert agents["both"].prompt == "ws"


def test_a_markdown_agent_appears_in_the_subagents_listing(tmp_path, monkeypatch):
    """`GET /v1/subagents` (the manager's `list_subagents`) resolves the same config, so a
    markdown definition is served with its description/mode/model."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(
        ws / "agents" / "reviewer.md",
        "---\n"
        "description: Reviews a diff\n"
        "mode: subagent\n"
        "model: deepseek:chat\n"
        "---\n"
        "You review diffs.\n",
    )
    monkeypatch.setattr(
        "harness.config.config.global_config_path", lambda: tmp_path / "config.toml"
    )
    mgr = SessionManager(workspace=ws, provider=_Provider())

    row = next(r for r in mgr.list_subagents(str(ws)) if r["name"] == "reviewer")

    assert row["description"] == "Reviews a diff"
    assert row["mode"] == "subagent"
    assert row["model"] == "deepseek:chat"
    assert row["native"] is False


# -- `{file:}` / `{env:}` substitution -------------------------------------------------


def test_file_substitution_reads_relative_to_the_config_file(tmp_path):
    _write(tmp_path / "prompts" / "librarian.md", "  You index the archive.  \n")

    agent = resolve_agents(
        _load(
            tmp_path,
            global_text='[agents.librarian]\nprompt = "{file:prompts/librarian.md}"\n',
        )
    )["librarian"]

    assert agent.prompt == "You index the archive."  # the file content is trimmed


def test_env_substitution_and_a_missing_reference_stays_literal(
    tmp_path, monkeypatch, caplog
):
    """A strict loader substitutes a missing env var with the empty string; this one leaves the
    literal and warns once — a typo must be visible, not silently blank."""
    monkeypatch.setenv("REEVE_F3_MODEL", "deepseek:chat")

    with caplog.at_level(logging.WARNING, logger="harness.config.config"):
        cfg = _load(
            tmp_path,
            global_text=(
                '[agents.tuned]\nmodel = "{env:REEVE_F3_MODEL}"\n'
                'description = "{env:REEVE_F3_ABSENT}"\n'
            ),
        )

    agent = resolve_agents(cfg)["tuned"]
    assert agent.model == "deepseek:chat"
    assert agent.description == "{env:REEVE_F3_ABSENT}"
    assert any("REEVE_F3_ABSENT" in record.getMessage() for record in caplog.records)


def test_a_missing_file_reference_stays_literal(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="harness.config.config"):
        cfg = _load(
            tmp_path, global_text='[agents.x]\nprompt = "{file:ghost.md}"\n'
        )

    assert resolve_agents(cfg)["x"].prompt == "{file:ghost.md}"
    assert any("ghost.md" in record.getMessage() for record in caplog.records)


def test_substitution_reaches_nested_values(tmp_path, monkeypatch):
    """The values live inside the agent tables too, so the walk is recursive."""
    monkeypatch.setenv("REEVE_F3_COLOR", "#00ff00")
    cfg = _load(
        tmp_path,
        global_text=(
            "[agents.tinted]\ncolor = \"{env:REEVE_F3_COLOR}\"\n"
            "[agents.tinted.options]\nlabel = \"{env:REEVE_F3_COLOR}\"\n"
        ),
    )

    agent = resolve_agents(cfg)["tinted"]
    assert agent.color == "#00ff00"
    assert agent.options["label"] == "#00ff00"


# -- the global `tools` map ------------------------------------------------------------


def test_global_tools_deny_removes_the_tool_from_a_childs_registry(tmp_path, monkeypatch):
    """ +: the top-level `tools` map becomes a
    permission rule on EVERY agent, so `write_file = false` removes the write tool from a
    spawned child's registry (the alias fold reaches `edit`)."""
    from harness.config.config import Config

    config = Config(tools={"write_file": False})
    monkeypatch.setattr("harness.compose.assembly.load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = SessionManager(workspace=tmp_path, provider=_Provider([_text("the report")]))
    parent = SessionRecord(
        session_id="parent-tools",
        workspace=str(tmp_path),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "the parent's question"}],
        title="a conversation",
        agent="reeve",
    )
    mgr.session_store.save(parent)
    parent_engine = mgr.get_engine(parent.session_id)
    # The map is a CHILD-side rule: the session's own registry keeps the tool.
    assert "write_file" in parent_engine.registry.names()

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
        )
    )

    names = set(mgr._engines[out["task_id"]].registry.names())
    assert "write_file" not in names
    assert "read_file" in names  # only the named family is denied


def test_the_written_permission_table_merges_over_the_global_tools_map(tmp_path):
    """The merge order: the `tools` map is the base,
    the top-level `permission` table merges over it, so a specific `permission` entry wins
    over the broad boolean."""
    from harness.config.config import Config
    from harness.tools.task_agents import evaluate

    config = Config(tools={"write_file": False}, permission={"edit": "allow"})
    agents = resolve_agents(config)
    assert evaluate("write_file", "a.py", agents["general"].permission) == "allow"


def test_the_global_tools_map_loads_from_toml(tmp_path):
    cfg = _load(tmp_path, global_text="[tools]\nwrite_file = false\nweb_search = true\n")
    assert cfg.tools == {"write_file": False, "web_search": True}
