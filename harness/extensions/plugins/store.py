"""Plugin state on disk (design §6): what is installed, what is enabled where, which
marketplaces are known.

    state_dir()/plugins/            (REEVE_PLUGIN_DIR overrides it)
      cache/<marketplace>/<plugin>/<version>/   an installed version (${CLAUDE_PLUGIN_ROOT})
      data/<plugin-id>/                          ${CLAUDE_PLUGIN_DATA}, kept across updates
      marketplaces/<name>/                       a fetched marketplace
      staged/<token>/                            a previewed install, waiting for consent
      installed.json  marketplaces.json  settings.json  config.json  consent.json
      flagged.json    plugins a marketplace stopped listing (they stop loading)
      updates.json    updates auto-update found but left for a person (code changed)

Every file is written atomically (a temp file, then `os.replace`) and owner-only, like
`HookTrustStore`. A missing or torn file reads as empty: plugin state must never stop a
session from starting. Only a person changes any of it — the paths are protected from the
agent (`boundary.protected_paths`, design §9.6).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

ENV_ROOT = "REEVE_PLUGIN_DIR"
STATE_FILES = (
    "installed.json",
    "marketplaces.json",
    "settings.json",
    "config.json",
    "consent.json",
    "flagged.json",
    "updates.json",
)


def plugins_dir() -> Path:
    override = os.environ.get(ENV_ROOT, "").strip()
    if override:
        return Path(override).expanduser()
    from harness.safety.secrets import state_dir

    return state_dir() / "plugins"


def read_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def safe_id(plugin_id: str) -> str:
    """A plugin id as one path segment (design §4.6)."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", plugin_id)


# -- installed.json ---------------------------------------------------------------------------


def installed() -> dict[str, list[dict[str, Any]]]:
    data = read_json(plugins_dir() / "installed.json", {})
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def save_installed(plugins: dict[str, list[dict[str, Any]]]) -> None:
    write_json(plugins_dir() / "installed.json", {"version": 1, "plugins": plugins})


def install_record(plugin_id: str) -> Optional[dict[str, Any]]:
    """The record a session loads for this id: the most recently updated one."""
    records = [r for r in installed().get(plugin_id, []) if isinstance(r, dict)]
    return max(records, key=lambda r: str(r.get("updatedAt") or ""), default=None)


def install_root(record: dict[str, Any]) -> Path:
    """Where an installed version lives: a cache path (relative) or a local folder."""
    path = Path(str(record.get("installPath") or ""))
    return path if path.is_absolute() else plugins_dir() / path


# -- enabled state, per scope (design §6.3) ------------------------------------------------------


def _enabled_map(path: Path, key: str = "enabledPlugins") -> dict[str, bool]:
    data = read_json(path, {})
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, bool)}


def scope_file(scope: str, workspace: Optional[str | Path] = None) -> Optional[Path]:
    """The file a person's choice at `scope` is written to (None: a scope nobody writes)."""
    if scope == "user":
        return plugins_dir() / "settings.json"
    if scope in ("project", "local") and workspace:
        name = "plugins.json" if scope == "project" else "plugins.local.json"
        return Path(workspace).expanduser() / ".reeve" / name
    return None


def enabled_layers(workspace: Optional[str | Path] = None) -> list[tuple[str, dict[str, bool]]]:
    """Every place that says a plugin is on or off, LOW → HIGH. A higher layer that names
    an id wins it. `.claude/` files are read (Claude Code compatibility), never written,
    and rank under their `.reeve/` counterparts. `managed` is an administrator's."""
    from harness.safety.secrets import state_dir

    layers: list[tuple[str, dict[str, bool]]] = [("user", _enabled_map(plugins_dir() / "settings.json"))]
    if workspace:
        ws = Path(workspace).expanduser()
        layers += [
            ("project", _enabled_map(ws / ".claude" / "settings.json")),
            ("project", _enabled_map(ws / ".reeve" / "plugins.json")),
            ("local", _enabled_map(ws / ".claude" / "settings.local.json")),
            ("local", _enabled_map(ws / ".reeve" / "plugins.local.json")),
        ]
    layers.append(("managed", _enabled_map(state_dir() / "managed" / "plugins.json")))
    return layers


def effective_enabled(
    plugin_id: str, workspace: Optional[str | Path] = None, default: bool = True
) -> tuple[bool, str]:
    """(on?, the scope that decided it). `managed` cannot be overridden."""
    value, scope = default, "default"
    for name, layer in enabled_layers(workspace):
        if plugin_id in layer:
            value, scope = layer[plugin_id], name
    return value, scope


def set_enabled(
    plugin_id: str, enabled: bool, scope: str = "user", workspace: Optional[str | Path] = None
) -> None:
    path = scope_file(scope, workspace)
    if path is None:
        raise ValueError(f"cannot write the {scope!r} scope")
    data = read_json(path, {})
    enabled_plugins = data.get("enabledPlugins") if isinstance(data.get("enabledPlugins"), dict) else {}
    enabled_plugins[plugin_id] = bool(enabled)
    data["enabledPlugins"] = enabled_plugins
    write_json(path, data)


