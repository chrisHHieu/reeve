"""The plugins one session runs with, resolved once (design §10).

`resolve_plugins(dirs, workspace)` reads every plugin folder and returns a `PluginSet`:
what each enabled plugin contributes to the loaders that already exist (skill roots,
agents, commands), plus what was skipped and what failed. It never raises and never uses
the network; a broken plugin becomes an error entry and the session starts with the rest.

This phase (P1) loads plugins from folders named at startup — `REEVE_PLUGIN_DIRS` or
`reeve-server --plugin-dir` — under the reserved origin `inline`. Installation,
marketplaces and the executable components come with consent in later phases.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .compat import agent_from_claude
from .manifest import Manifest, read_manifest, split_frontmatter
from .paths import data_dir, substitute, substitute_body, substitute_paths  # noqa: F401 (re-exported)

INLINE_ORIGIN = "inline"
ENV_DIRS = "REEVE_PLUGIN_DIRS"


@dataclass(frozen=True)
class PluginSkillRoot:
    """One folder of `<name>/SKILL.md` skills, named `<plugin>:<name>`."""

    path: Path
    plugin: str
    #: The root itself is the one skill (a plugin with a top-level SKILL.md).
    single: bool = False
    #: The plugin's own folder, for `${CLAUDE_PLUGIN_ROOT}` in a skill body.
    plugin_root: Optional[Path] = None
    #: `<name>@<origin>`, for the Settings row and the log.
    plugin_id: str = ""


@dataclass(frozen=True)
class PluginCommand:
    name: str  # `<plugin>:<file>`
    path: Path
    plugin: str
    #: The plugin's own folder, for `${CLAUDE_PLUGIN_ROOT}` in the body.
    root: Optional[Path] = None
    plugin_id: str = ""
    #: The object form (design §5.2): an inline body instead of the file's, and metadata
    #: that wins over the file's frontmatter, as (key, value) pairs.
    content: Optional[str] = None
    meta: tuple[tuple[str, Any], ...] = ()


@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    root: str
    scope: str
    display_name: str = ""
    description: str = ""
    components: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    #: Plugin ids this one needs (design §8.6).
    dependencies: list[str] = field(default_factory=list)
    #: Its code: "none", "running", or "needs_consent" (design §9.3).
    code: str = "none"
    executable_digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "root": self.root,
            "scope": self.scope,
            "display_name": self.display_name,
            "description": self.description,
            "components": dict(self.components),
            "skipped": list(self.skipped),
            "dependencies": list(self.dependencies),
            "code": self.code,
            "executable_digest": self.executable_digest,
        }


@dataclass
class PluginSet:
    plugins: list[PluginInfo] = field(default_factory=list)
    skill_roots: list[PluginSkillRoot] = field(default_factory=list)
    #: name -> agent entry in the config-table shape `resolve_agents` reads.
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    commands: list[PluginCommand] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    #: Code-running parts that may run (design §9.3): hook declarations with their plugin
    #: and environment, MCP servers by name, extension -> language server, bin/ folders.
    hooks: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    lsp: dict[str, dict[str, Any]] = field(default_factory=dict)
    bin_dirs: list[Path] = field(default_factory=list)

    def log_fields(self) -> dict[str, Any]:
        """The `plugins/active` event's payload (design §10.2)."""
        return {
            "plugins": [
                {
                    "id": p.id,
                    "version": p.version,
                    "scope": p.scope,
                    "components": dict(p.components),
                    "skipped": list(p.skipped),
                    "code": p.code,
                    **({"executable_digest": p.executable_digest} if p.executable_digest else {}),
                }
                for p in self.plugins
            ],
            "errors": list(self.errors),
        }

    def as_dict(self) -> dict[str, Any]:
        return {"plugins": [p.as_dict() for p in self.plugins], "errors": list(self.errors)}


EMPTY = PluginSet()


def plugin_dirs_from_env() -> list[Path]:
    """The folders `REEVE_PLUGIN_DIRS` names (the OS path separator between them)."""
    raw = os.environ.get(ENV_DIRS, "")
    return [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]


