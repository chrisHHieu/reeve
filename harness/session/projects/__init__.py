"""Project identity: the key workspace memory is stored under, and the names people give it."""

from __future__ import annotations

from typing import Any, Optional

from .identity import project_key, project_label
from .names import MAX_NAME_CHARS, NAME_KINDS, ProjectNames

__all__ = [
    "MAX_NAME_CHARS",
    "NAME_KINDS",
    "ProjectNames",
    "project_key",
    "project_label",
    "project_presence",
    "resolve_memory_key",
]


def _bound_key(kind: str, binding: Optional[str], names: Optional[ProjectNames]) -> Optional[str]:
    """The key an explicit binding points at, marking it used, or None when there is no such binding."""
    if binding and names is not None:
        key = names.resolve(kind, binding)
        if key:
            names.touch(kind, binding)
            return key
    return None


def resolve_memory_key(
    workspace: Optional[str], *, binding: Optional[str] = None, names: Optional[ProjectNames] = None
) -> Optional[str]:
    """The identity ladder for memory: an explicit binding, then git, then the path."""
    bound = _bound_key("memory", binding, names)
    if bound:
        return bound
    return project_key(workspace) if workspace else None


def project_presence(key: str, *, memory: Any = None) -> dict[str, int]:
    """What already exists under a project key, for the notice shown when a folder is granted. These
    are counts and never content. `memory` is the project's memory directory (`AutoMemory`)."""
    memories = 0
    if memory is not None:
        try:
            memories = sum(1 for entry in memory.list_files() if entry["name"] != "MEMORY.md")
        except Exception:
            pass
    return {"memories": memories}
