"""SubagentCatalogMixin: the subagent definitions Settings shows and edits, and @-mentions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from harness.config.config import load_config
from harness.safety.secrets import state_dir

# A project-scoped definition lives in the workspace's own state folder. Its `agents/` child
# merges AFTER `<workspace>/agents`, so a file written by the GUI is the effective one.
_PROJECT_STATE_DIR = ".reeve"


def _scope(value: Any) -> str:
    return str(value or "user").strip().lower()


class SubagentCatalogMixin:
    def _agent_config(self, workspace: Optional[str]):
        """(workspace, config) for the per-workspace layer, trusted when the person trusts it."""
        folder = self.resolve_workspace(workspace)
        trusted = bool(folder and self.workspace_trust.is_trusted(folder))
        return folder, load_config(folder, workspace_trusted=trusted)

    def list_subagents(self, workspace: Optional[str] = None) -> list[dict[str, Any]]:
        """The `task` agent definitions as data for the GUI.

        It uses the very resolver `spawn_subagent` uses, so the picker cannot offer an agent the
        spawn would refuse, and the task card can read an agent's `color` without re-deriving
        the config. `workspace` selects the per-workspace config layer, and None reads the
        manager's default. `description` is the sentence the task listing shows, with a
        manual-only fallback for an agent that wrote none, and `default` marks the config's
        `default_agent` so a client can preselect it; the default is listed first.

        Settings ▸ Subagents adds, all additive: `source` (`builtin`, `user` or `project`),
        `file` (the markdown path, null for a built-in), `overrides` (a built-in name that a
        markdown file shadows), the editable raw fields (`prompt`, `tools` as
        `{preset, allow, deny}`, `inject_agents`) and `tool_count` (null means "All tools"). A
        file just saved shows up on the very next call, because the config is read from disk on
        every request and no resolution is cached.
        """
        from harness.orchestration.subagent_files import editable_fields, scan_agent_files
        from harness.tools.task_agents import preset_tool_count, resolve_agents, summarize_rules

        folder, config = self._agent_config(workspace)
        agents = resolve_agents(config)
        default = str(getattr(config, "default_agent", "") or "").strip()
        # The loader's own precedence: user markdown, then `<workspace>/agents`, then the
        # workspace state folder, and the later wins.
        sources: list[tuple[Path, str]] = [(state_dir(), "user")]
        if folder:
            sources += [(Path(folder), "project"), (Path(folder) / _PROJECT_STATE_DIR, "project")]
        files = scan_agent_files(sources)

        def default_first(agent: Any) -> tuple[int, str]:
            return (0 if default and agent.name == default else 1, agent.name)

        rows: list[dict[str, Any]] = []
        for agent in sorted(agents.values(), key=default_first):
            raw = files.get(agent.name)
            tools = summarize_rules(agent.permission)
            prompt = agent.prompt
            inject_agents = getattr(agent, "inject_agents", True)
            if raw:
                fields = editable_fields(raw["entry"])
                tools, prompt, inject_agents = fields["tools"], fields["prompt"], fields["inject_agents"]
            rows.append(
                {
                    "name": agent.name,
                    "description": agent.describes(),
                    "mode": agent.mode,
                    "model": agent.model,
                    "color": agent.color,
                    "steps": agent.steps,
                    "hidden": agent.hidden,
                    "native": agent.native,
                    # "" means in-process; "codex" or "claude-code" is an external CLI agent whose
                    # `model` is the CLI's OWN model and not one of our provider ids.
                    "provider": agent.provider,
                    "default": bool(default and agent.name == default),
                    "source": raw["scope"] if raw else "builtin",
                    "file": raw["file"] if raw else None,
                    "overrides": bool(raw and agent.native),
                    "prompt": prompt,
                    "tools": tools,
                    "inject_agents": inject_agents,
                    # None means every tool (the badge reads "All tools"); a number is the
                    # concrete tools the allowed groups carry.
                    "tool_count": None if tools.get("preset") == "all" else preset_tool_count(list(tools.get("allow") or [])),
                    # Off in Settings: the row stays, so the switch can flip back, but nothing
                    # may delegate to it.
                    "disabled": bool(getattr(agent, "disabled", False)),
                }
            )
        return rows

    # -- the markdown definitions Settings writes ----------------------------------------

    def subagent_root(self, scope: str, workspace: Optional[str] = None) -> Path:
        """The scan root one scope writes into. `user` is the state folder itself, whose
        `agents/` child `load_config` scans beside `config.toml`. `project` is the workspace's
        state folder. An unknown scope, or a project write with no workspace, is a 400."""
        from harness.orchestration.subagent_files import SubagentError

        key = _scope(scope)
        if key == "user":
            return state_dir()
        if key == "project":
            folder = self.resolve_workspace(workspace)
            if not folder:
                raise SubagentError("open a folder to save a project-scoped subagent")
            return Path(folder) / _PROJECT_STATE_DIR
        raise SubagentError("scope must be 'user' or 'project'")

    def save_subagent(self, name: str, payload: Any, workspace: Optional[str] = None) -> dict[str, Any]:
        """Create or replace one markdown definition (`PUT /v1/subagents/{name}`). The body is
        the GUI's shape; `compose_agent_file` validates it and renders the subset of markdown
        `load_config` parses. The write is atomic, so the next read sees a complete file."""
        from harness.orchestration.subagent_files import compose_agent_file, validate_name, write_agent_file

        body = payload if isinstance(payload, dict) else {}
        validated = validate_name(name)
        scope = _scope(body.get("scope"))
        path, text = compose_agent_file(self.subagent_root(scope, workspace), validated, body)
        write_agent_file(path, text)
        return {"ok": True, "name": validated, "scope": scope, "file": str(path)}

    def delete_subagent(self, name: str, scope: str = "user", workspace: Optional[str] = None) -> dict[str, Any]:
        """Delete one markdown definition (`DELETE /v1/subagents/{name}?scope=…`). Deleting an
        override removes the file only, and the built-in (or the other scope's file) resolves
        again on the next read. A missing file is a 404 and never an error in the sidecar."""
        from harness.orchestration.subagent_files import SubagentError, agent_file, validate_name

        validated = validate_name(name)
        key = _scope(scope)
        path = agent_file(self.subagent_root(key, workspace), validated)
        if not path.is_file():
            raise SubagentError(f"no {key}-scoped subagent file for {validated!r}", status=404)
        path.unlink()
        return {"ok": True, "name": validated, "scope": key}

    def _builtin_agent(self, name: str, workspace: Optional[str], *, not_builtin: str, change: str) -> str:
        """The validated name of a BUILT-IN subagent that no config or markdown file defines, or
        a `SubagentError` saying what to do instead. A name that does not resolve, one that is
        not built in, or one that the config defines owns its settings through its own file."""
        from harness.orchestration.subagent_files import SubagentError, validate_name
        from harness.tools.task_agents import _config_agents, resolve_agents

        validated = validate_name(name)
        _folder, config = self._agent_config(workspace)
        agent = resolve_agents(config).get(validated)
        if agent is None:
            raise SubagentError(f"unknown subagent {validated!r}")
        if not agent.native:
            raise SubagentError(not_builtin)
        if validated in _config_agents(config):
            raise SubagentError(
                f"{validated!r} is defined by your config or a markdown file; edit that definition to {change}"
            )
        return validated

    def _subagent_row(self, name: str, workspace: Optional[str]) -> Optional[dict[str, Any]]:
        return next((row for row in self.list_subagents(workspace) if row["name"] == name), None)

    def set_builtin_model(self, name: str, payload: Any, workspace: Optional[str] = None) -> dict[str, Any]:
        """Point a BUILT-IN subagent at another model (`PUT /v1/subagents/{name}/model`).

        The one editable field of a built-in. The value is kept in the state folder's
        `subagent_models.json` and not as a markdown file, so the shipped description, prompt and
        ruleset stay untouched (an override file would replace all of them). `model: null` or
        `""` clears the pin and the built-in inherits the session's model again. The answer is
        the effective agent row, in the shape `GET /v1/subagents` carries.
        """
        from harness.orchestration.subagent_files import validate_model
        from harness.orchestration.subagent_models import set_model_override

        validated = self._builtin_agent(
            name,
            workspace,
            not_builtin="model overrides are only available for built-in subagents; "
            "edit the definition to change its model",
            change="change its model",
        )
        body = payload if isinstance(payload, dict) else {}
        set_model_override(validated, validate_model(body.get("model")) or None)
        return {"ok": True, "agent": self._subagent_row(validated, workspace)}

    def set_subagent_enabled(self, name: str, payload: Any, workspace: Optional[str] = None) -> dict[str, Any]:
        """Flip a BUILT-IN subagent's enable switch (`PUT /v1/subagents/{name}/enabled`).

        Off means the row stays in the list, so the switch can flip back, while `describe_task`,
        the `task` tool and `spawn_subagent` refuse to delegate to it. The value is kept in the
        state folder's `disabled_subagents.json` and not as a markdown override, because a file
        would replace the shipped definition and hiding the row would hide this very switch.
        """
        from harness.orchestration.subagent_flags import set_subagent_disabled

        validated = self._builtin_agent(
            name,
            workspace,
            not_builtin="the enable switch is only available for built-in subagents; "
            "edit or delete the definition to disable it",
            change="disable it",
        )
        body = payload if isinstance(payload, dict) else {}
        set_subagent_disabled(validated, not bool(body.get("enabled", True)))
        return {"ok": True, "agent": self._subagent_row(validated, workspace)}

    # -- depth and @-mentions ------------------------------------------------------------

    def session_depth(self, session_id: str) -> int:
        """How many `kind="subagent"` hops separate a session from its root.

        The parent link is shared with forks, which are visible roots with lineage but not
        containment, so the walk follows it only through a `kind="subagent"` row: a fork has
        depth 0, a child 1 and a grandchild 2. A missing or deleted parent ends the walk, and a
        `seen` set bounds a corrupted cycle instead of hanging a turn.

        A background workflow's runner is not a level. Its agents are the parent's delegation, so
        they sit at depth 1 exactly as they would in a foreground run, and the runner itself at 0.
        """
        from harness.tools.workflow import WORKFLOW_RUNNER

        depth = 0
        seen: set[str] = set()
        current = session_id
        while current and current not in seen:
            seen.add(current)
            record = self.session_store.load(current)
            if record is None or record.kind != "subagent" or not record.parent_session:
                break
            if (record.agent or "") != WORKFLOW_RUNNER:
                depth += 1
            current = record.parent_session
        return depth

    def mention_instructions(self, workspace: Optional[str], caller: str, mentions: Any) -> dict[str, Any]:
        """Validate @-mentions and build the instruction text they add for the model.

        The composer sends the literal agent names it recognised. Every name must be an agent
        the mention picker would offer (not hidden, not a primary mode), and each yields the
        instruction appended to the model-facing text. The `denied` clause is resolved against
        the CALLER's own ruleset: the caller may not be allowed to spawn this agent, and the
        mention is the person's authority overriding that.

        Answers `{"ok": True, "text": …}` or `{"ok": False, "error": …}`. The caller keeps the
        person's literal line as `_display`, so the transcript never shows the instruction.
        """
        from harness.tools.task_agents import (
            DENY,
            AgentInfo,
            caller_task_rules,
            evaluate,
            mention_instruction,
            mentionable_agents,
            resolve_agents,
        )

        names = [str(mention).strip() for mention in (mentions or []) if str(mention).strip()]
        if not names:
            return {"ok": False, "error": "No agent mentioned."}
        _folder, config = self._agent_config(workspace)
        agents = resolve_agents(config)
        allowed = {agent.name for agent in mentionable_agents(agents)}
        rules = caller_task_rules(agents.get(caller) or AgentInfo(name=caller), config)
        parts: list[str] = []
        for name in names:
            if name not in allowed:
                return {"ok": False, "error": f"Unknown agent type: {name} is not a valid agent type"}
            parts.append(mention_instruction(name, evaluate("task", name, rules) == DENY))
        return {"ok": True, "text": "".join(parts)}
