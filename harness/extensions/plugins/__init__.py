"""Plugins: a packaging layer over Reeve's existing loaders.

`resolve_plugins` is the one entry point the loaders use; nothing under `harness/`
imports the rest of this package directly.
"""

from .manifest import split_frontmatter
from .resolve import (
    ENV_DIRS,
    INLINE_ORIGIN,
    PluginCommand,
    PluginInfo,
    PluginSet,
    PluginSkillRoot,
    log_active,
    plugin_dirs_from_env,
    resolve_plugins,
    substitute_body,
    substitute_paths,
)

__all__ = [
    "ENV_DIRS",
    "INLINE_ORIGIN",
    "PluginCommand",
    "PluginInfo",
    "PluginSet",
    "PluginSkillRoot",
    "log_active",
    "plugin_dirs_from_env",
    "resolve_plugins",
    "split_frontmatter",
    "substitute_body",
    "substitute_paths",
]
