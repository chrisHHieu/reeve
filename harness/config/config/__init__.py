"""Configuration: layered TOML, built-in defaults < global < per-workspace.

    Global:     <state-dir>/config.toml   (see `secrets.state_dir`; platform-native)
    Workspace:  <workspace>/.reeve/config.toml   (overrides global)

Workspace command allowances apply only after the person trusts that exact canonical workspace
path. Every other permission grant stays global-only.

Markdown agents (`{agent,agents}/**/*.md`), `{file:...}` / `{env:...}` substitution in string values
and the top-level `tools` map are read here too. A markdown file whose frontmatter does not parse is
logged and skipped, and the rest of the config still loads.
"""

from __future__ import annotations

from .agent_files import merge_agent_files, read_agent_files
from .frontmatter import parse_agent_markdown
from .loading import load_config, workspace_allowed_commands
from .reading import global_config_path
from .settings import DEFAULT_ALLOWED_COMMANDS, Config

__all__ = [
    "DEFAULT_ALLOWED_COMMANDS",
    "Config",
    "global_config_path",
    "load_config",
    "merge_agent_files",
    "parse_agent_markdown",
    "read_agent_files",
    "workspace_allowed_commands",
]

