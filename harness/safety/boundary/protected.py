"""The files nothing the agent does may change, and those that only a person may approve."""

from __future__ import annotations

from pathlib import Path

# Files inside a workspace that run on a later, innocent-looking action. Writing
# `.git/hooks/pre-commit` and then running `git commit` runs it. They stay writable, but never
# without a human, and no automatic path may clear them.
_DEFERRED_EXECUTION = (
    ".git/hooks/",
    # `core.fsmonitor`, `core.pager`, `diff.external` and aliases run programs, and a `git status`
    # that only reads runs with no ask in every preset, so the file is a hook too.
    ".git/config",
    ".github/workflows/",
    ".gitlab-ci.yml",
    ".vscode/tasks.json",
    # Workspace policy the agent would grant itself. Not all of `.reeve/`: `output/`, `plans/`,
    # `undo/` and `intents/` are where it is told to write, and guarding them asked for every
    # deliverable in full access (8 of 11 such prompts on 2026-09-26).
    ".reeve/config.toml",
    ".reeve/hooks.toml",
    ".reeve/mcp.json",
    ".reeve/plugins.json",
    ".reeve/plugins.local.json",
    ".harness/extensions/skills/",
    ".harness/modes/agents/",
    ".reeve/commands/",
    # The settings file Reeve reads `enabledPlugins` from, and plugin manifests: a write there
    # turns a plugin on in every later session (design §9.6).
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude-plugin/",
    ".reeve-plugin/",
)


def _plugin_state_files() -> list[Path]:
    from harness.extensions.plugins.store import STATE_FILES, plugins_dir

    root = plugins_dir()
    return [root / name for name in STATE_FILES]


def protected_paths() -> list[Path]:
    """Files that govern the permission system itself. Nothing the agent does may write them, in
    any mode, through any tool.

    The escalation this stops is: approve one ordinary-looking command, it quietly appends to the
    rule file, and every later session is more permissive. That happens in the default mode, so it
    cannot be a property of a sandbox or one mode. It is a floor."""
    from ..secrets import protected_database_names, state_dir

    base = state_dir()
    return [
        base / "config.toml",
        base / "risk_overrides.json",
        base / "workspace_trust.json",
        base / "hook_trust.json",  # which workspace hook declarations may run
        *(base / name for name in protected_database_names()),  # session records carry the saved "always allow" grants
        base / "secrets.json",
        # What the agent is told (SOUL.md), its memory switches, and the default mode
        # new conversations start in (prefs.json): one write would widen them all.
        base / "SOUL.md",
        base / "memory-settings.json",
        base / "prefs.json",
        # What is installed, enabled, consented to and configured for plugins: the
        # agent must never install one or turn one on for itself.
        *_plugin_state_files(),
    ]


def secret_paths() -> list[Path]:
    """Files whose CONTENTS are credentials: no file tool reads them, in any mode. A
    read reaches the provider request and the session log, and neither may carry a key. The
    other protected files are guarded against writes; reading them harms nothing."""
    from ..secrets import state_dir

    base = state_dir()
    return [base / "secrets.json", base / ".env"]


def protected_dirs() -> list[Path]:
    """Folders in which every file counts as a protected path: an installed plugin's code (the
    quickest way round its consented digest, design §9.6) and the administrator's `managed/`
    policy (the instructions, and `plugins.json`)."""
    from harness.extensions.plugins.store import plugins_dir

    from ..secrets import protected_database_names, state_dir

    return [plugins_dir() / "cache", state_dir() / "managed"]


def is_protected_in_project(candidate: Path) -> bool:
    posix = candidate.as_posix()
    return any(
        (f"/{marker}" in posix or posix.startswith(marker))
        if marker.endswith("/")
        else posix.endswith("/" + marker)
        for marker in _DEFERRED_EXECUTION
    )
