"""What a project asks of this machine's plugins (design §6.3, §7.3, §11.1).

A project can declare plugins (`enabledPlugins` in `.reeve/plugins.json`, or Claude Code's
`.claude/settings.json`) and suggest marketplaces (`extraKnownMarketplaces`), never install
either. `project_plugins(workspace)` says what that leaves a person to do here — the
session's banner and the Plugins page's "This project" card read it:

- a declared plugin that is not installed (install it, or ignore it),
- one whose code this workspace has not agreed to (review it),
- a suggested marketplace this machine does not know yet (add it).

None of it is read before the workspace is trusted: an untrusted project's declarations do
not count (design §9.4), so there is nothing to show for them but the trust question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import store

#: The files a project's suggestions come from, LOW → HIGH (the `.reeve/` one wins a name).
_SUGGESTION_FILES = (
    Path(".claude") / "settings.json",
    Path(".reeve") / "plugins.json",
)


def _source_text(raw: Any) -> str:
    """A suggested marketplace's source as `add_marketplace` takes it ('' when it cannot).

    Claude Code writes `{"source": {"source": "github", "repo": …}}`, `"git"`/`"url"` with
    a `url`, or `"directory"` with a `path`; a bare string is taken as typed."""
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return ""
    inner = raw.get("source") if isinstance(raw.get("source"), dict) else raw
    kind = str(inner.get("source") or "")
    if kind == "github":
        return str(inner.get("repo") or "").strip()
    if kind in ("git", "url"):
        return str(inner.get("url") or "").strip()
    if kind == "directory":
        return str(inner.get("path") or "").strip()
    return ""


def suggested_marketplaces(workspace: str | Path) -> list[dict[str, Any]]:
    ws = Path(workspace).expanduser()
    found: dict[str, dict[str, Any]] = {}
    for rel in _SUGGESTION_FILES:
        data = store.read_json(ws / rel, {})
        extra = data.get("extraKnownMarketplaces") if isinstance(data, dict) else None
        if not isinstance(extra, dict):
            continue
        for name, raw in extra.items():
            source = _source_text(raw)
            if not source:
                continue
            path = Path(source).expanduser()
            if not path.is_absolute() and (source.startswith("./") or source.startswith("../")):
                source = str((ws / path).resolve())  # a folder named relative to the project
            found[str(name)] = {"name": str(name), "source": source, "file": rel.as_posix()}
    known = store.marketplaces()
    return [{**row, "known": row["name"] in known} for _, row in sorted(found.items())]


def trusted_workspace(workspace: Any) -> bool:
    """Whether a project's plugin choices count here (design §9.4)."""
    if not workspace:
        return False
    try:
        from harness.safety.workspace_trust import WorkspaceTrustStore

        return WorkspaceTrustStore().is_trusted(workspace)
    except Exception:
        return False


def project_plugins(workspace: Optional[str | Path]) -> dict[str, Any]:
    if not workspace:
        return {"trusted": False, "plugins": [], "marketplaces": [], "pending": 0}
    from .resolve import resolve_plugins

    if not trusted_workspace(workspace):
        return {"trusted": False, "plugins": [], "marketplaces": [], "pending": 0}
    declared: dict[str, str] = {}
    for scope, layer in store.enabled_layers(workspace):
        if scope in ("project", "local"):
            for plugin_id, on in layer.items():
                if on and "@" in plugin_id:
                    declared[plugin_id] = scope
                elif not on:
                    declared.pop(plugin_id, None)
    resolved = resolve_plugins(workspace=workspace)
    loaded = {p.id: p for p in resolved.plugins}
    installed = store.installed()
    rows = []
    for plugin_id in sorted(declared):
        info = loaded.get(plugin_id)
        if plugin_id not in installed:
            state = "missing"
        elif info is not None and info.code == "needs_consent":
            state = "needs_consent"
        elif info is not None:
            state = "ready"
        else:
            state = "not_loaded"
        rows.append({"id": plugin_id, "scope": declared[plugin_id], "state": state})
    markets = suggested_marketplaces(workspace)
    pending = sum(1 for r in rows if r["state"] in ("missing", "needs_consent")) + sum(
        1 for m in markets if not m["known"]
    )
    return {"trusted": True, "plugins": rows, "marketplaces": markets, "pending": pending}
