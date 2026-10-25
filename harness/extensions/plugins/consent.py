"""What a person sees before a plugin installs or updates (design §8.2).

The same data for every surface. Components by name; anything that would run code,
verbatim; what is left out and why; the settings it will ask for; the plugins it needs.
For an update, what changed — and above all whether what runs changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .manifest import read_manifest
from .marketplace import PluginError


def executable_surface(root: Path, raw: dict[str, Any], name: str) -> tuple[list[dict[str, str]], str, list[str]]:
    """What would run, as the person must read it (design §8.2): each hook's event,
    matcher and command, each server's program or URL, each language server, each `bin/`
    file (flagging a name that shadows a command already on the PATH). Returns the rows,
    the digest they amount to, and what could not be converted."""
    import shutil

    from .executable import executable_digest, read_executable

    exe = read_executable(root, raw, plugin=name)
    rows: list[dict[str, str]] = []
    for hook in exe.hooks:
        how = " ".join(hook["command"]) if not hook["shell"] else f"(shell) {hook['command'][0]}"
        rows.append({"kind": "hook", "detail": f"{hook['event']} {hook['matcher'] or '(every tool)'}: {how}"})
    for server, spec in sorted(exe.mcp.items()):
        what = spec.get("url") or " ".join([str(spec.get("command") or ""), *map(str, spec.get("args") or [])])
        env = sorted((spec.get("env") or {}).keys()) if isinstance(spec.get("env"), dict) else []
        rows.append({"kind": "mcp", "detail": f"{server}: {what}" + (f" (env: {', '.join(env)})" if env else "")})
    for ext, spec in sorted(exe.lsp.items()):
        rows.append({"kind": "lsp", "detail": f"{ext} → {spec['name']}: {spec.get('command')} {' '.join(map(str, spec.get('args') or []))}".rstrip()})
    if exe.bin_dir is not None:
        for path in sorted(p for p in exe.bin_dir.rglob("*") if p.is_file()):
            shadows = " — also a command on this machine's PATH" if shutil.which(path.stem) else ""
            rows.append({"kind": "bin", "detail": path.relative_to(root).as_posix() + shadows})
    if not exe.empty:
        from .node import node_setup

        node = node_setup(root)
        if node and "argv" in node:
            rows.append(
                {
                    "kind": "node",
                    "detail": f"{' '.join(node['argv'])} — installs the Node packages {node['lockfile']} pins; no install script runs",
                }
            )
    return rows, executable_digest(root, exe), exe.skipped


def _skill_names(roots: list[Any]) -> list[str]:
    """`<plugin>:<folder>` for every skill folder a plugin brings (a root skill is its
    plugin folder), for the consent screen."""
    names = []
    for root in roots:
        folders = [Path(root.path)] if root.single else sorted(Path(root.path).iterdir())
        names += [f"{root.plugin}:{f.name}" for f in folders if (f / "SKILL.md").is_file()]
    return sorted(names)


def consent_summary(root: Path, *, plugin_id: str, version: str, fetched_detail: str, sha: str, entry: dict[str, Any]) -> dict[str, Any]:
    from .resolve import resolve_plugins

    manifest = read_manifest(root, entry=entry)
    if manifest.errors:
        raise PluginError("; ".join(manifest.errors))
    preview = resolve_plugins([root], entries={str(root): entry})
    executable, digest, _exe_skipped = executable_surface(root, manifest.raw, manifest.name)
    from .node import node_setup

    node = node_setup(root) if digest else None
    node_note = [str(node["skip"])] if node and "skip" in node else []
    info = preview.plugins[0] if preview.plugins else None
    user_config = manifest.raw.get("userConfig") if isinstance(manifest.raw.get("userConfig"), dict) else {}
    author = manifest.raw.get("author") if isinstance(manifest.raw.get("author"), dict) else {}
    return {
        "id": plugin_id,
        "name": manifest.name,
        "display_name": manifest.display_name,
        "version": version,
        "description": manifest.description,
        "author": str(author.get("name") or ""),
        "source": fetched_detail,
        "sha": sha,
        "skills": _skill_names(preview.skill_roots),
        "commands": sorted(c.name for c in preview.commands),
        "agents": sorted(preview.agents),
        "executable": executable,
        "executable_digest": digest,
        "skipped": (list(info.skipped) if info else list(manifest.skipped)) + node_note,
        "user_config": [
            {
                "key": key,
                "title": str(option.get("title") or key),
                "description": str(option.get("description") or ""),
                "type": str(option.get("type") or "string"),
                "sensitive": bool(option.get("sensitive")),
                "required": bool(option.get("required")),
            }
            for key, option in user_config.items()
            if isinstance(option, dict)
        ],
        "dependencies": list(manifest.raw.get("dependencies") or []),
    }


def diff_summaries(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What an update changes: components added and removed, and whether anything that
    runs code changed — the one change that must be read again (design §8.4)."""
    out: dict[str, Any] = {}
    for key in ("skills", "commands", "agents"):
        before, after = set(old.get(key) or []), set(new.get(key) or [])
        out[key] = {"added": sorted(after - before), "removed": sorted(before - after)}
    out["executable_changed"] = (old.get("executable_digest") or "") != (new.get("executable_digest") or "")
    return out