def forget_enabled(plugin_id: str, scope: str, workspace: Optional[str | Path] = None) -> None:
    path = scope_file(scope, workspace)
    if path is None or not path.is_file():
        return
    data = read_json(path, {})
    enabled_plugins = data.get("enabledPlugins")
    if isinstance(enabled_plugins, dict) and plugin_id in enabled_plugins:
        enabled_plugins.pop(plugin_id)
        write_json(path, data)


# -- marketplaces.json ---------------------------------------------------------------------------


def marketplaces() -> dict[str, dict[str, Any]]:
    data = read_json(plugins_dir() / "marketplaces.json", {})
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def save_marketplaces(value: dict[str, dict[str, Any]]) -> None:
    write_json(plugins_dir() / "marketplaces.json", value)


# -- consent.json: which code a person agreed to run (design §9.3, §9.4) ---------------------------


def _consents() -> dict[str, Any]:
    data = read_json(plugins_dir() / "consent.json", {})
    return data if isinstance(data, dict) else {}


def _canonical(workspace: str | Path) -> str:
    return str(Path(workspace).expanduser().resolve())


def consented(plugin_id: str, digest: str, workspace: Optional[str | Path] = None) -> bool:
    """Whether a person agreed to run exactly this code: everywhere, or — for a plugin a
    project turned on — in this workspace."""
    entry = _consents().get(plugin_id)
    if not isinstance(entry, dict) or not digest:
        return False
    if workspace is None:
        return entry.get("digest") == digest
    return entry.get("everywhere_digest") == digest or (entry.get("workspaces") or {}).get(_canonical(workspace)) == digest


def record_consent(
    plugin_id: str, digest: str, *, workspace: Optional[str | Path] = None, everywhere: bool = False
) -> None:
    data = _consents()
    entry = data.get(plugin_id) if isinstance(data.get(plugin_id), dict) else {}
    if workspace is None:
        entry["digest"] = digest
    else:
        entry.setdefault("workspaces", {})[_canonical(workspace)] = digest
    if everywhere:
        entry["everywhere_digest"] = digest
    data[plugin_id] = entry
    write_json(plugins_dir() / "consent.json", data)


def forget_consent(plugin_id: str) -> None:
    data = _consents()
    if data.pop(plugin_id, None) is not None:
        write_json(plugins_dir() / "consent.json", data)


# -- flagged.json / updates.json (design §6.1, §8.4) ---------------------------------------------


def _id_map(name: str) -> dict[str, dict[str, Any]]:
    data = read_json(plugins_dir() / name, {})
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def flagged() -> dict[str, dict[str, Any]]:
    """Installed plugins their marketplace no longer lists: {id: {marketplace, flaggedAt, kept?}}."""
    return _id_map("flagged.json")


def save_flagged(value: dict[str, dict[str, Any]]) -> None:
    write_json(plugins_dir() / "flagged.json", value)


def waiting_updates() -> dict[str, dict[str, Any]]:
    """Updates auto-update found and did not apply: {id: {version, reason, checkedAt}}."""
    return _id_map("updates.json")


def save_waiting_updates(value: dict[str, dict[str, Any]]) -> None:
    write_json(plugins_dir() / "updates.json", value)


# -- old versions (design §8.4) --------------------------------------------------------------------

ORPHAN_DAYS = 14


def orphan(record: dict[str, Any]) -> None:
    """Mark an old version for removal; a running session may still read it."""
    from datetime import datetime, timezone

    path = install_root(record)
    if path.is_dir() and plugins_dir() / "cache" in path.parents:
        (path / ".orphaned_at").write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8")
    sweep_orphans()


def sweep_orphans() -> None:
    """Remove the versions marked more than ORPHAN_DAYS ago."""
    import shutil
    import time

    cache = plugins_dir() / "cache"
    if not cache.is_dir():
        return
    for marker in cache.rglob(".orphaned_at"):
        try:
            if time.time() - marker.stat().st_mtime > ORPHAN_DAYS * 86400:
                shutil.rmtree(marker.parent, ignore_errors=True)
        except OSError:
            continue


def list_installed(workspace: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    marks, waiting = flagged(), waiting_updates()
    for plugin_id, records in sorted(installed().items()):
        record = install_record(plugin_id) or {}
        enabled, decided = effective_enabled(plugin_id, workspace)
        out.append(
            {
                "id": plugin_id,
                "version": record.get("version", ""),
                "scopes": sorted({str(r.get("scope")) for r in records}),
                "enabled": enabled,
                "enabled_by": decided,
                "install_path": str(install_root(record)) if record else "",
                **({"flagged": marks[plugin_id]} if plugin_id in marks else {}),
                **({"warnings": list(record["warnings"])} if record.get("warnings") else {}),
                **({"update_waiting": waiting[plugin_id]} if plugin_id in waiting else {}),
            }
        )
    return out
