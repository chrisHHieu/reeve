"""Memory routes: the switches and SOUL.md, and the two memory directories —
the user's (`scope=user`) and each project's — as files."""
from __future__ import annotations

from __future__ import annotations
from typing import Any, Optional


def register_memory(app, manager):
        @app.get("/v1/memory/settings")
        def memory_settings() -> dict[str, Any]:
            return manager.get_memory_settings()
        @app.put("/v1/memory/settings")
        def memory_settings_put(body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.set_memory_settings(
                enabled=bool(body["enabled"]) if "enabled" in body else None,
                user_rules=str(body["user_rules"]) if "user_rules" in body else None,
                auto_memory=bool(body["auto_memory"]) if "auto_memory" in body else None,
            )
        # `scope`: "project" (the workspace's notes, the default) or "user" — who the
        # person is, one directory for every project. Anything else is project.
        def _scope(value: Any) -> str:
            return "user" if str(value or "") == "user" else "project"

        @app.get("/v1/memory/auto")
        def auto_memory_list(workspace: str = "", scope: str = "") -> dict[str, Any]:
            return manager.list_auto_memory(workspace or None, _scope(scope))
        @app.get("/v1/memory/auto/file")
        def auto_memory_read(workspace: str = "", name: str = "", scope: str = "") -> dict[str, Any]:
            return manager.read_auto_memory_file(workspace or None, name, _scope(scope))
        @app.put("/v1/memory/auto/file")
        def auto_memory_write(body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.write_auto_memory_file(
                body.get("workspace") or None,
                str(body.get("name", "")),
                str(body.get("content", "")),
                str(body.get("mode", "overwrite")),
                _scope(body.get("scope")),
            )
        @app.delete("/v1/memory/auto/file")
        def auto_memory_delete(workspace: str = "", name: str = "", scope: str = "") -> dict[str, Any]:
            return manager.delete_auto_memory_file(workspace or None, name, _scope(scope))
        @app.post("/v1/memory/auto/restore")
        def auto_memory_restore(body: dict) -> dict[str, Any]:
            # The rollback half of memory versioning: every destructive write/delete kept
            # a snapshot, and this puts one back (newest by default).
            body = body or {}
            return manager.restore_auto_memory_file(
                body.get("workspace") or None,
                str(body.get("name", "")),
                str(body.get("version", "")),
                _scope(body.get("scope")),
            )
        @app.post("/v1/memory/auto/promote")
        def auto_memory_promote(body: dict) -> dict[str, Any]:
            # Phase 4: copy one machine-local memory into the project's `.agents/memory/`.
            # A user action (review surface), never something the agent triggers.
            body = body or {}
            return manager.promote_auto_memory_file(
                body.get("workspace") or None, str(body.get("name", ""))
            )
