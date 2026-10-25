"""Path variables and settings in a plugin's text (design §4.5, §4.6).

`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, `${CLAUDE_PROJECT_DIR}` (and their
`REEVE_` aliases) and `${user_config.KEY}` in a skill, agent or command body. Paths are
written with `/` so no shell reads a backslash as an escape; a secret becomes a
placeholder, because a body is text the model reads.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .manifest import Manifest, read_manifest

INLINE_ORIGIN = "inline"


def data_dir(plugin_id: str) -> Path:
    """`${CLAUDE_PLUGIN_DATA}`: kept across updates, created when first named."""
    from .store import plugins_dir, safe_id

    return plugins_dir() / "data" / safe_id(plugin_id)


_VARS = re.compile(r"\$\{(CLAUDE|REEVE)_(PLUGIN_ROOT|PLUGIN_DATA|PROJECT_DIR)\}")


def substitute(text: str, manifest: Manifest, workspace: Any, plugin_id: str = "") -> str:
    """The path variables and `${user_config.*}` in an agent body (design §4.5, §4.6)."""
    from .config import options_of, substitute as user_config

    plugin_id = plugin_id or f"{manifest.name}@{INLINE_ORIGIN}"
    text = substitute_paths(text, manifest.root, workspace, plugin_id=plugin_id)
    return user_config(text, plugin_id, options_of(manifest.raw)[0])


def substitute_body(text: str, *, plugin_root: Path, plugin_id: str, workspace: Any = None) -> str:
    """A skill's or a command's body, as `substitute` treats an agent's."""
    return substitute(text, read_manifest(Path(plugin_root)), workspace, plugin_id)


def substitute_paths(
    text: str, plugin_root: Path, workspace: Any, *, plugin_id: str = ""
) -> str:
    """`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and `${CLAUDE_PROJECT_DIR}` (and
    their `REEVE_` aliases) in a body, written with `/` so no shell reads `\\` as an
    escape (design §4.6). The data folder is created the first time a body names it."""
    plugin_id = plugin_id or f"{Path(plugin_root).name}@{INLINE_ORIGIN}"

    def value(match: re.Match[str]) -> str:
        which = match.group(2)
        if which == "PLUGIN_ROOT":
            return Path(plugin_root).resolve().as_posix()
        if which == "PLUGIN_DATA":
            path = data_dir(plugin_id)
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            return path.as_posix()
        return Path(workspace).resolve().as_posix() if workspace else match.group(0)

    return _VARS.sub(value, text) if text else text