def resolve_plugins(
    dirs: Optional[list[Path]] = None,
    workspace: Optional[str | Path] = None,
    *,
    entries: Optional[dict[str, dict[str, Any]]] = None,
    installed: Optional[bool] = None,
) -> PluginSet:
    """The plugins a session runs with. Never raises, never uses the network.

    Session plugins (`dirs`, default the environment's `--plugin-dir` folders) come first
    and win a name; then every INSTALLED plugin whose effective enabled state is on for
    this workspace (design §6.3). `dirs` given explicitly — a preview, `validate` — reads
    only those folders unless `installed=True`. `entries` maps a folder to its marketplace
    entry, for a preview of what an install would load.
    """
    out = PluginSet()
    seen: dict[str, str] = {}
    explicit = dirs is not None
    for folder in dirs if explicit else plugin_dirs_from_env():
        folder = Path(folder)
        entry = (entries or {}).get(str(folder))
        manifest = read_manifest(folder, entry=entry)
        plugin_id = f"{manifest.name}@{INLINE_ORIGIN}"
        _add(out, seen, manifest, plugin_id, "session", str(folder), workspace)
    if installed if installed is not None else not explicit:
        _installed(out, seen, workspace)
    from .deps import prune

    prune(out)
    return out


def _add(out: PluginSet, seen: dict[str, str], manifest: Manifest, plugin_id: str, scope: str, where: str, workspace: Any) -> None:
    if manifest.errors:
        out.errors += [{"id": plugin_id, "root": where, "error": e} for e in manifest.errors]
        return
    if manifest.name in seen:
        out.errors.append(
            {
                "id": plugin_id,
                "root": where,
                "error": f"a plugin named {manifest.name} is already loaded from {seen[manifest.name]}",
            }
        )
        return
    seen[manifest.name] = where
    try:
        _contribute(out, manifest, plugin_id, workspace, scope=scope)
    except Exception as exc:  # one plugin's bug is its own error entry
        out.errors.append({"id": plugin_id, "root": where, "error": str(exc)})


def _installed(out: PluginSet, seen: dict[str, str], workspace: Any) -> None:
    """Installed plugins that are on here. A project may declare a plugin, never install
    it, and its declaration counts only in a workspace the person trusts (design §9.4)."""
    from . import store

    from .deps import dependency_ids

    records = store.installed()
    marks = store.flagged()
    trusted: Optional[bool] = None
    decisions: dict[str, tuple[bool, str]] = {}
    for plugin_id in sorted(records):
        record = store.install_record(plugin_id)
        if record is None:
            continue
        entry = record.get("entry") if isinstance(record.get("entry"), dict) else None
        default = True
        if isinstance(entry, dict) and isinstance(entry.get("defaultEnabled"), bool):
            default = entry["defaultEnabled"]
        decisions[plugin_id] = store.effective_enabled(plugin_id, workspace, default=default)
    # A plugin an enabled plugin needs is on too, unless someone turned it off (design §8.6).
    for plugin_id, (on, _scope) in list(decisions.items()):
        if not on:
            continue
        record = store.install_record(plugin_id) or {}
        root = store.install_root(record)
        needs = dependency_ids(read_manifest(root, entry=record.get("entry")).raw, plugin_id) if root.is_dir() else []
        for dep in needs:
            if decisions.get(dep) == (False, "default"):
                decisions[dep] = (True, "default")
    for plugin_id in sorted(decisions):
        record = store.install_record(plugin_id) or {}
        entry = record.get("entry") if isinstance(record.get("entry"), dict) else None
        enabled, scope = decisions[plugin_id]
        if not enabled:
            continue
        mark = marks.get(plugin_id)
        if mark and not mark.get("kept"):
            out.errors.append(
                {
                    "id": plugin_id,
                    "error": f"{mark.get('marketplace') or 'its marketplace'} no longer lists it, so it is not loaded — "
                    "keep using it or uninstall it in Settings ▸ Plugins",
                }
            )
            continue
        if scope in ("project", "local"):
            if trusted is None:
                from .project import trusted_workspace

                trusted = trusted_workspace(workspace)
            if not trusted:
                out.errors.append(
                    {"id": plugin_id, "error": "turned on by this project, which is not trusted yet"}
                )
                continue
        root = store.install_root(record)
        if not root.is_dir():
            out.errors.append({"id": plugin_id, "error": "installed, but its files are missing — reinstall it"})
            continue
        manifest = read_manifest(root, entry=entry)
        _add(out, seen, manifest, plugin_id, scope if scope != "default" else str(record.get("scope") or "user"), str(root), workspace)
        if manifest.version == "" and record.get("version") and out.plugins and out.plugins[-1].id == plugin_id:
            out.plugins[-1].version = str(record["version"])
    # A project that turns on a plugin this machine does not have: say so, never fetch it.
    for scope, layer in store.enabled_layers(workspace):
        if scope not in ("project", "local"):
            continue
        for plugin_id, on in layer.items():
            if on and plugin_id not in records and "@" in plugin_id and not plugin_id.endswith(f"@{INLINE_ORIGIN}"):
                out.errors.append({"id": plugin_id, "error": "this project uses it; it is not installed here"})


