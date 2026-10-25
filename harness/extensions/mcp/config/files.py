"""The global `mcp.json`, read and written as raw entries (the REST edits target this file)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.safety.secrets import state_dir


def global_mcp_path() -> Path:
    return state_dir() / "mcp.json"


def read_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_global() -> dict[str, dict[str, Any]]:
    """The raw `mcpServers` map from the global file, with no `${VAR}` resolution."""
    return dict(read_file(global_mcp_path()).get("mcpServers") or {})


def _write_global(servers: dict[str, dict[str, Any]]) -> None:
    path = global_mcp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    scratch.replace(path)


def put_global_server(name: str, config: dict[str, Any]) -> None:
    servers = read_global()
    servers[name] = config
    _write_global(servers)


def patch_global_server(name: str, changes: dict[str, Any]) -> bool:
    servers = read_global()
    if name not in servers:
        return False
    servers[name] = {**servers[name], **changes}
    _write_global(servers)
    return True


def delete_global_server(name: str) -> bool:
    servers = read_global()
    if name not in servers:
        return False
    del servers[name]
    _write_global(servers)
    return True
