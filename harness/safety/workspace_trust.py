"""The workspaces the person has said they trust.

A repository's `.reeve/config.toml` may list command prefixes to allow, and they count only once
the person trusts that very workspace, by its resolved path. What is trusted is the path, not the
file as it was: a later change in a trusted workspace holds until the person takes the trust back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .secrets import state_dir

_KEY = "trusted_workspaces"


class WorkspaceTrustStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = state_dir() / "workspace_trust.json" if path is None else Path(path)

    @staticmethod
    def canonical(path: str | Path) -> str:
        """The form a workspace is trusted under: `~` expanded, links and `..` resolved."""
        return str(Path(path).expanduser().resolve())

    def _load(self) -> set[str]:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        listed = stored.get(_KEY) if isinstance(stored, dict) else None
        return {entry for entry in listed if isinstance(entry, str) and entry} if isinstance(listed, list) else set()

    def is_trusted(self, workspace: str | Path) -> bool:
        return self.canonical(workspace) in self._load()

    def list(self) -> list[str]:
        return sorted(self._load())

    def set_trusted(self, workspace: str | Path, trusted: bool) -> str:
        """Trust the workspace or stop trusting it; returns the path as recorded. Written to a
        temporary file only this user can read, then moved over the old one."""
        canonical = self.canonical(workspace)
        workspaces = self._load()
        (workspaces.add if trusted else workspaces.discard)(canonical)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        pending.write_text(json.dumps({_KEY: sorted(workspaces)}, indent=2) + "\n", encoding="utf-8")
        os.chmod(pending, 0o600)
        pending.replace(self.path)
        return canonical
