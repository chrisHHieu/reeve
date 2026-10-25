"""Which of a plugin's code-running parts a session may run (design §9.3-§9.5).

A plugin loaded from `--plugin-dir` runs its code: the person named that folder for this
run. An installed plugin runs its code only when the code's digest is the one the person
agreed to (`consent.json`) — a changed hook script is changed code, and it waits for a
second yes. A plugin that only a PROJECT turned on needs that yes for this workspace, and
never gets `bin/` on the PATH or a server whose program lives outside the plugin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .config import options_of, values
from .executable import shell_argv, executable_digest, read_executable, resolve_vars


def contribute(out: Any, info: Any, manifest: Any, plugin_id: str, scope: str, workspace: Any) -> None:
    """Add a plugin's runnable hooks, MCP servers, language servers and `bin/` to `out`,
    or say why they wait."""
    exe = read_executable(manifest.root, manifest.raw, plugin=manifest.name)
    info.skipped += exe.skipped
    if exe.empty:
        return
    digest = executable_digest(manifest.root, exe)
    info.executable_digest = digest
    if scope == "session":
        allowed = True
    elif scope in ("project", "local"):
        allowed = store.consented(plugin_id, digest, workspace) or (
            scope == "local" and store.consented(plugin_id, digest)
        )
    else:
        allowed = store.consented(plugin_id, digest)
    if not allowed:
        info.code = "needs_consent"
        out.errors.append(
            {
                "id": plugin_id,
                "kind": "consent",
                "error": "its code is not running: review it and agree in Settings ▸ Plugins",
            }
        )
        return
    info.code = "running"
    from .resolve import data_dir

    options = options_of(manifest.raw)[0]
    config = values(plugin_id, options, include_sensitive=True)
    data = data_dir(plugin_id)
    root = manifest.root

    def fill(value: Any) -> Any:
        return resolve_vars(value, root=root, data=data, workspace=workspace, config=config)

    env = {
        "CLAUDE_PLUGIN_ROOT": root.resolve().as_posix(),
        "CLAUDE_PLUGIN_DATA": data.as_posix(),
        **({"CLAUDE_PROJECT_DIR": Path(workspace).resolve().as_posix()} if workspace else {}),
    }
    env.update({k.replace("CLAUDE_", "REEVE_", 1): v for k, v in list(env.items())})
    for key, value in config.items():
        env[f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] = str(value)
        env[f"REEVE_PLUGIN_OPTION_{key.upper()}"] = str(value)

    for hook in exe.hooks:
        command = fill(hook["command"])
        if hook["shell"]:
            # A shell string runs through the shell it was written for (design §5.4); with
            # none on this machine the hook is left out, never run some other way.
            command = shell_argv(command[0], "" if hook["shell"] == "auto" else hook["shell"])
            if command is None:
                info.skipped.append(f"hook {hook['name']}: no shell on this machine to run it")
                continue
        out.hooks.append({**hook, "command": command, "plugin": plugin_id, "env": env})
    for name, spec in exe.mcp.items():
        if scope == "project" and spec.get("command"):
            program = Path(str(fill(spec["command"])))
            if not (program.is_absolute() and root.resolve() in program.resolve().parents):
                info.skipped.append(f"mcp {name}: a project-enabled plugin may only run its own programs")
                continue
        out.mcp_servers[name] = {**fill(spec), "requires_approval": True, "_plugin": plugin_id}
    for ext, spec in exe.lsp.items():
        if ext in out.lsp:
            info.skipped.append(f"lsp {ext}: {out.lsp[ext]['_plugin']} already serves it")
            continue
        out.lsp[ext] = {**fill(spec), "_plugin": plugin_id}
    if exe.bin_dir is not None:
        if scope == "project":
            info.skipped.append("bin/: a plugin only a project turned on does not get a place on the PATH")
        else:
            out.bin_dirs.append(exe.bin_dir.resolve())
