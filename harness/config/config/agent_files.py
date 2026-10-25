"""Agents defined as markdown files, and agents that arrive with plugins."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .frontmatter import parse_agent_markdown
from .reading import deep_merge

if TYPE_CHECKING:
    from .settings import Config

logger = logging.getLogger("harness.config.config")

#: Both spellings, both searched recursively.
_AGENT_FOLDERS = ("agent", "agents")


def _agent_name(directory: Path, folder: str, path: Path) -> str:
    """`agents/team/helper.md` is `team/helper`: the path under the folder, without `.md`."""
    relative = path.relative_to(directory).as_posix()
    prefix = f"{folder}/"
    name = relative[len(prefix) :] if relative.startswith(prefix) else path.name
    return name[:-3] if name.endswith(".md") else name


def read_agent_files(directory: Path) -> dict[str, Any]:
    """The markdown agents under one config directory, by name.

    A file whose frontmatter does not parse is reported and skipped, because one broken file must
    not cost the person every other agent.
    """
    found: dict[str, Any] = {}
    for folder in _AGENT_FOLDERS:
        root = directory / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            name = _agent_name(directory, folder, path)
            if not name:
                continue
            try:
                found[name] = parse_agent_markdown(path)
            except Exception as exc:
                logger.warning("ignoring markdown agent %s: %s", path, exc)
    return found


def merge_agent_files(config: "Config", directory: Path) -> None:
    """One directory's markdown agents laid over the TOML tables already loaded.

    `load_config` calls this in layer order (global TOML, global markdown, workspace TOML,
    workspace-root markdown, workspace `.reeve` markdown), so markdown wins a same-name clash and
    the config directory wins over the workspace root.
    """
    if not directory.is_dir():
        return
    markdown = read_agent_files(directory)
    if not markdown:
        return
    agents = dict(config.agents or {})
    for name, entry in markdown.items():
        existing = agents.get(name)
        agents[name] = deep_merge(existing, entry) if isinstance(existing, dict) else entry
    config.agents = agents


def merge_plugin_agents(config: "Config", workspace: Optional[str | Path]) -> None:
    """Enabled plugins' agents, named `<plugin>:<name>`, under everything the person
    wrote: a config or markdown entry of the same name wins. Never raises, since a plugin that
    cannot load is the plugin's error and not a reason the config fails to load."""
    try:
        from harness.extensions.plugins import resolve_plugins

        agents = resolve_plugins(workspace=workspace).agents
    except Exception as exc:
        logger.warning("plugin agents not loaded: %s", exc)
        return
    if agents:
        config.agents = {**agents, **(config.agents or {})}