def log_active(log: Any, workspace: Any = None, *, reason: str = "") -> None:
    """One `plugins/active` row when a session's engine is built (design §10.2): what the
    plugins put in front of the model — skills in the catalog, agents in the task list —
    is logged like everything else the model sees. Nothing when no plugin is
    configured, unless a reload asked for it (then an empty set is the news). `reason` is
    "reload" for the build `/reload-plugins` caused. Best-effort: a log failure must never
    cost the session."""
    try:
        plugins = resolve_plugins(workspace=workspace)
        if plugins.plugins or plugins.errors or reason:
            log.append("plugins/active", **plugins.log_fields(), **({"reason": reason} if reason else {}))
    except Exception:
        pass


def _contribute(out: PluginSet, manifest: Manifest, plugin_id: str, workspace: Any, *, scope: str = "session") -> None:
    info = PluginInfo(
        id=plugin_id,
        name=manifest.name,
        version=manifest.version,
        root=str(manifest.root),
        scope=scope,
        display_name=manifest.display_name,
        description=manifest.description,
        skipped=list(manifest.skipped),
    )
    skills = 0
    if manifest.root_skill:
        out.skill_roots.append(
            PluginSkillRoot(
                manifest.root, manifest.name, single=True, plugin_root=manifest.root, plugin_id=plugin_id
            )
        )
        skills = 1
    for folder in manifest.skill_dirs:
        out.skill_roots.append(
            PluginSkillRoot(folder, manifest.name, plugin_root=manifest.root, plugin_id=plugin_id)
        )
        skills += sum(1 for p in folder.iterdir() if (p / "SKILL.md").is_file())

    for path in manifest.command_files:
        stem = path.relative_to(_command_base(manifest, path)).with_suffix("").as_posix()
        out.commands.append(
            PluginCommand(
                f"{manifest.name}:{stem.replace('/', ':')}",
                path,
                manifest.name,
                root=manifest.root,
                plugin_id=plugin_id,
            )
        )

    for spec in manifest.command_objects:
        meta = tuple(
            (k, tuple(v) if isinstance(v, list) else v)
            for k, v in spec.items()
            if k in ("description", "argument_hint", "model", "allowed_tools") and v
        )
        out.commands.append(
            PluginCommand(
                f"{manifest.name}:{spec['name']}",
                spec.get("path") or manifest.root,
                manifest.name,
                root=manifest.root,
                plugin_id=plugin_id,
                content=spec.get("content"),
                meta=meta,
            )
        )

    agents = 0
    for path, base in manifest.agent_files:
        relative = path.relative_to(base).with_suffix("").as_posix().split("/")
        try:
            frontmatter, body, problem = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            info.skipped.append(f"agent {path.name}: {exc}")
            continue
        if problem:
            info.skipped.append(f"agent {path.name}: {problem}; loaded with no fields")
        declared = str(frontmatter.get("name") or "").strip()
        if declared and re.fullmatch(r"[a-z0-9][a-z0-9-]*", declared):
            relative[-1] = declared
        name = ":".join([manifest.name, *relative])
        entry, skipped = agent_from_claude(
            frontmatter, substitute(body, manifest, workspace, plugin_id), plugin=manifest.name
        )
        entry["_plugin"] = plugin_id
        out.agents[name] = entry
        info.skipped += [f"agent {name}: {s}" for s in skipped]
        agents += 1

    from .config import missing, options_of

    unset = missing(plugin_id, options_of(manifest.raw)[0])
    if unset:
        info.skipped.append(f"settings not given yet: {', '.join(unset)}")
    from .deps import dependency_ids
    from .running import contribute as contribute_code

    info.dependencies = dependency_ids(manifest.raw, plugin_id)
    contribute_code(out, info, manifest, plugin_id, scope, workspace)
    # Reeve has one agent: a plugin's `personas/` folder (Reeve's own component until
    # 2026-10-12) installs nothing, and the plugin's page says so.
    if (manifest.root / "personas").is_dir():
        info.skipped.append("personas/ is not used: Reeve has one agent")

    info.components = {
        key: count
        for key, count in (
            ("skills", skills),
            ("commands", len(manifest.command_files) + len(manifest.command_objects)),
            ("agents", agents),
        )
        if count
    }
    out.plugins.append(info)


def _command_base(manifest: Manifest, path: Path) -> Path:
    default = manifest.root / "commands"
    try:
        path.relative_to(default)
        return default
    except ValueError:
        return path.parent
