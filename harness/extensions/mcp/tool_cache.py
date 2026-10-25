"""The last tool list each MCP server answered with, kept on disk.

A session's tool list leads every request it sends, so it is the first thing a provider's
prompt cache matches on. Until this existed that list followed the connection: an engine
rebuilt after a restart, or after a long idle, waited up to twenty seconds for each server,
and one that was not up in time simply had no tools that turn. They joined on the next —
a second full-price read of the whole conversation — and the turn in between ran without
the tools the conversation had been using.

What a server offers changes rarely; whether it is connected at this instant changes all
the time. So the list a server last answered with is what a session is told, and the
connection is made when a tool is called (`MCPManager.call` connects before the call when
there is no connection). A live list replaces the kept one whenever one arrives.

A kept list belongs to an ENDPOINT, not to a name: a server re-pointed at another url
answers with different tools, and the list kept under its name is then not used. The
endpoint is stored as a digest, because its headers can carry a credential.

Best-effort on both sides: a list that cannot be written or read costs nothing but the old
behaviour.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from harness.safety.secrets import state_dir
from .client import connection_key
from .config import MCPServerDef

logger = logging.getLogger(__name__)

#: The annotations `mcp/tools.py` reads to decide approval and overlap. Nothing else a
#: server says about a tool is used here, so nothing else is kept.
_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint")

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")

#: A list larger than this is not kept: it is not a tool list any server in use sends, and
#: the file is read in front of a turn.
MAX_BYTES = 4 * 1024 * 1024


def endpoint_digest(server: MCPServerDef) -> str:
    """What the kept list was answered BY, as a digest that reveals none of it."""
    return hashlib.sha256(repr(connection_key(server)).encode("utf-8")).hexdigest()


def _plain(tool: Any) -> Optional[dict[str, Any]]:
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name:
        return None
    out: dict[str, Any] = {"name": name}
    description = getattr(tool, "description", None)
    if isinstance(description, str) and description:
        out["description"] = description
    schema = getattr(tool, "inputSchema", None)
    if isinstance(schema, dict):
        out["inputSchema"] = schema
    annotations = getattr(tool, "annotations", None)
    hints = {
        hint: bool(getattr(annotations, hint))
        for hint in _HINTS
        if annotations is not None and getattr(annotations, hint, None) is not None
    }
    if hints:
        out["annotations"] = hints
    return out


def _tool(raw: Any) -> Optional[SimpleNamespace]:
    """A kept tool in the shape `build_callables` reads a live one in."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        return None
    hints = raw.get("annotations")
    return SimpleNamespace(
        name=raw["name"],
        description=raw.get("description") if isinstance(raw.get("description"), str) else None,
        inputSchema=raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else None,
        annotations=(
            SimpleNamespace(**{hint: bool(hints.get(hint, False)) for hint in _HINTS})
            if isinstance(hints, dict) and hints
            else None
        ),
    )


class ToolListCache:
    """One JSON file per server name under `<state>/mcp-tools/`."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else state_dir() / "mcp-tools"

    def _path(self, name: str) -> Path:
        # The name is a config key a person typed; the digest keeps two names that
        # sanitise to the same string in two files.
        tag = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        return self.directory / f"{_UNSAFE.sub('_', name)[:48]}-{tag}.json"

    def save(self, server: MCPServerDef, tools: Any) -> bool:
        """Keep `tools` as what `server` offers. True when the file now holds them."""
        try:
            plain = [entry for entry in (_plain(tool) for tool in tools or ()) if entry]
            if not plain:
                return False  # a server that listed nothing is not worth stating from memory
            body = json.dumps(
                {"endpoint": endpoint_digest(server), "tools": plain},
                ensure_ascii=False,
                sort_keys=True,
            )
            if len(body.encode("utf-8")) > MAX_BYTES:
                return False
            path = self._path(server.name)
            try:
                if path.read_text(encoding="utf-8") == body:
                    return True  # the common case: nothing changed, nothing written
            except OSError:
                pass
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, path)
            return True
        except Exception:  # noqa: BLE001 — keeping a list must never cost a connection
            logger.debug("mcp tool list for %s not kept", server.name, exc_info=True)
            return False

    def load(self, server: MCPServerDef) -> Optional[list[SimpleNamespace]]:
        """The list `server` last answered with, or None when nothing usable is kept:
        no file, a file for another endpoint, or one that does not read as a list."""
        try:
            path = self._path(server.name)
            if path.stat().st_size > MAX_BYTES:
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            if not isinstance(raw, dict) or raw.get("endpoint") != endpoint_digest(server):
                return None
            tools = [tool for tool in (_tool(entry) for entry in raw.get("tools") or ()) if tool]
        except Exception:  # noqa: BLE001 — an unreadable entry is the same as no file
            return None
        return tools or None

    def forget(self, name: str) -> None:
        try:
            self._path(name).unlink(missing_ok=True)
        except OSError:
            pass
