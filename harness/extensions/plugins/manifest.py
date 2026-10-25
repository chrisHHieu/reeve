"""A plugin's manifest and layout, read the Claude Code way (design §4).

A plugin is a folder. Its manifest is optional: `.reeve-plugin/plugin.json` when a
plugin needs Reeve's own fields, else `.claude-plugin/plugin.json`, else nothing and the
name is the folder's. The standard layout names the components (`skills/`, `commands/`,
`agents/`, …) and the manifest may add or replace their paths.

Parsing never raises. A bad manifest is an error for that plugin; a bad path, an
unsupported field or a component this phase does not load yet is recorded against the
plugin and the rest still loads — the same "degrade, never break" contract hooks and
presenters keep.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

#: Kebab-case: no spaces, `@`, `:`, path separators, control or bidi characters can pass.
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MANIFEST_PATHS = (
    Path(".reeve-plugin") / "plugin.json",
    Path(".claude-plugin") / "plugin.json",
)

#: Top-level manifest keys this version reads, shows, or knowingly stores.
_KNOWN = {
    "$schema", "name", "displayName", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "metadata", "defaultEnabled", "dependencies",
    "settings", "userConfig", "skills", "commands", "agents", "hooks", "mcpServers",
    "lspServers", "reeve",
}
#: Claude Code fields Reeve does not support; the plugin loads without them.
_UNSUPPORTED = {"channels", "outputStyles", "workflows", "experimental"}
_UNSUPPORTED_DIRS = ("output-styles", "themes", "workflows", "monitors")


@dataclass
class Manifest:
    """What one plugin folder declares, after validation."""

    root: Path
    name: str
    version: str = ""
    display_name: str = ""
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    #: Folders holding `<name>/SKILL.md` skills, and whether the root itself is one skill.
    skill_dirs: list[Path] = field(default_factory=list)
    root_skill: bool = False
    #: `.md` files, each one command / one agent.
    command_files: list[Path] = field(default_factory=list)
    #: `commands` in its object form: {name, path?, content?, description, argument_hint,
    #: model, allowed_tools} per command (Claude Code's `source` or inline `content`).
    command_objects: list[dict[str, Any]] = field(default_factory=list)
    agent_files: list[tuple[Path, Path]] = field(default_factory=list)  # (file, its base)
    #: Why a component or field was left out; the plugin still loads.
    skipped: list[str] = field(default_factory=list)
    #: Why the plugin cannot load at all (empty when it can).
    errors: list[str] = field(default_factory=list)
    #: Which manifest was read, if any (relative to the root).
    manifest_path: str = ""


def split_frontmatter(text: str) -> tuple[dict[str, Any], str, str]:
    """`(frontmatter, body, error)` of a Markdown component. Claude Code's rule for a bad
    block: the component still loads, with no fields (`error` says why)."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text, ""
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return {}, text, "unterminated frontmatter"
    block = "\n".join(lines[1:close])
    body = "\n".join(lines[close + 1 :]).lstrip("\n")
    try:
        import yaml

        data = yaml.safe_load(block) or {}
    except Exception as exc:
        return {}, body, f"frontmatter is not YAML: {exc}"
    if not isinstance(data, dict):
        return {}, body, "frontmatter is not a mapping"
    return data, body, ""


#: A marketplace entry's keys that describe components (design §7.1).
_ENTRY_COMPONENTS = ("skills", "commands", "agents", "hooks", "mcpServers", "lspServers")
#: A marketplace entry's keys that win over the plugin's own manifest.
_ENTRY_WINS = ("defaultEnabled", "displayName", "description", "author", "homepage", "keywords")


def read_manifest(root: Path, entry: Optional[dict[str, Any]] = None) -> Manifest:
    """The plugin at `root`, validated — merged with its marketplace entry when installed
    from one (Claude Code's rules, design §7.1). Never raises."""
    root = Path(root)
    try:
        return _read(root, entry)
    except Exception as exc:  # a parser bug must cost this plugin, not the session
        return Manifest(root=root, name=root.name, errors=[f"could not read the plugin: {exc}"])


