"""Layering the config files (defaults, global, workspace) into one `Config`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from .agent_files import merge_agent_files, merge_plugin_agents
from .reading import deep_merge, read_toml
from .settings import DEFAULT_GOAL_MAX_ROUNDS, FIELDS, MAX_MAX_ROUNDS, WORKSPACE_FIELDS, Config

logger = logging.getLogger("harness.config.config")


def workspace_allowed_commands(workspace: str | Path) -> list[str]:
    """Command prefixes the repository's config asks for; advisory until the workspace is trusted."""
    value = read_toml(Path(workspace).expanduser() / ".reeve" / "config.toml").get("allowed_commands", [])
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def _goal_max_rounds(raw: Any) -> int:
    """The one value of `[goal] max_rounds`. An unusable value logs and degrades to the default,
    since a config file must not kill the load, and a huge one clamps to the ceiling."""
    if raw is None:
        return DEFAULT_GOAL_MAX_ROUNDS
    try:
        rounds = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "config: [goal] max_rounds=%r is not a number; using the default %d", raw, DEFAULT_GOAL_MAX_ROUNDS
        )
        return DEFAULT_GOAL_MAX_ROUNDS
    if rounds < 1:
        logger.warning(
            "config: [goal] max_rounds=%r must be positive; using the default %d", raw, DEFAULT_GOAL_MAX_ROUNDS
        )
        return DEFAULT_GOAL_MAX_ROUNDS
    return min(rounds, MAX_MAX_ROUNDS)


def _number(value: Any, kind: Callable[[Any], Any], clamp: Callable[[Any], Any], fallback: Any) -> Any:
    """`value` as `kind` then clamped; `fallback` when it is not a number."""
    try:
        return clamp(kind(value))
    except (TypeError, ValueError):
        return fallback


def _routes(value: Any) -> list[str]:
    """`fallback_models` as typed (a bare string, blanks, duplicates) reduced to a clean list of
    route ids. Anything unusable is dropped rather than raised."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    routes: list[str] = []
    for route in value:
        route = route.strip() if isinstance(route, str) else ""
        if route and route not in routes:
            routes.append(route)
    return routes


def _names(table: Any, key: str) -> list[str]:
    raw = table.get(key, []) if isinstance(table, dict) else []
    raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [name for name in raw if isinstance(name, str)]


def _workspace_features(person: Any, workspace: Any) -> dict[str, Any]:
    """`[features]` with a workspace's table laid over the person's: a workspace can only take away.
    Its `remove` joins the person's, and its `add` is never read. It used to replace the
    person's table whole, so a cloned repository writing `remove = []` brought back a feature the
    person had removed."""
    merged = dict(person) if isinstance(person, dict) else {}
    removed = list(dict.fromkeys([*_names(person, "remove"), *_names(workspace, "remove")]))
    if removed:
        merged["remove"] = removed
    return merged


_TABLES = ("compaction", "delegation", "context_editing", "web", "lsp", "agents", "tools", "permission", "features")


def _normalize(config: Config) -> None:
    """TOML holds whatever the person typed. Bring each knob to a usable value here, so a typo
    degrades to the default instead of raising mid-run."""
    config.subagent_steps = _number(config.subagent_steps, int, lambda n: max(0, n), 0)
    config.subagent_depth = _number(config.subagent_depth, int, lambda n: max(0, n), 1)
    config.subagent_max_parallel = _number(config.subagent_max_parallel, int, lambda n: max(0, n), 4)
    config.fallback_models = _routes(config.fallback_models)
    config.workflow_timeout = _number(config.workflow_timeout, float, lambda n: min(3600.0, max(30.0, n)), 600.0)
    config.workflow_max_agents = _number(config.workflow_max_agents, int, lambda n: max(1, n), 24)
    config.workflow_max_tokens = _number(config.workflow_max_tokens, int, lambda n: max(0, n), 0)
    # The one goal knob is resolved once, so the model's tool, the person's route and the driver's
    # cap check all read the same number.
    if not isinstance(config.goal, dict):
        config.goal = {}
    config.goal_max_rounds = _goal_max_rounds(config.goal.get("max_rounds"))
    # The remaining tables are optional and must never raise mid-turn: a scalar typo degrades to
    # "nothing set" instead of a session that cannot resolve its agents. `[lsp]` is read by
    # `harness/integrations/lsp/servers.py` at the first server spawn.
    for name in _TABLES:
        if not isinstance(getattr(config, name), dict):
            setattr(config, name, {})
    config.default_agent = str(config.default_agent or "").strip()
    if isinstance(config.primary_tools, list):
        config.primary_tools = [str(name).strip() for name in config.primary_tools if str(name).strip()]
    else:
        config.primary_tools = []


def load_config(
    workspace: Optional[str | Path] = None,
    *,
    global_path: Optional[Path] = None,
    workspace_trusted: bool = False,
) -> Config:
    # Looked up on the package at call time, so a test (or a caller) that replaces the package's
    # `global_config_path` changes where the global file is read from.
    from .. import config as package

    config = Config()
    global_file = Path(global_path) if global_path is not None else package.global_config_path()
    if global_file.is_file():
        for key, value in read_toml(global_file).items():
            if key in FIELDS:
                setattr(config, key, value)
    merge_agent_files(config, global_file.parent)
    if workspace:
        root = Path(workspace).expanduser()
        workspace_file = root / ".reeve" / "config.toml"
        if workspace_file.is_file():
            for key, value in read_toml(workspace_file).items():
                if key not in WORKSPACE_FIELDS:
                    continue
                if key == "goal" and isinstance(value, dict) and isinstance(config.goal, dict):
                    # `[goal]` merges per key across layers: a workspace that writes one goal knob
                    # must not drop the global value of another.
                    config.goal = deep_merge(config.goal, value)
                elif key == "features":
                    config.features = _workspace_features(config.features, value)
                else:
                    setattr(config, key, value)
            if workspace_trusted:
                config.allowed_commands = list(
                    dict.fromkeys([*config.allowed_commands, *workspace_allowed_commands(workspace)])
                )
        # Both the workspace root and the config directory are scanned, the latter last, so a file
        # under `.reeve/` beats the same name at the workspace root.
        merge_agent_files(config, root)
        merge_agent_files(config, root / ".reeve")
    merge_plugin_agents(config, workspace)
    _normalize(config)
    return config
