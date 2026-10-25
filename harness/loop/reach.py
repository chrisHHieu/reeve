"""Reach: the folders a call touches outside the session, and adding them before it runs.

A file tool stays scoped to the session's roots. When the permission engine allows a call that
reaches another folder (a person approved it, or the session is in full access), that folder's
project becomes a root first, so the reach is on the one list the tools, the engine and the Files
panel read, and nothing reaches a folder that is not on it. The root is marked `reached`: the
session took it, nobody marked it, so its read-only access is where it starts and not a refusal.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any

from harness.config.roots import RootDir
from harness.llm.providers import ToolCall
from harness.safety.permissions.guards import out_of_root_reads, out_of_root_writes
from harness.safety.risk import classify


def reach_of(engine: Any, tool_call: ToolCall) -> list[dict[str, Any]]:
    """`[{path, writable}]`: the folders `extend_reach` would add for this call."""
    permissions = engine.permissions
    boundary = getattr(permissions, "boundary", None)
    if boundary is None:
        return []
    risk = classify(tool_call.name, None, getattr(permissions, "risk_overrides", None))
    reads = out_of_root_reads(permissions, tool_call.name, tool_call.arguments)
    writes = out_of_root_writes(permissions, tool_call.name, tool_call.arguments, risk)
    world = getattr(engine, "world", None)
    if getattr(world, "is_remote", False):
        found = _remote_folders(world, tool_call.name, reads, writes)
    else:
        found = {}
        for path in reads:
            found.setdefault(str(boundary.read_folder(path)), False)
        for path in writes:
            found[str(boundary.read_folder(path))] = True
    return [{"path": path, "writable": writable} for path, writable in sorted(found.items())]


def extend_reach(engine: Any, tool_call: ToolCall) -> None:
    """An allowed call that reaches outside the session's folders: their projects become roots
    of the session BEFORE it runs, read-only for a read and writable for a write, and
    marked `reached`. A later write into a project only read is asked like any write outside the
    writable roots (full access runs it), and once allowed the same root turns writable.

    The file tools stay scoped to the roots; what grows is the roots list, the one record the
    tools, the permission engine and the Files panel all read. So the reach a person approved
    (or full access ran) is visible afterwards and removable there, and nothing reaches a
    folder that is not on that list. The host's `root_granter` adds a folder the way one added
    by hand is added (persisted, announced to the panel); without one (the CLI, a test) the
    folder joins the live roots list directly."""
    reach = reach_of(engine, tool_call)
    if not reach:
        return
    granter = getattr(engine, "root_granter", None)
    remote = getattr(getattr(engine, "world", None), "is_remote", False)
    for entry in reach:
        if granter is not None:
            try:
                granter(entry["path"], entry["writable"])
                continue
            except Exception:  # noqa: BLE001
                # The host could not save it as a folder of the session (a folder a write is
                # about to create does not exist yet). The call keeps its reach for this run.
                pass
        if not remote:
            # A remote path cannot be resolved here; the remote backend's own scoping answers.
            _add_reached(engine.permissions.roots, entry["path"], entry["writable"])


def _add_reached(roots: list, path: str, writable: bool) -> None:
    """The host-less add (the CLI, a test): the same root widened in place, else a new one."""
    resolved = Path(path).expanduser().resolve()
    for root in roots:
        if getattr(root, "path", None) == resolved:
            root.writable = bool(getattr(root, "writable", False) or writable)
            return
    roots.append(RootDir(path=resolved, writable=writable, reached=True))


#: The file tools whose path names a folder (the rest name a file, whose folder is granted).
_FOLDER_TOOLS = frozenset({"list_files", "grep", "search_files"})


def _remote_folders(world: Any, tool_name: str, reads: list[str], writes: list[str]) -> dict[str, bool]:
    """The POSIX folders an out-of-root call on a remote session reaches, `{folder: writable}`."""
    base = str(getattr(world, "root", "") or "/")

    def absolute(path: str) -> str:
        return posixpath.normpath(path if posixpath.isabs(path) else posixpath.join(base, path))

    wanted: dict[str, bool] = {}
    for path in reads:
        target = absolute(path)
        wanted.setdefault(target if tool_name in _FOLDER_TOOLS else posixpath.dirname(target), False)
    for path in writes:
        wanted[posixpath.dirname(absolute(path))] = True
    return wanted