def _merge_entry(root: Path, raw: dict[str, Any], found: bool, entry: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """`(merged manifest, notes, errors)` for a plugin and its marketplace entry.

    No manifest: the entry IS the manifest. `strict` (the default): the entry's component
    paths are ADDED to the plugin's. `strict: false` with components in the entry: the two
    disagree about what the plugin is, and it does not load.
    """
    fields = {k: v for k, v in entry.items() if k not in ("source", "strict")}
    if not found:
        return fields, [], []
    notes: list[str] = []
    declared = [k for k in _ENTRY_COMPONENTS if k in entry]
    if entry.get("strict") is False and declared:
        return raw, notes, [f"conflicting manifests: the marketplace entry declares {', '.join(declared)}"]
    merged = dict(raw)
    for key in ("skills", "commands", "agents"):
        if key not in entry:
            continue
        mine = merged.get(key)
        if mine is None:
            mine = [f"./{key}"] if (root / key).is_dir() else []
        if isinstance(mine, dict):
            notes.append(f"{key}: the plugin lists them by name, so the marketplace entry's {key} paths are not added")
            continue
        mine = mine if isinstance(mine, list) else [mine]
        extra = entry[key] if isinstance(entry[key], list) else [entry[key]]
        merged[key] = [*mine, *extra]
    for key in ("hooks", "mcpServers", "lspServers"):
        if key in entry:
            merged[key] = entry[key]
    for key in _ENTRY_WINS:
        if key in entry:
            merged[key] = entry[key]
    if "version" not in raw and "version" in entry:
        merged["version"] = entry["version"]
    if entry.get("name") and raw.get("name") and entry["name"] != raw["name"]:
        notes.append(
            f"the marketplace calls it {entry['name']} and its manifest {raw['name']}; "
            "components are named after the manifest"
        )
    return merged, notes, []


def _read(root: Path, entry: Optional[dict[str, Any]] = None) -> Manifest:
    if not root.is_dir():
        return Manifest(root=root, name=root.name, errors=["not a folder"])
    raw: dict[str, Any] = {}
    found = [p for p in MANIFEST_PATHS if (root / p).is_file()]
    skipped: list[str] = []
    if found:
        chosen = found[0]
        if len(found) > 1:
            skipped.append(f"manifest {found[1].as_posix()} ignored: {chosen.as_posix()} wins")
        try:
            data = json.loads((root / chosen).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return Manifest(root=root, name=root.name, errors=[f"{chosen.as_posix()}: {exc}"])
        if not isinstance(data, dict):
            return Manifest(root=root, name=root.name, errors=[f"{chosen.as_posix()} is not an object"])
        raw = data
        manifest_path = chosen.as_posix()
    else:
        manifest_path = ""

    entry_notes: list[str] = []
    if entry:
        raw, entry_notes, entry_errors = _merge_entry(root, raw, bool(found), entry)
        if entry_errors:
            return Manifest(root=root, name=str(raw.get("name") or root.name), manifest_path=manifest_path, errors=entry_errors)
        skipped += entry_notes
    name = raw.get("name", root.name if not found else None)
    if not isinstance(name, str) or not NAME_RE.match(name):
        return Manifest(
            root=root,
            name=str(name or root.name),
            manifest_path=manifest_path,
            errors=[
                f"plugin name {name!r} must be kebab-case (lowercase letters, digits, single "
                "hyphens)"
            ],
        )
    out = Manifest(root=root, name=name, raw=raw, skipped=skipped, manifest_path=manifest_path)
    out.version = str(raw.get("version") or "")
    out.display_name = str(raw.get("displayName") or "")
    out.description = str(raw.get("description") or "")

    homepage = raw.get("homepage")
    if homepage is not None and not _is_url(homepage):
        out.errors.append(f"homepage {homepage!r} is not a URL")
        return out
    from .config import options_of

    _options, config_errors = options_of(raw)
    if config_errors:
        out.errors += config_errors
        return out
    for key in raw:
        if key in _UNSUPPORTED:
            out.skipped.append(f"{key}: not supported")
        elif key not in _KNOWN:
            out.skipped.append(f"{key}: unknown field, ignored")
    if "reeve" in raw and not manifest_path.startswith(".reeve-plugin"):
        out.skipped.append("reeve: read only from .reeve-plugin/plugin.json")

    _skills(out)
    out.command_files = _markdown_component(out, "commands", default="commands", recursive=True)
    for base, files in _agent_component(out):
        out.agent_files += [(f, base) for f in files]
    for folder in _UNSUPPORTED_DIRS:
        if (root / folder).is_dir():
            out.skipped.append(f"{folder}/: not supported")
    return out


def _is_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _declared_paths(out: Manifest, key: str, *, allow_dot: bool = False) -> Optional[list[Path]]:
    """The manifest's paths for `key`, each checked by the path rules (design §4.4), or
    None when the manifest does not declare it. A bad path is skipped and recorded."""
    value = out.raw.get(key)
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    paths: list[Path] = []
    for item in items:
        path = _checked_path(out, key, item, allow_dot=allow_dot)
        if path is not None:
            paths.append(path)
    return paths


def _checked_path(out: Manifest, key: str, item: Any, *, allow_dot: bool) -> Optional[Path]:
    if not isinstance(item, str) or not item:
        out.skipped.append(f"{key}: {item!r} is not a path")
        return None
    if "\\" in item:
        if os.name != "nt":
            out.skipped.append(f"{key}: {item} uses a backslash")
            return None
        out.skipped.append(f"{key}: {item} uses a backslash; write it with / for other systems")
        item = item.replace("\\", "/")
    if not (item.startswith("./") or (allow_dot and item == ".")):
        out.skipped.append(f"{key}: {item} must start with ./")
        return None
    if ".." in Path(item).parts:
        out.skipped.append(f"{key}: {item} leaves the plugin folder")
        return None
    target = out.root / item
    try:
        if not target.resolve().is_relative_to(out.root.resolve()):
            out.skipped.append(f"{key}: {item} leaves the plugin folder")
            return None
    except OSError as exc:
        out.skipped.append(f"{key}: {item}: {exc}")
        return None
    if not target.exists():
        out.skipped.append(f"{key}: {item} does not exist")
        return None
    return target


def _skills(out: Manifest) -> None:
    """`skills/` plus any declared folders (additive); a lone root `SKILL.md` is one skill."""
    default = out.root / "skills"
    declared = _declared_paths(out, "skills", allow_dot=True)
    dirs = ([default] if default.is_dir() else []) + [
        p for p in (declared or []) if p.is_dir() and p != default
    ]
    out.skill_dirs = dirs
    out.root_skill = (
        not dirs and declared is None and (out.root / "SKILL.md").is_file()
    )


def _markdown_component(out: Manifest, key: str, *, default: str, recursive: bool) -> list[Path]:
    """`.md` files for a component whose declared paths REPLACE the default folder."""
    if isinstance(out.raw.get(key), dict):
        if key == "commands":
            out.command_objects = _command_objects(out)
        else:
            out.skipped.append(f"{key}: the object form is not supported")
        return []
    declared = _declared_paths(out, key)
    bases = declared if declared is not None else [out.root / default]
    files: list[Path] = []
    for base in bases:
        if base.is_file() and base.suffix == ".md":
            files.append(base)
        elif base.is_dir():
            files += sorted(p for p in (base.rglob("*.md") if recursive else base.glob("*.md")) if p.is_file())
    return files


_COMMAND_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*(:[a-z0-9][a-z0-9_-]*)*$")


def _command_objects(out: Manifest) -> list[dict[str, Any]]:
    """`"commands": {"deploy": {"source": "./docs/deploy.md", "description": ...},
    "hello": {"content": "Say hello to $ARGUMENTS"}}` - each command's body from a file in
    the plugin or written inline, its metadata from the object (winning over the file's
    frontmatter). A command that has neither, or both, is left out and noted."""
    found: list[dict[str, Any]] = []
    for name, spec in (out.raw.get("commands") or {}).items():
        where = f"commands.{name}"
        if not isinstance(name, str) or not _COMMAND_NAME.match(name):
            out.skipped.append(f"{where}: not a command name (lowercase, digits, - _ :)")
            continue
        if not isinstance(spec, dict):
            out.skipped.append(f"{where}: must be an object with `source` or `content`")
            continue
        source, content = spec.get("source"), spec.get("content")
        if (source is None) == (content is None):
            out.skipped.append(f"{where}: needs exactly one of `source` or `content`")
            continue
        entry: dict[str, Any] = {"name": name}
        if source is not None:
            path = _checked_path(out, where, source, allow_dot=False)
            if path is None:
                continue
            if not path.is_file():
                out.skipped.append(f"{where}: {source} is not a file")
                continue
            entry["path"] = path
        else:
            if not isinstance(content, str) or not content.strip():
                out.skipped.append(f"{where}: `content` must be text")
                continue
            entry["content"] = content
        tools = spec.get("allowedTools") or spec.get("allowed-tools") or []
        entry.update(
            description=str(spec.get("description") or ""),
            argument_hint=str(spec.get("argumentHint") or spec.get("argument-hint") or ""),
            model=str(spec.get("model") or ""),
            allowed_tools=[str(t).strip() for t in (tools if isinstance(tools, list) else str(tools).split(",")) if str(t).strip()],
        )
        found.append(entry)
    return found


def _agent_component(out: Manifest) -> list[tuple[Path, list[Path]]]:
    """Agent files grouped by the folder their names are relative to."""
    declared = _declared_paths(out, "agents")
    bases = declared if declared is not None else [out.root / "agents"]
    groups: list[tuple[Path, list[Path]]] = []
    for base in bases:
        if base.is_file():
            if base.suffix == ".md":
                groups.append((base.parent, [base]))
            else:
                out.skipped.append(f"agents: {base.name} is not a .md file")
        elif base.is_dir():
            groups.append((base, sorted(p for p in base.rglob("*.md") if p.is_file())))
    return groups
