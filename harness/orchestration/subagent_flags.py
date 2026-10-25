"""Per-subagent enable switches — a tiny JSON list beside config.toml.

Settings ▸ Subagents' off switch for a BUILT-IN subagent (`general`, `explore`, and the
two CLI agents `codex` / `claude-code`) needs a store the GUI can write without rewriting
`config.toml` — the same rule `subagent_models.json` follows for model pins. The value
lives in ``<state-dir>/disabled_subagents.json`` as a sorted JSON array of names.

`resolve_agents` FLAGS a disabled name (`AgentInfo.disabled`) instead of dropping it: the
Settings list must still render the row — with its switch off — so the same name can be
turned back on, while `describe_task` (the listing the model reads), the `task` tool and
`spawn_subagent` all refuse it. The config path (`disable = true` in an agent
definition) still REMOVES a name outright; this store is the GUI's reversible switch. A
missing, torn or foreign file degrades to "nothing disabled" — a broken settings file
must never cost a delegation.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.safety.secrets import state_dir, write_private_text

_FILE_NAME = "disabled_subagents.json"


def disabled_subagents_path() -> Path:
    """`<state dir>/disabled_subagents.json` — the one canonical location."""
    return state_dir() / _FILE_NAME


def load_disabled_subagents() -> set[str]:
    """Every disabled name, or an empty set when absent/unreadable."""
    path = disabled_subagents_path()
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {
        str(name).strip()
        for name in data
        if isinstance(name, str) and str(name).strip()
    }


def set_subagent_disabled(name: str, disabled: bool) -> set[str]:
    """Store (or clear) one name's switch; returns the whole set.

    Clearing writes the file back without the name, so "enabled" round-trips as an
    absence instead of a sentinel the resolver would have to special-case.
    """
    names = load_disabled_subagents()
    key = str(name or "").strip()
    if disabled and key:
        names.add(key)
    else:
        names.discard(key)
    write_private_text(
        disabled_subagents_path(), json.dumps(sorted(names), indent=2) + "\n"
    )
    return names


__all__ = [
    "disabled_subagents_path",
    "load_disabled_subagents",
    "set_subagent_disabled",
]
