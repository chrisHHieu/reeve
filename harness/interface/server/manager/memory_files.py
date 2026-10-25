"""MemoryFilesMixin — the memory directories: the user's and each project's.
"""
from __future__ import annotations

import re
from harness.context.memory.auto_memory import AutoMemory
from typing import Any, Optional



class MemoryFilesMixin:
    def _recent_engine_model(self) -> Optional[str]:
        """The model of the most recently built live session — the closest thing the
        server has to "what the user is running" when the caller names no model. Engines
        are inserted in creation order, so the last one is the newest."""
        for engine in reversed(list(self._engines.values())):
            if getattr(engine, "model", None):
                return engine.model
        return None

    # -- auto memory (machine-local markdown files) ------------------------------
    #
    # The agent's own notes, kept OUTSIDE the workspace (Claude-shaped) so they never
    # show up in `git status`. Keyed by the project ladder (binding > git > path), which
    # the Settings screen reaches by passing a workspace path. Reads never require the
    # toggle; the toggle only governs what a SESSION injects and writes (`_build_engine`).

    def _user_memory(self) -> AutoMemory:
        """Who the person is — one directory for every project."""
        from harness.context.memory.auto_memory import USER

        return AutoMemory(self._data_base / "memory", USER)

    def _memory_dir_for(self, workspace: Optional[str], scope: str = "project") -> AutoMemory:
        """The project directory for `workspace`, or the user's own for scope "user"."""
        if scope == "user":
            files = self._user_memory()
            try:
                files.upgrade()
            except OSError:
                pass
            return files
        return self._auto_memory_for_workspace(workspace)

    def _auto_memory_for(self, key: Optional[str]) -> AutoMemory:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(key or "global")).strip("-")
        if len(safe) > 80:
            # Cut at 80 alone, two deep paths that share their first 80 characters shared
            # one memory (found by test_grant_notice…, 2026-09-28): the readable head stays,
            # a hash of the whole key keeps them apart.
            import hashlib

            safe = f"{safe[:69]}-{hashlib.sha256(str(key).encode('utf-8')).hexdigest()[:10]}"
        return AutoMemory(self._data_base / "projects" / (safe or "global") / "memory")

    def _auto_memory_for_workspace(self, workspace: Optional[str]) -> AutoMemory:
        raw = (workspace or "").strip() or (self.default_workspace or "")
        # The session's own ladder (scratch folders share one key), so the
        # Settings view shows the directory the agent actually reads and writes.
        key = self._memory_key_for(None, raw) if raw else None
        files = self._auto_memory_for(key)
        try:
            files.upgrade()
        except OSError:
            pass
        return files

    def list_auto_memory(self, workspace: Optional[str] = None, scope: str = "project") -> dict[str, Any]:
        try:
            files = self._memory_dir_for(workspace, scope)
            return {"files": files.list_files(), "dir": str(files.root)}
        except ValueError as e:
            return {"files": [], "error": str(e)}

    def read_auto_memory_file(self, workspace: Optional[str], name: str, scope: str = "project") -> dict[str, Any]:
        try:
            return self._memory_dir_for(workspace, scope).read(name)
        except ValueError as e:
            return {"name": name, "exists": False, "error": str(e)}

    def write_auto_memory_file(
        self, workspace: Optional[str], name: str, content: str, mode: str = "overwrite", scope: str = "project"
    ) -> dict[str, Any]:
        try:
            return self._memory_dir_for(workspace, scope).write(name, content, mode=mode)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def delete_auto_memory_file(self, workspace: Optional[str], name: str, scope: str = "project") -> dict[str, Any]:
        """Delete a memory and its index line (the Settings editor and a notice's Undo)."""
        try:
            return self._memory_dir_for(workspace, scope).forget(name)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def restore_auto_memory_file(
        self, workspace: Optional[str], name: str, version: str = "", scope: str = "project"
    ) -> dict[str, Any]:
        """Put a saved version back — the rollback half of memory versioning. Newest
        version by default; the current state is snapshotted first, so restoring is
        itself reversible."""
        try:
            return self._memory_dir_for(workspace, scope).restore(name, version)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def promote_auto_memory_file(self, workspace: Optional[str], name: str) -> dict[str, Any]:
        """Copy one machine-local memory into the project's `.agents/memory/` (Phase 4).

        A USER action surfaced for review: it writes a reviewable export into the repo and
        never links the two copies — the machine-local file stays canonical."""
        raw = (workspace or "").strip() or (self.default_workspace or "")
        if not raw:
            return {"ok": False, "error": "a workspace is required to promote into the repo"}
        try:
            return self._auto_memory_for_workspace(raw).promote(name, workspace_root=raw)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def get_memory_settings(self) -> dict[str, Any]:
        out = self.memory_settings.snapshot()
        # The user's SOUL.md lives as a real file beside the settings — surface the path
        # so Settings can show where it is.
        out["soul_path"] = str(self.memory_settings.soul_path)
        return out

    def set_memory_settings(
        self,
        enabled: Optional[bool] = None,
        user_rules: Optional[str] = None,
        auto_memory: Optional[bool] = None,
    ) -> dict[str, Any]:
        return self.memory_settings.set(
            enabled=enabled,
            user_rules=user_rules,
            auto_memory=auto_memory,
        )
