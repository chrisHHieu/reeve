"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
import asyncio
from typing import Any


def register_workspaces(app, manager):
        @app.get("/v1/workspaces/recent")
        def recent_workspaces() -> dict[str, Any]:
            return {"workspaces": manager.recent_workspaces()}
        @app.post("/v1/workspaces/open")
        def open_workspace(body: dict) -> dict[str, Any]:
            return manager.open_workspace(
                body.get("path", ""), create=bool(body.get("create"))
            )
        @app.get("/v1/workspaces/trusted")
        def trusted_workspaces() -> dict[str, Any]:
            return {"workspaces": manager.trusted_workspaces()}
        @app.post("/v1/workspaces/trust")
        def set_workspace_trust(body: dict) -> dict[str, Any]:
            return manager.set_workspace_trust(
                str((body or {}).get("path", "")),
                trusted=bool((body or {}).get("trusted", False)),
            )
        @app.post("/v1/workspaces/temp")
        def provision_temp_workspace(body: dict) -> dict[str, Any]:
            # A code-family session starting "in a temporary folder" — created only
            # at send time, with git ready. Knowledge families keep their auto-provisioned dir.
            return manager.provision_temp_workspace(
                str((body or {}).get("session_id", "")),
                git=bool((body or {}).get("git", True)),
            )
        @app.post("/v1/sessions/{session_id}/save-as-project")
        def save_session_as_project(session_id: str, body: dict) -> dict[str, Any]:
            # "Save as project…": move the temporary folder somewhere real. The GUI
            # reconnects afterwards so the engine rebinds to the new path.
            return manager.save_temp_as_project(session_id, str((body or {}).get("path", "")))
        @app.post("/v1/workspaces/pick")
        async def pick_workspace() -> dict[str, Any]:
            # Native folder picker opened by the LOCAL sidecar (browser GUIs can't get absolute
            # paths from web file dialogs). Off the event loop: blocks until pick/cancel.
            return await asyncio.to_thread(manager.pick_native_folder)
        # The branch picker: the person's branch, per folder.
        @app.get("/v1/workspaces/branches")
        def workspace_branches(path: str = "") -> dict[str, Any]:
            return manager.workspace_branches(path)
        @app.post("/v1/workspaces/switch-branch")
        def workspace_switch_branch(body: dict) -> dict[str, Any]:
            b = body or {}
            return manager.workspace_switch_branch(str(b.get("path", "")), str(b.get("branch", "")), create=bool(b.get("create")))
        @app.post("/v1/workspaces/worktree")
        def workspace_new_worktree(body: dict) -> dict[str, Any]:
            b = body or {}
            return manager.workspace_new_worktree(str(b.get("path", "")), str(b.get("branch", "")), create=bool(b.get("create")))
        @app.get("/v1/workspaces/worktrees")
        def workspace_worktrees(path: str = "") -> dict[str, Any]:
            # The `/workspace` section: list the current
            # folder's git worktrees. Read-only; a non-repo answers `is_git: false`.
            return manager.workspace_worktrees(path or None)
